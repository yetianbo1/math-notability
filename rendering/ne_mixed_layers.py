"""One native z-order for pictures, text blocks and ink, shared by screen/PDF."""
from dataclasses import replace
from collections import OrderedDict
from zipfile import ZipFile
from lxml import etree
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QImage,QFont,QColor
from PyQt6.QtSvg import QSvgRenderer
from editing.ne_edits import paint_strokes,Stroke,svg_paths
from editing.ne_inksmooth import smooth_svg_path

# Unsaved annotations sit above every persisted uint64 native layer.
NEW_INK_LAYER=2**64


def collect_layers(ink,native_text,local_ink,note_height,cancelled=lambda:False):
    """Pure geometry preparation; safe on the file-loading thread."""
    records=[]
    path_bounds={}
    render_bounds={}
    for page in ink:
        if cancelled():
            from formats.ne_loader import LoadCancelled
            raise LoadCancelled()
        bounds={}
        z_indexes={}
        # Register exact Bezier bounds and layers now; flatten into editable
        # polylines only when that page is edited or a gesture needs it.
        offset=0 if local_ink else page*note_height
        for raw,(element,path) in zip(ink[page],svg_paths(ink[page],0 if local_ink else page,note_height)):
            # Control points retain conservative cross-page bounds without a
            # second XML/token parse. Layer selection still uses exact bounds.
            control=path.controlPointRect()
            path_bounds[raw]=(control.top()+offset,control.bottom()+offset)
            margin=max(2.,float(element.get('stroke-width',2))*2)
            render_bounds[raw]=(control.left()-margin,control.top()+offset-margin,
                                control.right()+margin,control.bottom()+offset+margin)
            event_id=element.get('data-event-id')
            if event_id is None:continue
            ident=int(event_id)
            z_indexes[ident]=int(element.get('data-z-index',0))
            box=path.boundingRect()
            rect=(box.left(),box.top(),box.right(),box.bottom())
            current=bounds.get(ident)
            bounds[ident]=(rect if current is None else
                (min(current[0],rect[0]),min(current[1],rect[1]),
                 max(current[2],rect[2]),max(current[3],rect[3])))
        for ident,rect in bounds.items():
            z=z_indexes.get(ident,0)
            records.append((page,ident,z,'ink',rect))
    for page,texts in native_text.items():
        for text in texts:
            records.append((
                page,int(text['event_id']),int(text.get('z_index',0)),'text',
                (float(text['x']),float(text['y']),
                 float(text['x'])+float(text['width']),
                 float(text['y'])+float(text['height'])),
            ))
    return records,path_bounds,render_bounds


def register_layers(c):
    records,path_bounds,c._ink_render_bounds=collect_layers(c.ink,c.native_text,c.local_ink,c.note_height)
    c.official_image_edits.register_layers(records)
    return path_bounds


def z_value(c,ident,fallback=0):
    item=c.official_image_edits.layer_items.get(ident)
    return int(item['raw'].get('data-z-index',fallback)) if item else fallback


