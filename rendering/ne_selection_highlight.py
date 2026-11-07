"""One bounded, disposable screen overlay; never used for notebook saving."""
import math
from PyQt6.QtGui import QImage,QPainter,QPen,QColor


def paint_highlight(canvas,painter,page,target,strokes,indices):
    if not indices:return
    transform=painter.combinedTransform()
    dpr=painter.device().devicePixelRatioF()
    sx=max(.01,math.hypot(transform.m11(),transform.m12())*dpr)
    sy=max(.01,math.hypot(transform.m21(),transform.m22())*dpr)
    width=max(1,round(target.width()*sx));height=max(1,round(target.height()*sy))
    factor=min(1.,math.sqrt(canvas.MAX_RENDER_PIXELS/(width*height)))
    width=max(1,round(width*factor));height=max(1,round(height*factor))
    core=canvas.edits.core if hasattr(canvas.edits,'core') else canvas.edits
    key=(page,width,height,core.token,core.cursor,canvas.ink_smoothing,frozenset(indices))
    cached=canvas._selection_highlight_cache
    if cached is None or cached[0]!=key:
        image=QImage(width,height,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
        p=QPainter(image)
        try:
            nw,nh=canvas.editPageSize(page);p.scale(width/nw,height/nh)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen=QPen(QColor(37,99,235,180),1.2*sx*factor);pen.setCosmetic(True)
            p.setPen(pen);p.setBrush(QColor(82,169,255,40))
            for i in sorted(indices):
                if 0<=i<len(strokes):
                    stroke=strokes[i]
                    p.drawPath(stroke.selection_outline if canvas.ink_smoothing else stroke.linear_selection_outline)
        finally:p.end()
        canvas._selection_highlight_cache=(key,image)
    painter.drawImage(target,canvas._selection_highlight_cache[1])
