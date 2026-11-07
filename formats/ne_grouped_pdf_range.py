"""Normalize grouped PDF page ranges to stable one-page CreatePage events.

Official Notability stores a multi-page PDF as one CreatePage with
pageCount=N. The official importer later normalizes it to N CreatePage events,
each with pagesConsumed=1 and a distinct PDF pageOffset. Page deletion and
reordering require that normalized identity graph.
"""
import math
import os
import struct
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import fitz
import formats.ne_ntb as n
from formats.ne_native import Builder, NativeWriteError, read_structure
from import_export.ne_official_pdf import _replace_template
from core.ne_safecommit import replace_with_retry
from core.ne_titles import effective_title, validate_note_title


def _pointer(buf, table, slot):
    field=table.field(slot) if table else 0
    if not field or not buf.valid(field,4):
        return None
    return field+buf.u32(field)


def _inline(data, table, slot, size):
    field=table.field(slot) if table else 0
    return bytes(data[field:field+size]) if field and len(data)>=field+size else None


def _visibility_targets(buf,event):
    payload=n.payload_table(buf,event)
    if payload is None:
        return set()
    targets=set()
    for slot in (0,1):
        field=payload.field(slot)
        if not field or not buf.valid(field,4):
            continue
        vector=field+buf.u32(field)
        if not buf.valid(vector,4):
            continue
        count=buf.u32(vector)
        if count>200_000 or not buf.valid(vector+4,count*8):
            continue
        for index in range(count):
            actor,target=struct.unpack_from(
                '<2I',buf.data,vector+4+index*8)
            if actor==0:
                targets.add(target)
    return targets


def _drop_hidden_template_blocks(data):
    """Remove the bundled template's finally-hidden CreateBlock history.

    The official template uses one placeholder block and three visibility
    operations to prove its event graph. The block is not visible document
    content, but retaining it prevents the checked per-page writer from
    reopening the generated PDF note. Only blocks whose final visibility is
    hidden and whose type-25 references are wholly template-owned are removed.
    """
    buf,root,events=read_structure(data)
    hidden=n.hidden_image_event_ids(events,buf)
    if not hidden:
        return data
    hidden_blocks=set()
    for _,event in events:
        kind=event.field(4)
        ref=event.field(0)
        if (kind and ref and buf.valid(kind,1) and buf.valid(ref,8)
                and buf.data[kind]==22 and buf.u32(ref)==0
                and buf.u32(ref+4) in hidden):
            hidden_blocks.add(buf.u32(ref+4))
    if not hidden_blocks or hidden_blocks!=hidden:
        return data

    remove=set(hidden_blocks)
    for _,event in events:
        kind=event.field(4)
        if kind and buf.valid(kind,1) and buf.data[kind]==25:
            targets=_visibility_targets(buf,event)
            if targets and targets<=hidden_blocks:
                remove.add(buf.u32(event.field(0)+4))
    kept=[(offset,event) for offset,event in events
          if buf.u32(event.field(0)+4) not in remove]
    builder=Builder()
    builder.data=bytearray(data[:root.offset+root.osize])
    vector=builder.vector(b'\0'*(4*len(kept)),len(kept))
    builder.uoffset(root.field(6),vector)
    builder.align(8)
    shift=len(builder.data)
    builder.add(data)
    for slot in (3,5):
        field=root.field(slot)
        if field:
            builder.uoffset(field,field+buf.u32(field)+shift)
    for index,(offset,_) in enumerate(kept):
        builder.uoffset(vector+4+4*index,offset+shift)
    return bytes(builder.data)


