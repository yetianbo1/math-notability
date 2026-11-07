"""Bounded screen-only stable layers. Vector export keeps its original path."""
from collections import OrderedDict
import math
from PyQt6.QtCore import QRectF,QSize
from PyQt6.QtGui import QImage,QPainter
from rendering.ne_livestroke import LiveStroke
from rendering.ne_mixed_layers import scene,paint_native_scene,paint_content,NEW_INK_LAYER
from images.ne_imagedrag import ImageDragLayers
from editing.ne_edits import Stroke


class ContentCache:
    BUDGET=96*1024*1024

    def __init__(self):
        self.entries=OrderedDict();self.bytes=0;self.live=LiveStroke()
        self.hits=0;self.misses=0
        self.image_drag=ImageDragLayers()
        self.incremental_hits=0
        self.epoch=0

    def key_for(self,c,page,target,dpr=1.):
        core=c.edits.core if hasattr(c.edits,'core') else c.edits
        width,height=max(1,round(target.width()*dpr)),max(1,round(target.height()*dpr))
        factor=min(1.,math.sqrt(c.MAX_RENDER_PIXELS/(width*height)))
        return (page,max(1,round(width*factor)),max(1,round(height*factor)),core.token,core.cursor,
                c.show_ink,c.ink_smoothing,(NEW_INK_LAYER+1,0),c.tape_revealed)

    def store(self,key,pair):
        if key in self.entries:return
        cost=sum(i.sizeInBytes() for i in pair)
        if cost+self.image_drag.bytes>self.BUDGET:return
        self.entries[key]=pair;self.bytes+=cost
        while self.bytes+self.image_drag.bytes>self.BUDGET:
            _,old=self.entries.popitem(last=False);self.bytes-=sum(i.sizeInBytes() for i in old)

    def _append_draws(self,c,key,core):
        """Composite new ink over stable layers, including completed gestures."""
        for old_key in reversed(self.entries):
            if old_key[:3]!=key[:3] or old_key[5:]!=key[5:]:continue
            cursor=old_key[4]
            if cursor>core.cursor or (core.history[cursor-1][0] if cursor else None)!=old_key[3]:continue
            added=core.history[cursor:core.cursor]
            if not added:continue
            source=c.edits.page_key(key[0]) if hasattr(c.edits,'page_key') else key[0]
            owners={source}|{owner for owner,_ in c.spill_ink.get(source,())}
            added_draws=[];valid=True
            for _,op in added:
                affected=core.document_index.owners(op)
                # Keep cached pages when an erasure/move only affects another
                # stable owner. Unknown/global operations remain conservative.
                if affected is not None and not owners.intersection(affected):continue
                if op['kind']=='draw':added_draws.append(op)
                elif op['kind']=='compound' and op.get('_append_draws'):
                    added_draws.extend(op['_append_draws'])
                else:
                    valid=False;break
            if not valid:continue
            source=c.edits.page_key(key[0]) if hasattr(c.edits,'page_key') else key[0]
            # New pen fragments are confined to their native page. Existing
            # incoming ink remains in the unchanged cached background.
            records=[op for op in added_draws if op['page']==source]
            pair=list(self.entries[old_key])
            if records and c.show_ink:
                pair[0]=pair[0].copy();painter=QPainter(pair[0])
                try:
                    entries=[(NEW_INK_LAYER,i,'stroke',Stroke(tuple(op['points']),op['color'],op['width'],
                             op.get('opacity',1.),tool=op.get('tool','pen'),
                             geometry_is_smoothed=bool(op.get('capture_smoothing')),outline=op.get('outline'),
                             tape_pattern=op.get('tape_pattern',3),smart_interpolation=bool(op.get('smart_interpolation'))))
                             for i,op in enumerate(records)]
                    paint_native_scene(c,painter,key[0],QRectF(pair[0].rect()),entries=entries,reveal_tape=c.tape_revealed)
                finally:painter.end()
            self.incremental_hits+=1
            return pair
        return None

    def clear(self):
        self.epoch+=1
        self.entries.clear();self.bytes=0
        self.image_drag.clear()

    def invalidate_pages(self,pages):
        self.epoch+=1
        for key in list(self.entries):
            if key[0] in pages:
                old=self.entries.pop(key);self.bytes-=sum(i.sizeInBytes() for i in old)

    def paint(self,c,painter,page,target,dpr=1.):
        core=c.edits.core if hasattr(c.edits,'core') else c.edits
        source=c.edits.page_key(page) if hasattr(c.edits,'page_key') else page
        op=c.edits.pending
        active=op and op['page']==page
        image_gesture=getattr(c,'_image_drag',None) or getattr(c,'_image_resize_drag',None)
        if image_gesture and self.image_drag.paint(c,painter,page,target,dpr,self.BUDGET):
            while self.entries and self.bytes+self.image_drag.bytes>self.BUDGET:
                _,old=self.entries.popitem(last=False);self.bytes-=sum(i.sizeInBytes() for i in old)
            return
        # Erasure and object moves have mutable stable contents. They retain
        # the shared vector path rather than displaying an obsolete bitmap.
        if (active and op['kind']!='draw') or source in core.erasing_pages or c.selectionMovingOnPage(page):
            paint_content(c,painter,page,target,include_ink=c.show_ink,reveal_tape=c.tape_revealed);return
        # Pens and highlighters share the same top insertion boundary. A
        # behind-content highlighter cache would cover its live path with images.
        boundary=(NEW_INK_LAYER+1,0)
        key=self.key_for(c,page,target,dpr)
        size=QSize(key[1],key[2])
        pair=self.entries.get(key)
        if pair is None:
            self.misses+=1
            pair=self._append_draws(c,key,core)
        if pair is None:
            entries=scene(c,page,include_ink=c.show_ink,static=True)
            back=[e for e in entries if (e[0],e[1])<=boundary]
            front=[e for e in entries if (e[0],e[1])>boundary]
            target_image=QRectF(0,0,size.width(),size.height())
            pair=[]
            for before,records in ((True,back),(False,front)):
                image=QImage(size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
                p=QPainter(image)
                try:
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
                    if before:c.paintInsertedImages(p,page,target_image,front=False)
                    paint_native_scene(c,p,page,target_image,entries=records,reveal_tape=c.tape_revealed)
                    if not before:c.paintInsertedImages(p,page,target_image,front=True)
                finally:p.end()
                pair.append(image)
        # Also warm an existing reading cache when switching to the pen. Never
        # do speculative parsing in the middle of a pointer gesture.
        if (c.seamless_pages and c.edit_tool in ('draw','highlight','tape','erase') and not core.pending
                and getattr(c,'_continuous_draw',None) is None and getattr(c,'page_warmup',None) is None):
            c.edits.state(page)
        if key not in self.entries:
            self.store(key,pair)
        else:self.hits+=1
        if key in self.entries:self.entries.move_to_end(key)
        painter.drawImage(target,pair[0])
        if active and c.show_ink:
            nw,nh=c.editPageSize(page)
            painter.save()
            try:
                painter.setClipRect(target)
                painter.translate(target.x(),target.y());painter.scale(target.width()/nw,target.height()/nh)
                # PageEdits.pending is a display-page wrapper created on each
                # access; the core operation retains identity across samples.
                self.live.paint(painter,core.pending,c.ink_smoothing)
            finally:painter.restore()
        painter.drawImage(target,pair[1])
