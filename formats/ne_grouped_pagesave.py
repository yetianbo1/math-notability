"""Conservative grouped-PDF page operations for official-template NTBs.

A grouped type-3 event represents a *PDF document*, not an individual page.
Never feed this format through the per-page event rewriter. Instead:
  - validate the complete source archive and a known grouped event graph;
  - build the ordered visible PDF-page background without rasterizing it;
  - serialize every visible ink stroke against its final page number;
  - generate a *new* official-template group and atomically commit it.

Legacy grouped layouts use the common per-page geometry writer. Only one source PDF, no independent assets/images,
and supported 1/3/15/22/25 event kinds are allowed.
Opaque edits, pressure metadata, or unrecognized events require refusing or
explicit user acknowledgement; this writer re-encodes ink polylines.
"""
from __future__ import annotations

from hashlib import sha512
from pathlib import Path
from zipfile import ZipFile
import math

import fitz

import formats.ne_ntb as ntb
from formats.ne_native import NativeWriteError
from import_export.ne_official_pdf import _record_tables, MAX_PAGES

# External PDF import is limited to 1..30 pages. A *page edit* of an
# already-validated grouped document may safely keep a single PDF page.
MIN_OUTPUT_PAGES = 1
from core.ne_page_geometry import page_size


class GroupedPageError(NativeWriteError):
    pass


def grouped_info(archive, bundle=None):
    """Validate a source from our narrow, official-confirmed grouped PDF family.

    Return (original PDF bytes, count). Throws GroupedPageError for unsupported
    documents. Accepts type-15 ink, type-25 hide/show, and title events, but
    forbids independent images and other opaque content.
    """
    try:
        names=archive.namelist()
        if len(names)!=len(set(names)) or archive.testzip() is not None:
            raise GroupedPageError('ZIP 文件损坏或包含重复资源')
        if bundle is None:bundle=archive.read('noteBundle')
        buf,root,events,payload,bg,pdf,asset=_record_tables(bundle)
        version=buf.u16(root.field(7)) if root.field(7) else None
        if version!=20:
            raise GroupedPageError('暂时只支持已验证的 NTB schema-20 官方 PDF 分组结构')
        kinds=[bundle[e.field(4)] for _,e in events if e.field(4)]
        if len(kinds)!=len(events) or kinds.count(3)!=1 or kinds.count(22)!=1 or not all(t in (1,3,15,22,25) for t in kinds):
            raise GroupedPageError('存在尚未验证的事件类型、图片或其他页面对象，已拒绝修改')
        if not all(e.slots==6 for _,e in events):
            raise GroupedPageError('事件含未识别的扩展字段')
        count=ntb.document_page_count(events,buf)
        if not MIN_OUTPUT_PAGES <= count <= MAX_PAGES or ntb.field_u32(buf,payload,2)!=count or ntb.field_u32(buf,pdf,2)!=count or ntb.field_u32(buf,pdf,3)!=count:
            raise GroupedPageError('分组页数字段不一致或页数未经验证')
        h=asset.field(0); size=asset.field(3)
        if not h or not buf.valid(h,64) or not size:
            raise GroupedPageError('PDF 资源哈希和尺寸字段缺失')
        digest=bytes(bundle[h:h+64]); name=f'assets/{digest.hex()}.pdf'
        required={'version','noteBundle',name,'manifest.json','index/ios/PDFIndex.fb','thumbnail.png'}
        from images.ne_inserted_images import PREFIX
        if {name for name in names if not name.startswith(PREFIX)}!=required:
            raise GroupedPageError('包含独立图片或额外资源，不能保证完整保留')
        data=archive.read(name)
        if sha512(data).digest()!=digest or len(data)!=buf.u32(size):
            raise GroupedPageError('PDF 资源内容与索引不一致')
        if archive.read('index/ios/PDFIndex.fb').count(digest)!=1:
            raise GroupedPageError('PDF 搜索索引哈希不一致')
        if ntb.image_records(events,buf,archive.filename):
            raise GroupedPageError('检测到独立图片，暂不支持保留')
        # Refuse any original visible ink that the app cannot parse. Otherwise
        # recreating only model-visible strokes could silently destroy ink.
        if kinds.count(15):
            visible={buf.u32(e.field(0)+4) for _,e in events if bundle[e.field(4)]==15}
            visible -= ntb.hidden_stroke_event_ids(events,buf)
            try:
                svg,_,_=ntb.make_svg(archive.filename,True)
                observed={int(el.get('data-event-id')) for el in svg
                          if el.tag.endswith('path') and el.get('data-event-id') is not None}
            except (ValueError,KeyError,TypeError) as exc:
                raise GroupedPageError('无法安全读取全部原生笔迹：'+str(exc)) from exc
            if visible!=observed:
                raise GroupedPageError('存在无法完整解析的原生笔迹；为避免丢失数据，页面管理已禁用')
        with fitz.open(stream=data,filetype='pdf') as document:
            if document.needs_pass or document.page_count!=count:
                raise GroupedPageError('PDF 内部页数与 NTB 分组页数不同')
            for i,page in enumerate(document):
                page_size(page.rect.width,page.rect.height)
        return data,count
    except (KeyError,ValueError,IndexError,fitz.FileDataError) as exc:
        if isinstance(exc,GroupedPageError):raise
        raise GroupedPageError('分组 NTB 无法通过结构校验：'+str(exc)) from exc


