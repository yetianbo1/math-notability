"""Visible-first, bounded QImage tiles. No raster work runs in paintEvent."""
from collections import OrderedDict
from dataclasses import dataclass,replace
from threading import Condition
import copy
import math
from lxml import etree
from PyQt6.QtCore import QObject,QThread,QTimer,QRectF,pyqtSignal
from rendering.ne_page_warmup import FrozenPage,render_pair
from rendering.ne_mixed_layers import scene
from editing.ne_inksmooth import smooth_svg_path
from infrastructure.ne_diagnostics import exception as diagnostic_exception


@dataclass
class TileScene:
    context: object
    entries: tuple
    bounds: dict
    layers: dict
    prepared: object = None

    def select(self,source,rect):
        # Only the worker accesses prepared. Original bytes/GUI layer values
        # are copied once; SVG smoothing is reused by all tiles of this view.
        if self.prepared is None:
            prepared=[]
            for z,ident,kind,value in self.entries:
                bounds=None
                if kind=='svg-bytes':
                    bounds=self.bounds.get(value)
                    value=etree.fromstring(value);ident=int(value.get('data-event-id',0))
                    z=self.layers.get(ident,int(value.get('data-z-index',0)));kind='svg'
                prepared.append((z,ident,kind,value,bounds))
            self.prepared=sorted(prepared,key=lambda item:(item[0],item[1]))
        offset=0 if self.context.local_ink else source*self.context.note_height
        # Unknown or edited geometry is conservatively retained. The original
        # Bezier control hull includes stroke width and smoothing displacement.
        selected=[]
        for z,ident,kind,value,bounds in self.prepared:
            if bounds is not None and (bounds[2]<rect.left() or bounds[0]>rect.right()
                    or bounds[3]-offset<rect.top() or bounds[1]-offset>rect.bottom()):continue
            if (kind=='svg' and self.context.ink_smoothing and value.get('data-tool')!='tape'
                    and value.get('data-render-smoothed')!='1'):
                value.set('d',smooth_svg_path(value.get('d',''),float(value.get('stroke-width',2))))
                value.set('data-render-smoothed','1')
            selected.append((z,ident,kind,value))
        return selected


def render_tile(job):
    key=job['key'][1];snapshot=job['snapshot'];tile=job.get('tile')
    nw,nh=snapshot.context.dimensions
    rect=QRectF(0,0,nw,nh) if tile is None else QRectF(
        tile[0]*nw/key[1],tile[1]*nh/key[2],tile[2]*nw/key[1],tile[3]*nh/key[2])
    entries=snapshot.select(job['source'],job.get('clip_native',rect))
    return render_pair(dict(job,context=snapshot.context,entries=entries))


class TileWorker(QThread):
    ready=pyqtSignal(object,object,str)

    def __init__(self,parent):
        super().__init__(parent)
        self.condition=Condition();self.jobs=[];self.current=None;self.stopping=False

    def replace_jobs(self,jobs):
        with self.condition:
            self.jobs=[j for j in jobs if j['request']!=self.current][:64]
            self.condition.notify()

    def run(self):
        while True:
            with self.condition:
                while not self.stopping and not self.jobs:self.condition.wait()
                if self.stopping:return
                job=self.jobs.pop(0);self.current=job['request']
            pair=None;error=''
            try:pair=render_tile(job)
            except Exception as exc:error=str(exc)
            self.ready.emit(job,pair,error)
            with self.condition:self.current=None

    def stop(self):
        with self.condition:
            self.stopping=True;self.jobs=[];self.condition.notify()
        self.wait()


