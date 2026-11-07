"""Opaque tape fills with reusable pattern tiles; no gaps reveal the answer."""
from functools import lru_cache
import math
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QBrush, QTransform


@lru_cache(maxsize=96)
def pattern_tile(pattern, color):
    # Four samples per paper unit keep thumbnails, zoom and PDF fills crisp.
    image=QImage(96,96,QImage.Format.Format_RGB32);image.fill(QColor(color))
    p=QPainter(image)
    try:
        p.scale(4,4);p.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        p.setPen(QPen(QColor('white'),1.1));p.setBrush(QColor('white'))
        if pattern==0:
            for x in (-24,-12,0,12,24,36,48):p.drawLine(QPointF(x,24),QPointF(x+24,0))
        elif pattern==1:
            for x in (0,12,24):
                p.drawLine(QPointF(x,0),QPointF(x,24));p.drawLine(QPointF(0,x),QPointF(24,x))
        elif pattern==2:
            p.setPen(Qt.PenStyle.NoPen)
            for x,y in ((6,6),(18,18)):p.drawEllipse(QPointF(x,y),2.8,2.8)
        elif pattern==4:
            p.setPen(Qt.PenStyle.NoPen)
            for cx,cy,radius in ((6,6,3.4),(18,18,2.5)):
                star=QPainterPath()
                for i in range(10):
                    r=radius if i%2==0 else radius*.43;a=-math.pi/2+i*math.pi/5
                    pt=QPointF(cx+r*math.cos(a),cy+r*math.sin(a))
                    star.moveTo(pt) if i==0 else star.lineTo(pt)
                star.closeSubpath();p.drawPath(star)
        elif pattern==5:
            p.setPen(Qt.PenStyle.NoPen)
            for cx,cy in ((6,6),(18,18)):
                for i in range(5):
                    a=i*2*math.pi/5
                    p.drawEllipse(QPointF(cx+2.1*math.cos(a),cy+2.1*math.sin(a)),1.7,1.7)
        elif pattern==6:
            p.setPen(Qt.PenStyle.NoPen)
            for cx,cy in ((6,6),(18,18)):
                heart=QPainterPath(QPointF(cx,cy+3.4))
                heart.cubicTo(cx-8,cy-2,cx-3,cy-7,cx,cy-2)
                heart.cubicTo(cx+3,cy-7,cx+8,cy-2,cx,cy+3.4);p.drawPath(heart)
        elif pattern==7:
            p.setBrush(Qt.BrushStyle.NoBrush)
            for y in (6,18):
                path=QPainterPath(QPointF(0,y));path.cubicTo(6,y-5,6,y+5,12,y)
                path.cubicTo(18,y-5,18,y+5,24,y);p.drawPath(path)
        elif pattern==8:
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(QRectF(0,0,12,12));p.drawRect(QRectF(12,12,12,12))
    finally:p.end()
    return image


def paint_tape(painter, outline, color, width, pattern, *, revealed=False):
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        if revealed:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(color),.8))
            painter.drawPath(outline)
            return
        brush=QBrush(QColor(color))
        if pattern!=3:
            brush=QBrush(pattern_tile(pattern,color))
            # Stripe/checker cells occupy roughly one quarter of the tape.
            unit=max(5.,min(36.,float(width)/2))
            if pattern in (4,5,6):unit*=1.4
            brush.setTransform(QTransform.fromScale(unit/96,unit/96))
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(brush)
        painter.drawPath(outline)
    finally:painter.restore()


@lru_cache(maxsize=128)
def svg_tape_strokes(raw, page, height):
    from editing.ne_edits import svg_strokes
    return tuple(svg_strokes((raw,),page,height))
