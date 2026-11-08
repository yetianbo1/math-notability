"""Continuous pages with per-page ink and a bounded raster cache."""
from bisect import bisect_right
from collections import OrderedDict, defaultdict
import json
import copy
import math
from zipfile import ZipFile

import numpy as np
from lxml import etree
from PyQt6.QtCore import QRectF, QSize, QLineF, Qt, pyqtSignal
from editing.ne_editview import EditingMixin
from editing.ne_inklayers import map_cross_page_ink
from editing.ne_inksmooth import smooth_svg_path
from infrastructure.ne_diagnostics import exception as diagnostic_exception
from images.ne_inserted_images import ImageLayers, read_archive
from images.ne_official_image_edits import OfficialImageEdits, PREFIX as NATIVE_IMAGE_PREFIX
from images.ne_image_resize import image_corners
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QFont
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget


class PdfNoteCanvas(EditingMixin, QWidget):
    editsChanged = pyqtSignal()
    selectionChanged = pyqtSignal(int)
    pagesChanged = pyqtSignal(int)
    CACHE_BYTES = 64 * 1024 * 1024
    MAX_RENDER_PIXELS = 4_000_000

    def __init__(self, document, svg, note_width, note_height, parent=None, archive_path=None, pdf_source_path=None,render_index=None):
        super().__init__(parent)
        root = etree.fromstring(svg)
        self.pdf_supersampling = root.get('data-note-format') == 'keyed-v9'
        self.document = document
        self.archive_path = archive_path
        self.note_width, self.note_height = float(note_width), float(note_height)
        self.zoom = 1.0
        self.page_gap = 18
        self.seamless_pages = False
        self.cache, self.renderers = OrderedDict(), OrderedDict()
        self.cache_bytes = 0
        from rendering.ne_rendercache import ContentCache
        self.content_cache = ContentCache()
        self.pdf_rasters=None
        self.page_warmup=None
        self.view_tiles=None
        if document and pdf_source_path:
            from rendering.ne_pdfrender import PdfRasterCache
            self.pdf_rasters=PdfRasterCache(pdf_source_path,self,supersample=self.pdf_supersampling)
            self.pdf_rasters.ready.connect(self._pdfReady)
            self.destroyed.connect(self.pdf_rasters.close)
        self._last_visible_page=0
        self.enhance_pages = False
        self.enhancement_strength = 0.6
        self.show_ink = True
        self.ink_smoothing = True
        # make_svg() returns SVG total height (page_height * page_count),
        # not the size of one native notebook page. The bundle also exports
        # the verified per-sheet height in data-page-height; without this,
        # per-page notes such as the 43-sheet OS sample accidentally get a
        # 32,000pt *per-page* canvas. Page Save then fails size validation.
        from core.ne_page_geometry import native_sheet_height
        self.note_height = native_sheet_height(root, self.note_height)
        self.local_ink = root.get('data-local-ink') == '1'
        self.paper_template = json.loads(root.get('data-paper-template', 'null'))
        self.ink = defaultdict(list)
        self.images = defaultdict(list)
        self.native_text = defaultdict(list)
        indexed=render_index is not None and render_index.svg==svg
        for el in (() if indexed else root.iter()):
            tag = el.tag.rsplit('}', 1)[-1]
            if tag == 'path':
                self.ink[int(el.get('data-page', '0'))].append(etree.tostring(el))
            elif tag == 'image':
                self.images[int(el.get('data-page'))].append(dict(el.attrib))
            elif tag == 'text' and el.get('data-native-text'):
                record=json.loads(el.get('data-native-text'))
                self.native_text[record['page']].append(record)
        if indexed:
            self.ink=render_index.ink
            self.images=render_index.images
            self.native_text=render_index.texts
        self.page_layout = json.loads(root.get('data-pages','[]'))
        if self.page_layout:
            self.page_count = len(self.page_layout)
            self.pdf_pages = [record.get('pdf') for record in self.page_layout]
            # Blank inserted pages use the notebook's display page size.
            default = next(((p['width'],p['height']) for p in self.page_layout if 'width' in p), (self.note_width,self.note_height))
            self.sizes = [(p.get('width',default[0]),p.get('height',default[1])) for p in self.page_layout]
            if document and any(p is not None and not 0 <= p < document.pageCount() for p in self.pdf_pages):
                raise ValueError('页面引用超出内嵌 PDF 范围')
        else:
            self.page_count = document.pageCount() if document else int(root.get('data-page-count', '1'))
            self.pdf_pages = list(range(self.page_count)) if document else [None]*self.page_count
            self.sizes = [(document.pagePointSize(p).width(), document.pagePointSize(p).height())
                          if document else (self.note_width, self.note_height) for p in range(self.page_count)]
        # NTB strokes can continue across a paper boundary while their event is
        # owned by the preceding page. Build an index using original identities.
        self.spill_ink = {}
        self._spill_signatures = {}
        self.initEditing()
        # The viewer-owned overlay is independent of official native picture events.
        if self.archive_path and self.archive_path.lower().endswith('.ntb'):
            pages, assets=read_archive(self.archive_path,self.page_count)
            self.image_layers=ImageLayers(pages,assets)
        else:
            self.image_layers=ImageLayers([{'stack':['ink'],'images':[]} for _ in range(self.page_count)])
        self.official_image_edits=OfficialImageEdits(self.images)
        from rendering.ne_mixed_layers import register_layers
        if indexed:
            self.official_image_edits.register_layers(render_index.layers)
            path_bounds=render_index.path_bounds
            self._ink_render_bounds=render_index.render_bounds
        else:path_bounds=register_layers(self)
        if not self.local_ink:
            self.spill_ink=map_cross_page_ink(self.ink,self.note_height,self.page_count,path_bounds=path_bounds)
        del path_bounds
        self.selected_image=None
        self._image_drag=None
        self._image_resize_drag=None
        self._image_history_before=None
        self._image_qcache=OrderedDict()
        self._image_qcache_bytes=0
        if self.archive_path and self.archive_path.lower().endswith(('.ntb','.note')) and self.page_layout:
            from pages.ne_pages import PageEdits
            self.edits=PageEdits(self)
        self.setZoom(1.0)

    def _image_records(self,page,front):
        key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
        record=self.image_layers._page(key)
        indexed={item['id']:item for item in record['images']}
        anchor=record['stack'].index('ink')
        portion=record['stack'][anchor+1:] if front else record['stack'][:anchor]
        return [indexed[identifier] for identifier in portion]

    def paintInsertedImages(self,painter,page,target,*,front=False,items=None):
        if items is None:items=self._image_records(page,front)
        if not items:return
        nw,nh=self.editPageSize(page)
        painter.save()
        try:
            painter.setClipRect(target)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
            painter.translate(target.left(),target.top())
            painter.scale(target.width()/nw,target.height()/nh)
            for item in items:
                digest=item['asset']
                bitmap=self._image_qcache.get(digest)
                if bitmap is None:
                    bitmap=QImage.fromData(self.image_layers.assets[digest],'PNG')
                    if bitmap.isNull():raise ValueError('插图素材无法解码')
                    size_bytes=bitmap.sizeInBytes()
                    if size_bytes<=64*1024*1024:
                        while self._image_qcache and self._image_qcache_bytes+size_bytes>64*1024*1024:
                            _,expired=self._image_qcache.popitem(last=False)
                            self._image_qcache_bytes-=expired.sizeInBytes()
                        self._image_qcache[digest]=bitmap
                        self._image_qcache_bytes+=size_bytes
                else:
                    self._image_qcache.move_to_end(digest)
                painter.drawImage(QRectF(item['x'],item['y'],item['width'],item['height']),bitmap)
        finally:
            painter.restore()

    def nativeImagePage(self,page):
        # Source event IDs belong to stable original pages. Copies have no
        # independent native IDs until saved and reopened.
        if hasattr(self.edits,'page_key'):
            return self.edits.specs[self.edits.page_key(page)].get('source')
        return page

    def imageState(self):
        return (copy.deepcopy(self.image_layers.pages),
                {ident:dict(raw=dict(info['raw']),locked=info['locked'],deleted=info['deleted'],page=info['page'])
                 for ident,info in self.official_image_edits.items.items()},
                {ident:dict(info['raw']) for ident,info in self.official_image_edits.layer_items.items() if ident not in self.official_image_edits.items})

    def restoreImageState(self,state):
        pages,native,other_layers=state
        # Keep inactive page identities: a later redo can restore those pages.
        self.image_layers.pages.update(copy.deepcopy(pages))
        for ident,saved in native.items():
            info=self.official_image_edits.items[ident]
            info['raw'].clear();info['raw'].update(saved['raw'])
            info['locked']=saved['locked'];info['deleted']=saved['deleted']
            info['page']=saved['page']
        for ident,raw in other_layers.items():
            self.official_image_edits.layer_items[ident]["raw"].update(raw)
        self.syncNativeImagePages()
        self.selected_image=None
        self.clearCache();self.update()

    def syncNativeImagePages(self):
        """Keep live native records on their owning page; copied snapshots stay independent."""
        live={id(info['raw']) for info in self.official_image_edits.items.values()}
        if hasattr(self.edits,'specs'):
            for spec in self.edits.specs.values():
                spec['images'][:]=[r for r in spec['images'] if id(r) not in live]
            for info in self.official_image_edits.items.values():
                spec=self.edits.specs.get(info['page'])
                if spec is not None:spec['images'].append(info['raw'])
            self.images={i:self.edits.specs[key]['images'] for i,key in enumerate(self.edits.order)}
        else:
            for page in self.images:self.images[page][:]=[r for r in self.images[page] if id(r) not in live]
            for info in self.official_image_edits.items.values():
                self.images.setdefault(info['page'],[]).append(info['raw'])

    def beginImageGesture(self):
        self.finishGesture()
        self._image_history_before=self.imageState()

    def finishImageGesture(self,cancel=False):
        self._drag_scroll_timer.stop()
        before=self._image_history_before
        self.content_cache.image_drag.clear()
        self._image_history_before=None
        self._image_drag=None;self._image_resize_drag=None
        if before is None:return
        if cancel:self.restoreImageState(before)
        else:self.edits.record_external(before,self.imageState(),self.restoreImageState)
        self.editsChanged.emit()

    def editableImageItem(self,page,identifier):
        if isinstance(identifier,str) and identifier.startswith(NATIVE_IMAGE_PREFIX):
            return self.official_image_edits.item(self.nativeImagePage(page),identifier)
        key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
        return self.image_layers.item(key,identifier)

    def moveEditableImage(self,page,identifier,x,y):
        if identifier.startswith(NATIVE_IMAGE_PREFIX):
            return self.official_image_edits.move(self.nativeImagePage(page),identifier,x,y)
        key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
        return self.image_layers.move(key,identifier,x,y)

    def moveEditableImageToPage(self,page,target,identifier,x,y):
        if not 0<=target<self.page_count:return False
        item=self.editableImageItem(page,identifier)
        if not item or item['locked']:return False
        item=dict(item)
        nw,nh=self.editPageSize(target)
        if page!=target and (item['width']>nw+.001 or item['height']>nh+.001):
            raise ValueError('图片大于目标页，请先等比例缩小图片')
        x=max(0.,min(nw-item['width'],x));y=max(0.,min(nh-item['height'],y))
        if identifier.startswith(NATIVE_IMAGE_PREFIX):
            source_page=self.nativeImagePage(page);target_page=self.nativeImagePage(target)
            if page!=target and hasattr(self.edits,'order') and self.edits.order!=self.edits.initial:
                raise ValueError('请先保存页面新增、复制或重排，再跨页移动原生图片')
            if target_page is None:
                raise ValueError('请先保存新增或复制的页面，再移动原生图片到该页')
            changed=self.official_image_edits.transfer(source_page,target_page,identifier,x,y)
            if changed and page!=target:self.syncNativeImagePages()
        else:
            key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
            target_key=self.edits.page_key(target) if hasattr(self.edits,'page_key') else target
            changed=self.image_layers.transfer(key,target_key,identifier,x,y)
        if changed:
            self.selected_image=(target,identifier)
            self.imageGeometryChanged(page,target,item,self.editableImageItem(target,identifier))
        return changed

    def moveSelectedToPage(self,target):
        """Menu operation shares the same models/history as dragging."""
        self.finishGesture()
        if not 0<=target<self.page_count:raise ValueError('目标页超出范围')
        if self.selected_image and self.edit_tool!='select':
            page,identifier=self.selected_image
            item=self.editableImageItem(page,identifier)
            if item is None or page==target:return False
            if item['locked']:raise ValueError('请先解除图片锁定')
            self.beginImageGesture()
            try:changed=self.moveEditableImageToPage(page,target,identifier,item['x'],item['y'])
            except Exception:
                self.finishImageGesture(cancel=True);raise
            self.finishImageGesture()
            return changed
        if self.edit_tool=='image':return False
        return self.moveSelectedInkToPage(target)

    def imageGeometryChanged(self,page,target,before,after):
        pages={page,target}
        # PDF/paper backgrounds contain no editable images and remain valid.
        for key in list(self.cache):
            if key[0] in pages and not key[4]:
                self.cache_bytes-=self.cache.pop(key).sizeInBytes()
        self.content_cache.invalidate_pages(pages)
        for index,item in ((page,before),(target,after)):
            if item is None:continue
            dw,dh=self.draw_sizes[index];nw,nh=self.editPageSize(index)
            rect=QRectF((self.width()-dw)/2+item['x']*dw/nw,
                        self.pageTop(index)+item['y']*dh/nh,
                        item['width']*dw/nw,item['height']*dh/nh)
            self.update(rect.adjusted(-12,-12,12,12).toAlignedRect())

    def resizeEditableImage(self,page,identifier,box):
        before=self.editableImageItem(page,identifier)
        if before is None:return False
        before=dict(before)
        if identifier.startswith(NATIVE_IMAGE_PREFIX):
            changed=self.official_image_edits.resize(self.nativeImagePage(page),identifier,*box,page_size=self.editPageSize(page))
        else:
            key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
            changed=self.image_layers.resize(key,identifier,*box,page_size=self.editPageSize(page))
        if changed:self.imageGeometryChanged(page,page,before,self.editableImageItem(page,identifier))
        return changed

    def paintImageSelection(self,painter,page,target):
        if self.edit_tool!='image' or not self.selected_image or self.selected_image[0]!=page:return
        item=self.editableImageItem(page,self.selected_image[1])
        if not item:return
        nw,nh=self.editPageSize(page)
        painter.save()
        try:
            painter.setPen(QPen(QColor('#f59e0b' if item['locked'] else '#2563eb'),2,Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setClipRect(target)
            rect=QRectF(target.left()+item['x']*target.width()/nw,
                        target.top()+item['y']*target.height()/nh,
                        item['width']*target.width()/nw,item['height']*target.height()/nh)
            painter.drawRect(rect)
            if not item['locked'] and self.edit_tool=='image':
                painter.setBrush(QColor('#2563eb'))
                painter.setPen(QPen(QColor('white'),1))
                for cx,cy in image_corners(rect.left(),rect.top(),rect.width(),rect.height()).values():
                    painter.drawRect(QRectF(cx-4.5,cy-4.5,9,9))
        finally:painter.restore()

    def imagePagePoint(self,page,position):
        """Translate a drag point even outside the page; resize clamps it."""
        dw,dh=self.draw_sizes[page]
        left=(self.width()-dw)/2
        nw,nh=self.editPageSize(page)
        return ((position.x()-left)*nw/dw,
                (position.y()-self.pageTop(page))*nh/dh)

    def hitImageResizeHandle(self,position):
        if self.edit_tool!='image' or not self.selected_image:return None
        page,identifier=self.selected_image
        item=self.editableImageItem(page,identifier)
        if item is None or item['locked']:return None
        dw,dh=self.draw_sizes[page]
        left=(self.width()-dw)/2
        nw,nh=self.editPageSize(page)
        for corner,(cx,cy) in image_corners(item['x'],item['y'],item['width'],item['height']).items():
            sx=left+cx*dw/nw
            sy=self.pageTop(page)+cy*dh/nh
            if abs(position.x()-sx)<=10 and abs(position.y()-sy)<=10:
                return page,identifier,corner
        return None

    def hitImage(self,position):
        hit=self.pointOnPage(position)
        if hit is None:return None
        page,(x,y)=hit
        key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
        layer=self.image_layers._page(key)
        records={item['id']:item for item in layer['images']}
        anchor=layer['stack'].index('ink')
        for identifier in reversed(layer['stack'][anchor+1:]):
            item=records[identifier]
            if item['x']<=x<=item['x']+item['width'] and item['y']<=y<=item['y']+item['height']:
                return page,identifier,(x,y)
        from rendering.ne_mixed_layers import scene
        from editing.ne_edits import distance2,svg_strokes
        for _,ident,kind,record in reversed(scene(self,page,include_ink=self.show_ink and self.edit_tool!='image',
                                                include_text=self.edit_tool!='image')):
            # Image management ignores ink/text even when they cover an image.
            if self.edit_tool=='image' and kind!='image':continue
            if kind=='image':
                identifier=NATIVE_IMAGE_PREFIX+str(ident)
                item=self.editableImageItem(page,identifier)
                if item and item['x']<=x<=item['x']+item['width'] and item['y']<=y<=item['y']+item['height']:
                    return page,identifier,(x,y)
            elif kind=='text':
                if record['x']<=x<=record['x']+record['width'] and record['y']<=y<=record['y']+record['height']:
                    return None
            elif kind in ('stroke','svg'):
                source=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
                strokes=([record] if kind=='stroke' else svg_strokes([etree.tostring(record)],0 if self.local_ink else source,self.note_height))
                for stroke in strokes:
                    if any(distance2((x,y),a,b)<=(stroke.width/2+2)**2 for a,b in zip(stroke.points,stroke.points[1:] or stroke.points)):
                        return None
        for identifier in reversed(layer['stack'][:anchor]):
            item=records[identifier]
            if item['x']<=x<=item['x']+item['width'] and item['y']<=y<=item['y']+item['height']:
                return page,identifier,(x,y)
        return None

    def addImage(self,blob,width_px,height_px,page):
        self.finishGesture()
        self.clearSelection()
        before=self.imageState()
        nw,nh=self.editPageSize(page)
        w=min(nw*.6,float(width_px)*72/96)
        h=w*height_px/width_px
        if h>nh*.6:
            h=nh*.6;w=h*width_px/height_px
        key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
        identifier=self.image_layers.insert(key,blob,(nw-w)/2,(nh-h)/2,w,h)
        self.edits.record_external(before,self.imageState(),self.restoreImageState)
        self.selected_image=(page,identifier)
        self.clearCache();self.editsChanged.emit();self.update()
        return identifier

    def updateImageAction(self,operation):
        if self.edit_tool=='select' or not self.selected_image:return False
        self.finishGesture()
        before=self.imageState()
        page,identifier=self.selected_image
        key=self.edits.page_key(page) if hasattr(self.edits,'page_key') else page
        model=self.image_layers
        if operation=='toggle_lock':
            if identifier.startswith(NATIVE_IMAGE_PREFIX):
                item=self.editableImageItem(page,identifier)
                changed=self.official_image_edits.lock(self.nativeImagePage(page),identifier,not item['locked']) if item else False
            else:
                item=model.item(key,identifier)
                changed=model.lock(key,identifier,not item['locked']) if item else False
        elif operation=='delete':
            changed=(self.official_image_edits.remove(self.nativeImagePage(page),identifier)
                     if identifier.startswith(NATIVE_IMAGE_PREFIX) else model.remove(key,identifier))
            if changed:self.selected_image=None
        else:
            changed=(self.official_image_edits.layer(self.nativeImagePage(page),identifier,operation)
                     if identifier.startswith(NATIVE_IMAGE_PREFIX) else model.layer(key,identifier,operation))
        if changed:
            self.edits.record_external(before,self.imageState(),self.restoreImageState)
            self.clearCache();self.editsChanged.emit();self.update()
        return changed

    def clearCache(self):
        self.cache.clear()
        self.cache_bytes = 0
        self.content_cache.clear()

    def nativeImageRecords(self,page):
        records=[]
        for record in self.images.get(page,()):
            try:info=(self.official_image_edits.items.get(int(record['data-event-id']))
                      if self.nativeImagePage(page) is not None else None)
            except (KeyError,TypeError,ValueError):info=None
            records.append(info['raw'] if info is not None else record)
        return sorted(records,key=lambda r:(int(r.get('data-z-index',0)),int(r.get('data-event-id',0))))

    def pageTop(self, page):
        return self.tops[max(0, min(self.page_count - 1, int(page))) ]

    def pageAt(self, y):
        return max(0, min(self.page_count - 1, bisect_right(self.tops, y) - 1))

    def setZoom(self, zoom):
        if hasattr(self, 'edits'):
            self.finishGesture()
        self.zoom = max(0.2, min(5.0, float(zoom)))
        self.draw_sizes = [(max(1, round(w*self.zoom)), max(1, round(h*self.zoom))) for w,h in self.sizes]
        gap = 0 if self.seamless_pages else max(8, round(self.page_gap * self.zoom))
        self.tops = []
        top = 12
        for _, height in self.draw_sizes:
            self.tops.append(top)
            top += height + gap
        self.setFixedSize(max(w for w,h in self.draw_sizes) + 32, top - gap + 12)
        self.clearCache()
        self._syncBrushCursor()
        self.update()
        if hasattr(self,'_updateCardButton'):self._updateCardButton()

    def setSeamlessPages(self,enabled):
        enabled=bool(enabled)
        if self.seamless_pages==enabled:return
        self.finishGesture()
        self.seamless_pages=enabled
        self.setZoom(self.zoom)

    def applyPageSpecs(self,specs,focus):
        self.clearSelection();self.page_count=len(specs)
        self.sizes=[(p['width'],p['height']) for p in specs];self.pdf_pages=[p['pdf'] for p in specs]
        self.images={i:p['images'] for i,p in enumerate(specs)};self.page_layout=list(specs)
        self.renderers.clear();self.setZoom(self.zoom);self.pagesChanged.emit(focus)

    def _ink_renderer(self, page, spill_only=False):
        """Render this paper including original strokes owned by other pages.

        The viewBox must use the source-page coordinate origin, not the current
        position in a reordered document. Paint callers clip to the paper.
        """
        source = self.edits.page_key(page) if hasattr(self.edits, 'page_key') else page
        allowed = set(self.edits.order) if hasattr(self.edits, 'order') else set(range(self.page_count))
        core = self.edits.core if hasattr(self.edits, 'core') else self.edits
        edited_sources = core.edited_pages()
        continuations = [(owner, item) for owner, item in self.spill_ink.get(source, ())
                         if owner in allowed and owner not in edited_sources]
        signature = tuple(owner for owner, _ in continuations)
        cache_key = (source, 'spill') if spill_only else source
        if self._spill_signatures.get(cache_key) != signature:
            self.renderers.pop(cache_key, None)
            self._spill_signatures[cache_key] = signature
        if cache_key not in self.renderers:
            own = [] if spill_only else self.ink.get(source, ())
            if not own and not continuations:
                return None
            width, height = self.editPageSize(page)
            top = 0 if self.local_ink else source * self.note_height
            root = etree.Element('svg', xmlns='http://www.w3.org/2000/svg',
                                 viewBox=f'0 {top} {width} {height}',
                                 width=str(width), height=str(height))
            for data in own:
                element = etree.fromstring(data)
                if self.ink_smoothing and element.get('data-render-smoothed')!='1':
                    try:
                        stroke_width = float(element.get('stroke-width', '2'))
                    except (TypeError, ValueError, OverflowError):
                        stroke_width = 2.0
                    element.set('d', smooth_svg_path(element.get('d', ''), stroke_width))
                root.append(element)
            for _, data in continuations:
                element = etree.fromstring(data)
                if self.ink_smoothing and element.get('data-render-smoothed')!='1':
                    try:
                        stroke_width = float(element.get('stroke-width', '2'))
                    except (TypeError, ValueError, OverflowError):
                        stroke_width = 2.0
                    element.set('d', smooth_svg_path(element.get('d', ''), stroke_width))
                root.append(element)
            self.renderers[cache_key] = QSvgRenderer(etree.tostring(root))
            while len(self.renderers) > 8:
                old, _ = self.renderers.popitem(last=False)
                self._spill_signatures.pop(old, None)
        self.renderers.move_to_end(cache_key)
        return self.renderers[cache_key]

    def pagePaperTemplate(self, page):
        """A newly inserted PDF-backed sheet can have its own paper design.

        Native notes continue to rely on their original global type-1 paper
        settings; no per-page background is fabricated in the native event log.
        """
        spec = self.page_layout[page] if 0<=page<len(self.page_layout) else {}
        if isinstance(spec,dict) and spec.get('background_kind'):
            kind=spec['background_kind']
            if (self.document is None and self.paper_template
                    and self.paper_template.get('kind') == kind):
                # Keep exact original native paper pitch/margins/colors.
                return self.paper_template
            from pages.ne_page_policy import native_pattern_spec
            return native_pattern_spec(kind,self.sizes[page][0])
        return self.paper_template

    def paintNativeText(self,painter,page,target):
        source=page
        if hasattr(self.edits,'specs'):
            source=self.edits.specs[self.edits.page_key(page)].get('background_source')
        records=self.native_text.get(source,())
        if not records:return
        nw,nh=self.editPageSize(page)
        painter.save()
        try:
            painter.setClipRect(target)
            painter.translate(target.x(),target.y())
            painter.scale(target.width()/nw,target.height()/nh)
            painter.setPen(QColor('black'))
            for record in records:
                font=QFont('Arial');font.setPixelSize(round(record.get('font_size',15)))
                painter.setFont(font)
                top,bottom,left,right=record.get('margins',(3.,10.,5.,5.))
                painter.drawText(QRectF(record['x']+left,record['y']+top,record['width']-left-right,record['height']-top-bottom),
                                 Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,
                                 record['text'])
        finally:painter.restore()

    def stopRendering(self):
        if self.view_tiles:self.view_tiles.close()
        if self.page_warmup:self.page_warmup.close()
        if self.pdf_rasters:self.pdf_rasters.close()
        self.view_tiles=self.page_warmup=self.pdf_rasters=None
        # Workers no longer read these caches after close(). Clear the payloads
        # while the deferred QWidget deletion is pending.
        self.clearCache()
        self.renderers.clear()
        for name in ('original_edit_cache','_image_qcache','_ink_render_bounds',
                     '_spill_signatures'):
            value=getattr(self,name,None)
            if hasattr(value,'clear'):
                value.clear()
        self._image_qcache_bytes=0
        self._selection_highlight_cache=None
        self._image_history_before=None
        self.selected_image=None
        # PageEdits points back at the canvas and is retained by edit-history
        # closures. Break only that back-reference; the page model remains
        # intact until its deferred widget deletion completes.
        if getattr(self,'edits',None) is not None and getattr(self.edits,'canvas',None) is self:
            self.edits.canvas=None
        core=getattr(getattr(self,'edits',None),'core',None)
        if core is not None:
            # PageEdits wraps originalStrokes in a closure for page copies.
            # The closed canvas no longer needs an original provider.
            core.originals=lambda _key:()

    def enablePageWarmup(self):
        if self.page_warmup is None:
            from rendering.ne_page_warmup import PageWarmup
            self.page_warmup=PageWarmup(self)
            self.destroyed.connect(self.page_warmup.close)

    def enableViewTiles(self):
        if self.view_tiles is None:
            from rendering.ne_view_tiles import ViewTiles
            self.view_tiles=ViewTiles(self)
            self.content_cache.BUDGET=64*1024*1024
            self.destroyed.connect(self.view_tiles.close)

    def _pdfReady(self,key):
        for page,index in enumerate(self.pdf_pages):
            if index==key[0]:
                w,h=self.draw_sizes[page]
                self.update(QRectF((self.width()-w)/2,self.pageTop(page),w,h).toAlignedRect())

    def _requestPdfPages(self,first,last,dpr):
        if not self.pdf_rasters:return
        forward=first>=self._last_visible_page;self._last_visible_page=first
        neighbours=[last+1,first-1] if forward else [first-1,last+1]
        pages=list(range(first,last+1))+neighbours
        requests=[]
        for page in pages:
            if not 0<=page<self.page_count or self.pdf_pages[page] is None:continue
            w,h=self.draw_sizes[page];w=max(1,round(w*dpr));h=max(1,round(h*dpr))
            factor=min(1.,math.sqrt(self.MAX_RENDER_PIXELS/(w*h)))
            requests.append((self.pdf_pages[page],max(1,round(w*factor)),max(1,round(h*factor))))
        self.pdf_rasters.request(requests)

    def _render_page(self, page, size, *, include_native_text=True, background_only=False, allow_async=False):
        # Bound allocations even at 500% zoom and on high-DPI displays.
        factor = min(1.0, math.sqrt(self.MAX_RENDER_PIXELS / max(1, size.width()*size.height())))
        size = QSize(max(1, round(size.width()*factor)), max(1, round(size.height()*factor)))
        key = (page, size.width(), size.height(), include_native_text, background_only)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        paper_template=self.pagePaperTemplate(page)
        image = QImage(size, QImage.Format.Format_RGB32)
        image.fill(QColor((paper_template or {}).get('background','white')))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pending=False
        try:
            if self.pdf_pages[page] is None:
                from rendering.ne_paper_texture import paint_paper_texture
                paint_paper_texture(painter, paper_template,
                                    QRectF(0, 0, size.width(), size.height()),
                                    self.editPageSize(page))
            if self.document and self.pdf_pages[page] is not None:
                from rendering.ne_pdfrender import render_pdf_page
                rendered=(self.pdf_rasters.get((self.pdf_pages[page],size.width(),size.height()))
                          if allow_async and self.pdf_rasters else render_pdf_page(self.document,self.pdf_pages[page],size,supersample=self.pdf_supersampling))
                pending=rendered is None
                if not pending and rendered.isNull():
                    raise ValueError(f'第 {page+1} 页 PDF 渲染失败')
                if not pending:painter.drawImage(0, 0, rendered)
            # The native Notability ruled-paper template is NOT a PDF in the
            # archive: it lives in document settings. Keep its rendering
            # separate from imported PDF pages, and never alter the NTB data.
            if (paper_template and paper_template.get('kind') in ('ruled', 'dotted')
                    and self.pdf_pages[page] is None):
                # Native patterns are generated in PDF-point coordinates; the
                # painter scales them to the current zoom. Avoid stretching a
                # cached PNG template when the note uses another pitch/size.
                from core.ne_paper_patterns import ruled_segments, dotted_centres
                nw, nh = self.editPageSize(page)
                pitch = float(paper_template['pitch'])
                painter.save()
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    painter.scale(size.width() / nw, size.height() / nh)
                    color = QColor(paper_template.get('color', '#a3b7d3'))
                    if paper_template['kind'] == 'ruled':
                        pen = QPen(color)
                        pen.setWidthF(0.5)
                        painter.setPen(pen)
                        for x0, y0, x1, y1 in ruled_segments(
                                nw, nh, pitch, float(paper_template.get('margin', 36.0))):
                            painter.drawLine(QLineF(x0, y0, x1, y1))
                    else:
                        radius = float(paper_template.get('radius', 1.0))
                        if 0.3 <= radius <= 3:
                            painter.setPen(Qt.PenStyle.NoPen)
                            painter.setBrush(color)
                            origin = float(paper_template.get('x_origin', 0.0))
                            for x, y in dotted_centres(nw, nh, pitch, origin):
                                painter.drawEllipse(QRectF(x-radius, y-radius,
                                                           radius*2, radius*2))
                finally:
                    painter.restore()
            if not background_only:
                self.paintInsertedImages(painter,page,QRectF(0,0,size.width(),size.height()),front=False)
                from rendering.ne_mixed_layers import paint_native_scene
                paint_native_scene(self,painter,page,QRectF(0,0,size.width(),size.height()),include_ink=False,include_text=include_native_text)

        finally:
            painter.end()
        # A loading placeholder must not become a permanent paper cache entry.
        if pending:return image
        # Enhancement is for scanned PDF backgrounds. Native paper colours
        # and grain are intentional, including newly inserted paper sheets.
        if self.enhance_pages and self.document and self.pdf_pages[page] is not None:
            image = self._enhance_page(image, self.enhancement_strength)
        self.cache[key] = image
        self.cache_bytes += image.sizeInBytes()
        while self.cache_bytes > self.CACHE_BYTES and len(self.cache) > 1:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= old.sizeInBytes()
        return image

    @staticmethod
    def _enhance_page(image, strength=0.6):
        """Gentle, continuous tone adjustment; never threshold text edges.

        Lift the deepest shadows slightly, soften white paper, and gently lift
        bright scan haze. Saturated diagrams and the separate ink layer retain
        their original colors. Work in strips to bound temporary allocations.
        """
        strength = max(0.0, min(1.0, float(strength)))
        if strength == 0:
            return image
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = rgba.bits()
        ptr.setsize(rgba.sizeInBytes())
        pixels = np.frombuffer(ptr, dtype=np.uint8).reshape(rgba.height(), rgba.bytesPerLine())[:, :rgba.width()*4].reshape(rgba.height(), rgba.width(), 4)

        def smoothstep(low, high, values):
            t = np.clip((values-low)/(high-low), 0, 1)
            return t*t*(3-2*t)

        sample = pixels[::max(1, rgba.height()//256), ::max(1, rgba.width()//256), :3].astype(np.float32)
        weights = np.array([.299,.587,.114], dtype=np.float32)
        sample_lum = sample @ weights
        paper = (sample.max(2)-sample.min(2) < 24) & (sample_lum > 180)
        white = float(np.percentile(sample_lum[paper], 90)) if np.count_nonzero(paper) >= 64 else 255.0
        haze_lift = min(10.0, max(0.0, (250.0-white)*.3))
        for start in range(0, rgba.height(), 128):
            strip = pixels[start:start+128, :, :3]
            rgb = strip.astype(np.float32)
            saturation = rgb.max(2)-rgb.min(2)
            lum = rgb @ weights
            t = lum/255.0
            # Smooth curves keep all antialiasing levels, with no black-point
            # clipping, sharpening, or binary cleanup of scanned characters.
            delta = 16.0*(1.0-t)**3 - 8.0*t**6
            delta += haze_lift*smoothstep(.55,.88,t)*(1.0-smoothstep(.88,1.0,t))
            amount = strength*(1.0-smoothstep(12.0,56.0,saturation))
            adjusted = rgb + (delta*amount)[:,:,None]
            # A very slight warm paper tint, restricted to near-white neutrals.
            paper_amount = amount*smoothstep(210.0,250.0,lum)
            adjusted[:,:,1] -= paper_amount*.5
            adjusted[:,:,2] -= paper_amount*2.0
            strip[:] = np.rint(np.clip(adjusted,0,255)).astype(np.uint8)
        return rgba.convertToFormat(QImage.Format.Format_RGB32)

    def setEnhancementStrength(self, strength):
        strength = max(0.0, min(1.0, float(strength)))
        if self.enhancement_strength != strength:
            self.enhancement_strength = strength
            if self.enhance_pages:
                self.clearCache()
                self.update()

    def setEnhanced(self, enabled):
        if self.enhance_pages != bool(enabled):
            self.enhance_pages = bool(enabled)
            self.clearCache()
            self.update()

    def setInkSmoothing(self, enabled):
        enabled = bool(enabled)
        if self.ink_smoothing == enabled:
            return
        self.ink_smoothing = enabled
        # Cached renderers contain the previous display-only SVG geometry.
        self.renderers.clear()
        self._spill_signatures.clear()
        self.update()

    def setInkVisible(self, enabled):
        self.show_ink = bool(enabled)
        self._syncBrushCursor()
        self.update()

    def paintEvent(self, event):
        # A Qt paint callback must never leak Python exceptions: PyQt can abort
        # the GUI process before a normal error dialog can appear.
        if getattr(self,'_gpu_view',None) and self._gpu_view.active:return
        painter = QPainter(self)
        try:self.paintVisible(painter,event.rect(),self.devicePixelRatioF())
        finally:
            if painter.isActive():painter.end()

    def update(self,*args):
        gpu=getattr(self,'_gpu_view',None)
        if gpu and gpu.active:gpu.refresh()
        else:super().update(*args)

    def paintVisible(self,painter,rect,dpr):
        """Shared raster/OpenGL composition in unchanged canvas coordinates."""
        painter.save()
        try:
            painter.setClipRect(rect)
            painter.fillRect(rect, QColor('#42464d'))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            first, last = self.pageAt(rect.top()), self.pageAt(rect.bottom())
            visible=rect if getattr(self,'_gpu_view',None) and self._gpu_view.active else self.visibleRegion().boundingRect()
            if visible.isEmpty():visible=rect
            self._requestPdfPages(self.pageAt(visible.top()),self.pageAt(visible.bottom()),dpr)
            use_tiles=self.view_tiles is not None and self.edit_tool=='read' and not self.edits.pending
            if use_tiles:
                self.view_tiles.begin_view(visible,dpr)
                first,last=self.pageAt(visible.top()),self.pageAt(visible.bottom())
            for page in range(first, last+1):
                width,height = self.draw_sizes[page]
                left = (self.width()-width)/2
                target = QRectF(left, self.tops[page], width, height)
                try:
                    image = self._render_page(page, QSize(round(width*dpr), round(height*dpr)),background_only=True,allow_async=True)
                    painter.drawImage(target,image)
                    if use_tiles:self.view_tiles.paint(painter,page,target,dpr)
                    else:self.content_cache.paint(self,painter,page,target,dpr)
                    # Light "already has a card" marks are a screen-only edit
                    # aid: they live in paintVisible(), so PDF export (which
                    # paints through paint_content()) never includes them.
                    if hasattr(self,'paintCardMarks'):
                        self.paintCardMarks(painter,page,target)
                    if self.show_ink:
                        self.paintSelectionOverlay(painter,page,target)
                    self.paintImageSelection(painter,page,target)
                except Exception as exc:
                    diagnostic_exception('page paint, page ' + str(page+1),exc)
                    painter.fillRect(target, QColor('white'))
                    painter.setPen(QColor('#b42318'))
                    painter.drawText(target.adjusted(16,16,-16,-16),
                                     Qt.AlignmentFlag.AlignCenter,
                                     '页面显示失败，请查看 NotabilityEditor 日志')
                if not self.seamless_pages:
                    painter.setPen(QPen(QColor('#c8c8c8'),1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(target)
            if self.page_warmup:
                self.page_warmup.visible(self.pageAt(visible.top()),self.pageAt(visible.bottom()),dpr)
            if use_tiles:self.view_tiles.end_view()
            try:
                self.paintEraserCursor(painter)
                self.paintBrushCursor(painter)
                if hasattr(self,'paintShapePreview'):self.paintShapePreview(painter)
                # Draw the floating question-card action here rather than as a
                # child widget: the optional QOpenGLWidget overlays the whole
                # viewport and repaints the canvas through this very method.
                if hasattr(self,'paintCardButton'):
                    painter.save()
                    painter.setClipping(False)
                    try:self.paintCardButton(painter)
                    finally:painter.restore()
            except Exception as exc:
                diagnostic_exception('paint tool cursor',exc)
        except Exception as exc:
            diagnostic_exception('paintEvent',exc)
        finally:
            painter.restore()
