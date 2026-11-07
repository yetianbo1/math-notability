"""Adapt the checked desktop NTB pipeline; never edit the uploaded original.

Qt objects are confined to the single server thread. Original event IDs are
matched against the server snapshot, never trusted from client-supplied IDs.
"""
from __future__ import annotations
import base64
import copy
import gc
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtCore import QSize, QBuffer, QIODevice, QByteArray
from PyQt6.QtGui import QImage
from PyQt6 import sip
from formats.ne_loader import prepare_note
from formats.ne_native import write_ntb
from editing.ne_edits import Stroke
from rendering.ne_pdfview import PdfNoteCanvas
from pages.ne_page_access import grant_page_access
from images.ne_inserted_images import ImageLayers, rewrite_archive
from import_export.ne_newnote import create_newnote_ntb, paper_preset

META = 'NotabilityEditor/tablet.json'
MAX_UPLOAD = 2 * 1024 * 1024 * 1024


def fingerprint(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


class Conflict(ValueError):
    pass


def finite(value, minimum=-100000, maximum=100000):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('数值超出允许范围')
    return float(value)


def title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError('笔记名称须为 1～100 个字符')
    return value.strip()


def file_title(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title(value)).rstrip(' .')[:80] or '未命名笔记'
    if name.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        name = '_' + name
    return name


def png(image):
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise ValueError('页面图像生成失败')
    return bytes(data)


def validate_archive(path):
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 50000 or len({e.filename for e in entries}) != len(entries):
            raise ValueError('NTB ZIP 成员数量异常或存在重复成员')
        if sum(e.file_size for e in entries) > 4 * 1024 * 1024 * 1024:
            raise ValueError('NTB 解压大小超过 4 GB 限制')
        if any(e.flag_bits & 1 for e in entries):
            raise ValueError('不支持加密 NTB')
        if 'noteBundle' not in archive.namelist() or archive.getinfo('noteBundle').file_size > 128 * 1024 * 1024:
            raise ValueError('NTB 缺少有效的 noteBundle')
        if archive.testzip() is not None:
            raise ValueError('NTB 校验失败')


class Document:
    def __init__(self, engine, ident, path):
        self.engine, self.ident, self.path = engine, ident, path
        self.prepared = None; self.canvas = None; self.pdf = None
        try:
            self.prepared = prepare_note(str(path))
            if self.prepared.pdf_path:
                self.pdf = QPdfDocument(None)
                error = self.pdf.load(self.prepared.pdf_path)
                if error != QPdfDocument.Error.None_:
                    raise ValueError('内嵌 PDF 无法打开')
            p = self.prepared
            self.canvas = PdfNoteCanvas(self.pdf, p.svg, p.width, p.height, archive_path=str(path))
            self.access = grant_page_access(self.canvas, p)
            with ZipFile(path) as archive:
                metadata = json.loads(archive.read(META)) if META in archive.namelist() else {}
            if not isinstance(metadata, dict):
                metadata = {}
            self.revision = fingerprint(path)
            pages = []
            count = self.canvas.page_count
            if not 1 <= count <= 500:
                raise ValueError('当前平板处理服务支持 1～500 页 NTB')
            point_count = 0
            for i in range(count):
                w, h = self.canvas.editPageSize(i); scale = 900 / w
                if not 1 <= 900*h/w <= 20000:
                    raise ValueError('页面长宽比超出当前触控画布范围')
                strokes = []
                for part, stroke in enumerate(self.canvas.originalStrokes(i)):
                    points = [[x*scale,y*scale,.5] for x,y in stroke.points]
                    point_count += len(points)
                    if point_count > 2000000:
                        raise ValueError('笔迹采样超过当前平板处理上限')
                    strokes.append(dict(id=f'ink-{i}-{part}', tool='highlight' if stroke.is_highlighter else 'pen',
                                        color=stroke.color, width=stroke.width*scale, opacity=stroke.opacity,
                                        points=points, filled=stroke.outline is not None))
                saved = metadata.get('pages', [])
                pm = saved[i] if isinstance(saved, list) and i < len(saved) and isinstance(saved[i], dict) else {}
                pages.append(dict(id=f'page-{i}', source=i, title=str(pm.get('title',f'第 {i+1} 页'))[:100],
                                  background='plain', lesson=None, strokes=strokes, images=[],
                                  bookmarked=pm.get('bookmarked') is True, viewHeight=900*h/w,
                                  backdrop=f'/api/background/{ident}/{self.revision}/{i}',
                                  protectedBackground=bool(self.canvas.images.get(i) or self.canvas.native_text.get(i))))
            page_writer=self.access.writer
            if page_writer in ('official_grouped_pdf','materialized_grouped_pdf','ntb18_mixed_pdf'):
                page_writer=None
            self.model = dict(id=ident, title=str(metadata.get('title', path.stem))[:100],
                              categoryId=None, pages=pages, updated=int(path.stat().st_mtime*1000),
                              backend=dict(id=ident, revision=self.revision, pageWriter=page_writer,
                                           pageError=self.access.reason or ('' if page_writer else '此页面结构的调整尚未接入，仍可编辑笔迹'), status='saved',
                                           lastEditToken=metadata.get('lastEditToken'),
                                           lastEditSession=metadata.get('lastEditSession'),
                                           paperKind=(self.canvas.paper_template or {}).get('kind','blank')))
            self.native = {f'ink-{i}-{part}':(i,s) for i in range(count)
                           for part,s in enumerate(self.canvas.originalStrokes(i))}
        except Exception:
            self.close(); raise

    def close(self):
        if self.canvas is not None:
            self.canvas.close(); sip.delete(self.canvas); self.canvas = None
        if self.pdf is not None:
            self.pdf.close(); sip.delete(self.pdf); self.pdf = None
        if self.prepared is not None:
            self.prepared.cleanup(); self.prepared = None
        self.engine.app.processEvents()

    def background(self, index):
        if not 0 <= index < len(self.model['pages']):
            raise ValueError('页码超出范围')
        # Native pictures and text stay in the original event stream. Rendering
        # them here does not rasterize them in the saved NTB.
        h = round(self.model['pages'][index]['viewHeight'])
        return png(self.canvas._render_page(index, QSize(900, h)))


class Engine:
    def __init__(self, data_dir):
        self.app = QApplication.instance() or QApplication([])
        self.root = Path(data_dir).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.sessions = {}

    def get(self, ident):
        if not re.fullmatch(r'[0-9a-f]{32}', ident):
            raise ValueError('笔记编号无效')
        if ident in self.sessions and self.sessions[ident].canvas is None:
            del self.sessions[ident]
        if ident not in self.sessions:
            folder = self.root / ident
            paths = [p for p in folder.glob('*.ntb') if not p.name.startswith('.')] if folder.is_dir() else []
            if len(paths) != 1:
                raise ValueError('工作副本不存在，请重新选择 NTB 文件')
            # Requests run serially on the Qt owner thread. Keep only the
            # requested document in memory; every other note can be reopened
            # from its working copy and durable recovery journal.
            self.close()
            # Deleted Qt wrappers can participate in Python reference cycles.
            # Reclaim them before allocating another large document.
            gc.collect()
            self.sessions[ident] = Document(self, ident, paths[0])
        return self.sessions[ident]

    def background(self,ident,revision,index):
        doc=self.get(ident)
        if revision==doc.revision:return doc.background(index)
        previous=doc.path.parent/'.previous.ntb'
        if previous.is_file() and fingerprint(previous)==revision:
            old=Document(self,ident,previous)
            try:return old.background(index)
            finally:old.close()
        raise Conflict('页面版本已改变，请重新打开笔记')

    def open_bytes(self, name, data):
        return self.open_stream(name,io.BytesIO(data),len(data))

    def open_stream(self, name, stream, length):
        if not isinstance(name, str) or Path(name).suffix.lower() != '.ntb':
            raise ValueError('平板版仅支持 NTB 格式')
        if not 0 < length <= MAX_UPLOAD:
            raise ValueError('NTB 为空或超过 2 GB 限制')
        filename = file_title(Path(name).stem) + '.ntb'
        ident = uuid.uuid4().hex; folder = self.root / ident; folder.mkdir()
        path = folder / filename
        try:
            remaining=length
            with path.open('wb') as target:
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('NTB 上传不完整')
                    target.write(chunk);remaining-=len(chunk)
            validate_archive(path)
            return copy.deepcopy(self.get(ident).model)
        except BadZipFile as exc:
            shutil.rmtree(folder);raise ValueError('文件不是有效的 NTB ZIP 笔记') from exc
        except Exception:
            shutil.rmtree(folder); raise

    def new(self, name, background='plain'):
        background = {'plain':'blank','lined':'ruled','grid':'dotted','dotted':'dotted'}.get(background)
        if background is None:
            raise ValueError('不支持的纸张模板')
        filename = file_title(name) + '.ntb'
        ident = uuid.uuid4().hex; folder = self.root / ident; folder.mkdir()
        path = folder / filename
        try:
            create_newnote_ntb(path, paper_preset('a4'), background)
            return copy.deepcopy(self.get(ident).model)
        except Exception:
            shutil.rmtree(folder); raise

    def save(self, ident, note, revision):
        current = self.get(ident)
        self.validate(note)
        if fingerprint(current.path) != current.revision:
            raise Conflict('笔记已被其他窗口或程序修改，请重新打开；当前编辑仍保留。')
        doc=current; previous=current.path.parent/'.previous.ntb'
        if revision != current.revision:
            # A pagehide/process-exit flush can finish after the browser has
            # cached its last edit. Only that editing session may replay its
            # newer cache against the immediately preceding native baseline.
            if not self.can_recover(current,note,revision):
                raise Conflict('笔记基线已改变，当前恢复快照保留，请重新打开')
            doc=Document(self,ident,previous)
        try:
            return self._write_snapshot(current,doc,ident,note)
        finally:
            if doc is not current:doc.close()

    def _write_snapshot(self,current,doc,ident,note):
        previous=current.path.parent/'.previous.ntb'
        baseline = doc.model; pages = note['pages']; originals = {p['id']:p for p in baseline['pages']}
        retained = []; page_sources = []; specs = []
        template_kind = (doc.canvas.paper_template or {}).get('kind', 'blank')
        for i, page in enumerate(pages):
            original = originals.get(page['id'])
            source = original['source'] if original else None
            if source is not None:
                if source in retained: raise ValueError('页面身份重复')
                retained.append(source)
                if page.get('backdrop') != original.get('backdrop') or page.get('viewHeight') != original['viewHeight']:
                    raise ValueError('原生页面底图和比例不可直接修改')
                spec = copy.deepcopy(doc.canvas.edits.specs[source]) if hasattr(doc.canvas.edits,'specs') else {}
                w,h = doc.canvas.editPageSize(source)
                spec.update(width=w,height=h)
            else:
                # Copied opaque pages need an event-aware clone, not a blank
                # page with an existing background URL accidentally attached.
                if page.get('backdrop') or page.get('source') is not None:
                    raise ValueError('当前连接层尚未开放原生页面复制，请添加空白页')
                w,h = doc.canvas.editPageSize(0)
                spec = dict(key=f'new-{i}', source=None, background_source=None, pdf=None, images=[],
                            width=w,height=h,known_page=True,background_kind=template_kind)
            page_sources.append(source); specs.append(spec)
        structure_changed = page_sources != list(range(len(baseline['pages'])))
        if structure_changed and (not doc.access.writer or doc.access.writer in ('official_grouped_pdf','materialized_grouped_pdf','ntb18_mixed_pdf')):
            raise ValueError(doc.access.reason or '此分组 PDF 的页面调整尚未接入；仍可编辑和保存笔迹')
        hidden = set(); added = []; changed_ids = set()
        input_by_id = {}
        for i,p in enumerate(pages):
            for s in p['strokes']:
                if s['id'] in input_by_id: raise ValueError('笔迹编号重复')
                input_by_id[s['id']] = (page_sources[i],s)
        for p in baseline['pages']:
            if p['source'] not in retained: continue
            for original in p['strokes']:
                incoming = input_by_id.get(original['id'])
                if incoming != (p['source'],original):
                    _, native = doc.native[original['id']]
                    if native.source_id is None:
                        raise ValueError('此笔迹缺少可安全修改的事件编号')
                    changed_ids.add(native.source_id)
        hidden.update(changed_ids)
        layers = ImageLayers([{'stack':['ink'],'images':[]} for _ in pages])
        for i,p in enumerate(pages):
            scale = specs[i]['width'] / 900; converted = []
            for s in p['strokes']:
                points = tuple((q[0]*scale,q[1]*scale) for q in s['points'])
                filled = s.get('filled') is True
                native = doc.native.get(s['id'])
                stroke = Stroke(points,s['color'],s['width']*scale,
                                s.get('opacity',.35 if s['tool']=='highlight' else 1),
                                native[1].source_id if native else None,
                                'highlighter' if s['tool']=='highlight' else 'pen', geometry_is_linear=True,
                                outline=tuple((x-points[0][0],y-points[0][1]) for x,y in points) if filled else None)
                converted.append(stroke)
                if not native or stroke.source_id in changed_ids or page_sources[i] is None:
                    added.append((i,stroke))
            specs[i]['strokes'] = tuple(converted)
            for im in p['images']:
                raw = base64.b64decode(im['data'].split(',',1)[1],validate=True)
                if len(raw) > 20*1024*1024:raise ValueError('图片超过 20 MB')
                image = QImage.fromData(raw)
                if image.isNull() or image.width()*image.height()>16000000:
                    raise ValueError('图片无效或像素过多')
                key = layers.insert(i,png(image),im['x']*scale,im['y']*scale,im['w']*scale,im['h']*scale)
                layers.lock(i,key,im.get('locked') is True)
        # Stage all phases beside the destination. Only a fully parsed final
        # archive is committed; a failed image/page/save leaves working NTB intact.
        with tempfile.TemporaryDirectory(prefix='.save-',dir=current.path.parent) as work:
            work = Path(work); stage = work/'ink.ntb'; packed = work/'images.ntb'; final = work/'final.ntb'
            write_ntb(doc.path,stage,hidden,added,base_bundle=doc.prepared.bundle_data,
                      expected_bundle=doc.prepared.bundle_data,page_plan=specs if structure_changed else None,
                      effective_target=current.path.parent/(file_title(note['title'])+'.ntb'))
            if any(p['images'] for p in pages):
                payload = layers.serialize(range(len(pages)))
                rewrite_archive(stage,packed,payload,layers.referenced_assets(payload),native=True)
            else:
                packed = stage
            metadata = dict(title=note['title'],pages=[dict(title=p['title'],bookmarked=p.get('bookmarked') is True) for p in pages],
                            lastEditToken=note.get('backend',{}).get('editToken'),
                            lastEditSession=note.get('backend',{}).get('editSessionId'))
            with ZipFile(packed) as src, ZipFile(final,'w',ZIP_DEFLATED) as dst:
                dst.comment=src.comment
                for info in src.infolist():
                    if info.filename!=META:
                        # A PDF member can approach the size of the whole NTB.
                        # Copy metadata as ZipFile.open mutates its ZipInfo.
                        with src.open(info) as source, dst.open(copy.copy(info),'w',force_zip64=True) as target:
                            shutil.copyfileobj(source,target,length=1024*1024)
                dst.writestr(META,json.dumps(metadata,ensure_ascii=False).encode())
            validate_archive(final)
            probe = Document(self,ident,final)
            if len(probe.model['pages']) != len(pages):
                probe.close(); raise ValueError('保存回读页数校验失败')
            probe.close()
            if fingerprint(current.path) != current.revision:
                raise Conflict('保存期间工作副本被外部修改，请重新打开')
            # Close Qt/PDF readers before the Windows atomic replacement.
            doc.close();current.close()
            with final.open('rb+') as stream:stream.flush();os.fsync(stream.fileno())
            # Retain the original recovery baseline across a replay. It is a
            # private runtime artifact, excluded from all source packages.
            if doc is current:
                backup=work/'previous.ntb';shutil.copyfile(current.path,backup);os.replace(backup,previous)
            os.replace(final,current.path)
            (current.path.parent/'snapshot.json').unlink(missing_ok=True)
        del self.sessions[ident]
        result = copy.deepcopy(self.get(ident).model)
        result['categoryId'] = note.get('categoryId')
        return result

    def export_pdf_path(self, ident):
        from import_export.ne_export import write_pdf
        doc = self.get(ident); path=doc.path.parent/'export.pdf'
        write_pdf(doc.canvas,doc.prepared,path)
        return path

    def export_pdf(self, ident):
        """Compatibility byte API; HTTP downloads use export_pdf_path."""
        return self.export_pdf_path(ident).read_bytes()

    def snapshot(self, ident, note, revision):
        doc=self.get(ident)
        self.validate(note)
        if revision!=doc.revision and not self.can_recover(doc,note,revision):raise Conflict('快照基线已改变，请重新打开笔记')
        target=doc.path.parent/'snapshot.json';stage=target.with_suffix('.tmp')
        data=json.dumps(dict(note=note,revision=revision),ensure_ascii=False,allow_nan=False).encode()
        with stage.open('wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(stage,target)

    @staticmethod
    def can_recover(doc,note,revision):
        session=note.get('backend',{}).get('editSessionId')
        previous=doc.path.parent/'.previous.ntb'
        return (bool(session) and session==doc.model['backend'].get('lastEditSession')
                and previous.is_file() and fingerprint(previous)==revision)

    def resume(self, ident):
        doc=self.get(ident);snapshot=doc.path.parent/'snapshot.json'
        if snapshot.exists():
            pending=json.loads(snapshot.read_text(encoding='utf-8'))
            return self.save(ident,pending['note'],pending['revision'])
        return copy.deepcopy(doc.model)

    @staticmethod
    def validate(note):
        if not isinstance(note,dict):raise ValueError('缺少笔记数据')
        title(note.get('title'))
        pages=note.get('pages')
        if not isinstance(pages,list) or not 1<=len(pages)<=500:raise ValueError('笔记须为 1～500 页')
        page_ids=set(); count=0
        for page in pages:
            if not isinstance(page,dict) or not isinstance(page.get('id'),str) or page['id'] in page_ids:raise ValueError('页面身份无效')
            page_ids.add(page['id']);title(page.get('title'))
            strokes=page.get('strokes');images=page.get('images')
            if not isinstance(strokes,list) or len(strokes)>20000 or not isinstance(images,list) or len(images)>100:raise ValueError('页面内容超出限制')
            for s in strokes:
                if not isinstance(s,dict) or not isinstance(s.get('id'),str) or len(s['id'])>100:raise ValueError('笔迹编号无效')
                if s.get('tool') not in ('pen','highlight') or not re.fullmatch(r'#[0-9a-fA-F]{6}',str(s.get('color'))):raise ValueError('笔迹样式无效')
                finite(s.get('width'),.001,2000); finite(s.get('opacity',1),0,1)
                points=s.get('points')
                if not isinstance(points,list) or not 1<=len(points)<=65535:raise ValueError('笔迹采样点无效')
                count+=len(points)
                if count>2000000:raise ValueError('笔迹采样点超过上限')
                for p in points:
                    if not isinstance(p,list) or len(p)!=3:raise ValueError('笔迹坐标无效')
                    finite(p[0]);finite(p[1]);finite(p[2],0,1)
            for im in images:
                if not isinstance(im,dict) or not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+',str(im.get('data'))) or len(im['data'])>28000000:raise ValueError('图片数据无效')
                for name in ('x','y'):finite(im.get(name))
                for name in ('w','h'):finite(im.get(name),.01,10000)

    def close(self):
        for doc in self.sessions.values():doc.close()
        self.sessions.clear()

    def flush_snapshots(self):
        """Graceful process shutdown writes every durable journal before exit.

        A failed journal remains on disk for recovery; other notes still flush.
        Forceful OS termination cannot run Python cleanup, so recovery also runs
        before the next explicit open.
        """
        failures=[]
        for snapshot in self.root.glob('*/snapshot.json'):
            try:self.resume(snapshot.parent.name)
            except Exception as exc:failures.append((snapshot.parent.name,str(exc)))
        return failures
