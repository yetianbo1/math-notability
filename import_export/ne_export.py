"""PDF export for the live page model. Uses Qt 6's PDF painting backend.

The PDF contains current page order, embedded PDF/image backgrounds, and the
visible editable ink. Cursor, selection rectangle, and page gutter are excluded.
"""
from pathlib import Path
import os
import tempfile
import math

from PyQt6.QtCore import QSize, QSizeF, QRectF, QMarginsF, Qt
from PyQt6.QtGui import QPdfWriter, QPainter, QPageSize, QPageLayout
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtSvg import QSvgRenderer


class ExportCancelled(Exception):
    pass


def _safe_point_size(size):
    w,h=map(float,size)
    if not all(math.isfinite(n) and 0 < n <= 14400 for n in (w,h)):
        raise ValueError('页面尺寸无效或超出 PDF 支持范围（最大 200 英寸）')
    return w,h


def _pages(canvas, prepared):
    if hasattr(canvas,'page_count') and hasattr(canvas,'_render_page'):
        return [tuple(canvas.sizes[p]) for p in range(canvas.page_count)]
    # Older NOTE files are one continuous SVG canvas; paginate long content.
    width,height=_safe_point_size((prepared.width,min(prepared.height,14400)))
    page_height=792.0 if prepared.height>792.0 else height
    pages=[]
    top=0.0
    while top < prepared.height:
        span=min(page_height,prepared.height-top)
        pages.append((width,span))
        top+=span
        if len(pages)>10000:raise ValueError("NOTE 页面过多，无法导出")
    return pages


def write_pdf(canvas,prepared,target,progress=lambda current,total:None,cancelled=lambda:False,
              include_ink=True,enhancement=False,*,preserve_pdf_sizes=True):
    """Write to a same-directory temp file; never clobber a document on failure.

    Note: the raster background is bounded by canvas._render_page's pixel cache;
    handwriting remains drawn through Qt's vector painter when enabled.
    """
    output=Path(target).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    logical_sizes=[_safe_point_size(size) for size in _pages(canvas,prepared)]
    sizes=list(logical_sizes)
    if preserve_pdf_sizes and getattr(canvas, 'document', None):
        for page,index in enumerate(canvas.pdf_pages):
            if index is not None:
                original=canvas.document.pagePointSize(index)
                sizes[page]=_safe_point_size((original.width(),original.height()))
    if not sizes:raise ValueError('没有可导出的页面')
    fd,temp=tempfile.mkstemp(prefix='.nv-export-',suffix='.pdf',dir=output.parent)
    os.close(fd)
    painter=None;document=None
    try:
        writer=QPdfWriter(str(temp))
        writer.setResolution(144)
        writer.setTitle(Path(prepared.path).stem)
        writer.setCreator('NotabilityEditor 0.7.9-Beta')
        writer.setPageMargins(QMarginsF(0,0,0,0),QPageLayout.Unit.Point)
        def page_size(w,h):
            # ExactMatch avoids snapping close sizes to a standard paper format.
            writer.setPageSize(QPageSize(QSizeF(w,h),QPageSize.Unit.Point,'',QPageSize.SizeMatchPolicy.ExactMatch))
        page_size(*sizes[0])
        painter=QPainter()
        if not painter.begin(writer):raise OSError('无法创建 PDF 输出')
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for page,(w,h) in enumerate(sizes):
            if cancelled():raise ExportCancelled('已取消 PDF 导出')
            if page:
                page_size(w,h)
                if not writer.newPage():raise OSError('无法写入下一页 PDF')
            painter.save()
            # PDF logical coordinate system: points, 144 DPI => 2 device pixels per point.
            painter.scale(writer.resolution()/72.0,writer.resolution()/72.0)
            # The NTB editor fits imported PDF pages to a shared logical width.
            # Keep that model and scale all layers together to physical points.
            lw,lh=logical_sizes[page]
            painter.scale(w/lw,h/lh)
            try:
                if hasattr(canvas,'_render_page'):
                    # Match the on-screen background rendering, including optional enhancement.
                    render_size=QSize(max(1,round(w*2)),max(1,round(h*2)))
                    if hasattr(canvas,'official_image_edits'):
                        source=canvas._render_page(page,render_size,background_only=True)
                        painter.drawImage(QRectF(0,0,lw,lh),source)
                        from rendering.ne_mixed_layers import paint_content
                        paint_content(canvas,painter,page,QRectF(0,0,lw,lh),include_ink=include_ink)
                    else:
                        source=canvas._render_page(page,render_size)
                        painter.drawImage(QRectF(0,0,lw,lh),source)
                        if include_ink:canvas.paintEditableInk(painter,page,QRectF(0,0,lw,lh))

                else:
                    # Older .note files open as a single SVG canvas in this viewer.
                    renderer=QSvgRenderer(prepared.svg)
                    if not renderer.isValid():raise ValueError('NOTE 中的 SVG 不能正确渲染')
                    painter.fillRect(QRectF(0,0,w,h),Qt.GlobalColor.white)
                    if include_ink:
                        # Render a clipped slice of the original continuous SVG.
                        painter.save()
                        painter.setClipRect(QRectF(0,0,w,h))
                        painter.translate(0,-page*792.0 if prepared.height>792.0 else 0)
                        renderer.render(painter,QRectF(0,0,prepared.width,prepared.height))
                        painter.restore()
            finally:
                painter.restore()
            progress(page+1,len(sizes))
        if cancelled():raise ExportCancelled('已取消 PDF 导出')
        painter.end();painter=None
        del writer  # Release the file handle before validation/replace on Windows.
        # Check the just-rendered document before replacing a previous export.
        document=QPdfDocument(None)  # PyQt6 6.5+ requires an explicit QObject parent
        error=document.load(str(temp))
        if error!=QPdfDocument.Error.None_ or document.pageCount()!=len(sizes):
            raise OSError('PDF 导出后校验失败，原文件未被覆盖')
        document.close();document=None
        os.replace(temp,output)
        return output
    finally:
        if painter is not None and painter.isActive():painter.end()
        if document is not None:document.close()
        if os.path.exists(temp):os.remove(temp)

