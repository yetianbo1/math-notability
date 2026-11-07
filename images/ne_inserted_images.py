"""Editable image overlays and legacy private-layer reading.

App saves promote overlays to native image blocks through ``native=True``.
The default private format remains available for reading/testing old archives.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import struct
from pathlib import Path
import tempfile
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from images.ne_image_resize import has_original_aspect

PREFIX = 'NotabilityViewer/inserted-images/'
MANIFEST = PREFIX + 'manifest.json'
MAX_ASSET = 20 * 1024 * 1024
MAX_IMAGES = 500


def _valid_item(item):
    if not isinstance(item, dict) or set(item) != {'id','asset','x','y','width','height','locked'}:
        raise ValueError('图片记录字段不符合规范')
    if not isinstance(item['id'],str) or len(item['id']) != 32 or any(c not in '0123456789abcdef' for c in item['id']):
        raise ValueError('图片编号不合法')
    if not isinstance(item['asset'],str) or len(item['asset'])!=64 or any(c not in '0123456789abcdef' for c in item['asset']):
        raise ValueError('图片哈希不合法')
    if type(item['locked']) is not bool: raise ValueError('图片锁定状态不合法')
    if any(type(item[n]) not in (int,float) or not math.isfinite(item[n]) or abs(item[n])>1e6 for n in ('x','y','width','height')):
        raise ValueError('图片坐标不合法')
    if item['width']<=0 or item['height']<=0:raise ValueError('图片尺寸不合法')


def _validate_page(page):
    if not isinstance(page,dict) or set(page) != {'stack','images'} or not isinstance(page['stack'],list) or not isinstance(page['images'],list):
        raise ValueError('图片图层数据格式错误')
    images={}
    for item in page['images']:
        _valid_item(item)
        if item['id'] in images:raise ValueError('重复的图片编号')
        images[item['id']]=item
    if len(page['stack'])!=len(images)+1 or page['stack'].count('ink')!=1 or set(page['stack'])!=set(images)|{'ink'}:
        raise ValueError('图层顺序必须包含每张图片和唯一笔迹层')
    return images


def read_archive(path, page_count):
    """Returns (per-page image layers, validated PNG byte assets)."""
    with ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('NTB 含重复的 ZIP 成员')
        if MANIFEST not in names:
            return [{'stack':['ink'],'images':[]} for _ in range(page_count)],{}
        if archive.getinfo(MANIFEST).file_size>256*1024:
            raise ValueError('图片图层清单异常庞大')
        obj=json.loads(archive.read(MANIFEST))
        if not isinstance(obj,dict) or obj.get('version')!=1 or not isinstance(obj.get('pages'),list) or len(obj['pages'])!=page_count:
            raise ValueError('图层与 NTB 页数不一致；为保护数据已停止读取')
        assets=set()
        for page in obj['pages']:
            assets.update(item['asset'] for item in _validate_page(page).values())
        if sum(len(page['images']) for page in obj['pages'])>MAX_IMAGES:raise ValueError('插入图片数量超过上限')
        data={}
        for digest in assets:
            name=PREFIX+digest+'.png'
            if name not in names or archive.getinfo(name).file_size>MAX_ASSET:
                raise ValueError('插入的图片素材缺失或超出限制')
            blob=archive.read(name)
            if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('插图 SHA-256 校验失败')
            if len(blob)<24 or blob[:8]!=b'\x89PNG\r\n\x1a\n' or blob[12:16]!=b'IHDR':
                raise ValueError('图片不是有效的 PNG 文件')
            width,height=struct.unpack_from('>II',blob,16)
            if not width or not height or width*height>16_000_000:
                raise ValueError('图片尺寸超出安全限制')
            data[digest]=blob
        return obj['pages'],data


class ImageLayers:
    """Bottom-to-top order includes the ink anchor; background is always below it."""
    def __init__(self,pages=None,assets=None):
        pages=pages or []
        self.pages={i:copy.deepcopy(page) for i,page in enumerate(pages)}
        self.assets=dict(assets or {})
        self.visible_keys=tuple(range(len(pages)))
        self._saved=self._digest()

    def _page(self,key):
        if key not in self.pages:self.pages[key]={'stack':['ink'],'images':[]}
        return self.pages[key]

    def _digest(self):
        visible=[self._page(key) for key in self.visible_keys]
        return json.dumps(visible,sort_keys=True,ensure_ascii=False,separators=(',',':'))

    def set_order(self,keys):
        self.visible_keys=tuple(keys)

    @property
    def dirty(self):return self._digest()!=self._saved

    def mark_saved(self):self._saved=self._digest()

    def items(self,key):return self._page(key)['images']

    def item(self,key,identifier):
        return next((i for i in self.items(key) if i['id']==identifier),None)

    def insert(self,key,blob,x,y,width,height):
        if len(blob)>MAX_ASSET:raise ValueError('图片数据超过 20 MB 限制')
        if sum(len(p['images']) for p in self.pages.values())>=MAX_IMAGES:raise ValueError('最多插入 500 张图片')
        digest=hashlib.sha256(blob).hexdigest(); self.assets[digest]=bytes(blob)
        item={'id':uuid.uuid4().hex,'asset':digest,'x':float(x),'y':float(y),
              'width':float(width),'height':float(height),'locked':False}
        _valid_item(item)
        page=self._page(key)
        # Default = bottom-most editable layer, directly above immovable background.
        page['images'].append(item);page['stack'].insert(0,item['id'])
        return item['id']

    def move(self,key,identifier,x,y):
        item=self.item(key,identifier)
        if item is None or item['locked']:return False
        if any(not math.isfinite(v) or abs(v)>1e6 for v in (x,y)):return False
        if item['x']==x and item['y']==y:return False
        item['x'],item['y']=float(x),float(y);return True

    def resize(self,key,identifier,x,y,width,height,*,page_size=None):
        """Change the selected image's display rectangle, never its PNG pixels."""
        item=self.item(key,identifier)
        if item is None or item['locked']:return False
        candidate=dict(item,x=float(x),y=float(y),width=float(width),height=float(height))
        _valid_item(candidate)
        # Every resize must preserve the image's PRE-EDIT visible proportions.
        # Position-only updates are unaffected. This prevents distorted images
        # from entering our NTB overlay via any non-GUI entry point.
        if not has_original_aspect(item['width'], item['height'],
                                   candidate['width'], candidate['height']):
            return False
        if page_size is not None:
            pw,ph=page_size
            if (not all(math.isfinite(float(v)) for v in (pw,ph)) or
                candidate['x'] < -1e-5 or candidate['y'] < -1e-5 or
                candidate['x']+candidate['width']>float(pw)+1e-5 or
                candidate['y']+candidate['height']>float(ph)+1e-5):
                return False
        keys=('x','y','width','height')
        if all(abs(item[k]-candidate[k])<1e-8 for k in keys):return False
        for k in keys:item[k]=candidate[k]
        return True

    def transfer(self,key,target,identifier,x,y):
        if key==target:return self.move(key,identifier,x,y)
        item=self.item(key,identifier)
        if item is None or item['locked']:return False
        candidate=dict(item,x=float(x),y=float(y));_valid_item(candidate)
        old=self._page(key);new=self._page(target)
        front=old['stack'].index(identifier)>old['stack'].index('ink')
        old['images'].remove(item);old['stack'].remove(identifier)
        item.update(x=float(x),y=float(y));new['images'].append(item)
        if front:new['stack'].append(identifier)
        else:new['stack'].insert(new['stack'].index('ink'),identifier)
        return True

    def lock(self,key,identifier,locked):
        item=self.item(key,identifier)
        if not item:return False
        item['locked']=bool(locked);return True

    def layer(self,key,identifier,direction):
        item=self.item(key,identifier)
        if item is None or item['locked']:return False
        page=self._page(key);stack=page['stack']
        if identifier not in stack:return False
        old=stack.index(identifier)
        if direction=='up':new=min(len(stack)-1,old+1)
        elif direction=='down':new=max(0,old-1)
        elif direction=='front':new=len(stack)-1
        elif direction=='back':new=0
        else:raise ValueError('未知层级操作')
        if old==new:return False
        stack.pop(old);stack.insert(new,identifier);return True

    def remove(self,key,identifier):
        item=self.item(key,identifier)
        if item is None or item['locked']:return False
        page=self._page(key)
        if identifier not in page['stack'] or identifier=='ink':return False
        page['stack'].remove(identifier)
        page['images']=[i for i in page['images'] if i['id']!=identifier]
        return True

    def duplicate_page(self,source_key,target_key):
        old=self._page(source_key)
        mapping={item['id']:uuid.uuid4().hex for item in old['images']}
        new=copy.deepcopy(old)
        for item in new['images']:item['id']=mapping[item['id']]
        new['stack']=[mapping.get(identifier,identifier) for identifier in old['stack']]
        self.pages[target_key]=new

    def serialize(self,keys):
        pages=[copy.deepcopy(self._page(key)) for key in keys]
        for page in pages:_validate_page(page)
        return pages

    def referenced_assets(self,pages):
        refs={item['asset'] for page in pages for item in page['images']}
        return {digest:self.assets[digest] for digest in refs}


