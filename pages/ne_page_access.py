"""Safe native page writer discovery, independent of GUI and selected page.

The *file writer* and the *individual page* have distinct permissions:
valid writer => can insert notebook-paper pages and duplicate full pages;
unknown per-page content => cut/delete/replace must still be guarded.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile


@dataclass(frozen=True)
class PageAccess:
    writer: str | None = None
    reason: str = ''
    maximum: int | None = None


def inspect_page_access(bundle: bytes | None, path: str, visible_count: int,
                        *, has_page_model: bool = True) -> PageAccess:
    if Path(path).suffix.lower() != '.ntb':
        return PageAccess(reason='页面结构编辑仅支持原始 NTB 文件')
    if not bundle or not has_page_model:
        return PageAccess(reason='尚无可供保存的原生页面编辑模型')
    from formats.ne_paper_graph import paper_graph,expand_paper_graph
    from formats.ne_native import read_structure
    buf,root,events=read_structure(bundle)
    graph=paper_graph(events,buf,buf.u16(root.field(7)) if root.field(7) else None)
    if graph and len(graph['records'])==visible_count:
        try:
            expand_paper_graph(bundle)
            return PageAccess('native_per_page')
        except ValueError as exc:
            return PageAccess(reason='多分组页面不能安全修改：'+str(exc))
    from formats.ne_range_pages import range_page_info
    range_info=range_page_info(bundle)
    if range_info and range_info['count']==visible_count:
        return PageAccess('native_range_pages',maximum=10000)
    # Independent per-page structure: this includes the confirmed schema-20
    # 43-page OS note with type-22 pictures and type-15 handwriting.
    from formats.ne_pagewrite import validate_page_structure
    try:
        _,_,_,pages=validate_page_structure(bundle)
        if len(pages) != visible_count:
            raise ValueError('内部页面事件数量与当前显示页数不一致')
        return PageAccess('native_per_page')
    except ValueError as exc:
        reasons=[str(exc)]
    # Grouped PDFs: need the actual ZIP background and strict structural
    # validator before exposing structural buttons.
    try:
        from formats.ne_grouped_pagesave import grouped_info
        from import_export.ne_official_pdf import MAX_PAGES
        with ZipFile(path) as archive:
            info=grouped_info(archive,bundle)
        # grouped_info may return metadata in different shapes across releases.
        # The strict check above is still the authority on its schema.
        return PageAccess('official_grouped_pdf',maximum=MAX_PAGES)
    except (ValueError,OSError,KeyError) as exc:
        reasons.append(str(exc))
    # Native grouped notebooks have a per-document paper template. Add only
    # the same type, never invent per-page mixed background styles.
    try:
        from formats.ne_grouped_paper import native_paper_info
        count=native_paper_info(bundle)
        if count != visible_count:
            raise ValueError('原生分组纸张页数与显示页数不匹配')
        return PageAccess('official_grouped_paper',maximum=10000)
    except (ValueError,OSError,KeyError) as exc:
        reasons.append(str(exc))
    try:
        from formats.ne_grouped_pdf_range import grouped_pdf_page_management_info
        info=grouped_pdf_page_management_info(bundle,path)
        if info and info['page_count']==visible_count:
            return PageAccess('materialized_grouped_pdf',maximum=10000)
    except (ValueError,OSError,KeyError):
        pass
    try:
        from formats.ne_grouped_pdf_range import mixed_grouped_pdf_page_management_info
        info=mixed_grouped_pdf_page_management_info(bundle,path)
        if info and info['page_count']==visible_count:
            return PageAccess('ntb18_mixed_pdf',maximum=10000)
    except (ValueError,OSError,KeyError) as exc:
        reasons.append(str(exc))
    return PageAccess(reason='未找到能够安全写回当前 NTB 的页面结构：'+(reasons[0] or '未识别事件布局'))


def grant_page_access(canvas,prepared) -> PageAccess:
    model=getattr(canvas,'edits',None)
    cached=getattr(prepared,'page_access_result',None)
    if (cached and cached[0] is getattr(prepared,'bundle_data',None)
            and cached[1]==getattr(canvas,'page_count',0) and model and hasattr(model,'page_plan')):
        access=cached[2]
    elif getattr(prepared,'page_access_pending',False):
        access=PageAccess(reason='正在后台验证页面编辑能力…')
    else:access=inspect_page_access(getattr(prepared,'bundle_data',None),
                               getattr(prepared,'path',''),
                               getattr(canvas,'page_count',0),
                               has_page_model=bool(model and hasattr(model,'page_plan')))
    canvas.page_edit_kind=access.writer
    canvas.page_edit_error=access.reason
    if access.writer:
        model.min_pages=1
        model.max_pages=access.maximum
        if hasattr(model,'set_known_original_pages'):
            model.set_known_original_pages(range(canvas.page_count))
    return access