class ViewTiles(QObject):
    thumbnail_ready=pyqtSignal(int)
    visible_ready=pyqtSignal()
    TILE=512
    BUDGET=32*1024*1024

    def __init__(self,canvas):
        super().__init__(canvas)
        self.canvas=canvas;self.closed=False;self.cache=OrderedDict();self.bytes=0
        self.snapshots=OrderedDict();self.requests=[];self.wanted=set();self.errors=set()
        self.thumbnail_requests=OrderedDict();self.preview_clips={}
        self.view=QRectF();self.dpr=1.;self.completed=[];self.hits=0
        self.worker=TileWorker(self);self.worker.ready.connect(self.received)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.schedule)
        self.revision_signature=None;self.revisions={};self.global_revision=None

    def _key(self,page,dpr):
        c=self.canvas;core=c.edits.core if hasattr(c.edits,'core') else c.edits
        source=c.edits.page_key(page) if hasattr(c.edits,'page_key') else page
        owners={source}|{owner for owner,_ in c.spill_ink.get(source,())}
        version=(c.content_cache.epoch,)+core.document_index.revision(owners)
        w,h=c.draw_sizes[page]
        base=c.content_cache.key_for(c,page,QRectF(0,0,w,h),dpr)
        return base[:3]+(version,source)+base[5:]

    def begin_view(self,rect,dpr):
        self.view=QRectF(rect);self.dpr=dpr;self.requests=[];self.wanted=set();self.preview_clips={}

    def paint(self,painter,page,target,dpr):
        key=self._key(page,dpr)
        c=self.canvas
        # A full raster already warmed for editing can be reused immediately.
        full=c.content_cache.entries.get(c.content_cache.key_for(c,page,target,dpr))
        if full is not None:
            painter.drawImage(target,full[0]);painter.drawImage(target,full[1]);return
        clipped=target.intersected(self.view)
        if clipped.isEmpty():return
        nw,nh=c.editPageSize(page)
        clip_native=QRectF((clipped.left()-target.left())*nw/target.width(),
                          (clipped.top()-target.top())*nh/target.height(),
                          clipped.width()*nw/target.width(),clipped.height()*nh/target.height())
        clip_key=tuple(round(v,3) for v in (clip_native.x(),clip_native.y(),clip_native.width(),clip_native.height()))
        preview_key=('preview',key,clip_key)
        self.preview_clips[preview_key]=clip_native
        preview=self.cache.get(preview_key)
        if preview is None:
            # A valid previous zoom is a temporary preview; revisions/options
            # must match so deleted ink and covered tape never reappear.
            for old in reversed(self.cache):
                if old[0]=='preview' and old[1][0]==page and old[1][3:]==key[3:]:
                    preview=self.cache[old];break
        self.wanted.add(preview_key)
        if preview_key not in self.cache:self.requests.append((preview_key,page,key,None))
        sx=key[1]/target.width();sy=key[2]/target.height()
        x0=max(0,math.floor((clipped.left()-target.left())*sx/self.TILE))
        x1=min((key[1]-1)//self.TILE,math.floor((clipped.right()-target.left())*sx/self.TILE))
        y0=max(0,math.floor((clipped.top()-target.top())*sy/self.TILE))
        y1=min((key[2]-1)//self.TILE,math.floor((clipped.bottom()-target.top())*sy/self.TILE))
        tiles=[(x*self.TILE,y*self.TILE) for y in range(y0,y1+1) for x in range(x0,x1+1)]
        cx=(clipped.center().x()-target.left())*sx;cy=(clipped.center().y()-target.top())*sy
        tiles.sort(key=lambda p:(p[0]+self.TILE/2-cx)**2+(p[1]+self.TILE/2-cy)**2)
        for x,y in tiles:
            tw=min(self.TILE,key[1]-x);th=min(self.TILE,key[2]-y)
            request=('tile',key,x,y);self.wanted.add(request)
            pair=self.cache.get(request)
            dest=QRectF(target.left()+x/sx,target.top()+y/sy,tw/sx,th/sy)
            if pair is None:
                self.requests.append((request,page,key,(x,y,tw,th)))
                if preview is not None:
                    pw,ph=preview[0].width(),preview[0].height()
                    source=QRectF(x*pw/key[1],y*ph/key[2],tw*pw/key[1],th*ph/key[2])
                    painter.drawImage(dest,preview[0],source);painter.drawImage(dest,preview[1],source)
            else:
                self.cache.move_to_end(request);self.hits+=1
                # Tiles are drawn with an exact source rectangle (one-pixel
                # gutter is excluded) to avoid doubled translucent edge ink.
                source=QRectF(1,1,tw,th)
                painter.drawImage(dest,pair[0],source);painter.drawImage(dest,pair[1],source)

    def end_view(self):
        if not self.closed:
            self.timer.start(0)
            if not self.busy():self.visible_ready.emit()

    def _snapshot(self,page,key):
        snapshot_key=(key[0],)+key[3:]
        if snapshot_key in self.snapshots:
            self.snapshots.move_to_end(snapshot_key);return self.snapshots[snapshot_key]
        c=self.canvas;source=key[4]
        overlays=tuple(tuple(copy.deepcopy(item) for item in c._image_records(page,front)) for front in (False,True))
        assets={item['asset']:c.image_layers.assets[item['asset']] for records in overlays for item in records}
        context=FrozenPage(c.editPageSize(page),c.archive_path,c.note_height,c.local_ink,
                           c.ink_smoothing,c.tape_revealed,overlays,assets)
        entries=tuple((z,ident,kind,copy.deepcopy(value) if isinstance(value,dict) else
                       replace(value) if kind=='stroke' else value)
                      for z,ident,kind,value in scene(c,page,include_ink=c.show_ink,static=True,defer_svg=True))
        bounds={value:c._ink_render_bounds.get(value) for _,_,kind,value in entries if kind=='svg-bytes'}
        layers={ident:int(item['raw'].get('data-z-index',0)) for ident,item in c.official_image_edits.layer_items.items()}
        snapshot=TileScene(context,entries,bounds,layers)
        self.snapshots[snapshot_key]=snapshot
        while len(self.snapshots)>2:self.snapshots.popitem(last=False)
        return snapshot

    def thumbnail(self,page,size):
        key=self._key(page,1)
        key=(key[0],size.width(),size.height())+key[3:]
        request=('thumbnail',key)
        if request in self.errors:raise ValueError('后台缩略图生成失败，请查看日志')
        if request in self.cache:
            self.cache.move_to_end(request);return self.cache[request]
        if request not in self.errors:
            self.thumbnail_requests[request]=(request,page,key,None)
            while len(self.thumbnail_requests)>4:self.thumbnail_requests.popitem(last=False)
            self.timer.start(0)
        return None

    def schedule(self):
        if self.closed:return
        c=self.canvas
        if c.edits.pending or getattr(c,'_continuous_draw',None) is not None:
            self.worker.replace_jobs([]);return
        jobs=[]
        try:
            requests=(self.requests if c.edit_tool=='read' else [])+list(self.thumbnail_requests.values())
            for request,page,key,tile in requests:
                if request in self.cache or request in self.errors:continue
                if page>=c.page_count:continue
                current=self._key(page,self.dpr)
                if request[0]=='thumbnail':
                    if current[0]!=key[0] or current[3:]!=key[3:]:
                        self.thumbnail_requests.pop(request,None);continue
                elif current!=key:continue
                snapshot=self._snapshot(page,key);raster_key=key
                if request[0]=='preview':
                    factor=min(1.,256/key[1],512/key[2])
                    raster_key=(page,max(1,round(key[1]*factor)),max(1,round(key[2]*factor)))+key[3:]
                elif tile is not None:tile=(tile[0]-1,tile[1]-1,tile[2]+2,tile[3]+2)
                jobs.append(dict(request=request,key=(c.content_cache.epoch,raster_key),source=key[4],
                                 snapshot=snapshot,tile=tile,dpr=self.dpr,view_key=key))
                if request[0]=='preview':jobs[-1]['clip_native']=self.preview_clips[request]
        except Exception as exc:
            diagnostic_exception('prepare visible tiles',exc)
        self.worker.replace_jobs(jobs)
        if jobs and not self.worker.isRunning():self.worker.start(QThread.Priority.NormalPriority)

    def busy(self):
        return bool(self.requests and any(r[0] not in self.cache and r[0] not in self.errors for r in self.requests))

    def received(self,job,pair,error):
        if self.closed:return
        c=self.canvas;key=job['view_key'];page=key[0];request=job['request']
        if page>=c.page_count:return
        current=self._key(page,job['dpr'])
        if request[0]=='thumbnail':
            if current[0]!=key[0] or current[3:]!=key[3:]:return
        elif current!=key:return
        if request not in self.wanted and request not in self.thumbnail_requests:return
        if error:
            self.errors.add(request)
            if len(self.errors)>64:self.errors.clear()
            diagnostic_exception('render visible tile',RuntimeError(error));return
        if request not in self.cache:
            cost=sum(i.sizeInBytes() for i in pair)
            if cost<=self.BUDGET:
                self.cache[request]=pair;self.bytes+=cost
                while self.bytes>self.BUDGET:
                    _,old=self.cache.popitem(last=False);self.bytes-=sum(i.sizeInBytes() for i in old)
        self.completed.append(request);del self.completed[:-64]
        if not self.busy():self.visible_ready.emit()
        if request[0]=='thumbnail':
            self.thumbnail_requests.pop(request,None);self.thumbnail_ready.emit(page);return
        w,h=c.draw_sizes[page]
        c.update(QRectF((c.width()-w)/2,c.pageTop(page),w,h).toAlignedRect())

    def close(self,*_):
        if self.closed:return
        self.closed=True;self.timer.stop();self.worker.stop()
        self.snapshots.clear();self.cache.clear();self.bytes=0
        self.thumbnail_requests.clear()
        self.canvas=None;self.worker=None