def scene(c,page,include_ink=True,include_text=True,*,static=False,defer_svg=False):
    entries=[]
    source=c.edits.page_key(page) if hasattr(c.edits,'page_key') else page
    background=c.edits.specs[source].get('background_source') if hasattr(c.edits,'specs') else page
    for r in c.images.get(page,()):
        ident=int(r['data-event-id'])
        item=c.official_image_edits.items.get(ident) if c.nativeImagePage(page) is not None else None
        if item and item['deleted'] and c.nativeImagePage(page) is not None:continue
        current=item['raw'] if item else r
        entries.append((int(current.get('data-z-index',0)),ident,'image',current))
    if include_text:
        for r in c.native_text.get(background,()):
            ident=r['event_id'];entries.append((z_value(c,ident,int(r.get('z_index',0))),ident,'text',r))
    if include_ink:
        core=c.edits.core if hasattr(c.edits,'core') else c.edits
        edited=core.edited_pages()
        active=not static and c.edits.pending and c.edits.pending['page']==page
        moving=c.selectionMovingOnPage(page)
        owners={source}
        allowed=set(c.edits.order) if hasattr(c.edits,'order') else set(range(c.page_count))
        owners.update(owner for owner,_ in c.spill_ink.get(source,()) if owner in allowed)
        for owner in owners:
            copied=hasattr(c.edits,'specs') and c.edits.specs[owner].get('source') is None
            if copied or owner in edited or (owner==source and (active or moving)):
                strokes=core.state(owner)[0]
                if owner==source:strokes=c.selectedPreview(page,strokes)
                delta=(owner-source)*c.note_height if not c.local_ink else 0
                for index,stroke in enumerate(strokes):
                    ident=stroke.source_id
                    z=z_value(c,ident,NEW_INK_LAYER) if ident is not None else NEW_INK_LAYER
                    shifted=(stroke if not delta else
                             replace(stroke,points=tuple((x,y+delta) for x,y in stroke.points)))
                    entries.append((z,ident if ident is not None else index,'stroke',shifted))
            else:
                paths=c.ink.get(owner,()) if owner==source else [data for o,data in c.spill_ink.get(source,()) if o==owner]
                for data in paths:
                    if defer_svg:
                        # Original XML and its z metadata are immutable. Decode
                        # them on the raster worker, outside paintEvent.
                        entries.append((0,0,'svg-bytes',data))
                        continue
                    el=etree.fromstring(data);ident=int(el.get('data-event-id',0))
                    z=z_value(c,ident,int(el.get('data-z-index',0)))
                    entries.append((z,ident,'svg',el))
        if active and c.edits.pending['kind']=='draw':
            op=c.edits.pending
            z=NEW_INK_LAYER+1
            entries.append((z,0,'stroke',Stroke(
                tuple(op['points']),op['color'],op['width'],
                op.get('opacity',1.0),tool=op.get('tool','pen'),
                geometry_is_smoothed=bool(op.get('capture_smoothing')),tape_pattern=op.get('tape_pattern',3),
                smart_interpolation=bool(op.get('smart_interpolation')))))
    return sorted(entries,key=lambda x:(x[0],x[1]))