def rewrite_archive(source,target,pages,assets,*,cancelled=lambda:False,before_commit=lambda:None,native=False):
    """Commit checked images in a new complete archive, leaving source untouched.

    Caller is responsible for selecting the final target and performing this only
    after the native NTB save has completed and closed its input handles.
    """
    if sum(len(page['images']) for page in pages)>MAX_IMAGES:raise ValueError('插入图片超过上限')
    for page in pages:_validate_page(page)
    refs={item['asset'] for page in pages for item in page['images']}
    if refs!=set(assets):raise ValueError('图片素材与清单不匹配')
    for digest,data in assets.items():
        if len(data)>MAX_ASSET or hashlib.sha256(data).hexdigest()!=digest:
            raise ValueError('图片素材校验失败')
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.nv-image-',suffix='.tmp',dir=target.parent);os.close(fd)
    try:
        with ZipFile(source) as old, ZipFile(temp,'w',allowZip64=True) as output:
            if old.testzip() is not None:raise ValueError('源 NTB ZIP 校验失败')
            bundle=old.read('noteBundle')
            official_assets={}
            if native and any(page['images'] for page in pages):
                from images.ne_image_embed import embed_images
                bundle,official_assets=embed_images(bundle,pages,assets)
            output.comment=old.comment
            for info in old.infolist():
                if cancelled():
                    from formats.ne_loader import LoadCancelled
                    raise LoadCancelled()
                if info.filename.startswith(PREFIX):continue
                if native and info.filename=='noteBundle':
                    output.writestr(info,bundle)
                    continue
                if info.filename in official_assets:
                    if old.read(info)!=official_assets[info.filename]:
                        raise ValueError('原生图片素材哈希冲突')
                    continue
                with old.open(info) as src,output.open(info,'w',force_zip64=True) as dst:
                    while True:
                        chunk=src.read(1024*1024)
                        if not chunk:break
                        if cancelled():
                            from formats.ne_loader import LoadCancelled
                            raise LoadCancelled()
                        dst.write(chunk)
            for name,blob in official_assets.items():
                output.writestr(name,blob,compress_type=ZIP_DEFLATED)
            if not native and any(page['images'] for page in pages):
                manifest=json.dumps({'version':1,'pages':pages},ensure_ascii=False,separators=(',',':')).encode('utf-8')
                output.writestr(MANIFEST,manifest,compress_type=ZIP_DEFLATED)
                for digest,data in sorted(assets.items()):
                    output.writestr(PREFIX+digest+'.png',data,compress_type=ZIP_DEFLATED)
        with ZipFile(temp) as chk:
            if chk.testzip() is not None:raise ValueError('保存后 ZIP 校验失败')
            if 'noteBundle' not in chk.namelist():raise ValueError('保存后的 NTB 缺少 noteBundle')
            if not native and any(page['images'] for page in pages):
                assert json.loads(chk.read(MANIFEST))['pages']==pages
            if native and official_assets:
                from formats.ne_native import read_structure
                from formats.ne_ntb import image_records
                buf,_,events=read_structure(bundle)
                image_records(events,buf,temp)
        from core.ne_safecommit import replace_with_retry
        if cancelled():
            from formats.ne_loader import LoadCancelled
            raise LoadCancelled()
        # Flush the validated archive before acknowledging its recovery
        # snapshot. A successful rename alone does not flush buffered bytes.
        with open(temp,'rb+') as durable:
            os.fsync(durable.fileno())
        before_commit()
        replace_with_retry(temp,target)
        temp=None
        return bundle
    finally:
        if temp is not None:
            try:os.unlink(temp)
            except FileNotFoundError:pass