def _validate_plan(plan,count):
    if not isinstance(plan,(list,tuple)) or not MIN_OUTPUT_PAGES <= len(plan) <= MAX_PAGES:
        raise GroupedPageError(f'实验性分组页面操作后须保留 {MIN_OUTPUT_PAGES}～{MAX_PAGES} 页')
    for i,spec in enumerate(plan):
        if not isinstance(spec,dict):raise GroupedPageError('页面快照格式无效')
        try:
            width,height=page_size(spec.get('width',0),spec.get('height',0))
        except ValueError as exc:
            raise GroupedPageError(f'第 {i+1} 页：{exc}') from exc
        if spec.get('images'):raise GroupedPageError('暂不支持独立图片随页面复制或重排')
        if 'pdf' not in spec:
            raise GroupedPageError('缺少页面底图来源字段，已停止保存')
        source=spec.get('pdf')
        if source is not None and (type(source) is not int or not 0<=source<count):
            raise GroupedPageError(f'第 {i+1} 页的 PDF 底图引用无效')
        if 'strokes' not in spec:raise GroupedPageError('页面缺少完整可见笔迹快照，禁止重建')
        for stroke in spec['strokes']:
            try:
                if not math.isfinite(stroke.width) or not 0<stroke.width<=1000 or not 0<=stroke.opacity<=1:
                    raise ValueError('笔迹宽度或透明度超出范围')
                if not stroke.points or not all(math.isfinite(c) and abs(c)<1e7 for pt in stroke.points for c in pt):
                    raise ValueError('笔迹坐标无效')
                # Ink crossing into another original page cannot be safely
                # remapped by a single-page snapshot. Fail instead of moving
                # half of a stroke to the wrong destination after page reorder.
                if any(x < -2 or x > width+2 or y < -2 or y > height+2
                       for x,y in stroke.points):
                    raise ValueError('跨页笔迹尚不支持页面重排，请先另存为 PDF')
            except (TypeError,AttributeError,ValueError) as exc:
                raise GroupedPageError('页面笔迹快照包含不支持的数据：'+str(exc)) from exc


def rebuild_grouped_pages(source,target,plan,*,expected_bundle=None,progress=lambda _:None,cancelled=lambda:False,effective_target=None):
    """Validate legacy templates, then use the geometry-preserving writer."""
    from formats.ne_grouped_pdf_range import materialize_grouped_pdf_pages
    with ZipFile(source) as archive:
        current=archive.read('noteBundle')
        if expected_bundle is not None and current!=expected_bundle:
            raise GroupedPageError('源笔记已在磁盘被其他程序修改，请重新打开')
        _,count=grouped_info(archive,current)
    _validate_plan(plan,count)
    return materialize_grouped_pdf_pages(
        source,target,plan,expected_bundle=current,progress=progress,
        cancelled=cancelled,effective_target=effective_target)