def normalize_grouped_pdf_pages(data, *, validate=True, crop_sizes=None,
                                page_sizes=None):
    """Return a per-page bundle, or the original bytes when not applicable."""
    buf,root,events=read_structure(data)
    if not root.field(6):
        return data
    group_entry=None
    for event_index,(_,event) in enumerate(events):
        kind=event.field(4)
        if not kind or data[kind]!=3:
            continue
        payload=n.payload_table(buf,event)
        if payload is None:
            continue
        count=n.field_u32(buf,payload,2) or 1
        background=n.child_table(buf,payload,1)
        pdf=n.child_table(buf,background,1)
        asset=n.child_table(buf,pdf,0)
        if (count>1 or (crop_sizes is not None or page_sizes is not None) and count==1) and pdf and asset:
            group_entry=(event_index,event,payload,count,background,pdf,asset)
            break
    if group_entry is None:
        return data
    _,group_event,group_payload,page_count,background,pdf,asset=group_entry
    if not 1<=page_count<=100_000:
        raise NativeWriteError('分组 PDF 页数超出安全范围')
    normalized_sizes=None
    if page_sizes is not None:
        from core.ne_page_geometry import page_size
        if len(page_sizes)!=page_count:
            raise NativeWriteError('逐页尺寸数量与页面计划不一致')
        try:
            normalized_sizes=[struct.pack('<2f',*page_size(*size))
                              for size in page_sizes]
        except ValueError as exc:
            raise NativeWriteError(str(exc)) from exc
    if crop_sizes is not None:
        if len(crop_sizes)!=page_count:
            raise NativeWriteError('逐页裁剪框数量与页面计划不一致')
        normalized_crops=[]
        for width,height in crop_sizes:
            if not (math.isfinite(width) and math.isfinite(height)
                    and 0<width<=10000 and 0<height<=10000):
                raise NativeWriteError('逐页裁剪框尺寸无效')
            normalized_crops.append(struct.pack('<2f',width,height))
    metadata_field=pdf.field(0)
    metadata=_pointer(buf,pdf,0)
    if not metadata:
        raise NativeWriteError('分组 PDF 缺少素材元数据')
    size=_inline(data,background,3,8)
    if not size:
        raise NativeWriteError('分组 PDF 缺少页面尺寸')
    total_pages=n.field_u32(buf,pdf,2) or 1
    page_offset=n.field_u32(buf,pdf,4) or 0
    if total_pages<page_offset+page_count:
        raise NativeWriteError('PDF 总页数小于页面消费数')

    max_ident=max(buf.u32(event.field(0)+4) for _,event in events)
    new_page_ids=[buf.u32(group_event.field(0)+4)]
    new_page_ids.extend(range(max_ident+1,max_ident+page_count))
    if len(set(new_page_ids))!=len(new_page_ids):
        raise NativeWriteError('规范化页面编号冲突')
    page_id_map={(group_id,index):new_page_ids[index]
                 for group_id,index in
                 ((buf.u32(group_event.field(0)+4),i) for i in range(page_count))}

    working=bytearray(data)
    # Remap native ink/image references from (group, sheet index) to
    # (normalized page event, 0).
    for _,event in events:
        kind_pos=event.field(4)
        if not kind_pos or not buf.valid(kind_pos,1):
            continue
        kind=data[kind_pos]
        if kind in (15,22):
            payload=n.payload_table(buf,event)
            slot=0 if kind==15 else 2
            ref=payload.field(slot) if payload else 0
            if not ref or not buf.valid(ref,12):
                raise NativeWriteError('分组 PDF 内容缺少完整页面引用')
            actor,group_id,index=struct.unpack_from('<3I',data,ref)
            target=page_id_map.get((group_id,index))
            if target is not None:
                struct.pack_into('<3I',working,ref,actor,target,0)
        elif kind==3 and event is not group_event:
            payload=n.payload_table(buf,event)
            location=payload.field(0) if payload else 0
            if not location or not buf.valid(location,12):
                continue
            actor,group_id,index=struct.unpack_from('<3I',data,location)
            target=page_id_map.get((group_id,index))
            if target is not None:
                struct.pack_into('<3I',working,location,actor,target,0)

    builder=Builder()
    builder.data=bytearray(working[:root.offset+root.osize])
    vector_count=len(events)+page_count-1
    event_vector=builder.vector(b'\0'*(4*vector_count),vector_count)
    builder.uoffset(root.field(6),event_vector)
    fixups=[]
    new_page_events=[]
    original_location=_inline(data,group_payload,0,12)
    for index in range(page_count):
        page_id=new_page_ids[index]
        location=(original_location if index==0 else
                  struct.pack('<3I',0,new_page_ids[index-1],0))

        event,event_refs=builder.table(6,{
            0:(struct.pack('<2I',0,page_id),4),
            1:(_inline(data,group_event,1,8) or struct.pack('<Q',0),8),
            4:(b'\x03',1),
            5:(b'\0'*4,4),
        })

        payload_fields={1:(b'\0'*4,4)}
        if location is not None:
            payload_fields[0]=(location,4)
        payload,payload_refs=builder.table(2,payload_fields)
        builder.uoffset(event_refs[5],payload)

        # FlatBuffers offsets are forward-only: create the parent background
        # before the PDFAsset it references.
        bg_fields={1:(b'\0'*4,4),
                   3:(normalized_sizes[index] if normalized_sizes else size,4)}
        paper=_pointer(buf,background,0)
        if paper is not None:
            bg_fields[0]=(b'\0'*4,4)
        rotation=_inline(data,background,2,4)
        if rotation is not None:
            bg_fields[2]=(rotation,4)
        margins=_inline(data,background,4,16)
        if margins is not None:
            bg_fields[4]=(margins,4)
        new_bg,bg_refs=builder.table(5,bg_fields)
        if paper is not None:
            fixups.append((bg_refs[0],paper))

        # Cloning PDFAsset keeps the same source asset but makes each page a
        # one-page consumer, matching the official importer's normalized graph.
        pdf_fields={0:(b'\0'*4,4)}
        layout=_inline(data,pdf,1,1)
        if layout is not None:
            pdf_fields[1]=(layout,1)
        # Official PDFAsset: slot 2 = totalPageCount, slot 3 = pagesConsumed.
        # A grouped source hides a swapped-field bug because both equal N.
        pdf_fields[2]=(struct.pack('<I',total_pages),4)
        pdf_fields[3]=(struct.pack('<I',1),4)
        pdf_fields[4]=(struct.pack('<I',page_offset+index),4)
        crop=_pointer(buf,pdf,5)
        if crop is None:
            raise NativeWriteError('分组 PDF 缺少逐页裁剪框')
        if not buf.valid(crop,4) or not buf.u32(crop):
            raise NativeWriteError('分组 PDF 缺少逐页裁剪框')
        source_crop_count=buf.u32(crop)
        pdf_fields[5]=(b'\0'*4,4)
        new_pdf,pdf_refs=builder.table(6,pdf_fields)
        builder.uoffset(bg_refs[1],new_pdf)
        fixups.append((pdf_refs[0],metadata))
        # Use the generated PDF page's actual box when available. The bundled
        # official three-page template carries rounded 595x842 boxes, while the
        # real PDF can be 594.96x840.96; importers use this box to place the
        # background relative to the page-local ink coordinate system.
        if crop_sizes is not None:
            crop_bytes=normalized_crops[index]
        else:
            crop_entry=crop+4+min(index,source_crop_count-1)*8
            if not buf.valid(crop_entry,8):
                raise NativeWriteError('分组 PDF 裁剪框数据不完整')
            crop_bytes=bytes(data[crop_entry:crop_entry+8])
        one_crop=builder.vector(crop_bytes,1)
        builder.uoffset(pdf_refs[5],one_crop)

        builder.uoffset(payload_refs[1],new_bg)
        new_page_events.append(event)

    builder.align(8)
    shift=len(builder.data)
    builder.add(working)
    for slot in (3,5):
        field=root.field(slot)
        if field:
            builder.uoffset(field,field+buf.u32(field)+shift)
    for field,target in fixups:
        builder.uoffset(field,target+shift)

    group_index=group_entry[0]
    output_index=0
    for index,(offset,_) in enumerate(events):
        if index==group_index:
            for new_event in new_page_events:
                builder.uoffset(event_vector+4+4*output_index,new_event)
                output_index+=1
        else:
            builder.uoffset(event_vector+4+4*output_index,offset+shift)
            output_index+=1
    if output_index!=vector_count:
        raise NativeWriteError('规范化 PDF 页面事件数量不一致')
    result=bytes(builder.data)
    verify,verify_root,written=read_structure(result)
    records=n.page_records(written,verify)
    expected_pages=len(n.page_records(events,buf))-1+page_count
    if len(records)!=expected_pages:
        raise NativeWriteError('规范化 PDF 页面回读数量不一致')
    if validate:
        from formats.ne_pagewrite import validate_page_structure
        validate_page_structure(result)
    return result


