"""Clip continuous pointer motion to native page rectangles, including fast jumps."""
import math
from PyQt6.QtCore import QPointF,QRectF
from PyQt6.QtGui import QPainterPath,QTransform
from editing.ne_edits import Stroke
from editing.ne_eraser import stroke_outline,tape_outline


def segment_interval(a,b,left,top,width,height):
    """Liang–Barsky clipping; return the exact entry/exit along the segment."""
    lo,hi=0.,1.
    for start,delta,minimum,maximum in ((a.x(),b.x()-a.x(),left,left+width),
                                       (a.y(),b.y()-a.y(),top,top+height)):
        if abs(delta)<1e-12:
            if not minimum<=start<=maximum:return None
        else:
            first,last=sorted(((minimum-start)/delta,(maximum-start)/delta))
            lo=max(lo,first);hi=min(hi,last)
            if hi-lo<=1e-10:return None
    return lo,hi


class ContinuousInkMixin:
    def _beginInkFragment(self,page,point):
        smooth=bool(self.ink_smoothing)
        if self.edit_tool=='tape':
            self.edits.begin(page,point,'draw',self.tape_width,self.tape_color,
                             'partial',1.,smooth,'tape',tape_pattern=self.tape_pattern)
        elif self.edit_tool=='highlight':
            self.edits.begin(page,point,'draw',self.highlight_width,self.highlight_color,
                             'partial',self.highlight_opacity,smooth,'highlighter')
        else:
            self.edits.begin(page,point,'draw',self.pen_width,self.pen_color,
                             'partial',1.,smooth,'pen')
        op=(self.edits.core if hasattr(self.edits,'core') else self.edits).pending
        op['_paper_size']=self.editPageSize(page)
        op['_seam_start']=((page>0 and abs(point[1])<1e-7) or
                           (page+1<self.page_count and abs(point[1]-op['_paper_size'][1])<1e-7))

    def _beginContinuousDraw(self,page,point,position):
        if self.edit_tool=='erase':
            self.edits.begin_erase_gesture()
            self._continuous_draw=QPointF(position)
            self._continuous_erase_radius=max(self.eraser_width*max(dw/self.editPageSize(i)[0],dh/self.editPageSize(i)[1])/2
                                              for i,(dw,dh) in enumerate(self.draw_sizes))
            self._eraseContinuousSegment(position,position)
            self._drag_scroll_timer.start()
            return
        self.edits.begin_compound()
        self._continuous_draw=QPointF(position)
        self._beginInkFragment(page,point)
        self._drag_scroll_timer.start()

    def _extendContinuousDraw(self,position):
        start=self._continuous_draw;end=QPointF(position)
        self._continuous_draw=end
        if self.edit_tool=='erase':
            self._eraseContinuousSegment(start,end);return
        if start==end and not getattr(self,'_force_endpoint',False):return
        first,last=sorted((self.pageAt(start.y()),self.pageAt(end.y())))
        if first>0 and abs(min(start.y(),end.y())-self.pageTop(first))<1e-9:
            first-=1
        intervals=[];damage=QRectF()
        for page in range(first,last+1):
            width,height=self.draw_sizes[page];left=(self.width()-width)/2;top=self.pageTop(page)
            interval=segment_interval(start,end,left,top,width,height)
            if interval is not None:intervals.append((*interval,page,left,top,width,height))
        current=self.edits.pending
        active_page=current['page'] if current else -1
        intervals.sort(key=lambda item:(item[0],item[1],item[2]!=active_page))
        for lo,hi,page,left,top,width,height in intervals:
            nw,nh=self.editPageSize(page)
            def local(t):
                x=start.x()+(end.x()-start.x())*t
                y=start.y()+(end.y()-start.y())*t
                return (max(0.,min(nw,(x-left)*nw/width)),
                        max(0.,min(nh,(y-top)*nh/height)))
            pending=self.edits.pending
            if pending and (pending['page']!=page or lo>1e-10):
                self.edits.finish();pending=None
            if pending is None:self._beginInkFragment(page,local(lo))
            pending=self.edits.pending
            from editing.ne_inkcurve import tail_bounds
            previous_bounds=tail_bounds(
                pending['points'],pending['width'],pending.get('capture_smoothing',False),
                pending.get('smart_interpolation',False))
            # Never drop a subpixel edge sample: both fragments must meet.
            self.edits.extend(local(hi),force=(hi<1.-1e-10 or lo>1e-10 or getattr(self,'_force_endpoint',False)),timestamp=getattr(self,'_input_time',None))
            bounds=previous_bounds.united(tail_bounds(
                pending['points'],pending['width'],pending.get('capture_smoothing',False),
                pending.get('smart_interpolation',False)))
            rect=QRectF(left+bounds.left()*width/nw,top+bounds.top()*height/nh,
                        bounds.width()*width/nw,bounds.height()*height/nh)
            damage=damage.united(rect.adjusted(-3,-3,3,3))
            core=self.edits.core if hasattr(self.edits,'core') else self.edits
            y=local(hi)[1]
            core.pending['_seam_end']=((page>0 and abs(y)<1e-7) or
                                      (page+1<self.page_count and abs(y-nh)<1e-7))
            if hi<1.-1e-10:self.edits.finish()
        if not intervals:self.edits.finish()
        if not damage.isEmpty():self.update(damage.toAlignedRect())

    def _eraseContinuousSegment(self,start,end):
        radius=self._continuous_erase_radius
        first=self.pageAt(min(start.y(),end.y())-radius)
        last=self.pageAt(max(start.y(),end.y())+radius)
        for page in range(first,last+1):
            dw,dh=self.draw_sizes[page];nw,nh=self.editPageSize(page)
            left=(self.width()-dw)/2;top=self.pageTop(page)
            rx,ry=self.eraser_width*dw/nw/2,self.eraser_width*dh/nh/2
            if segment_interval(start,end,left-rx,top-ry,dw+2*rx,dh+2*ry) is None:continue
            # Keep the centre outside a neighbouring paper: clamping it would
            # enlarge the erased area at the seam instead of clipping the disk.
            local=lambda p:((p.x()-left)*nw/dw,(p.y()-top)*nh/dh)
            source=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
            core=self.edits.core if hasattr(self.edits,'core') else self.edits
            core.extend_erase_gesture(source,local(start),local(end),self.eraser_width,self.eraser_mode,self.ink_smoothing)
        self.update()

    def _finishContinuousDraw(self,cancel=False):
        if getattr(self,'_continuous_draw',None) is None:return False
        self._continuous_draw=None
        core=self.edits.core if hasattr(self.edits,'core') else self.edits
        if core._erase_gesture is not None:return core.finish_erase_gesture(cancel)
        if cancel:
            self.edits.cancel_compound();return False
        core.finish()
        # Round caps at a page cut would narrow thick diagonal ink. Extend its
        # endpoint tangents, clip the filled outline to the paper, and preserve
        # that same footprint in NTB, screen rendering, erasure and PDF export.
        _,start=core._compound
        for _,op in core.history[start:core.cursor]:
            size=op.pop('_paper_size')
            before=op.pop('_seam_start',False);after=op.pop('_seam_end',False)
            if before or after:
                outline=seam_outline(op,size,before,after,self.ink_smoothing)
                if outline:
                    op['outline']=outline
                    core.cache.pop(op['page'],None)
        return self.edits.finish_compound()


