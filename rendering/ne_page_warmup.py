"""Warm nearby pages after the visible page paints; workers own immutable input."""
from dataclasses import dataclass,replace
from threading import Condition
from PyQt6.QtCore import QObject,QThread,QTimer,QRectF,QSize,pyqtSignal
from PyQt6.QtGui import QImage,QPainter
from lxml import etree
from editing.ne_edits import svg_strokes
from rendering.ne_mixed_layers import scene,paint_native_scene
from infrastructure.ne_diagnostics import exception as diagnostic_exception


@dataclass
class FrozenPage:
    dimensions: tuple
    archive_path: str | None
    note_height: float
    local_ink: bool
    ink_smoothing: bool
    tape_revealed: bool
    overlays: tuple
    assets: dict

    def editPageSize(self,page):return self.dimensions

    def paintInsertedImages(self,painter,page,target,*,front=False):
        records=self.overlays[1 if front else 0]
        if not records:return
        painter.save()
        try:
            painter.setClipRect(target);painter.translate(target.left(),target.top())
            painter.scale(target.width()/self.dimensions[0],target.height()/self.dimensions[1])
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
            for item in records:
                image=QImage.fromData(self.assets[item['asset']],'PNG')
                if image.isNull():raise ValueError('插图素材无法解码')
                painter.drawImage(QRectF(item['x'],item['y'],item['width'],item['height']),image)
        finally:painter.restore()


