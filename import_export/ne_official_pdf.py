"""Official-template PDF writer preserving each page's actual geometry."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time
import uuid
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import fitz
import formats.ne_ntb as ntb
from core.ne_page_geometry import page_size, pdf_page_size, thumbnail_scale
from core.ne_titles import effective_title, validate_note_title

TEMPLATES = Path(__file__).resolve().parent.parent / 'assets' / 'official_templates'
MIN_PAGES = 1
MAX_PAGES = 30
MAX_IMPORT_PAGES = 5000


class OfficialTemplateError(ValueError):
    pass


def _record_tables(data):
    b=ntb.Buffer(data)
    root=ntb.table_at(b,b.u32(0)) if b.valid(0,4) else None
    if not root or root.slots!=8 or not root.field(6):
        raise OfficialTemplateError('官方模板根表不受支持')
    events=ntb.table_vector(b,root,6)
    pages=[e for _,e in events if e.field(4) and data[e.field(4)]==3]
    if len(pages)!=1:
        raise OfficialTemplateError('需要包含一个分组 PDF 页面事件的官方模板')
    payload=ntb.payload_table(b,pages[0]); bg=ntb.child_table(b,payload,1)
    pdf=ntb.child_table(b,bg,1);asset=ntb.child_table(b,pdf,0)
    if not bg or not pdf or not asset:
        raise OfficialTemplateError('PDF 素材对象缺失')
    return b,root,events,payload,bg,pdf,asset


def _add_string(buf, field, value):
    raw=value.encode('utf-8')
    if not raw or len(raw)>1024:
        raise OfficialTemplateError('内部标题或素材文件名无效')
    while len(buf)%4:buf.append(0)
    pos=len(buf)
    buf.extend(struct.pack('<I',len(raw)))
    buf.extend(raw+b'\0')
    relative=pos-field
    if relative<=0 or relative>=2**32:
        raise OfficialTemplateError('FlatBuffers 字符串引用超出范围')
    struct.pack_into('<I',buf,field,relative)


def _replace_template(pdf_bytes, pages, title, *, allow_single_page=False,
                      template_pages=None, max_pages=MAX_PAGES):
    lower=1 if allow_single_page else MIN_PAGES
    if not lower <= pages <= max_pages:
        raise OfficialTemplateError(f'PDF 导入支持 {lower}～{max_pages} 页（收到 {pages} 页）')
    # Patch the observed grouped-PDF counts before expanding to independent
    # CreatePage operations. Keep the original template event graph intact.
    if template_pages is None:
        template_pages=2 if pages <= 2 else 3
    if template_pages not in (2, 3):
        raise OfficialTemplateError('不支持的官方 PDF 模板')
    filename=TEMPLATES/f'noteBundle_{template_pages}.bin'
    indexname=TEMPLATES/f'PDFIndex_{template_pages}.fb'
    if not filename.is_file() or not indexname.is_file():
        raise OfficialTemplateError('程序缺少官方 PDF 模板资源，请使用完整更新包')
    buf=bytearray(filename.read_bytes()); index=bytearray(indexname.read_bytes())
    b,root,events,payload,bg,pdf,asset=_record_tables(buf)
    count=ntb.field_u32(b,payload,2)
    declared=ntb.field_u32(b,pdf,2)
    if count!=template_pages or declared!=template_pages:
        raise OfficialTemplateError('模板实际页数不一致')
    if ntb.field_u32(b,pdf,3)!=template_pages:
        raise OfficialTemplateError('PDF 组页数字段结构不一致')
    if not asset.field(0) or not b.valid(asset.field(0),64) or not asset.field(3):
        raise OfficialTemplateError('模板素材哈希或长度字段不存在')
    oldhash=bytes(buf[asset.field(0):asset.field(0)+64])
    if index.count(oldhash)!=1:
        raise OfficialTemplateError('PDF 索引哈希出现歧义；拒绝生成文件')
    newhash=hashlib.sha512(pdf_bytes).digest()
    index[index.index(oldhash):index.index(oldhash)+64]=newhash
    buf[asset.field(0):asset.field(0)+64]=newhash
    struct.pack_into('<I',buf,asset.field(3),len(pdf_bytes))
    # The three grouped count fields must agree before canonical expansion.
    if pages!=template_pages:
        for table,slot in ((payload,2),(pdf,2),(pdf,3)):
            field=table.field(slot)
            if not field or not b.valid(field,4) or b.u32(field)!=template_pages:
                raise OfficialTemplateError('PDF 分组页数字段异常，拒绝扩展页数')
            struct.pack_into('<I',buf,field,pages)
    # Use fresh identity/namespace to avoid colliding with the template in iCloud.
    if not root.field(0) or not b.valid(root.field(0),16):
        raise OfficialTemplateError('官方模板身份字段缺失')
    buf[root.field(0):root.field(0)+16]=uuid.uuid4().bytes
    key=('_'+uuid.uuid4().hex).encode('ascii')
    for slot in (3,5):
        field=root.field(slot)
        if not field:raise OfficialTemplateError('缺少命名空间引用')
        target=field+b.u32(field)
        if not b.valid(target,38) or b.u32(target)!=33:
            raise OfficialTemplateError('命名空间字符串结构不符')
        buf[target+4:target+4+33]=key
    # Asset display name is NOT the ZIP resource hash. Store an ordinary
    # printable PDF title, but keep the MIME type and its proper uoffset.
    if not asset.field(1):raise OfficialTemplateError('资源标题引用缺失')
    _add_string(buf,asset.field(1),title+'.pdf')
    # The document title is under the FIRST type-1 initialization event.
    first=events[0][1]
    doc=ntb.payload_table(b,first)
    wrapper=ntb.child_table(b,doc,0)
    if not wrapper or not wrapper.field(0):
        raise OfficialTemplateError('初始化标题字段缺失')
    _add_string(buf,wrapper.field(0),title)
    result=bytes(buf)
    check,root2,es,payload2,bg2,pdf2,asset2=_record_tables(result)
    if (ntb.field_u32(check,payload2,2)!=pages or
            ntb.field_u32(check,pdf2,2)!=pages or
            ntb.field_u32(check,pdf2,3)!=pages or
            ntb.document_page_count(es,check)!=pages):
        raise OfficialTemplateError('输出页面数校验失败')
    if bytes(result[asset2.field(0):asset2.field(0)+64])!=newhash:
        raise OfficialTemplateError('输出 PDF 资源哈希校验失败')
    if effective_title(result)!=title:
        raise OfficialTemplateError('输出内部标题校验失败')
    if result[asset2.field(1)+check.u32(asset2.field(1))+4:][:len((title+'.pdf').encode('utf-8'))]!=(title+'.pdf').encode('utf-8'):
        raise OfficialTemplateError('输出素材显示名称校验失败')
    return result,bytes(index),newhash.hex()


def pdf_to_official_template_ntb(source, target, progress=lambda _:None,
                                 cancelled=lambda:False):
    """Import up to 5000 pages using independent official PDF page events."""
    from import_export.ne_pdfimport import PdfImportCancelled
    if cancelled():raise PdfImportCancelled()
    source=Path(source).resolve();target=Path(target).resolve()
    if source.suffix.lower()!='.pdf' or not source.is_file():
        raise OfficialTemplateError('请选择有效 PDF 文件')
    if target.suffix.lower()!='.ntb' or source==target:
        raise OfficialTemplateError('请选择不同的 .ntb 输出文件')
    if source.stat().st_size>=2**32:
        raise OfficialTemplateError('PDF 超过 4 GiB，不能使用当前模板')
    chunks=[]
    with source.open('rb') as stream:
        while True:
            if cancelled():raise PdfImportCancelled()
            chunk=stream.read(4*1024*1024)
            if not chunk:break
            chunks.append(chunk)
    pdfbytes=b''.join(chunks)
    del chunks
    if len(pdfbytes)>=2**32:raise OfficialTemplateError('PDF 超过 4 GiB，不能使用当前模板')
    progress(5)
    with fitz.open(stream=pdfbytes,filetype='pdf') as document:
        if document.needs_pass:
            raise OfficialTemplateError('加密 PDF 暂不支持')
        pages=document.page_count
        if not MIN_PAGES <= pages <= MAX_IMPORT_PAGES:
            raise OfficialTemplateError(f'PDF 导入支持 {MIN_PAGES}～{MAX_IMPORT_PAGES} 页，当前输入 {pages} 页。')
        sizes=[]
        for i,page in enumerate(document):
            if cancelled():raise PdfImportCancelled()
            try:
                sizes.append(page_size(page.rect.width,page.rect.height))
            except ValueError as exc:
                raise OfficialTemplateError(f'第 {i+1} 页：{exc}') from exc
            progress(5+int(25*(i+1)/pages))
        scale=thumbnail_scale(*sizes[0])
        thumb=document[0].get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')
    if cancelled():raise PdfImportCancelled()
    title=validate_note_title(target.stem)
    bundle,index,digest=_replace_template(
        pdfbytes,pages,title,allow_single_page=True,template_pages=3,
        max_pages=MAX_IMPORT_PAGES)
    from formats.ne_grouped_pdf_range import (_drop_hidden_template_blocks,
                                      normalize_grouped_pdf_pages)
    bundle=_drop_hidden_template_blocks(bundle)
    coordinate_sizes=[pdf_page_size(sizes[0][0],*size) for size in sizes]
    bundle=normalize_grouped_pdf_pages(
        bundle,crop_sizes=sizes,page_sizes=coordinate_sizes)
    # A newly imported document has no source notebook coordinate frame.
    # Initialize its default paper from the first real sheet; existing notes
    # instead retain their complete original metadata during page management.
    data=bytearray(bundle)
    b=ntb.Buffer(data)
    root=ntb.table_at(b,b.u32(0))
    for _,event in ntb.table_vector(b,root,6):
        if event.field(4) and data[event.field(4)]==1:
            doc=ntb.payload_table(b,event)
            settings=ntb.child_table(b,doc,1)
            paper=ntb.child_table(b,settings,0)
            field=paper.field(3) if paper else 0
            if field and b.valid(field,8):
                struct.pack_into('<2f',data,field,*sizes[0])
    bundle=bytes(data)
    progress(55)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.nv-official-template-',suffix='.ntb',dir=target.parent)
    os.close(fd)
    assetname=f'assets/{digest}.pdf'
    try:
        with ZipFile(tmp,'w',allowZip64=True) as z:
            z.writestr('version',b'1',compress_type=ZIP_DEFLATED)
            z.writestr('noteBundle',bundle,compress_type=ZIP_STORED)
            with z.open(assetname,'w',force_zip64=True) as asset:
                for offset in range(0,len(pdfbytes),4*1024*1024):
                    if cancelled():raise PdfImportCancelled()
                    asset.write(memoryview(pdfbytes)[offset:offset+4*1024*1024])
                    progress(55+int(30*min(offset+4*1024*1024,len(pdfbytes))/max(1,len(pdfbytes))))
            z.writestr('manifest.json',b'{\n  "appVersion" : "16.13.1"\n}',compress_type=ZIP_DEFLATED)
            z.writestr('index/ios/PDFIndex.fb',index,compress_type=ZIP_DEFLATED)
            z.writestr('thumbnail.png',thumb,compress_type=ZIP_DEFLATED)
        with ZipFile(tmp) as z:
            if z.testzip() is not None:raise OfficialTemplateError('生成的 ZIP 校验失败')
            if z.read('noteBundle')!=bundle or z.read('index/ios/PDFIndex.fb')!=index:
                raise OfficialTemplateError('写入后结构不一致')
            if hashlib.sha512(z.read(assetname)).hexdigest()!=digest:
                raise OfficialTemplateError('写入后 PDF 哈希不一致')
        if cancelled():raise PdfImportCancelled()
        with open(tmp,'r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        from core.ne_safecommit import replace_with_retry
        replace_with_retry(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    progress(100)
    return target
