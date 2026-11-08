"""Mouse/tablet editing in page coordinates, independent of zoom and DPI."""
from collections import OrderedDict
from dataclasses import replace
from time import monotonic
from PyQt6.QtCore import Qt,QRectF,QEvent,QByteArray,QBuffer,QIODevice,QSize,QTimer,QPointF
from PyQt6.QtGui import QPainter,QPen,QColor,QImage,QCursor,QPainterPath
from PyQt6.QtWidgets import QApplication
from editing.ne_edits import EditModel,Stroke,HIGHLIGHT_COLORS,svg_strokes,paint_strokes
from editing.ne_selection import SelectionMixin
from editing.ne_brushmetrics import brush_preview_radii
from editing.ne_shapes import KINDS,clamp_point,shape_points
from images.ne_image_resize import resize_corner
from editing.ne_continuous_ink import ContinuousInkMixin

class EditingMixin(ContinuousInkMixin,SelectionMixin):
    def initEditing(self):
        self.original_edit_cache=OrderedDict()
        self.edits=EditModel(self.originalStrokes)
        self.edit_tool='read';self.pen_color='#e53935';self.pen_width=2.0
        self.highlight_color=HIGHLIGHT_COLORS['黄'];self.highlight_width=16.0;self.highlight_opacity=.42
        self.tape_color='#d5ebf5';self.tape_width=24.;self.tape_pattern=0;self.tape_revealed=False
        self.eraser_width=18.;self.eraser_mode='partial';self.hover_point=None
        self.initSelection()
        self._continuous_draw=None
        self.shape_drag=None;self.shape_kind='line'
        self.sampling_precision=.05
        self.smart_interpolation=True
        (self.edits.core if hasattr(self.edits,'core') else self.edits).smart_interpolation=True
        self._drag_scroll_timer=QTimer(self);self._drag_scroll_timer.setInterval(30)
        self._drag_scroll_timer.timeout.connect(self._scrollWhileDragging)
        self._reading_pan=None;self._reading_pan_momentum=(0.,0.)
        self._reading_pan_timer=QTimer(self);self._reading_pan_timer.setInterval(16)
        self._reading_pan_timer.timeout.connect(self._advanceReadingPanMomentum)
        self.setMouseTracking(True);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def originalStrokes(self,page):
        if page not in self.original_edit_cache:
            self.original_edit_cache[page]=svg_strokes(self.ink.get(page,[]),0 if self.local_ink else page,self.note_height)
            while len(self.original_edit_cache)>8:self.original_edit_cache.popitem(last=False)
        self.original_edit_cache.move_to_end(page)
        return self.original_edit_cache[page]

    def editPageSize(self,page):
        if self.page_layout:return self.sizes[page]
        return (self.note_width,self.note_height) if self.archive_path or self.ink else self.sizes[page]

    def setSamplingPrecision(self,value):
        import math
        try:value=float(value)
        except (ValueError,TypeError):value=.05
        if not math.isfinite(value):value=.05
        self.finishGesture()
        self.sampling_precision=max(.01,min(2.,value))
        core=self.edits.core if hasattr(self.edits,'core') else self.edits
        core.sample_spacing=self.sampling_precision

    def setSmartInterpolation(self,enabled):
        self.finishGesture()
        self.smart_interpolation=bool(enabled)
        core=self.edits.core if hasattr(self.edits,'core') else self.edits
        core.smart_interpolation=self.smart_interpolation

    def setEditTool(self,tool):
        self.finishGesture()
        self._cancelReadingPan()
        if tool!='select':self.clearSelection()
        image_selection_cleared=tool!='image' and bool(getattr(self,'selected_image',None))
        if tool!='image' and hasattr(self,'selected_image'):self.selected_image=None
        self.edit_tool=tool
        if image_selection_cleared:self.selectionChanged.emit(len(self.selected_indices))
        self._updatePointerHover(self.hover_point)
        self.update()
        if hasattr(self,'_updateCardButton'):self._updateCardButton()

    def _updatePointerHover(self, fallback=None):
        view = getattr(self.window(), 'editor_view', None)
        if view is not None and view.owner.canvas is self:
            view.sync_cursor()
        else:
            self._moveHover(fallback)

    def _syncBrushCursor(self):
        is_brush = self.edit_tool in ('draw', 'highlight', 'tape', 'erase')
        inside_page = (is_brush and self.show_ink and self.hover_point is not None
                       and self.pointOnPage(self.hover_point) is not None)
        if getattr(self, '_navigation_drag', False):
            shape = Qt.CursorShape.ClosedHandCursor
        elif inside_page:
            shape = Qt.CursorShape.BlankCursor
        elif self._reading_pan is not None:
            shape = Qt.CursorShape.ClosedHandCursor
        elif (self.edit_tool == 'select' and self.hover_point is not None
              and self.hitSelectionResizeHandle(self.hover_point)):
            corner = self.hitSelectionResizeHandle(self.hover_point)[1]
            shape = (Qt.CursorShape.SizeFDiagCursor if corner in ('nw', 'se')
                     else Qt.CursorShape.SizeBDiagCursor)
        elif self.edit_tool == 'read' or (self.hover_point is not None and self.pointOnPage(self.hover_point) is None):
            shape = Qt.CursorShape.OpenHandCursor
        elif self.edit_tool in ('erase', 'select', 'image', 'shape'):
            shape = Qt.CursorShape.CrossCursor
        else:
            shape = Qt.CursorShape.ArrowCursor
        if self.cursor().shape() != shape:
            self.setCursor(shape)

    def _scrollArea(self):
        """The enclosing QScrollArea, or None for a card / viewer-less canvas.

        Every QWidget already owns a built-in ``scroll(dx, dy)`` method, so on
        a question-card window ``getattr(window, 'scroll', None)`` returns a
        builtin_function_or_method rather than None.  Only the main window's
        QScrollArea has viewport()/verticalScrollBar(); callers must ignore
        anything else instead of treating it as a scrollable viewport.
        """
        scroll=getattr(self.window(),'scroll',None)
        if (scroll is None or not hasattr(scroll,'viewport')
                or not hasattr(scroll,'verticalScrollBar')):
            return None
        return scroll

    def _startDragScrollTimer(self):
        """Edge auto-scroll belongs to the scrollable notebook only."""
        if getattr(self,'card_sheet',False) or self._scrollArea() is None:
            return
        self._drag_scroll_timer.start()

    def _readingScrollArea(self):
        scroll=self._scrollArea()
        if scroll is None:
            return None
        vbar=scroll.verticalScrollBar();hbar=scroll.horizontalScrollBar()
        if vbar.maximum()<=vbar.minimum() and hbar.maximum()<=hbar.minimum():
            return None
        return scroll

    def _cancelReadingPan(self):
        if hasattr(self,'_reading_pan_timer'):self._reading_pan_timer.stop()
        self._reading_pan=None;self._reading_pan_momentum=(0.,0.)

    def beginReadingPan(self,position,global_position=None):
        if self.edit_tool!='read':
            return False
        scroll=self._readingScrollArea()
        if scroll is None:
            return False
        self._cancelReadingPan()
        vbar=scroll.verticalScrollBar();hbar=scroll.horizontalScrollBar()
        point=global_position if global_position is not None else position
        self._reading_pan={
            'start':(point.x(),point.y()),
            'vertical':vbar.value(),
            'horizontal':hbar.value(),
            'samples':[(monotonic(),point.x(),point.y())],
        }
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def extendReadingPan(self,position,global_position=None):
        pan=self._reading_pan
        if pan is None:
            return False
        scroll=self._readingScrollArea()
        if scroll is None:
            self._cancelReadingPan();self._syncBrushCursor()
            return False
        point=global_position if global_position is not None else position
        start_x,start_y=pan['start']
        dx=point.x()-start_x;dy=point.y()-start_y
        scroll.horizontalScrollBar().setValue(round(pan['horizontal']-dx))
        scroll.verticalScrollBar().setValue(round(pan['vertical']-dy))
        now=monotonic();samples=pan['samples']
        if (abs(point.x()-samples[-1][1])>.01 or
                abs(point.y()-samples[-1][2])>.01):
            samples.append((now,point.x(),point.y()))
        while len(samples)>4 or (len(samples)>2 and now-samples[0][0]>.12):
            samples.pop(0)
        return True

    def finishReadingPan(self,position=None,global_position=None):
        pan=self._reading_pan
        if pan is None:
            return False
        if position is not None:
            self.extendReadingPan(position,global_position)
        samples=pan['samples']
        self._reading_pan=None
        if len(samples)>=2:
            first=samples[-2];last=samples[-1];elapsed=last[0]-first[0]
            if 0.001<elapsed<.08:
                vx=-(last[1]-first[1])/elapsed
                vy=-(last[2]-first[2])/elapsed
                if vx*vx+vy*vy>=400:
                    self._reading_pan_momentum=(vx,vy)
                    self._reading_pan_timer.start()
        self._syncBrushCursor()
        return True

    def _advanceReadingPanMomentum(self):
        scroll=self._readingScrollArea()
        if scroll is None or self.edit_tool!='read':
            self._cancelReadingPan();self._syncBrushCursor();return
        vx,vy=self._reading_pan_momentum
        if vx*vx+vy*vy<400:
            self._cancelReadingPan();self._syncBrushCursor();return
        vbar=scroll.verticalScrollBar();hbar=scroll.horizontalScrollBar()
        old_v=vbar.value();old_h=hbar.value()
        hbar.setValue(round(old_h+vx/60))
        vbar.setValue(round(old_v+vy/60))
        if hbar.value()==old_h and (old_h<=hbar.minimum() or old_h>=hbar.maximum()):vx=0.
        if vbar.value()==old_v and (old_v<=vbar.minimum() or old_v>=vbar.maximum()):vy=0.
        vx*=.88;vy*=.88
        self._reading_pan_momentum=(vx,vy)

    def brushPreviewRect(self):
        """On-screen footprint of a pen/highlighter nib, in logical widget pixels."""
        if self.edit_tool not in ('draw', 'highlight', 'tape') or not self.show_ink or self.hover_point is None:
            return None
        hit = self.pointOnPage(self.hover_point)
        if hit is None:
            return None
        page, _ = hit
        page_width, page_height = self.editPageSize(page)
        draw_width, draw_height = self.draw_sizes[page]
        width = self.pen_width if self.edit_tool == 'draw' else self.tape_width if self.edit_tool == 'tape' else self.highlight_width
        rx, ry = brush_preview_radii(width, draw_width, draw_height, page_width, page_height)
        point = self.hover_point
        return (page, QRectF(point.x()-rx, point.y()-ry, rx*2, ry*2))

    def paintBrushCursor(self, painter):
        geometry = self.brushPreviewRect()
        if geometry is None:
            return
        page, bounds = geometry
        draw_width, draw_height = self.draw_sizes[page]
        left = (self.width()-draw_width)/2
        top = self.pageTop(page)
        color = QColor(self.pen_color if self.edit_tool == 'draw' else self.tape_color if self.edit_tool == 'tape' else self.highlight_color)
        # A semi-transparent fill helps assess the area without obscuring text.
        color.setAlphaF(.25 if self.edit_tool == 'draw' else self.highlight_opacity)
        outline = QColor(self.pen_color if self.edit_tool == 'draw' else self.tape_color if self.edit_tool == 'tape' else self.highlight_color)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(QRectF(left, top, draw_width, draw_height))
        # At subpixel widths the actual circular footprint may be hard to spot.
        # Draw a separate, nonfilled locator ring; the central painted nib remains
        # at exactly the chosen page-unit width (independent of the locator size).
        if bounds.width() < 5 or bounds.height() < 5:
            center = bounds.center()
            ring = QRectF(center.x()-4, center.y()-4, 8, 8)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255,255,255,220), 2))
            painter.drawEllipse(ring)
            painter.setPen(QPen(outline, 1))
            painter.drawEllipse(ring)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(bounds)
            painter.restore()
            return
        painter.setBrush(color)
        painter.setPen(QPen(QColor(255,255,255,220),2))
        painter.drawEllipse(bounds)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(outline,1))
        painter.drawEllipse(bounds)
        painter.restore()

    def setPenColor(self,color):self.pen_color=color

    def setTapeRevealed(self, revealed):
        self.finishGesture()
        self.tape_revealed=bool(revealed)
        self.content_cache.clear()
        self.update()

    def pointOnPage(self,point,page=None):
        page=self.pageAt(point.y()) if page is None else page
        w,h=self.draw_sizes[page];left=(self.width()-w)/2;top=self.pageTop(page)
        if not QRectF(left,top,w,h).contains(point):return None
        nw,nh=self.editPageSize(page)
        return page,((point.x()-left)*nw/w,(point.y()-top)*nh/h)

    def shapePagePoint(self,page,position):
        """Page coordinates for a pointer that may have left the paper."""
        w,h=self.draw_sizes[page];left=(self.width()-w)/2;top=self.pageTop(page)
        nw,nh=self.editPageSize(page)
        x=(position.x()-left)*nw/w;y=(position.y()-top)*nh/h
        return clamp_point((x,y),(nw,nh))

    def shapePointsForDrag(self,drag):
        return shape_points(drag['kind'],drag['start'],drag['current'],
                            shift=drag['shift'],page_size=self.editPageSize(drag['page']))

    def beginShape(self,page,point,modifiers=Qt.KeyboardModifier.NoModifier):
        if self._continuous_draw is not None:self.finishGesture()
        self.edits.finish()
        kind=getattr(self,'shape_kind','line')
        if kind not in KINDS:kind='line'
        self.shape_drag=dict(page=page,start=tuple(point),current=tuple(point),
                             kind=kind,shift=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
                             color=self.pen_color,width=self.pen_width)
        self.setFocus();self.update();return True

    def extendShape(self,position):
        drag=self.shape_drag
        if drag is None:return
        drag['current']=self.shapePagePoint(drag['page'],position)
        drag['shift']=bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.update()

    def finishShape(self,cancel=False):
        drag=self.shape_drag
        if drag is None:return False
        self.shape_drag=None
        if not cancel:
            try:
                points=self.shapePointsForDrag(drag)
                if self.edits.add_shape(drag['page'],points,drag['color'],drag['width']):
                    self.editsChanged.emit()
            except ValueError:
                pass  # A tap or a degenerate drag simply draws nothing.
        self.update();return True

    def paintShapePreview(self,painter):
        drag=getattr(self,'shape_drag',None)
        if drag is None or not self.show_ink:return
        try:points=self.shapePointsForDrag(drag)
        except ValueError:return
        page=drag['page'];w,h=self.draw_sizes[page]
        left=(self.width()-w)/2;top=self.pageTop(page)
        nw,nh=self.editPageSize(page)
        painter.save()
        painter.setClipRect(QRectF(left,top,w,h))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        painter.translate(left,top);painter.scale(w/nw,h/nh)
        pen=QPen(QColor(drag['color']),float(drag['width']),
                 Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush)
        path=QPainterPath(QPointF(*points[0]))
        for point in points[1:]:path.lineTo(*point)
        painter.drawPath(path)
        painter.restore()

    def beginGesture(self,position,modifiers=Qt.KeyboardModifier.NoModifier):
        if self._continuous_draw is not None:self.finishGesture()
        # The "make a card" action is painted into the canvas (see
        # paintCardButton) instead of being a child widget, because the GPU
        # viewport draws on top of all canvas children.  Hit-test it here so
        # both mouse and tablet presses activate it.
        if self.edit_tool=='select' and self.hitCardButton(position):
            self._createQuestionCardFromSelection()
            self.update();return True
        hit=self.pointOnPage(position)
        if self.edit_tool in ('read','image') or not self.show_ink:return False
        if self.edit_tool=='select':return self.beginSelection(position,modifiers)
        if hit is None:return False
        page,point=hit
        if self.edit_tool=='shape':return self.beginShape(page,point,modifiers)
        kind='erase' if self.edit_tool=='erase' else 'draw'
        if getattr(self,'seamless_pages',False) and self.edit_tool in ('draw','highlight','tape','erase'):
            self._beginContinuousDraw(page,point,position)
            self.setFocus();self.update();return True
        capture_smooth=bool(getattr(self,'ink_smoothing',True))
        if self.edit_tool=='tape':
            self.edits.begin(page,point,'draw',self.tape_width,self.tape_color,
                             'partial',1.,capture_smooth,'tape',tape_pattern=self.tape_pattern)
        elif self.edit_tool=='highlight':
            self.edits.begin(page,point,'draw',self.highlight_width,
                             self.highlight_color,'partial',self.highlight_opacity,
                             capture_smooth,'highlighter')
        else:
            self.edits.begin(page,point,kind,
                             self.eraser_width if kind=='erase' else self.pen_width,
                             self.pen_color,self.eraser_mode,1.0,
                             capture_smooth if kind=='draw' else False,'pen',capture_smooth)
        self.setFocus();self.update();return True

    def extendGesture(self,position):
        if getattr(self,'shape_drag',None) is not None:self.extendShape(position);return
        if self.selection_drag:self.extendSelection(position);return
        if self._continuous_draw is not None:
            self._extendContinuousDraw(position);return
        if not self.edits.pending:return
        hit=self.pointOnPage(position,self.edits.pending['page'])
        if hit is None:
            if self.edits.pending['kind']=='erase':
                # A fast final sample may land outside the paper. Erase the
                # crossed strip through the edge before closing this gesture.
                page=self.edits.pending['page'];w,h=self.draw_sizes[page]
                nw,nh=self.editPageSize(page)
                end=((position.x()-(self.width()-w)/2)*nw/w,
                     (position.y()-self.pageTop(page))*nh/h)
                start=self.edits.pending['points'][-1];fraction=1.
                for lo,hi,p,q in ((0.,nw,start[0],end[0]),(0.,nh,start[1],end[1])):
                    if q<lo:fraction=min(fraction,(lo-p)/(q-p))
                    elif q>hi:fraction=min(fraction,(hi-p)/(q-p))
                self.edits.extend(tuple(p+(q-p)*fraction for p,q in zip(start,end)))
            self.finishGesture();return
        op=self.edits.pending
        from editing.ne_inkcurve import tail_bounds
        previous_bounds=tail_bounds(op['points'],op['width'],op.get('capture_smoothing',False),
                                    op.get('smart_interpolation',False))
        self.edits.extend(hit[1],force=getattr(self,'_force_endpoint',False),timestamp=getattr(self,'_input_time',None))
        if op['kind']=='draw':
            page=op['page'];nw,nh=self.editPageSize(page);dw,dh=self.draw_sizes[page]
            bounds=previous_bounds.united(tail_bounds(
                op['points'],op['width'],op.get('capture_smoothing',False),
                op.get('smart_interpolation',False)))
            rect=QRectF((self.width()-dw)/2+bounds.left()*dw/nw,
                        self.pageTop(page)+bounds.top()*dh/nh,
                        bounds.width()*dw/nw,bounds.height()*dh/nh)
            self.update(rect.adjusted(-3,-3,3,3).toAlignedRect())
        else:self.update()

    def _cursorDamage(self):
        if self.hover_point is None or self.edit_tool not in ('draw','highlight','tape','erase') or not self.show_ink:return None
        hit=self.pointOnPage(self.hover_point)
        if hit is None:return None
        page=hit[0];nw,nh=self.editPageSize(page);dw,dh=self.draw_sizes[page]
        width={'draw':self.pen_width,'highlight':self.highlight_width,'tape':self.tape_width,'erase':self.eraser_width}[self.edit_tool]
        rx=max(6.,width*dw/nw/2+3);ry=max(6.,width*dh/nh/2+3)
        return QRectF(self.hover_point.x()-rx,self.hover_point.y()-ry,rx*2,ry*2).toAlignedRect()

    def _moveHover(self,position):
        if getattr(self, '_navigation_drag', False):position=None
        if self.hover_point == position:
            self._syncBrushCursor();return
        old=self._cursorDamage();self.hover_point=position;self._syncBrushCursor();new=self._cursorDamage()
        if old is not None:self.update(old)
        if new is not None:self.update(new)

    def finishGesture(self):
        if hasattr(self,'_drag_scroll_timer'):self._drag_scroll_timer.stop()
        if getattr(self,'_image_history_before',None) is not None:self.finishImageGesture()
        if hasattr(self,'selection_drag') and self.selection_drag:self.finishSelection()
        if getattr(self,'shape_drag',None) is not None:self.finishShape()
        continuous=self._finishContinuousDraw()
        if hasattr(self,'edits') and (self.edits.finish() or continuous):self.editsChanged.emit()
        self.update()

    def undoEdit(self):
        self.finishGesture()
        self.clearSelection();self.edits.undo();self.editsChanged.emit();self.update()

    def redoEdit(self):
        self.finishGesture()
        self.clearSelection();self.edits.redo();self.editsChanged.emit();self.update()

    def mousePressEvent(self,event):
        stamp=event.timestamp() if hasattr(event,'timestamp') else 0
        self._input_time=stamp/1000 if stamp else None
        # Ctrl+click a marked question to reopen its card. Intercept before
        # the reading-tool pan and before any ink gesture, so it never draws,
        # selects or erases by accident. Card canvases have no card list and
        # therefore never trigger this.
        if (event.button()==Qt.MouseButton.LeftButton
                and hasattr(self,'tryOpenCardAt')
                and self.tryOpenCardAt(event.position(),event.modifiers())):
            event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and self.edit_tool=='read':
            if self.beginReadingPan(event.position(),event.globalPosition()):
                event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and self.edit_tool=='image' and hasattr(self,'hitImage'):
            handle=self.hitImageResizeHandle(event.position())
            if handle:
                page,identifier,corner=handle
                item=self.editableImageItem(page,identifier)
                self.beginImageGesture()
                self._image_drag=None
                self._image_resize_drag=(page,identifier,corner,
                                          tuple(item[k] for k in ('x','y','width','height')))
                event.accept();return
            hit=self.hitImage(event.position())
            if hit:
                page,identifier,point=hit
                self.clearSelection()
                self.selected_image=(page,identifier)
                item=self.editableImageItem(page,identifier)
                if item and not item['locked']:self.beginImageGesture()
                self._image_drag=(page,identifier,point,item['x'],item['y']) if item and not item['locked'] else None
                if self._image_drag:self._startDragScrollTimer()
                if identifier.startswith('native:'):
                    window=self.window()
                    if hasattr(window,'statusBar'):
                        if item and item['locked']:
                            if item.get('lock_editable'):
                                notice='官方图片已锁定：点击上方“解除锁定”，然后即可拖动或等比例缩放'
                            else:
                                notice='该图片结构不支持安全锁定切换'
                        elif item and item.get('lock_source')=='unmarked':
                            notice='官方图片无锁定记录：可以移动、缩放，也可以点击“锁定图片”'
                        else:
                            notice='官方图片未锁定：可以拖动移动，四角等比例缩放'
                        window.statusBar().showMessage(notice,7000)
                self.editsChanged.emit();self.update();event.accept();return
            self.selected_image=None
            if self.edit_tool=='image':
                self.clearSelection()
                self.editsChanged.emit();self.update();event.accept();return
        self.hover_point=event.position()
        self._syncBrushCursor()
        if event.button()==Qt.MouseButton.LeftButton and self.beginGesture(event.position(),event.modifiers()):
            if self.selection_drag and self.selection_drag['kind']=='move':self._startDragScrollTimer()
            event.accept()
        else:super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        stamp=event.timestamp() if hasattr(event,'timestamp') else 0
        self._input_time=stamp/1000 if stamp else None
        if self._reading_pan is not None:
            self.extendReadingPan(event.position(),event.globalPosition());event.accept();return
        if getattr(self,'_image_resize_drag',None):
            page,identifier,corner,original=self._image_resize_drag
            key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
            try:
                point=self.imagePagePoint(page,event.position())
                box=resize_corner(original,corner,point,self.editPageSize(page))
                self.resizeEditableImage(page,identifier,box)
            except ValueError:
                pass  # Bad geometry must never abort Qt's event handler.
            event.accept();return
        if getattr(self,'_image_drag',None):
            self._moveImageDrag(event.position())
            event.accept();return
        hover=bool(self.edit_tool=='select' and self.hitCardButton(event.position()))
        if hover!=getattr(self,'_card_button_hover',False):
            self._card_button_hover=hover;self.update()
        self._updatePointerHover(event.position());self.extendGesture(event.position())
        if (self.edits.pending or self.selection_drag or self._continuous_draw is not None
                or getattr(self,'shape_drag',None) is not None):event.accept()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        stamp=event.timestamp() if hasattr(event,'timestamp') else 0
        self._input_time=stamp/1000 if stamp else None
        if event.button()==Qt.MouseButton.LeftButton:self._drag_scroll_timer.stop()
        if event.button()==Qt.MouseButton.LeftButton and self._reading_pan is not None:
            self.finishReadingPan(event.position(),event.globalPosition());event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and getattr(self,'_image_resize_drag',None):
            self.finishImageGesture()
            self.editsChanged.emit();self.update();event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and getattr(self,'_image_drag',None):
            self._moveImageDrag(event.position())
            self.finishImageGesture()
            self.editsChanged.emit();self.update();event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and (self.edits.pending or self.selection_drag or self._continuous_draw is not None or getattr(self,'shape_drag',None) is not None):
            self._force_endpoint=True
            try:self.extendGesture(event.position());self.finishGesture();event.accept()
            finally:self._force_endpoint=False
        else:super().mouseReleaseEvent(event)

    def _moveImageDrag(self,position):
        page,identifier,origin,x0,y0=self._image_drag
        hit=self.pointOnPage(position)
        if hit is None:return
        target,point=hit
        current=self.selected_image[0] if self.selected_image else page
        try:
            self.moveEditableImageToPage(current,target,identifier,point[0]-origin[0]+x0,point[1]-origin[1]+y0)
        except ValueError as exc:
            window=self.window()
            if hasattr(window,'statusBar'):window.statusBar().showMessage(str(exc),5000)

    def _scrollWhileDragging(self):
        active=getattr(self,'_image_drag',None) or self._continuous_draw is not None or (self.selection_drag and self.selection_drag['kind']=='move')
        if not active:self._drag_scroll_timer.stop();return
        scroll=self._scrollArea()
        if scroll is None:
            # A card sheet has no scroll bars; no edge scrolling is needed.
            self._drag_scroll_timer.stop();return
        position=QCursor.pos();viewport=scroll.viewport();point=viewport.mapFromGlobal(position)
        if not -32<=point.x()<=viewport.width()+32:return
        dy=(-20 if point.y()<36 else 20 if point.y()>viewport.height()-36 else 0)
        if not dy:return
        bar=scroll.verticalScrollBar();before=bar.value();bar.setValue(before+dy)
        if before==bar.value():return
        local=QPointF(self.mapFromGlobal(position))
        if getattr(self,'_image_drag',None):self._moveImageDrag(local)
        elif self._continuous_draw is not None:self.extendGesture(local)
        else:self.extendSelection(local)

    def tabletEvent(self,event):
        stamp=event.timestamp() if hasattr(event,'timestamp') else 0
        self._input_time=stamp/1000 if stamp else None
        if self.edit_tool=='read':
            global_position=(event.globalPosition()
                             if hasattr(event,'globalPosition') else event.position())
            if event.type()==QEvent.Type.TabletPress:
                if not self.beginReadingPan(event.position(),global_position):event.ignore();return
            elif event.type()==QEvent.Type.TabletMove:
                if not self.extendReadingPan(event.position(),global_position):event.ignore();return
            elif event.type()==QEvent.Type.TabletRelease:
                self.finishReadingPan(event.position(),global_position)
            event.accept();return
        self._moveHover(event.position())
        if event.type()==QEvent.Type.TabletPress:
            if (hasattr(self,'tryOpenCardAt')
                    and self.tryOpenCardAt(event.position(),event.modifiers())):
                event.accept();return
            if not self.beginGesture(event.position(),event.modifiers()):event.ignore();return
        elif event.type()==QEvent.Type.TabletMove:self.extendGesture(event.position())
        elif event.type()==QEvent.Type.TabletRelease:
            self._force_endpoint=True
            try:self.extendGesture(event.position());self.finishGesture()
            finally:self._force_endpoint=False
        event.accept()

    def enterEvent(self,event):
        self._updatePointerHover(event.position());super().enterEvent(event)

    def leaveEvent(self,event):
        self._updatePointerHover();super().leaveEvent(event)

    def _shortcut_matches(self,name,event):
        manager=getattr(self.window(),'shortcut_manager',None)
        return manager.matches(name,event) if manager else False

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self._drag_scroll_timer.stop()
            self._cancelReadingPan();self._syncBrushCursor()
            self._finishContinuousDraw(cancel=True)
            self.edits.cancel()
            if getattr(self,'shape_drag',None) is not None:self.finishShape(cancel=True)
            if hasattr(self,'finishImageGesture'):self.finishImageGesture(cancel=True)
            if hasattr(self,'selected_image'):self.selected_image=None
            self.clearSelection();self.update();event.accept()
        elif (event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace)
              and self.edit_tool=='image' and getattr(self,'selected_image',None)):
            self.updateImageAction('delete');event.accept()
        elif self.edit_tool=='select' and (self._shortcut_matches('ink_delete',event) or event.key()==Qt.Key.Key_Backspace):
            self.deleteSelection();event.accept()
        elif self.edit_tool=='select' and self._shortcut_matches('ink_select_all',event):
            self.selectAllInk();event.accept()
        else:super().keyPressEvent(event)

    def paintEditableInk(self,painter,page,target):
        """Draw exactly what belongs on this paper, including cross-page ink.

        The source NTB can own a continuous stroke on page N even when its tip
        reaches page N+1. This clipping/continuation path is shared by the
        on-screen viewer and PDF export to keep both representations aligned.
        """
        active=self.edits.pending and self.edits.pending['page']==page
        moving=self.selectionMovingOnPage(page)
        edited=page in self.edits.edited_pages()
        painter.save()
        # Native NTB paths are rendered by QSvgRenderer. The viewport painter
        # may have inherited default (aliased) hints, so enable AA here too.
        # This also covers thumbnails, PDF export and edited cross-page ink.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(target)
        try:
            if not edited and not active and not moving:
                renderer=self._ink_renderer(page)
                if renderer:renderer.render(painter,target)
            else:
                # The current page has editable pen strokes, but incoming ink
                # from unedited source pages must not disappear as a side effect.
                incoming=self._ink_renderer(page,spill_only=True)
                if incoming:incoming.render(painter,target)
                nw,nh=self.editPageSize(page)
                painter.save()
                painter.translate(target.left(),target.top())
                painter.scale(target.width()/nw,target.height()/nh)
                paint_strokes(painter,self.selectedPreview(page,self.edits.state(page)[0]),smooth=getattr(self,'ink_smoothing',True))
                if active and self.edits.pending['kind']=='draw':
                    op=self.edits.pending
                    paint_strokes(painter,[Stroke(tuple(op['points']),op['color'],op['width'],
                        op.get('opacity',1.0),tool=op.get('tool','pen'),tape_pattern=op.get('tape_pattern',3),
                        geometry_is_smoothed=bool(op.get('capture_smoothing')),
                        smart_interpolation=bool(op.get('smart_interpolation')))],
                        smooth=getattr(self,'ink_smoothing',True))
                painter.restore()

            # A neighboring original stroke that was edited must use the edited
            # geometry rather than its stale SVG data. Shift its local page
            # coordinates to the target paper's local coordinate system.
            source=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
            core=self.edits.core if hasattr(self.edits,'core') else self.edits
            edited_sources=core.edited_pages()
            allowed=set(self.edits.order) if hasattr(self.edits,'order') else set(range(self.page_count))
            owners={owner for owner,_ in self.spill_ink.get(source,())
                    if owner in allowed and owner in edited_sources}
            if owners:
                nw,nh=self.editPageSize(page)
                painter.save()
                painter.translate(target.left(),target.top())
                painter.scale(target.width()/nw,target.height()/nh)
                for owner in owners:
                    delta=(owner-source)*self.note_height
                    strokes=core.state(owner)[0]
                    incoming=[]
                    for stroke in strokes:
                        if stroke.bounds[3]+delta < 0 or stroke.bounds[1]+delta > nh:
                            continue
                        incoming.append(replace(stroke,points=tuple((x,y+delta) for x,y in stroke.points)))
                    if incoming:paint_strokes(painter,incoming,smooth=getattr(self,'ink_smoothing',True))
                painter.restore()
        finally:
            painter.restore()

    def paintEraserCursor(self,painter):
        if self.edit_tool!='erase' or not self.show_ink or self.hover_point is None:return
        hit=self.pointOnPage(self.hover_point)
        if not hit:return
        page,_=hit;nw,nh=self.editPageSize(page);w,h=self.draw_sizes[page]
        rx=self.eraser_width*w/nw/2;ry=self.eraser_width*h/nh/2
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('white'),3));painter.drawEllipse(self.hover_point,rx,ry)
        painter.setPen(QPen(QColor('#333333'),1));painter.drawEllipse(self.hover_point,rx,ry)
        painter.restore()

    def thumbnailBytes(self):
        w,h=self.sizes[0];size=QSize(600,max(1,round(600*h/w)))
        image=self._render_page(0,size,background_only=True).copy();painter=QPainter(image)
        from rendering.ne_mixed_layers import paint_content
        paint_content(self,painter,0,QRectF(image.rect()))
        painter.end()
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer,'PNG');buffer.close();return bytes(data)
