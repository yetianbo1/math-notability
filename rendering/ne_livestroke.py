"""Append stable curves once; rebuild only the capture filter's mutable tail."""
import math
from PyQt6.QtCore import QPointF,Qt
from PyQt6.QtGui import QPainterPath,QColor,QPen
from editing.ne_edits import Stroke


class LiveStroke:
    def __init__(self):
        self.operation=None
        self.prefix=QPainterPath()
        self.next_index=1
        self.curves_built=0
        self.mode=None
        self.result_key=None;self.result=None
        self.outline_key=None;self.outline=None
        self.path_hits=0;self.outline_hits=0

    def _curve(self,path,points,index,width):
        from editing.ne_inkcurve import append_curve
        append_curve(path,points,index,width)
        self.curves_built+=1

    def _smart_curve(self,path,points,index,width):
        from editing.ne_smartink import append_smart_segment
        append_smart_segment(path,points,index,width)
        self.curves_built+=1

    def path(self,op,smooth=True):
        points=op['points']
        smart=bool(op.get('smart_interpolation'))
        mode=(smooth,bool(op.get('capture_smoothing')),smart,op['width'])
        key=(id(op),mode,len(points),tuple(points[-4:]))
        if self.operation is op and self.result_key==key:
            self.path_hits+=1;return self.result
        if self.operation is not op or self.mode!=mode or len(points)<self.next_index+1:
            self.operation=op;self.prefix=QPainterPath(QPointF(*points[0]));self.next_index=1
            self.mode=mode
        if smooth and not op.get('capture_smoothing'):
            self.result=Stroke(tuple(points),op['color'],op['width']).render_path
            self.result_key=key
            return self.result
        # Capture stabilization can still move points[-2]. Its neighbouring
        # segment is kept out of the permanent prefix until the next sample.
        if smooth and smart:
            while self.next_index<=len(points)-3:
                self._smart_curve(self.prefix,points,self.next_index,op['width'])
                self.next_index+=1
            result=QPainterPath(self.prefix)
            for index in range(self.next_index,len(points)):
                self._smart_curve(result,points,index,op['width'])
        elif smooth:
            while self.next_index<=len(points)-4:
                self._curve(self.prefix,points,self.next_index,op['width']);self.next_index+=1
            result=QPainterPath(self.prefix)
            for index in range(self.next_index,len(points)):self._curve(result,points,index,op['width'])
        else:
            while self.next_index<len(points)-1:
                self.prefix.lineTo(*points[self.next_index]);self.next_index+=1
            result=QPainterPath(self.prefix)
        if not smooth and len(points)>1:result.lineTo(*points[-1])
        self.result_key=key;self.result=result
        return result

    def paint(self,painter,op,smooth=True):
        points=op['points']
        painter.save()
        try:
            painter.setRenderHint(painter.RenderHint.Antialiasing,True)
            painter.setOpacity(op.get('opacity',1.))
            if op.get('tool')=='tape':
                from editing.ne_eraser import tape_outline
                from rendering.ne_tape_patterns import paint_tape
                path=self.path(op,smooth)
                if self.outline_key==self.result_key and self.outline is not None:
                    self.outline_hits+=1
                else:
                    self.outline=tape_outline(path,op['width']);self.outline_key=self.result_key
                outline=self.outline
                paint_tape(painter,outline,op['color'],op['width'],op.get('tape_pattern',3))
                return
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(op['color']),op['width'],Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
            if len(points)==1:painter.drawPoint(QPointF(*points[0]))
            else:painter.drawPath(self.path(op,smooth))
        finally:painter.restore()
