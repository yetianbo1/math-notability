"""Rasterize stable layers once per image gesture, preserving native z order."""
import math
from PyQt6.QtCore import QRectF,QSize
from PyQt6.QtGui import QImage,QPainter
from rendering.ne_mixed_layers import scene,paint_native_scene


class ImageDragLayers:
    def __init__(self):self.clear()

    def clear(self):
        self.key=None;self.pair=None;self.moving=None;self.bytes=0

    @staticmethod
    def paint_records(c,painter,page,target,records):
        # Keep adjacent SVG paths batched, just as in the ordinary renderer.
        group=[];previous=None
        def flush(kind):
            if not group:return
            if kind=='native':paint_native_scene(c,painter,page,target,entries=group,reveal_tape=c.tape_revealed)
            else:c.paintInsertedImages(painter,page,target,items=group)
            group.clear()
        for kind,record in records:
            if kind!=previous:flush(previous)
            group.append(record);previous=kind
        flush(previous)

    def paint(self,c,painter,page,target,dpr,budget):
        selection=c.selected_image
        if not selection or selection[0]!=page:return False
        history=getattr(c,'_image_history_before',None)
        if history is None:return False
        width,height=max(1,round(target.width()*dpr)),max(1,round(target.height()*dpr))
        # Two transparent layers share the existing screen cache budget.
        pixels=min(c.MAX_RENDER_PIXELS,max(1,budget//8))
        factor=min(1.,math.sqrt(pixels/(width*height)))
        size=QSize(max(1,round(width*factor)),max(1,round(height*factor)))
        core=c.edits.core if hasattr(c.edits,'core') else c.edits
        key=(id(history),page,selection[1],size.width(),size.height(),
             c.show_ink,c.ink_smoothing,core.token,core.cursor)
        if self.key!=key:
            native=scene(c,page,include_ink=c.show_ink,static=True)
            records=([('inserted',r) for r in c._image_records(page,False)]+
                     [('native',r) for r in native]+
                     [('inserted',r) for r in c._image_records(page,True)])
            def selected(entry):
                kind,r=entry
                return ('native:'+str(r[1])==selection[1] and r[2]=='image'
                        if kind=='native' else r['id']==selection[1])
            index=next((i for i,r in enumerate(records) if selected(r)),None)
            if index is None:return False
            pair=[];rect=QRectF(0,0,size.width(),size.height())
            for subset in (records[:index],records[index+1:]):
                image=QImage(size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
                p=QPainter(image)
                try:self.paint_records(c,p,page,rect,subset)
                finally:p.end()
                pair.append(image)
            self.key=key;self.pair=pair;self.moving=[records[index]]
            self.bytes=sum(image.sizeInBytes() for image in pair)
        painter.drawImage(target,self.pair[0])
        self.paint_records(c,painter,page,target,self.moving)
        painter.drawImage(target,self.pair[1])
        return True