def grouped_pdf_page_management_info(bundle, path):
    """Validate the narrow pure grouped-PDF-plus-ink layout.

    Page editing materializes a new PDF, so independent images and unknown
    event kinds are excluded rather than silently flattened or discarded.
    """
    buf,root,events=read_structure(bundle)
    layout=n.grouped_pdf_page_records(events,buf)
    if not layout:
        return None
    kinds=Counter(bundle[event.field(4)] for _,event in events if event.field(4))
    if any(kind not in (1,3,4,7,15,25) for kind in kinds):
        return None
    with ZipFile(path) as archive:
        pdfs=[name for name in archive.namelist()
              if name.startswith('assets/') and name.lower().endswith('.pdf')]
        unsupported=[name for name in archive.namelist()
                     if name.startswith('assets/') and name not in pdfs]
        if len(pdfs)!=1 or unsupported:
            return None
        if archive.testzip() is not None:
            return None
    return dict(page_count=len(layout['records']),pdf=pdfs[0])


def mixed_grouped_pdf_page_management_info(bundle, path):
    """Validate schema-18 PDF ranges mixed with blank pages and images."""
    buf,root,events=read_structure(bundle)
    version=buf.u16(root.field(7)) if root.field(7) else None
    if version!=18 or root.slots!=8 or root.field(2):
        return None
    layout=n.mixed_grouped_pdf_page_records(events,buf)
    if not layout or not 1<=len(layout['records'])<=10000:
        return None
    kinds=Counter(bundle[event.field(4)] for _,event in events
                  if event.field(4))
    allowed={1,3,7,15,22,23,24,25}
    if not kinds or any(kind not in allowed for kind in kinds):
        return None
    assets=layout.get('assets') or []
    if len(assets)!=1:
        return None
    with ZipFile(path) as archive:
        pdfs=[name for name in archive.namelist()
              if name.startswith('assets/') and name.lower().endswith('.pdf')]
        if len(pdfs)!=1 or archive.testzip() is not None:
            return None
        if pdfs[0].rsplit('/',1)[-1].split('.')[0]!=assets[0]:
            return None
        with fitz.open(stream=archive.read(pdfs[0]),filetype='pdf') as pdf:
            if pdf.needs_pass or any(
                    record.get('pdf') is not None
                    and not 0<=record['pdf']<pdf.page_count
                    for record in layout['records']):
                return None
    return dict(page_count=len(layout['records']),pdf=pdfs[0],
                mixed=True,layout=layout)