def render_pair(job):
    key=job['key'][1];context=job['context'];page=key[0]
    target=QRectF(0,0,key[1],key[2]);boundary=key[7]
    frozen=[]
    for z,ident,kind,value in job['entries']:
        if kind=='svg-bytes':
            value=etree.fromstring(value);ident=int(value.get('data-event-id',0));kind='svg'
            z=job.get('layers',{}).get(ident,int(value.get('data-z-index',0)))
        frozen.append((z,ident,kind,value))
    frozen.sort(key=lambda e:(e[0],e[1]))
    pair=[]
    for before in (True,False):
        entries=[e for e in frozen if ((e[0],e[1])<=boundary)==before]
        tile=job.get('tile')
        size=QSize(tile[2],tile[3]) if tile else QSize(key[1],key[2])
        image=QImage(size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
        painter=QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
            if tile:painter.translate(-tile[0],-tile[1])
            if before:context.paintInsertedImages(painter,page,target,front=False)
            paint_native_scene(context,painter,page,target,entries=entries,reveal_tape=context.tape_revealed,source_page=job['source'])
            if not before:context.paintInsertedImages(painter,page,target,front=True)
        finally:painter.end()
        pair.append(image)
    return pair


class PageWorker(QThread):
    ready=pyqtSignal(object,object,object,str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.condition=Condition();self.jobs=[];self.current=None;self.stopping=False

    def replace_jobs(self,jobs):
        with self.condition:
            self.jobs=[job for job in jobs if job['key']!=self.current][:4]
            self.condition.notify()

    def run(self):
        while True:
            with self.condition:
                while not self.stopping and not self.jobs:self.condition.wait()
                if self.stopping:return
                job=self.jobs.pop(0);self.current=job['key']
            pair=strokes=None;error=''
            try:
                # Current-page editable geometry is first; no QWidget is touched.
                if job['raw'] is not None:
                    strokes=svg_strokes(job['raw'],job['offset'],job['context'].note_height)
                if job['entries'] is not None:pair=render_pair(job)
            except Exception as exc:error=str(exc)
            self.ready.emit(job,pair,strokes,error)
            with self.condition:self.current=None

    def stop(self):
        with self.condition:
            self.stopping=True;self.jobs=[];self.condition.notify()
        self.wait()


class PageWarmup(QObject):
    """One worker, four nearby pages, shared bounded screen/edit caches."""
    def __init__(self,canvas):
        super().__init__(canvas)
        self.canvas=canvas;self.closed=False;self.view=None;self.signature=None
        self.errors=set();self.completed=[]
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.schedule)
        self.worker=PageWorker(self);self.worker.ready.connect(self.received)

    def visible(self,first,last,dpr):
        if self.closed:return
        c=self.canvas;core=c.edits.core if hasattr(c.edits,'core') else c.edits
        signature=(first,last,dpr,c.zoom,core.token,core.cursor,c.content_cache.epoch,c.show_ink,c.ink_smoothing,c.tape_revealed)
        self.view=(first,last,dpr)
        if signature!=self.signature:
            self.signature=signature;self.timer.start(0)

    def schedule(self):
        try:self._schedule()
        except Exception as exc:
            # Background preparation is optional; retain the synchronous path
            # for malformed/unsupported pages instead of escaping a Qt slot.
            diagnostic_exception('schedule background page warmup',exc)

    def _schedule(self):
        if self.closed or self.view is None:return
        c=self.canvas
        tiles=getattr(c,'view_tiles',None)
        if c.edit_tool=='read' and tiles is not None and tiles.busy():
            # Raster the visible viewport before speculative edit geometry or
            # full-page neighbours compete for Python/CPU time.
            self.timer.start(40);return
        if (c.edits.pending or getattr(c,'_continuous_draw',None) is not None or c.selection_drag
                or getattr(c,'_image_drag',None) or getattr(c,'_image_resize_drag',None)):
            self.timer.start(80);return
        first,last,dpr=self.view
        pages=list(dict.fromkeys([*range(first,min(last+1,first+2)),last+1,first-1,last+2]))
        pages=[page for page in pages if 0<=page<c.page_count][:4]
        jobs=[];raster_bytes=0
        for page in pages:
            width,height=c.draw_sizes[page]
            cache_key=c.content_cache.key_for(c,page,QRectF(0,0,width,height),dpr)
            key=(c.content_cache.epoch,cache_key)
            source=c.edits.page_key(page) if hasattr(c.edits,'page_key') else page
            raw=tuple(c.ink.get(source,())) if source not in c.original_edit_cache else None
            needs_raster=cache_key not in c.content_cache.entries
            cost=cache_key[1]*cache_key[2]*8
            if needs_raster and raster_bytes+cost>c.content_cache.BUDGET//2:
                needs_raster=False
            elif needs_raster:raster_bytes+=cost
            if (raw is None and not needs_raster) or key in self.errors:continue
            overlays=tuple(tuple(dict(item) for item in c._image_records(page,front)) for front in (False,True))
            assets={item['asset']:c.image_layers.assets[item['asset']] for records in overlays for item in records}
            context=FrozenPage(c.editPageSize(page),c.archive_path,c.note_height,c.local_ink,c.ink_smoothing,c.tape_revealed,overlays,assets)
            entries=None
            if needs_raster:
                # Freeze mutable native image/text dictionaries and XML nodes.
                entries=[(z,ident,kind,dict(value) if isinstance(value,dict) else
                          replace(value) if kind=='stroke' else value)
                         for z,ident,kind,value in scene(c,page,include_ink=c.show_ink,static=True,defer_svg=True)]
            jobs.append(dict(key=key,source=source,raw=raw,offset=0 if c.local_ink else source,
                             context=context,entries=entries,dpr=dpr,
                             layers={ident:int(item['raw'].get('data-z-index',0))
                                     for ident,item in c.official_image_edits.layer_items.items()}))
        self.worker.replace_jobs(jobs)
        if jobs and not self.worker.isRunning():self.worker.start(QThread.Priority.LowPriority)

    def received(self,job,pair,strokes,error):
        if self.closed:return
        c=self.canvas;epoch,key=job['key'];page=key[0]
        if error:
            self.errors.add(job['key'])
            if len(self.errors)>32:self.errors.clear()
            diagnostic_exception('background page warmup',RuntimeError(error));return
        if strokes is not None and job['source'] not in c.original_edit_cache:
            c.original_edit_cache[job['source']]=strokes
            while len(c.original_edit_cache)>8:c.original_edit_cache.popitem(last=False)
        if page>=c.page_count:return
        width,height=c.draw_sizes[page]
        current=c.content_cache.key_for(c,page,QRectF(0,0,width,height),job['dpr'])
        if epoch!=c.content_cache.epoch or current!=key:return
        if pair is not None:c.content_cache.store(key,pair)
        self.completed.append(page)
        del self.completed[:-16]
        if pair is not None and self.view and self.view[0]<=page<=self.view[1]:
            c.update(QRectF((c.width()-width)/2,c.pageTop(page),width,height).toAlignedRect())

    def close(self,*_):
        if self.closed:return
        self.closed=True;self.timer.stop();self.worker.stop()
        self.canvas=None;self.worker=None
