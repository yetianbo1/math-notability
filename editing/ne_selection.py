"""Ink selection with cross-page previews and atomic undoable moves."""
import math
from infrastructure import ne_ai_runtime  # Windows: initialize before QApplication.
from dataclasses import replace
from PyQt6.QtCore import QPointF,QRectF,Qt,QTimer
from PyQt6.QtGui import QColor,QPen,QPainterPath,QPolygonF,QTransform
from editing.ne_smart_selection import InkSelectionIndex
from editing.ne_ink_scale import (CORNERS,HANDLE_TOLERANCE_PX,opposite_corner,
                                  rect_corners,scale_strokes,scaled_bounds)

def stroke_touches_rect(stroke,rect,smooth=True):
    """Select entire strokes by their displayed outline, including filled ink."""
    radius=0 if stroke.outline is not None else stroke.width/2
    x0,y0,x1,y1=stroke.bounds
    if x1+radius<rect.left() or x0-radius>rect.right() or y1+radius<rect.top() or y0-radius>rect.bottom():return False
    shape=stroke.selection_outline if smooth else stroke.linear_selection_outline
    return shape.intersects(rect) or rect.contains(shape.boundingRect())


def whole_stroke_indices(strokes,indices):
    """Keep all surviving subpaths of a native pen event together on this page."""
    source_ids={strokes[i].source_id for i in indices if strokes[i].source_id is not None}
    return indices|{i for i,s in enumerate(strokes) if s.source_id is not None and s.source_id in source_ids}


