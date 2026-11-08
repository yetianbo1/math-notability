"""Editable ink with vector erasure, independent fragments and shared undo."""
from dataclasses import dataclass, replace
from collections import OrderedDict
from document_core import HistoryIndex
from functools import cached_property
import math,re
from lxml import etree
from PyQt6.QtCore import QPointF,Qt
from PyQt6.QtGui import QPainterPath,QTransform,QColor,QPen
from editing.ne_inksmooth import smooth_points
from editing.ne_inkcapture import stabilize_middle_point,CaptureFilter
from editing.ne_ink_scale import scale_stroke

COLORS={'红':'#e53935','黄':'#e6b800','绿':'#198754','蓝':'#1976d2','黑':'#202124'}
HIGHLIGHT_COLORS={'黄':'#fff600','绿':'#b9f6ca','蓝':'#80d8ff','粉':'#f8bbd0','橙':'#ffcc80'}

@dataclass(frozen=True)
class Stroke:
    points: tuple
    color: str
    width: float
    opacity: float=1.0
    source_id: int | None=None
    tool: str='pen'
    geometry_is_smoothed: bool=False
    geometry_is_linear: bool=False
    outline: tuple | None=None
    tape_pattern: int=3
    smart_interpolation: bool=False

    @cached_property
    def bounds(self):
        if self.outline is not None:
            ox,oy=self.points[0]
            xs,ys=zip(*self.outline)
            return min(xs)+ox,min(ys)+oy,max(xs)+ox,max(ys)+oy
        if self.geometry_is_smoothed and not self.geometry_is_linear and self.render_path.elementCount()>1:
            rect=self.render_path.boundingRect()
            return rect.left(),rect.top(),rect.right(),rect.bottom()
        xs,ys=zip(*self.points)
        return min(xs),min(ys),max(xs),max(ys)

    @property
    def is_highlighter(self):
        return self.tool=='highlighter'

    def __copy__(self):
        return self

    def __deepcopy__(self,memo):
        clone=Stroke(self.points,self.color,self.width,self.opacity,self.source_id,
                     self.tool,self.geometry_is_smoothed,self.geometry_is_linear,self.outline,
                     self.tape_pattern,self.smart_interpolation)
        memo[id(self)]=clone
        return clone

    @cached_property
    def render_points(self):
        if self.geometry_is_smoothed or self.geometry_is_linear:
            return tuple(self.points)
        return tuple(smooth_points(self.points,self.width))

    @cached_property
    def render_path(self):
        points=self.render_points
        if not points:
            return QPainterPath()
        path=QPainterPath(QPointF(*points[0]))
        if len(points)==1:
            return path
        if self.geometry_is_linear:
            for point in points[1:]:path.lineTo(*point)
            return path
        if self.geometry_is_smoothed:
            if self.smart_interpolation:
                from editing.ne_smartink import smart_curve_path
                return smart_curve_path(points,self.width)
            from editing.ne_inkcurve import curve_path
            return curve_path(points,self.width)
        for index in range(1,len(points)-1):
            previous=points[index-1];current=points[index];following=points[index+1]
            u=(current[0]-previous[0],current[1]-previous[1])
            v=(following[0]-current[0],following[1]-current[1])
            ulen=math.hypot(*u);vlen=math.hypot(*v)
            cosine=((u[0]*v[0]+u[1]*v[1])/(ulen*vlen)
                    if ulen>1e-8 and vlen>1e-8 else 1.0)
            if cosine<0.5 and min(ulen,vlen)>max(2.0,self.width*0.65):
                path.lineTo(*current)
            else:
                midpoint=QPointF((current[0]+following[0])*0.5,
                                 (current[1]+following[1])*0.5)
                path.quadTo(QPointF(*current),midpoint)
        path.lineTo(*points[-1])
        return path

    @cached_property
    def erasure_points(self):
        """Flatten the displayed curve once; retain its shape after cutting."""
        if self.geometry_is_linear or len(self.points)==1:return self.points
        polygons=self.render_path.toSubpathPolygons(QTransform.fromScale(8,8))
        if not polygons:return self.points
        polygon=polygons[0]
        return tuple((p.x()/8,p.y()/8) for p in polygon)

    @cached_property
    def fill_path(self):
        path=QPainterPath()
        if self.outline:
            ox,oy=self.points[0]
            path.moveTo(self.outline[0][0]+ox,self.outline[0][1]+oy)
            for x,y in self.outline[1:]:path.lineTo(x+ox,y+oy)
            path.closeSubpath()
        # Rewinding bridges encode holes in one stored contour; normalize them
        # back to separate boundaries before hit testing or Boolean operations.
        return path.simplified()

    @cached_property
    def selection_outline(self):
        from editing.ne_eraser import visible_outline
        return visible_outline(self,True)

    @cached_property
    def linear_selection_outline(self):
        from editing.ne_eraser import visible_outline
        return visible_outline(self,False)