def _draw_blank(page,kind):
    from pages.ne_page_policy import resolve_paper_kind, DEFAULT_PITCH_PT
    kind=resolve_paper_kind(kind,'blank')
    width,height=page.rect.width,page.rect.height
    if kind=='ruled':
        y=DEFAULT_PITCH_PT
        while y<height:
            page.draw_line((36.,y),(width-36.,y),
                           color=(.64,.72,.83),width=.5)
            y+=DEFAULT_PITCH_PT
    elif kind=='dotted':
        x0=(width-math.floor(width/DEFAULT_PITCH_PT)*DEFAULT_PITCH_PT)/2
        x=x0
        while x<width:
            y=DEFAULT_PITCH_PT
            while y<height:
                page.draw_circle((x,y),1.,color=(.64,.72,.83),
                                 fill=(.64,.72,.83),width=0)
                y+=DEFAULT_PITCH_PT
            x+=DEFAULT_PITCH_PT


def _patch_grouped_page_size(bundle,width,height):
    data=bytearray(bundle)
    buf,root,events=read_structure(data)
    for _,event in events:
        if not event.field(4) or data[event.field(4)]!=3:
            continue
        payload=n.payload_table(buf,event)
        background=n.child_table(buf,payload,1)
        size=background.field(3) if background else 0
        if size and buf.valid(size,8):
            struct.pack_into('<2f',data,size,width,height)
    return bytes(data)