def paint_native_scene(c,painter,page,target,include_ink=True,include_text=True,*,entries=None,reveal_tape=False,source_page=None):
    if entries is None:entries=scene(c,page,include_ink,include_text)
    if not entries:return
    nw,nh=c.editPageSize(page)
    source=(source_page if source_page is not None else
            c.edits.page_key(page) if hasattr(c.edits,'page_key') else page)
    painter.save()
    archive=None
    svg_root=None
    def flush_svg():
        nonlocal svg_root
        if svg_root is not None:
            painter.setRenderHint(painter.RenderHint.Antialiasing,True)
            QSvgRenderer(etree.tostring(svg_root)).render(painter,QRectF(0,0,nw,nh))
            svg_root=None
    try:
        painter.setClipRect(target)
        painter.translate(target.x(),target.y());painter.scale(target.width()/nw,target.height()/nh)
        for _,ident,kind,r in entries:
            if kind=='svg' and r.get('data-tool')=='tape':
                flush_svg()
                from rendering.ne_tape_patterns import svg_tape_strokes
                strokes=svg_tape_strokes(etree.tostring(r),0 if c.local_ink else source,c.note_height)
                paint_strokes(painter,strokes,smooth=c.ink_smoothing,reveal_tape=reveal_tape)
                continue
            if kind!='svg':flush_svg()
            if kind=='image':
                if not c.archive_path:continue
                if not hasattr(c,'_native_bitmaps'):
                    c._native_bitmaps=OrderedDict();c._native_bitmap_bytes=0
                asset=(str(c.archive_path),r['data-asset'])
                bitmap=c._native_bitmaps.get(asset)
                if bitmap is None:
                    if archive is None:archive=ZipFile(c.archive_path)
                    bitmap=QImage.fromData(archive.read(r['data-asset']))
                    if bitmap.isNull():raise ValueError('原生图片无法解码')
                    if bitmap.sizeInBytes()<=32*1024*1024:
                        c._native_bitmaps[asset]=bitmap;c._native_bitmap_bytes+=bitmap.sizeInBytes()
                        while c._native_bitmap_bytes>32*1024*1024:
                            _,old=c._native_bitmaps.popitem(last=False);c._native_bitmap_bytes-=old.sizeInBytes()
                elif asset in c._native_bitmaps:c._native_bitmaps.move_to_end(asset)
                rect=QRectF(*(float(r['data-'+k]) for k in ('x','y','width','height')))
                crop=QRectF(*(float(r['data-crop-'+k]) for k in ('x','y','width','height'))) if 'data-crop-width' in r else QRectF(bitmap.rect())
                if r.get('data-note-image')=='1' and crop.width()>0 and crop.height()>0:
                    # Legacy notes contain large slide screenshots. Bilinear
                    # sampling alone aliases small text when zoomed out; Qt's
                    # smooth image scaler performs area filtering first.
                    device_rect=painter.deviceTransform().mapRect(rect)
                    factor=min(1.,max(device_rect.width()/crop.width(),device_rect.height()/crop.height()))
                    if factor<.5:
                        scaled_width=max(1,round(bitmap.width()*factor))
                        scaled_height=max(1,round(bitmap.height()*factor))
                        key=asset+(scaled_width,scaled_height)
                        reduced=c._native_bitmaps.get(key)
                        if reduced is None:
                            reduced=bitmap.scaled(scaled_width,scaled_height,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
                            c._native_bitmaps[key]=reduced;c._native_bitmap_bytes+=reduced.sizeInBytes()
                            while c._native_bitmap_bytes>32*1024*1024:
                                _,old=c._native_bitmaps.popitem(last=False);c._native_bitmap_bytes-=old.sizeInBytes()
                        else:c._native_bitmaps.move_to_end(key)
                        sx=reduced.width()/bitmap.width();sy=reduced.height()/bitmap.height()
                        crop=QRectF(crop.x()*sx,crop.y()*sy,crop.width()*sx,crop.height()*sy)
                        bitmap=reduced
                painter.setRenderHint(painter.RenderHint.Antialiasing,False)
                painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform,True)
                rotation=float(r.get('data-rotation',0))
                flip_x=r.get('data-flip-x')=='1'
                flip_y=r.get('data-flip-y')=='1'
                if rotation and r.get('data-rotation-origin')=='1':
                    painter.save()
                    painter.translate(rect.topLeft());painter.rotate(rotation)
                    painter.drawImage(QRectF(0,0,rect.width(),rect.height()),bitmap,crop)
                    painter.restore()
                elif rotation or flip_x or flip_y:
                    painter.save()
                    painter.translate(rect.center())
                    painter.rotate(rotation)
                    painter.scale(-1 if flip_x else 1,-1 if flip_y else 1)
                    painter.drawImage(QRectF(-rect.width()/2,-rect.height()/2,rect.width(),rect.height()),bitmap,crop)
                    painter.restore()
                else:
                    painter.drawImage(rect,bitmap,crop)
            elif kind=='text':
                painter.setRenderHint(painter.RenderHint.Antialiasing,True)
                painter.setPen(QColor('black'))
                font=QFont(r.get('font_family','Arial'));font.setPixelSize(round(r.get('font_size',15)));painter.setFont(font)
                top,bottom,left,right=r.get('margins',(3.,10.,5.,5.))
                painter.drawText(QRectF(r['x']+left,r['y']+top,r['width']-left-right,r['height']-top-bottom),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,r['text'])
            elif kind=='stroke':
                painter.setRenderHint(painter.RenderHint.Antialiasing,True)
                paint_strokes(painter,[r],smooth=c.ink_smoothing,reveal_tape=reveal_tape)
            else:
                if svg_root is None:
                    top=0 if c.local_ink else source*c.note_height
                    svg_root=etree.Element('svg',xmlns='http://www.w3.org/2000/svg',viewBox=f'0 {top} {nw} {nh}',width=str(nw),height=str(nh))
                r=etree.fromstring(etree.tostring(r))
                if c.ink_smoothing and r.get('data-render-smoothed')!='1':r.set('d',smooth_svg_path(r.get('d',''),float(r.get('stroke-width',2))))
                svg_root.append(r)
        flush_svg()
    finally:
        if archive is not None:archive.close()
        painter.restore()


def paint_content(c,painter,page,target,include_ink=True,*,reveal_tape=False):
    c.paintInsertedImages(painter,page,target,front=False)
    paint_native_scene(c,painter,page,target,include_ink=include_ink,reveal_tape=reveal_tape)
    c.paintInsertedImages(painter,page,target,front=True)