class SelectionMixin:
    def initSelection(self):
        self.selection_page=None;self.selected_indices=set();self.selection_drag=None
        self.selection_mode='exact';self.selection_shape='rectangle'
        self.selection_pen=True;self.selection_highlighter=True
        self.local_ai_status=''
        self._selection_preview_timer=QTimer(self)
        self._selection_preview_timer.setSingleShot(True)
        self._selection_preview_timer.setInterval(33)
        self._selection_preview_timer.timeout.connect(self._updateSelectionPreview)
        self._selection_highlight_cache=None
        self.selection_region=None

    def setSelectionOptions(self,*,mode=None,shape=None,pen=None,highlighter=None):
        self.finishGesture()
        if mode is not None:
            if mode not in ('ai','smart','exact','contained'):raise ValueError('未知笔迹选取模式')
            self.selection_mode=mode
        if shape is not None:
            if shape not in ('rectangle','lasso'):raise ValueError('未知选区形状')
            self.selection_shape=shape
        if pen is not None:self.selection_pen=bool(pen)
        if highlighter is not None:self.selection_highlighter=bool(highlighter)
        self.update()

    def selectionIncludes(self,stroke):
        return self.selection_highlighter if stroke.is_highlighter else self.selection_pen

    def expandInkSelection(self,level='character'):
        """Explicit geometric grouping, leaving the user in control of expansion."""
        self.finishGesture()
        if self.edit_tool!='select' or not self.selected_indices:return
        strokes=self.edits.state(self.selection_page)[0]
        index=InkSelectionIndex(strokes);bounds=self.selectionBounds()
        size=index.character_size(bounds);indices=set(self.selected_indices)
        groups=index.groups(size)
        for members,box in groups:
            if members&indices:indices.update(members)
        if level=='line':
            # Keep a fixed vertical band: never walk into a second line/column.
            boxes=[index.boxes[i] for i in indices]
            left=min(b[0] for b in boxes);right=max(b[2] for b in boxes)
            top=min(b[1] for b in boxes);bottom=max(b[3] for b in boxes)
            pending=list(groups)
            while pending:
                remaining=[];changed=False
                for members,b in pending:
                    overlap=min(bottom,b[3])-max(top,b[1])
                    gap=max(left-b[2],b[0]-right,0.)
                    if overlap>=.5*min(bottom-top,b[3]-b[1]) and gap<=size*.65:
                        indices.update(members);left=min(left,b[0]);right=max(right,b[2]);changed=True
                    else:remaining.append((members,b))
                if not changed:break
                pending=remaining
        indices={i for i in indices if self.selectionIncludes(strokes[i])}
        self.selected_indices=whole_stroke_indices(strokes,indices)
        self.selectionChanged.emit(len(self.selected_indices));self.update()

    def clearSelection(self):
        self._selection_preview_timer.stop();self._selection_highlight_cache=None
        self.selection_drag=None;self.selection_page=None;self.selected_indices=set()
        self.selection_region=None
        self.selectionChanged.emit(0);self.update()

    def selectionBounds(self):
        if self.selection_page is None or not self.selected_indices:return None
        strokes=self.edits.state(self.selection_page)[0]
        boxes=[(s.bounds,0. if s.outline is not None else s.width/2) for i,s in enumerate(strokes) if i in self.selected_indices]
        if not boxes:return None
        x0=min(b[0]-r for b,r in boxes);y0=min(b[1]-r for b,r in boxes)
        x1=max(b[2]+r for b,r in boxes);y1=max(b[3]+r for b,r in boxes)
        return QRectF(x0,y0,max(.01,x1-x0),max(.01,y1-y0))

    def selectionTolerance(self,page):
        return 6*self.editPageSize(page)[0]/self.draw_sizes[page][0]

    def selectionScreenBox(self):
        """Selection frame in widget coordinates, matching paintSelectionOverlay."""
        bounds=self.selectionBounds()
        if bounds is None or self.selection_page is None:return None,None
        page=self.selection_page
        w,h=self.draw_sizes[page];nw,nh=self.editPageSize(page)
        left=(self.width()-w)/2
        top=self.tops[page] if hasattr(self,'tops') else self.pageTop(page)
        rect=bounds.adjusted(-3,-3,3,3)
        box=QRectF(left+rect.x()*w/nw,top+rect.y()*h/nh,
                   rect.width()*w/nw,rect.height()*h/nh)
        return page,box

    def hitSelectionResizeHandle(self,position):
        """Return (page, corner, page-space bounds) when a corner is under the pointer."""
        if self.edit_tool!='select' or not self.show_ink or position is None:return None
        page,box=self.selectionScreenBox()
        if box is None:return None
        # A flat selection (one handwritten line) puts all four corners within
        # a few pixels of each other; always use the geometrically nearest one
        # instead of the first match, or the wrong anchor gets picked.
        best=None;best_dist=float('inf')
        for corner,point in rect_corners(box.left(),box.top(),
                                         box.width(),box.height()).items():
            dist=math.hypot(position.x()-point[0],position.y()-point[1])
            if dist<best_dist:
                best_dist=dist;best=(page,corner,self.selectionBounds())
        return best if best_dist<=HANDLE_TOLERANCE_PX else None

    def beginSelection(self,position,modifiers=Qt.KeyboardModifier.NoModifier):
        if self.edit_tool=='image':return False
        additive=bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        subtract=bool(modifiers & Qt.KeyboardModifier.AltModifier)
        handle=None if (additive or subtract) else self.hitSelectionResizeHandle(position)
        if handle is not None:
            page,corner,bounds=handle
            anchor=rect_corners(bounds.x(),bounds.y(),
                                bounds.width(),bounds.height())[opposite_corner(corner)]
            self.selection_drag=dict(kind='scale',page=page,target=page,corner=corner,
                bounds=bounds,anchor=anchor,factor=1.0,
                preview=(bounds.x(),bounds.y(),bounds.width(),bounds.height()))
            self.setFocus();self.update();return True
        hit=self.pointOnPage(position)
        if hit is None:self.clearSelection();return False
        page,point=hit;bounds=self.selectionBounds();tol=self.selectionTolerance(page)
        # The displayed frame (including its handles and empty spaces between
        # letters) is the drag target. Re-testing only the ink here silently
        # starts a new selection and drops strokes when a word is dragged.
        on_selected=(page==self.selection_page and bounds is not None and
                     bounds.adjusted(-3,-3,3,3).adjusted(-tol,-tol,tol,tol).contains(QPointF(*point)))
        base=set(self.selected_indices) if (additive or subtract) and page==self.selection_page else set()
        base_region=QPainterPath(self.selection_region) if base and self.selection_region is not None else None
        if on_selected and not (additive or subtract):
            self.selection_drag=dict(kind='move',page=page,target=page,start=point,last=point,dx=0.,dy=0.,bounds=bounds)
        else:
            self.clearSelection();self.selection_page=page
            self.selection_drag=dict(kind='box',page=page,start=point,last=point,base_indices=base,
                shape=self.selection_shape,mode=self.selection_mode,subtract=subtract,points=[point],base_region=base_region)
        self.setFocus();self.update();return True

    def extendSelection(self,position):
        drag=self.selection_drag
        if not drag:return
        page=drag['page']
        if drag['kind']=='scale':
            hit=self.pointOnPage(position,page)
            if hit is None:return
            _,point=hit;bounds=drag['bounds']
            try:
                nx,ny,nw,nh,ax,ay,factor=scaled_bounds(
                    (bounds.x(),bounds.y(),bounds.width(),bounds.height()),
                    drag['corner'],point,self.editPageSize(page))
            except ValueError:return
            drag.update(preview=(nx,ny,nw,nh),anchor=(ax,ay),factor=factor)
            self.update();return
        if drag['kind']=='move':
            hit=self.pointOnPage(position)
            if hit is None:return
            target,point=hit;nw,nh=self.editPageSize(target);bounds=drag['bounds']
            if target!=page and (bounds.width()>nw or bounds.height()>nh):return
            dx=point[0]-drag['start'][0];dy=point[1]-drag['start'][1]
            if target==page:
                dx=max(min(0.,-bounds.left()),min(max(0.,nw-bounds.right()),dx))
                dy=max(min(0.,-bounds.top()),min(max(0.,nh-bounds.bottom()),dy))
            else:
                dx=max(-bounds.left(),min(nw-bounds.right(),dx))
                dy=max(-bounds.top(),min(nh-bounds.bottom(),dy))
            drag.update(target=target,last=point,dx=dx,dy=dy)
            self.update();return
        w,h=self.draw_sizes[page];nw,nh=self.editPageSize(page)
        left=(self.width()-w)/2
        # The selection box stays on its source page; selected ink may move.
        point=(max(0.,min(nw,(position.x()-left)*nw/w)),max(0.,min(nh,(position.y()-self.pageTop(page))*nh/h)))
        drag['last']=point
        if math.dist(point,drag['points'][-1])>=self.editPageSize(page)[0]/self.draw_sizes[page][0]:
            drag['points'].append(point)
            if len(drag['points'])>4096:drag['points']=drag['points'][::2]+[point]
        if drag['shape']=='lasso':
            # Input never waits for an outline intersection scan. Qt coalesces
            # painting independently; the latest trajectory is visible now.
            if not self._selection_preview_timer.isActive():self._selection_preview_timer.start()
        else:
            drag['preview_indices']=self._querySelection(drag)
            self.selectionChanged.emit(len(drag['preview_indices']))
        self.update()

    def _updateSelectionPreview(self):
        self._selection_preview_timer.stop()
        drag=self.selection_drag
        if not drag or drag['kind']!='box':return
        indices=self._querySelection(drag)
        if indices!=drag.get('preview_indices',drag.get('base_indices',set())):
            drag['preview_indices']=indices
            self.selectionChanged.emit(len(indices));self.update()

    def _lassoPoints(self,drag):
        points=drag['points']+[drag['last']]
        scale=self.draw_sizes[drag['page']][0]/self.editPageSize(drag['page'])[0]
        length=sum(math.dist(p,q) for p,q in zip(points,points[1:]))
        closed=length*scale>24 and math.dist(points[0],points[-1])*scale<=8
        if closed:points=points[:-1]+[points[0]]
        return points,length,closed

    def _selectionRegion(self,drag):
        a,b=drag['start'],drag['last'];region=QPainterPath()
        click_tol=2*self.editPageSize(drag['page'])[0]/self.draw_sizes[drag['page']][0]
        if drag['shape']=='lasso':
            points,length,_=self._lassoPoints(drag)
            clicked=length<=click_tol
            if not clicked and len(points)>=3:
                region.addPolygon(QPolygonF([QPointF(*p) for p in points]));region.closeSubpath()
        else:
            clicked=math.dist(a,b)<=click_tol
            if not clicked:region.addRect(QRectF(QPointF(*a),QPointF(*b)).normalized())
        if clicked:
            tol=self.selectionTolerance(drag['page'])
            region.addRect(QRectF(b[0]-tol,b[1]-tol,tol*2,tol*2))
        return region,clicked

    def _querySelection(self,drag,*,final=False):
        page=drag['page'];strokes=self.edits.state(page)[0]
        if 'index' not in drag:drag['index']=InkSelectionIndex(strokes)
        region,clicked=self._selectionRegion(drag);rect=region.boundingRect()
        smooth=self.ink_smoothing
        def outline(s):return s.selection_outline if smooth else s.linear_selection_outline
        def touches(s,rect):
            return self.selectionIncludes(s) and stroke_touches_rect(s,rect,smooth) and region.intersects(outline(s))
        if clicked:
            chosen=next((i for i in range(len(strokes)-1,-1,-1) if touches(strokes[i],rect)),None)
            indices={chosen} if chosen is not None else set()
            if chosen is not None and drag['mode'] in ('smart','ai'):
                for members,_ in drag['index'].groups(drag['index'].character_size(rect)):
                    if chosen in members:indices.update(members);break
        else:
            mode='smart' if drag['mode']=='ai' else drag['mode']
            indices=drag['index'].select(rect,mode,touches,outline,region)
        if final and drag['mode']=='ai' and indices:
            try:
                from editing.ne_local_selection_ai import complete
                indices,lines=complete(drag['index'],rect,region,indices,smooth)
                self.local_ai_status=f'本地 AI 已完成 · {lines} 个文字区域'
            except Exception as exc:
                from infrastructure.ne_diagnostics import exception
                exception('local selection AI',exc)
                self.local_ai_status='本地 AI 不可用，已使用几何补全'
            window=self.window()
            if hasattr(window,'statusBar'):window.statusBar().showMessage(self.local_ai_status,5000)
        indices=whole_stroke_indices(strokes,{i for i in indices if self.selectionIncludes(strokes[i])})
        base=drag.get('base_indices',set())
        return base-indices if drag.get('subtract') else base|indices

    def finishSelection(self):
        self._selection_preview_timer.stop()
        drag=self.selection_drag
        if not drag:return
        self.selection_drag=None;page=drag['page']
        if drag['kind']=='move':
            target=drag.get('target',page)
            if target!=page:
                self._transferSelectedInk(target,drag['dx'],drag['dy'])
            elif self.edits.change_selection(page,self.selected_indices,drag['dx'],drag['dy']):
                if self.selection_region is not None:self.selection_region.translate(drag['dx'],drag['dy'])
                self.editsChanged.emit()
        elif drag['kind']=='scale':
            factor=drag.get('factor',1.0);anchor=drag.get('anchor')
            if anchor is not None and self.edits.scale_selection(
                    page,self.selected_indices,anchor,factor):
                if self.selection_region is not None:
                    ax,ay=anchor
                    transform=QTransform()
                    transform.translate(ax,ay);transform.scale(factor,factor);transform.translate(-ax,-ay)
                    self.selection_region=transform.map(self.selection_region)
                self.editsChanged.emit()
        else:
            self.selected_indices=self._querySelection(drag,final=True)
            region,clicked=self._selectionRegion(drag)
            self.selection_region=None
            if drag['shape']=='lasso' and not clicked and self.selected_indices:
                base=drag.get('base_region')
                self.selection_region=(base.subtracted(region) if drag.get('subtract') else base.united(region)) if base is not None else region
        self.selectionChanged.emit(len(self.selected_indices));self.update()

    def selectAllInk(self):
        if self.edit_tool!='select' or not self.show_ink:return
        self.finishGesture()
        if hasattr(self,'selected_image'):self.selected_image=None
        area=self.visibleRegion().boundingRect();page=self.pageAt(area.top()+min(100,area.height()/2))
        self.selection_page=page;self.selected_indices={i for i,s in enumerate(self.edits.state(page)[0]) if self.selectionIncludes(s)}
        self.selection_region=None
        self.selectionChanged.emit(len(self.selected_indices));self.update()

    def deleteSelection(self):
        if self.edit_tool!='select' or not self.selected_indices:return
        self.finishGesture()
        changed=self.edits.change_selection(self.selection_page,self.selected_indices,delete=True)
        self.clearSelection()
        if changed:self.editsChanged.emit()

    def selectedPreview(self,page,strokes):
        drag=self.selection_drag
        if not drag:return strokes
        if drag['kind']=='scale':
            if page!=drag['page']:return strokes
            anchor=drag.get('anchor');factor=drag.get('factor',1.0)
            if anchor is None or abs(factor-1.0)<1e-9:return strokes
            return scale_strokes(strokes,self.selected_indices,anchor,factor)
        if drag['kind']!='move':return strokes
        target=drag.get('target',drag['page'])
        if target!=drag['page']:
            if page==drag['page']:return [s for i,s in enumerate(strokes) if i not in self.selected_indices]
            if page==target:
                selected=self.edits.state(drag['page'])[0]
                return list(strokes)+[replace(s,source_id=None,points=tuple((x+drag['dx'],y+drag['dy']) for x,y in s.points))
                                      for i,s in enumerate(selected) if i in self.selected_indices]
            return strokes
        if drag['page']!=page:return strokes
        return [replace(s,points=tuple((x+drag['dx'],y+drag['dy']) for x,y in s.points)) if i in self.selected_indices else s for i,s in enumerate(strokes)]

    def selectionMovingOnPage(self,page):
        drag=self.selection_drag
        return bool(drag and drag['kind']=='move' and page in (drag['page'],drag.get('target',drag['page'])))

    def _transferSelectedInk(self,target,dx,dy):
        page=self.selection_page
        start=len(self.edits.state(target)[0]);count=len(self.selected_indices)
        changed=self.edits.transfer_selection(page,target,self.selected_indices,dx,dy)
        if changed:
            if self.selection_region is not None:self.selection_region.translate(dx,dy)
            self.selection_page=target;self.selected_indices=set(range(start,start+count))
            self.clearCache();self.editsChanged.emit();self.update()
        return changed

    def moveSelectedInkToPage(self,target):
        if self.edit_tool=='image':return False
        if self.selection_page is None or not self.selected_indices or self.selection_page==target:return False
        bounds=self.selectionBounds();nw,nh=self.editPageSize(target)
        if bounds.width()>nw or bounds.height()>nh:raise ValueError('所选笔迹大于目标页，无法完整放入')
        dx=max(-bounds.left(),min(0.,nw-bounds.right()))
        dy=max(-bounds.top(),min(0.,nh-bounds.bottom()))
        changed=self._transferSelectedInk(target,dx,dy)
        self.selectionChanged.emit(len(self.selected_indices))
        return changed

    def paintSelectionOverlay(self,painter,page,target):
        drag=self.selection_drag
        display_page=drag.get('target',self.selection_page) if drag and drag['kind']=='move' else self.selection_page
        if self.edit_tool!='select' or not self.show_ink or display_page!=page:return
        if drag and drag['kind']=='box':
            region,clicked=self._selectionRegion(drag);rect=region.boundingRect()
            if drag['shape']=='lasso' and not clicked:
                points,_,closed=self._lassoPoints(drag)
                polygon=QPolygonF([QPointF(target.left()+x*target.width()/self.editPageSize(page)[0],
                                         target.top()+y*target.height()/self.editPageSize(page)[1]) for x,y in points])
            else:polygon=None
        else:
            polygon=None
            rect=self.selectionBounds()
            if rect is None:return
            rect=rect.adjusted(-3,-3,3,3)
            if drag and drag['kind']=='move':rect.translate(drag['dx'],drag['dy'])
            elif drag and drag['kind']=='scale':
                rect=QRectF(*drag['preview']).adjusted(-3,-3,3,3)
        nw,nh=self.editPageSize(page)
        box=QRectF(target.left()+rect.x()*target.width()/nw,target.top()+rect.y()*target.height()/nh,rect.width()*target.width()/nw,rect.height()*target.height()/nh)
        painter.save();painter.setClipRect(target);painter.setRenderHint(painter.RenderHint.Antialiasing)
        # Highlight the actual ink set, including completion outside a marquee.
        source=drag['page'] if drag and drag['kind']=='move' else page
        strokes=self.edits.state(source)[0]
        indices=drag.get('preview_indices',drag.get('base_indices',set())) if drag and drag['kind']=='box' else self.selected_indices
        if drag and drag['kind'] in ('move','scale'):
            painter.save();painter.translate(target.left(),target.top());painter.scale(target.width()/nw,target.height()/nh)
            if drag['kind']=='move':painter.translate(drag['dx'],drag['dy'])
            else:
                ax,ay=drag['anchor'];f=drag.get('factor',1.0)
                painter.translate(ax,ay);painter.scale(f,f);painter.translate(-ax,-ay)
            pen=QPen(QColor(37,99,235,180),1.2);pen.setCosmetic(True)
            painter.setPen(pen);painter.setBrush(QColor(82,169,255,40))
            for i in sorted(indices):
                if 0<=i<len(strokes):
                    stroke=strokes[i]
                    painter.drawPath(stroke.selection_outline if self.ink_smoothing else stroke.linear_selection_outline)
            painter.restore()
        else:
            from rendering.ne_selection_highlight import paint_highlight
            paint_highlight(self,painter,page,target,strokes,indices)
        painter.setPen(QPen(QColor('#52a9ff'),1.5,Qt.PenStyle.DashLine))
        painter.setBrush(QColor(82,169,255,10))
        if polygon is not None:
            painter.drawPolygon(polygon)
            # Start-point feedback makes deliberate closed loops easy to draw.
            painter.setPen(QPen(QColor('#2563eb' if closed else '#52a9ff'),1.2))
            painter.setBrush(QColor(82,169,255,70) if closed else QColor('white'))
            painter.drawEllipse(polygon[0],5 if closed else 3,5 if closed else 3)
        else:painter.drawRect(box)
        if not drag or drag['kind'] in ('move','scale'):
            if self.selection_region is not None and not (drag and drag['kind']=='scale'):
                transform=QTransform();transform.translate(target.left(),target.top());transform.scale(target.width()/nw,target.height()/nh)
                if drag:transform.translate(drag['dx'],drag['dy'])
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(82,169,255,160),1.2,Qt.PenStyle.DashLine))
                painter.drawPath(transform.map(self.selection_region))
            painter.setPen(QPen(QColor('#52a9ff'),1.5));painter.setBrush(QColor('white'))
            for corner in (box.topLeft(),box.topRight(),box.bottomLeft(),box.bottomRight()):painter.drawEllipse(corner,4,4)
        painter.restore()