def _preserve_document_metadata(bundle, source):
    """Keep the source's page layout, margins and title-display settings.

    PageBackground.size alone does not determine the official PDF transform:
    the document's default width also participates. The PDF template's A4
    settings must never replace a source notebook's 610-point coordinate frame.
    Copy complete SetMetadata payloads, including fields we do not interpret.
    """
    def event_kind(data,event):
        field=event.field(4)
        return data[field] if field and field<len(data) else None

    buf,root,events=read_structure(bundle)
    src,_,source_events=read_structure(source)
    metadata=[event for _,event in source_events
              if event_kind(source,event)==1]
    if not metadata:
        raise NativeWriteError('源笔记缺少文档设置')
    kept=[(offset,event) for offset,event in events
          if event_kind(bundle,event)!=1]
    builder=Builder()
    builder.data=bytearray(bundle[:root.offset+root.osize])
    vector=builder.vector(b'\0'*(4*(len(metadata)+len(kept))),
                          len(metadata)+len(kept))
    builder.uoffset(root.field(6),vector)
    next_id=max(buf.u32(event.field(0)+4) for _,event in events)+1
    fixups=[]
    for index,event in enumerate(metadata):
        payload=_pointer(src,event,5)
        if payload is None:
            raise NativeWriteError('源笔记文档设置不完整')
        fields={0:(struct.pack('<2I',0,next_id+index),4),
                4:(b'\x01',1),5:(b'\0'*4,4)}
        for slot,size in ((1,8),(2,8),(3,8)):
            value=_inline(source,event,slot,size)
            if value is not None:fields[slot]=(value,8)
        offset,refs=builder.table(6,fields)
        builder.uoffset(vector+4+4*index,offset)
        fixups.append((refs[5],payload))
    builder.align(8)
    source_shift=len(builder.data)
    builder.add(source)
    for field,target in fixups:builder.uoffset(field,target+source_shift)
    builder.align(8)
    shift=len(builder.data)
    builder.add(bundle)
    for slot in (3,5):
        field=root.field(slot)
        if field:builder.uoffset(field,field+buf.u32(field)+shift)
    for index,(offset,_) in enumerate(kept,len(metadata)):
        builder.uoffset(vector+4+4*index,offset+shift)
    return bytes(builder.data)