def seam_outline(op,size,before,after,smooth):
    stroke=Stroke(tuple(op['points']),op['color'],op['width'],
                  geometry_is_smoothed=bool(op.get('capture_smoothing')),
                  smart_interpolation=bool(op.get('smart_interpolation')),
                  tool=op.get('tool','pen'))
    points=list(stroke.erasure_points if smooth else stroke.points)
    if len(points)<2:return None
    def extension(a,b):
        distance=math.dist(a,b)
        if distance<1e-10:return a
        return tuple(a[i]+(a[i]-b[i])*op['width']*2/distance for i in (0,1))
    if before:
        other=next((p for p in points[1:] if math.dist(points[0],p)>1e-10),points[0])
        points.insert(0,extension(points[0],other))
    if after:
        other=next((p for p in reversed(points[:-1]) if math.dist(points[-1],p)>1e-10),points[-1])
        points.append(extension(points[-1],other))
    path=QPainterPath(QPointF(*points[0]))
    for p in points[1:]:path.lineTo(*p)
    paper=QPainterPath();paper.addRect(QRectF(0,0,*size))
    footprint=(tape_outline(path,op['width']) if op.get('tool')=='tape'
               else stroke_outline(path,op['width']))
    # Strokers use winding fill for overlapping round joins. NTB custom
    # contours use even-odd fill; resolve those overlaps before flattening,
    # otherwise the overlaps become persistent circular holes.
    clipped=footprint.intersected(paper).simplified()
    polygon=clipped.toFillPolygon(QTransform.fromScale(8,8))
    if not 3<=len(polygon)<=65535:return None
    ox,oy=op['points'][0]
    return tuple((p.x()/8-ox,p.y()/8-oy) for p in polygon)