def distance2(point,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    length2=dx*dx+dy*dy
    t=max(0.,min(1.,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length2)) if length2 else 0.
    return (point[0]-a[0]-t*dx)**2+(point[1]-a[1]-t*dy)**2


def stroke_intersects(points,a,b,radius):
    """Whole-stroke hit testing needs no clipped fragments or length sums."""
    r2=radius*radius
    if len(points)==1:return distance2(points[0],a,b)<r2
    x0=min(a[0],b[0])-radius;x1=max(a[0],b[0])+radius
    y0=min(a[1],b[1])-radius;y1=max(a[1],b[1])+radius
    vx,vy=b[0]-a[0],b[1]-a[1]
    for p,q in zip(points,points[1:]):
        if max(p[0],q[0])<x0 or min(p[0],q[0])>x1 or max(p[1],q[1])<y0 or min(p[1],q[1])>y1:continue
        ux,uy=q[0]-p[0],q[1]-p[1];cross=ux*vy-uy*vx
        if abs(cross)>1e-12:
            dx,dy=a[0]-p[0],a[1]-p[1]
            t=(dx*vy-dy*vx)/cross;s=(dx*uy-dy*ux)/cross
            if 0<=t<=1 and 0<=s<=1:return True
        if min(distance2(p,a,b),distance2(q,a,b),
               distance2(a,p,q),distance2(b,p,q))<r2:return True
    return False


def clip_stroke(points,a,b,radius):
    """Subtract a round swept eraser analytically, including between samples."""
    if len(points)==1:
        return [] if distance2(points[0],a,b)<radius*radius else [points]
    axis=(b[0]-a[0],b[1]-a[1]);length=math.hypot(*axis)
    if length:ux,uy=axis[0]/length,axis[1]/length
    result=[];run=[];touched=False
    x0=min(a[0],b[0])-radius;x1=max(a[0],b[0])+radius
    y0=min(a[1],b[1])-radius;y1=max(a[1],b[1])+radius
    for p,q in zip(points,points[1:]):
        # Most segments of a long stroke are far from this mouse sample.
        if max(p[0],q[0])<x0 or min(p[0],q[0])>x1 or max(p[1],q[1])<y0 or min(p[1],q[1])>y1:
            if not run:run=[p]
            if q!=run[-1]:run.append(q)
            continue
        dx,dy=q[0]-p[0],q[1]-p[1];aa=dx*dx+dy*dy
        cuts=[0.,1.]
        if aa:
            for center in (a,b):
                fx,fy=p[0]-center[0],p[1]-center[1]
                bb=2*(fx*dx+fy*dy);cc=fx*fx+fy*fy-radius*radius
                disc=bb*bb-4*aa*cc
                if disc>=0:
                    root=math.sqrt(disc)
                    cuts.extend(t for t in ((-bb-root)/(2*aa),(-bb+root)/(2*aa)) if 0<t<1)
            if length:
                along=(p[0]-a[0])*ux+(p[1]-a[1])*uy
                across=-(p[0]-a[0])*uy+(p[1]-a[1])*ux
                du=dx*ux+dy*uy;dv=-dx*uy+dy*ux
                for start,delta,limits in ((along,du,(0,length)),(across,dv,(-radius,radius))):
                    if abs(delta)>1e-12:
                        cuts.extend(t for edge in limits if 0<(t:=(edge-start)/delta)<1)
        cuts=sorted(set(cuts))
        for lo,hi in zip(cuts,cuts[1:]):
            mid=(lo+hi)/2
            inside=distance2((p[0]+mid*dx,p[1]+mid*dy),a,b)<radius*radius-1e-10
            if inside:
                touched=True
                if run:result.append(tuple(run));run=[]
            else:
                start=(p[0]+lo*dx,p[1]+lo*dy);end=(p[0]+hi*dx,p[1]+hi*dy)
                if not run:run=[start]
                elif math.dist(run[-1],start)>1e-6:result.append(tuple(run));run=[start]
                if math.dist(run[-1],end)>1e-8:run.append(end)
    if run:result.append(tuple(run))
    return result if touched else [points]


def svg_paths(elements,page,page_height):
    """Yield checked native paths without flattening every curve for editing."""
    for data in elements:
        el=etree.fromstring(data)
        tokens=re.findall(r'[MCLmcl]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?',el.get('d',''))
        path=QPainterPath();i=0
        while i<len(tokens):
            command=tokens[i];i+=1
            count={'M':2,'L':2,'C':6}.get(command)
            if count is None:raise ValueError('遇到尚不支持编辑的笔迹路径')
            values=list(map(float,tokens[i:i+count]));i+=count
            if len(values)!=count:raise ValueError('笔迹路径不完整')
            for n in range(1,count,2):values[n]-=page*page_height
            if command=='M':path.moveTo(*values)
            elif command=='L':path.lineTo(*values)
            else:path.cubicTo(*values)
        yield el,path


def svg_strokes(elements,page,page_height):
    result=[]
    for el,path in svg_paths(elements,page,page_height):
        filled=el.get('data-ink-outline')=='1'
        color=el.get('fill' if filled else 'stroke','#000000');rgb=re.fullmatch(r'rgb\((\d+),(\d+),(\d+)\)',color.replace(' ',''))
        if rgb:color=QColor(*map(int,rgb.groups())).name()
        source=el.get('data-event-id')
        tool=el.get('data-tool','pen')
        tool=tool if tool in ('highlighter','tape') else 'pen'
        # Keep more detail when converting native SVG Bezier ink to editable
        # polylines. This changes only the in-memory editing representation;
        # the source NTB geometry is not rewritten on load.
        curve_scale = 8
        polygons=(path.toFillPolygons(QTransform.fromScale(curve_scale,curve_scale)) if filled else
                  path.toSubpathPolygons(QTransform.fromScale(curve_scale,curve_scale)))
        point_sets=[tuple((p.x()/curve_scale,p.y()/curve_scale) for p in polygon) for polygon in polygons]
        if not point_sets and path.elementCount():
            element=path.elementAt(0);point_sets=[((element.x,element.y),)]
        for points in point_sets:
            if points:
                result.append(Stroke(points,color,float(el.get('stroke-width','1')),
                                     float(el.get('fill-opacity' if filled else 'stroke-opacity','1')),
                                     int(source) if source is not None else None,tool,
                                     geometry_is_linear=True,
                                     tape_pattern=int(el.get('data-tape-pattern','3')),
                                     outline=(tuple((x-points[0][0],y-points[0][1]) for x,y in points) if filled else None)))
    return result


class EditModel:
    def __init__(self, originals):
        self.originals=originals
        self._history_index=HistoryIndex()
        self.history=[];self.cursor=0;self.next_token=0;self.saved_token=None
        self.cache=OrderedDict();self.pending=None;self.working=None;self.pending_changed=False
        self._compound=None
        self._erase_gesture=None
        self.smart_interpolation=False

    @property
    def dirty(self):return self.token!=self.saved_token
    @property
    def token(self):return self.history[self.cursor-1][0] if self.cursor else None
    @property
    def can_undo(self):return self.cursor>0
    @property
    def can_redo(self):return self.cursor<len(self.history)
    @property
    def operations(self):return [op for _,op in self.history[:self.cursor]]

    def mark_saved(self):self.saved_token=self.token

    def state(self,page):
        if page in self.erasing_pages:return self._erase_gesture[page]['state']
        if self.pending and self.pending['page']==page:return self.working
        if page not in self.cache:
            state=(list(self.originals(page)),set())
            for _,op in self.document_index.pages.get(page, ()):
                if op['kind'] in ('transfer','compound'):
                    strokes,changed=op['states'][page]
                    state=(list(strokes),set(changed))
                else:state=self.apply(state,op)
            self.cache[page]=state
            while len(self.cache)>8:self.cache.popitem(last=False)
        self.cache.move_to_end(page)
        return self.cache[page]

    @staticmethod
    def candidates(strokes,a,b,width,smooth):
        ax0=min(a[0],b[0]);ax1=max(a[0],b[0])
        ay0=min(a[1],b[1]);ay1=max(a[1],b[1])
        for index,stroke in enumerate(strokes):
            radius=width/2+(stroke.width/2 if stroke.outline is None else 0.)
            x0,y0,x1,y1=stroke.bounds
            # The smoothing filter can move a vertex by at most 1.5 units.
            padding=radius+(1.5 if smooth and not stroke.geometry_is_linear else 0.)
            if x1<ax0-padding or x0>ax1+padding or y1<ay0-padding or y0>ay1+padding:
                continue
            yield index,stroke,radius

    @staticmethod
    def erase_whole(state,a,b,width,smooth=True,hits=None,erased=None):
        from editing.ne_eraser import swept_outline,filled_intersects
        strokes,changed=state
        hits=set() if hits is None else hits
        eraser=None
        for index,stroke,radius in EditModel.candidates(strokes,a,b,width,smooth):
            if index in hits:continue
            if stroke.outline is not None:
                if eraser is None:eraser=swept_outline(a,b,width)
                hit=filled_intersects(stroke.fill_path,eraser)
            else:
                points=stroke.erasure_points if smooth else stroke.points
                hit=stroke_intersects(points,a,b,radius)
            if hit:hits.add(index)
        if not hits:return state,False
        # Prefer higher layers among hits, while retaining prior deletions.
        # A later highlighter/tape hit must never resurrect an erased pen.
        priority=max(3 if strokes[i].tool=='tape' else 2 if strokes[i].is_highlighter else 0 for i in hits)
        return EditModel._erase_whole_hits(state,hits,priority,erased)

    @staticmethod
    def _erase_whole_hits(state,hits,priority,erased=None):
        strokes,changed=state
        selected={i for i in hits if (3 if strokes[i].tool=='tape' else 2 if strokes[i].is_highlighter else 0)==priority}
        if erased is not None:
            erased.update(selected)
            selected=erased
        if not selected:return state,False
        whole_ids={strokes[i].source_id for i in selected
                   if strokes[i].source_id is not None and strokes[i].source_id not in changed}
        output=[s for i,s in enumerate(strokes) if i not in selected and s.source_id not in whole_ids]
        hidden=set(changed)|{strokes[i].source_id for i in selected if strokes[i].source_id is not None}
        return (output,hidden),True

    @staticmethod
    def erase(state,a,b,width,mode,smooth=True,tape_only=False):
        if mode!='partial':return EditModel.erase_whole(state,a,b,width,smooth)
        from editing.ne_eraser import swept_outline,subtract
        strokes,changed=state;changed=set(changed);replacements={}
        eraser=None
        for index,stroke,_ in EditModel.candidates(strokes,a,b,width,smooth):
            if tape_only and stroke.tool!='tape':continue
            if eraser is None:eraser=swept_outline(a,b,width)
            fragments=subtract(stroke,eraser,smooth)
            if fragments is None:continue
            replacements[index]=fragments
            if stroke.source_id is not None:changed.add(stroke.source_id)
        if not replacements:return state,False
        output=[]
        for index,stroke in enumerate(strokes):output.extend(replacements.get(index,[stroke]))
        return (output,changed),True

    @classmethod
    def apply(cls,state,op):
        if op['kind'] in ('move','delete'):
            selected=set(op['indices']);changed=set(state[1]);output=[]
            for index,stroke in enumerate(state[0]):
                if index not in selected:output.append(stroke);continue
                if stroke.source_id is not None:changed.add(stroke.source_id)
                if op['kind']=='move':
                    output.append(replace(stroke,points=tuple((x+op['dx'],y+op['dy']) for x,y in stroke.points)))
            return output,changed
        if op['kind']=='scale':
            selected=set(op['indices']);changed=set(state[1]);output=[]
            for index,stroke in enumerate(state[0]):
                if index not in selected:output.append(stroke);continue
                if stroke.source_id is not None:changed.add(stroke.source_id)
                output.append(scale_stroke(stroke,op['anchor'],op['factor']))
            return output,changed
        if op['kind']=='draw':
            stroke=Stroke(tuple(map(tuple,op['points'])),op['color'],op['width'],
                          op.get('opacity',1.0),tool=op.get('tool','pen'),
                          geometry_is_smoothed=bool(op.get('capture_smoothing')),
                          geometry_is_linear=bool(op.get('geometry_is_linear')),
                          outline=op.get('outline'),tape_pattern=op.get('tape_pattern',3),
                          smart_interpolation=bool(op.get('smart_interpolation')))
            return (state[0]+[stroke],set(state[1]))
        points=op['points']
        if op['mode']!='partial':
            hits=set();base=state
            # Old recovery logs keep their original priority semantics.
            erased=set() if op.get('whole_accumulate') else None
            for a,b in zip([points[0]]+points[:-1],points):
                state,_=cls.erase_whole(base,a,b,op['width'],op.get('erase_smoothing',True),hits,erased)
            return state
        for index,(a,b) in enumerate(zip([points[0]]+points[:-1],points)):
            start=op.get('tape_start')
            state,_=cls.erase(state,a,b,op['width'],op['mode'],op.get('erase_smoothing',True),
                              start is not None and index>=start)
        return state

    def change_selection(self,page,indices,dx=0.,dy=0.,delete=False):
        """One selection gesture is one undo entry; indices refer to its base state."""
        self.finish()
        if not all(math.isfinite(v) for v in (dx,dy)):raise ValueError('移动坐标无效')
        state=self.state(page)
        indices=tuple(sorted({i for i in indices if 0<=i<len(state[0])}))
        if not indices or (not delete and abs(dx)+abs(dy)<1e-8):return False
        op=dict(kind='delete' if delete else 'move',page=page,indices=indices,dx=dx,dy=dy)
        result=self.apply(state,op)
        del self.history[self.cursor:]
        self.next_token+=1;self.history.append((self.next_token,op));self.cursor+=1
        self.cache[page]=result;self.cache.move_to_end(page)
        while len(self.cache)>8:self.cache.popitem(last=False)
        return True

    def scale_selection(self,page,indices,anchor,factor):
        """One corner gesture is one undo entry; indices refer to its base state."""
        self.finish()
        factor=float(factor)
        ax,ay=float(anchor[0]),float(anchor[1])
        if not all(math.isfinite(v) for v in (ax,ay,factor)) or factor<=0:
            raise ValueError('缩放参数无效')
        if abs(factor-1.0)<1e-9:return False
        state=self.state(page)
        indices=tuple(sorted({i for i in indices if 0<=i<len(state[0])}))
        if not indices:return False
        op=dict(kind='scale',page=page,indices=indices,anchor=(ax,ay),factor=factor)
        result=self.apply(state,op)
        del self.history[self.cursor:]
        self.next_token+=1;self.history.append((self.next_token,op));self.cursor+=1
        self.cache[page]=result;self.cache.move_to_end(page)
        while len(self.cache)>8:self.cache.popitem(last=False)
        return True

    def add_shape(self,page,points,color,width):
        """One shape drag is one undo entry, stored as linear (unsmoothed) ink.

        Reusing the regular 'draw' op keeps history indexing, recovery logs and
        NTB write-back on their well-tested path; geometry_is_linear stops the
        handwritten-ink smoother from rounding the corners of a rectangle.
        """
        self.finish()
        points=tuple(tuple(map(float,p)) for p in points)
        if len(points)<2 or not all(len(p)==2 and all(math.isfinite(v) for v in p) for p in points):
            raise ValueError('图形点序列无效')
        try:
            width=float(width)
        except (TypeError,ValueError):
            raise ValueError('图形笔宽无效')
        if not isinstance(color,str) or not color or not math.isfinite(width) or width<=0:
            raise ValueError('图形样式无效')
        op=dict(kind='draw',page=page,points=points,color=color,width=width,
                opacity=1.0,tool='pen',mode='partial',geometry_is_linear=True)
        result=self.apply(self.state(page),op)
        del self.history[self.cursor:]
        self.next_token+=1;self.history.append((self.next_token,op));self.cursor+=1
        self.cache[page]=result;self.cache.move_to_end(page)
        while len(self.cache)>8:self.cache.popitem(last=False)
        return True

    def begin(self,page,point,kind,width,color,mode,opacity=1.0,
              capture_smoothing=False,tool='pen',erase_smoothing=True,tape_pattern=3):
        self.cancel()
        base=self.state(page)
        if not math.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
            raise ValueError('笔迹透明度应介于 0 和 1 之间')
        self.pending={'page':page,'kind':kind,'width':float(width),'color':color,
                      'mode':mode,'opacity':float(opacity),'points':[tuple(point)],
                      'tool':tool if tool in ('highlighter','tape') else 'pen','tape_pattern':int(tape_pattern),
                      'sample_spacing':getattr(self,'sample_spacing',.05),
                      'smart_interpolation':bool(getattr(self,'smart_interpolation',True))
                      if kind=='draw' and capture_smoothing else False}
        if kind=='erase':self.pending['erase_smoothing']=bool(erase_smoothing)
        # Only new draw/highlighter samples are stabilized, never eraser paths.
        # Store just two raw positions; the edited points are the final stroke.
        if kind=='draw' and capture_smoothing:
            self.pending['capture_smoothing']=True
            self.pending['_raw_previous']=None
            self.pending['_raw_last']=tuple(point)
            self.pending['_capture_filter']=CaptureFilter(self.pending['tool'],float(width))
        self.working=(list(base[0]),set(base[1]));self.pending_changed=False
        if kind=='erase':
            if mode=='partial':
                tapes=([s for s in base[0] if s.tool=='tape'],set())
                self.pending['tape_only']=bool(tapes[0] and self.erase_whole(tapes,point,point,width,erase_smoothing)[1])
                self.pending['tape_start']=0 if self.pending['tape_only'] else None
                self.working,self.pending_changed=self.erase(self.working,point,point,width,mode,erase_smoothing,self.pending['tape_only'])
            else:
                self.pending['_erase_base']=base;self.pending['_erase_hits']=set()
                self.pending['_erase_selected']=set();self.pending['whole_accumulate']=True
                self.working,self.pending_changed=self.erase_whole(base,point,point,width,erase_smoothing,
                    self.pending['_erase_hits'],self.pending['_erase_selected'])

    def transfer_selection(self,page,target,indices,dx=0.,dy=0.):
        """Move ink between stable page identities as one undoable operation."""
        self.finish()
        if page==target:return self.change_selection(page,indices,dx,dy)
        if not all(math.isfinite(v) for v in (dx,dy)):raise ValueError('移动坐标无效')
        strokes,changed=self.state(page)
        selected={i for i in indices if 0<=i<len(strokes)}
        if not selected:return False
        destination,destination_changed=self.state(target)
        hidden=set(changed)
        moved=[];remaining=[]
        for i,stroke in enumerate(strokes):
            if i not in selected:remaining.append(stroke);continue
            if stroke.source_id is not None:hidden.add(stroke.source_id)
            moved.append(replace(stroke,source_id=None,
                points=tuple((x+dx,y+dy) for x,y in stroke.points)))
        states={page:(tuple(remaining),frozenset(hidden)),
                target:(tuple(destination)+tuple(moved),frozenset(destination_changed))}
        del self.history[self.cursor:]
        self.next_token+=1
        self.history.append((self.next_token,dict(kind='transfer',page=page,target=target,states=states)))
        self.cursor+=1;self.cache.clear()
        return True

    def extend(self,point,force=False,timestamp=None):
        if not self.pending:return
        op=self.pending
        point=tuple(point)
        if op['kind']=='draw':op['_input_endpoint']=point
        previous=op.get('_raw_last',op['points'][-1])
        distance=math.dist(previous,point)
        threshold=op.get('sample_spacing',.05) if op['kind']=='draw' else .15
        if distance<1e-10 or (distance<threshold and not force):return
        if op.get('capture_smoothing'):
            gain=op['_capture_filter'].observe(point,timestamp)
            if op['_raw_previous'] is not None:
                # Update the previous vertex as each new sample arrives.
                # Keep the current tip at the exact incoming location.
                op['points'][-1]=stabilize_middle_point(
                    op['_raw_previous'],previous,point,op['width'],gain)
            op['_raw_previous']=previous
            op['_raw_last']=point
        op['points'].append(point)
        if op['kind']=='erase':
            if op['mode']=='partial':
                if not op.get('tape_only'):
                    tapes=([s for s in self.working[0] if s.tool=='tape'],set())
                    if tapes[0] and self.erase_whole(tapes,previous,point,op['width'],op.get('erase_smoothing',True))[1]:
                        op['tape_only']=True;op['tape_start']=len(op['points'])-1
                self.working,touched=self.erase(self.working,previous,point,op['width'],op['mode'],op.get('erase_smoothing',True),op.get('tape_only',False))
            else:
                self.working,touched=self.erase_whole(op['_erase_base'],previous,point,
                    op['width'],op.get('erase_smoothing',True),op['_erase_hits'],op['_erase_selected'])
            self.pending_changed |= touched

    def finish(self):
        if not self.pending:return False
        op=self.pending
        if op['kind']=='draw':
            endpoint=op.get('_input_endpoint')
            if endpoint is not None:self.extend(endpoint,force=True)
            op.pop('_input_endpoint',None)
            self.working=self.apply(self.working,op);self.pending_changed=True
        changed=self.pending_changed
        if changed:
            # Sampling internals are transient and never stored in undo history.
            op.pop('_raw_previous',None)
            op.pop('_raw_last',None)
            op.pop('_capture_filter',None)
            op.pop('_erase_base',None)
            op.pop('_erase_hits',None)
            op.pop('_erase_selected',None)
            del self.history[self.cursor:]
            self.next_token+=1;self.history.append((self.next_token,op));self.cursor+=1
            self.cache[op['page']]=self.working
            self.cache.move_to_end(op['page'])
            while len(self.cache)>8:self.cache.popitem(last=False)
        self.pending=None;self.working=None
        return changed

    def cancel(self):self.pending=None;self.working=None

    @property
    def erasing_pages(self):return self._erase_gesture.keys() if self._erase_gesture is not None else ()

    def begin_erase_gesture(self):
        self.finish()
        if self._erase_gesture is not None or self._compound is not None:raise ValueError('已有未完成的笔迹操作')
        self._erase_gesture={}

    def extend_erase_gesture(self,page,a,b,width,mode,smooth=True):
        record=self._erase_gesture.get(page)
        if record is None:
            base=self.state(page)
            tapes=([s for s in base[0] if s.tool=='tape'],set())
            tape_only=bool(tapes[0] and self.erase_whole(tapes,a,b,width,smooth)[1])
            record=dict(base=base,state=base,hits=set(),erased=set(),changed=False,tape_only=tape_only)
            self._erase_gesture[page]=record
        if mode=='partial':
            if not record['tape_only']:
                tapes=([s for s in record['state'][0] if s.tool=='tape'],set())
                record['tape_only']=bool(tapes[0] and self.erase_whole(tapes,a,b,width,smooth)[1])
            state,changed=self.erase(record['state'],a,b,width,mode,smooth,record['tape_only'])
        else:
            state,changed=self.erase_whole(record['base'],a,b,width,smooth,record['hits'])
            priority=max((3 if r['base'][0][i].tool=='tape' else 2 if r['base'][0][i].is_highlighter else 0
                          for r in self._erase_gesture.values() for i in r['hits']),default=0)
            for r in self._erase_gesture.values():
                r['state'],r['changed']=self._erase_whole_hits(r['base'],r['hits'],priority,r['erased'])
            return
        record['state']=state;record['changed']|=changed

    def finish_erase_gesture(self,cancel=False):
        records=self._erase_gesture;self._erase_gesture=None
        if cancel or not records:return False
        states={page:(tuple(r['state'][0]),frozenset(r['state'][1]))
                for page,r in records.items() if r['changed']}
        if not states:return False
        del self.history[self.cursor:]
        self.next_token+=1
        self.history.append((self.next_token,dict(kind='compound',page=None,states=states)))
        self.cursor+=1;self.cache.clear()
        return True

    def begin_compound(self):
        """Keep all page fragments of one pointer gesture in one undo entry."""
        if self._compound is not None:raise ValueError('已有未完成的跨页笔迹')
        self._compound=(list(self.history),self.cursor)

    def finish_compound(self):
        if self._compound is None:return False
        self.finish()
        history,start=self._compound
        operations=self.history[start:self.cursor]
        self._compound=None
        if not operations:return False
        # A tap or a stroke confined to one page is already one undo step.
        # Avoid retaining a complete page snapshot for every single dot.
        if len(operations)==1 and operations[0][1]['kind']=='draw':
            self.history=history[:start]+operations
            self.cursor=start+1
            return True
        pages={op['page'] for _,op in operations}
        states={page:(tuple(self.state(page)[0]),frozenset(self.state(page)[1])) for page in pages}
        self.history=history[:start]
        self.next_token+=1
        op=dict(kind='compound',page=None,states=states)
        if all(item['kind']=='draw' for _,item in operations):
            op['_append_draws']=tuple(item for _,item in operations)
        self.history.append((self.next_token,op))
        self.cursor=start+1;self.cache.clear()
        return True

    def cancel_compound(self):
        self.cancel()
        if self._compound is not None:
            self.history,self.cursor=self._compound
            self._compound=None;self.cache.clear()

    def record_external(self,before,after,restore):
        """Join image actions to the same chronological history as ink/pages."""
        self.finish()
        if before==after:return False
        del self.history[self.cursor:]
        self.next_token+=1
        self.history.append((self.next_token,dict(kind='images',page=None,
            before=before,after=after,restore=restore)))
        self.cursor+=1
        from infrastructure.ne_diagnostics import stage
        stage(f'image edit committed; history={self.cursor}')
        return True

    def undo(self):
        self.cancel()
        if self.cursor:
            op=self.history[self.cursor-1][1]
            if op['kind']=='images':op['restore'](op['before'])
            self.cursor-=1;self.cache.clear()
            from infrastructure.ne_diagnostics import stage
            stage(f'undo {op["kind"]}; history={self.cursor}')

    def redo(self):
        self.cancel()
        if self.cursor<len(self.history):
            op=self.history[self.cursor][1]
            if op['kind']=='images':op['restore'](op['after'])
            self.cursor+=1;self.cache.clear()
            from infrastructure.ne_diagnostics import stage
            stage(f'redo {op["kind"]}; history={self.cursor}')

    @property
    def document_index(self):
        return self._history_index.sync(self.history,self.cursor)

    def edited_pages(self):
        return set(self.document_index.pages) | set(self.erasing_pages)

    def native_changes(self):
        hidden=set();added=[]
        for page in sorted(self.edited_pages()):
            strokes,changed=self.state(page);hidden.update(changed)
            for stroke in strokes:
                if stroke.source_id is None or stroke.source_id in changed:added.append((page,stroke))
        return hidden,added


def paint_strokes(painter,strokes,smooth=True,*,reveal_tape=False):
    """Paint editable/live ink with antialiasing and fractional-width round pens.

    Preserve the caller's opacity, pen and render hints, including when this is
    used from thumbnails or a PDF writer. Never smooth stored NTB coordinates.
    """
    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        for stroke in strokes:
            painter.setOpacity(stroke.opacity)
            if stroke.tool=='tape':
                from rendering.ne_tape_patterns import paint_tape
                from editing.ne_eraser import visible_outline
                paint_tape(painter,(stroke.selection_outline if smooth else stroke.linear_selection_outline),stroke.color,stroke.width,
                           stroke.tape_pattern,revealed=reveal_tape)
                continue
            if stroke.outline is not None:
                painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(stroke.color))
                painter.drawPath(stroke.fill_path)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                continue
            pen=QPen(QColor(stroke.color),float(stroke.width),
                     Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,
                     Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if len(stroke.points)==1:
                painter.drawPoint(QPointF(*stroke.points[0]))
                continue
            # Geometry is smoothed only while rendering; native ink events,
            # undo history and exported NTB coordinates are never altered.
            if smooth:
                painter.drawPath(stroke.render_path)
            else:
                path=QPainterPath(QPointF(*stroke.points[0]))
                for point in stroke.points[1:]:
                    path.lineTo(*point)
                painter.drawPath(path)
    finally:
        painter.restore()