def materialize_grouped_pdf_pages(source,target,plan,*,expected_bundle=None,
                                  effective_target=None,progress=lambda _:None,
                                  cancelled=lambda:False):
    """Atomically rebuild a grouped PDF in the page-plan order."""
    source,target=Path(source).resolve(),Path(target).resolve()
    effective_target=(Path(effective_target) if effective_target is not None
                      else target)
    if cancelled():
        raise NativeWriteError('页面保存已取消')
    if not isinstance(plan,(list,tuple)) or not 1<=len(plan)<=10000:
        raise NativeWriteError('页面计划为空或页数超出范围')
    progress(2)
    with ZipFile(source) as archive:
        current=archive.read('noteBundle')
        if expected_bundle is not None and current!=expected_bundle:
            raise NativeWriteError('源笔记已在磁盘被其他程序修改，请重新打开')
        info=grouped_pdf_page_management_info(current,source)
        mixed=bool(info and info.get('mixed'))
        if info is None:
            info=mixed_grouped_pdf_page_management_info(current,source)
            mixed=info is not None
        if info is None:
            # Older official-template imports contain a placeholder block.
            # Only the legacy validator may establish that it is template-owned.
            from formats.ne_grouped_pagesave import grouped_info
            grouped_info(archive,current)
            info={'pdf':next(name for name in archive.namelist()
                             if name.startswith('assets/')
                             and name.lower().endswith('.pdf'))}
        source_pdf=archive.read(info['pdf'])
        version=archive.read('version')
        manifest=archive.read('manifest.json')
        extras={name:archive.read(name) for name in archive.namelist()
                if name not in ('version','noteBundle','manifest.json',
                                'thumbnail.png','index/ios/PDFIndex.fb',
                                info['pdf'])}
        image_assets={}
        if mixed:
            names=set(archive.namelist())
            for spec in plan:
                if (not isinstance(spec,dict)
                        or (spec.get('viewer_images') or {}).get('images')):
                    raise NativeWriteError('混合 PDF 页面包含尚未适配的 Viewer 独立图片')
                for image in spec.get('images',()):
                    name=image.get('data-asset')
                    if name not in names:
                        raise NativeWriteError('混合 PDF 页面引用的图片素材缺失')
                    image_assets[name]=archive.read(name)
        source_stat=source.stat()
    progress(10)
    from core.ne_page_geometry import page_size, thumbnail_scale
    src_buf,_,src_events=read_structure(current)
    document_size=n.document_page_size(src_events,src_buf)
    if not document_size:
        raise NativeWriteError('源笔记缺少有效的文档坐标尺寸')
    coordinate_width=document_size[0]
    sizes=[]
    scales=[]
    for index,spec in enumerate(plan):
        if not isinstance(spec,dict):
            raise NativeWriteError(f'第 {index+1} 页计划无效')
        if spec.get('images') and not mixed:
            raise NativeWriteError('PDF 分组页面重建暂不支持独立图片')
        try:
            width,height=page_size(spec.get('width',0),spec.get('height',0))
            scale=coordinate_width/width
            sizes.append(page_size(coordinate_width,height*scale))
            scales.append(scale)
        except ValueError as exc:
            raise NativeWriteError(f'第 {index+1} 页尺寸无效：{exc}') from exc

    strokes=[]
    crop_sizes=[]
    with fitz.open(stream=source_pdf,filetype='pdf') as pdf:
        if pdf.needs_pass:
            raise NativeWriteError('加密 PDF 不支持页面管理')
        with fitz.open() as output:
            for new_page,spec in enumerate(plan):
                if cancelled():
                    raise NativeWriteError('页面保存已取消')
                pdf_index=spec.get('pdf')
                if pdf_index is not None:
                    if type(pdf_index) is not int or not 0<=pdf_index<pdf.page_count:
                        raise NativeWriteError(f'第 {new_page+1} 页 PDF 引用无效')
                    source_page=pdf[pdf_index]
                    crop_sizes.append((source_page.rect.width,
                                       source_page.rect.height))
                    output.insert_pdf(pdf,from_page=pdf_index,to_page=pdf_index,
                                      links=True,annots=True)
                else:
                    width,height=sizes[new_page]
                    page=output.new_page(width=width,height=height)
                    _draw_blank(page,spec.get('background_kind'))
                    crop_sizes.append((width,height))
                if mixed and spec.get('images'):
                    from PyQt6.QtCore import QBuffer,QByteArray,QIODevice,QRect
                    from PyQt6.QtGui import QImage
                    page=output[-1]
                    note_width=float(spec.get('width',coordinate_width))
                    note_height=float(spec.get('height',document_size[1]))
                    sx=page.rect.width/note_width
                    sy=page.rect.height/note_height
                    for image in sorted(spec['images'],key=lambda item:(
                            int(item.get('data-z-index',0)),
                            int(item.get('data-event-id',0)))):
                        if (abs(float(image.get('data-rotation',0)))>1e-6
                                or image.get('data-flip-x')=='1'
                                or image.get('data-flip-y')=='1'):
                            raise NativeWriteError(
                                '混合 PDF 页面包含尚未适配的图片旋转或翻转')
                        bitmap=QImage.fromData(image_assets[image['data-asset']])
                        if bitmap.isNull():
                            raise NativeWriteError('混合 PDF 页面图片无法解码')
                        if 'data-crop-width' in image:
                            crop=QRect(
                                round(float(image.get('data-crop-x',0))),
                                round(float(image.get('data-crop-y',0))),
                                round(float(image['data-crop-width'])),
                                round(float(image['data-crop-height'])))
                            crop=crop.intersected(bitmap.rect())
                            if crop.isEmpty():
                                raise NativeWriteError('混合 PDF 页面图片裁剪范围为空')
                            bitmap=bitmap.copy(crop)
                        encoded=QByteArray()
                        buffer=QBuffer(encoded)
                        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                            raise NativeWriteError('无法编码混合 PDF 页面图片')
                        if not bitmap.save(buffer,'PNG'):
                            raise NativeWriteError('混合 PDF 页面图片编码失败')
                        buffer.close()
                        x=float(image['data-x'])*sx
                        y=float(image['data-y'])*sy
                        width=float(image['data-width'])*sx
                        height=float(image['data-height'])*sy
                        page.insert_image(
                            fitz.Rect(x,y,x+width,y+height),
                            stream=bytes(encoded),keep_proportion=False,
                            overlay=True)
                for stroke in spec.get('strokes',()):
                    scale=scales[new_page]
                    strokes.append((new_page,replace(stroke,source_id=None,
                        points=tuple((x*scale,y*scale) for x,y in stroke.points),
                        width=stroke.width*scale)))
                if new_page%8==0:
                    progress(12+int(38*(new_page+1)/len(plan)))
            generated=output.tobytes(garbage=4,deflate=True)
            scale=thumbnail_scale(output[0].rect.width,output[0].rect.height)
            thumbnail=output[0].get_pixmap(
                matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')
    progress(52)
    old_title=effective_title(current)
    title=(validate_note_title(effective_target.stem)
           if effective_target.resolve()!=source else old_title)
    bundle,index,digest=_replace_template(
        generated,len(plan),title,allow_single_page=True,template_pages=3,
        max_pages=10000)
    # The two-page template contains a visible example block. Rebuilding a
    # one/two-page note from it would introduce foreign content and fail the
    # per-page validator. The three-page template's placeholder is finally
    # hidden and can be removed with the checked visibility replay below.
    # The official importer normalizes a multi-page PDF range into one
    # CreatePage event per visible page. Materializing that canonical graph
    # before adding ink avoids the importer's range-rebase path, which changes
    # page-local handwriting origins on some grouped PDFs.
    bundle=_drop_hidden_template_blocks(bundle)
    bundle=normalize_grouped_pdf_pages(
        bundle,validate=True,crop_sizes=crop_sizes,page_sizes=sizes)
    bundle=_preserve_document_metadata(bundle,current)
    from formats.ne_native import build_bundle
    bundle=build_bundle(bundle,set(),strokes,
                        new_title=title if title!=old_title else None)
    progress(75)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.nv-grouped-pdf-page-',suffix='.tmp',
                            dir=target.parent)
    os.close(fd)
    new_asset=f'assets/{digest}.pdf'
    try:
        with ZipFile(tmp,'w',allowZip64=True) as archive:
            archive.writestr('version',version,compress_type=ZIP_DEFLATED)
            archive.writestr('noteBundle',bundle,compress_type=ZIP_STORED)
            archive.writestr(new_asset,generated,compress_type=ZIP_STORED)
            archive.writestr('manifest.json',manifest,
                             compress_type=ZIP_DEFLATED)
            archive.writestr('index/ios/PDFIndex.fb',index,
                             compress_type=ZIP_DEFLATED)
            archive.writestr('thumbnail.png',thumbnail,
                             compress_type=ZIP_DEFLATED)
            for name,payload in extras.items():
                archive.writestr(name,payload,compress_type=ZIP_DEFLATED)
        with ZipFile(tmp) as archive:
            if archive.testzip() is not None:
                raise NativeWriteError('页面重建 ZIP 校验失败')
            rebuilt=archive.read('noteBundle')
            verify,root,events=read_structure(rebuilt)
            source_buf,_,source_events=read_structure(current)
            if n.document_page_size(events,verify)!=n.document_page_size(source_events,source_buf):
                raise NativeWriteError('页面重建改变了文档坐标尺寸，已停止保存')
            layout=n.grouped_pdf_page_records(events,verify)
            if not layout or len(layout['records'])!=len(plan):
                raise NativeWriteError('页面重建后的页数校验失败')
            if any(abs(record.get('width',0)-width)>.01 or
                   abs(record.get('height',0)-height)>.01
                   for record,(width,height) in zip(layout['records'],sizes)):
                raise NativeWriteError('页面重建改变了页面坐标尺寸，已停止保存')
            if sum(rebuilt[e.field(4)]==15 for _,e in events)!=len(strokes):
                raise NativeWriteError('页面重建后的笔迹数量校验失败')
        if source.stat().st_size!=source_stat.st_size or source.stat().st_mtime_ns!=source_stat.st_mtime_ns:
            raise NativeWriteError('保存期间源文件发生外部修改')
        replace_with_retry(tmp,target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    progress(100)
    return bundle
