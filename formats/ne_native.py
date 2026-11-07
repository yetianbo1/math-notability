"""Preserving FlatBuffers writer for checked schema-18 and modern NTB layouts.

Old tables, unknown fields, and assets are retained verbatim. New type-25
visibility and type-15 stroke events are appended to a new event vector.
Notability application import, including schema-19/20 edit-event compatibility,
remains an external validation requirement.
"""
import copy,math,os,struct,tempfile,time
from pathlib import Path
from zipfile import ZipFile
from core.ne_safecommit import replace_with_retry
import formats.ne_ntb as n
from core.ne_save_work import checked_once

class NativeWriteError(ValueError):pass

class Builder:
    def __init__(self):self.data=bytearray()
    def align(self,alignment=8):
        self.data.extend(b'\0'*((-len(self.data))%alignment))
    def add(self,data):
        p=len(self.data);self.data.extend(data);return p
    def uoffset(self,field,target):
        if not 0<=target-field<2**32:raise NativeWriteError('FlatBuffers 引用超出范围')
        struct.pack_into('<I',self.data,field,target-field)
    def table(self,slots,fields):
        # fields: slot -> (raw bytes, alignment); uoffsets are patched later.
        offsets={};body=bytearray(b'\0'*4)
        for slot,(raw,alignment) in sorted(fields.items()):
            body.extend(b'\0'*((-len(body))%alignment));offsets[slot]=len(body);body.extend(raw)
        body.extend(b'\0'*((-len(body))%8))
        self.align(2)
        vt=self.add(struct.pack('<HH',4+2*slots,len(body))+b''.join(struct.pack('<H',offsets.get(s,0)) for s in range(slots)))
        self.align(8);start=self.add(body);struct.pack_into('<i',self.data,start,start-vt)
        return start,{s:start+off for s,off in offsets.items()}
    def vector(self,data,count,alignment=4):
        # Elements begin after the u32 length, so align the element start.
        while (len(self.data)+4)%alignment:self.data.append(0)
        return self.add(struct.pack('<I',count)+data)


@checked_once
def read_structure(data):
    buf=n.Buffer(data)
    root=n.table_at(buf,buf.u32(0)) if buf.valid(0,4) else None
    if not root:raise NativeWriteError('无效的 noteBundle 根结构')
    events=n.table_vector(buf,root,6)
    if not events:raise NativeWriteError('此 NTB 没有可识别的事件向量')
    return buf,root,events


def _canonicalize_schema18_paper(data, group, count):
    """Collapse range plus marker pages into one checked N-page range."""
    buf,root,events=read_structure(data)
    kept=[]
    count_field=None
    ranges=0
    for offset,event in events:
        kind=event.field(4)
        if not kind or not buf.valid(kind,1):
            return data
        if buf.data[kind]!=3:
            kept.append((offset,event))
            continue
        payload=n.payload_table(buf,event)
        page_count=(n.field_u32(buf,payload,2)
                    if payload is not None else None)
        if page_count is None:
            continue
        ranges+=1
        count_field=payload.field(2)
        kept.append((offset,event))
    if ranges!=1 or count_field is None:
        raise NativeWriteError('schema-18 纸张范围无法安全规范化')
    for _,event in kept:
        kind=buf.data[event.field(4)]
        if kind not in (15,18,22):
            continue
        payload=n.payload_table(buf,event)
        field=payload.field(2 if kind==22 else 0) if payload else 0
        if not field or not buf.valid(field,12):
            raise NativeWriteError('纸张内容缺少完整页面引用')
        actor,owner,index=struct.unpack_from('<3I',data,field)
        if actor or owner!=group or index>=count:
            raise NativeWriteError('纸张内容的页引用不属于当前页面范围')
    builder=Builder()
    builder.data=bytearray(data[:root.offset+root.osize])
    vector=builder.vector(b'\0'*(4*len(kept)),len(kept))
    builder.uoffset(root.field(6),vector)
    builder.align(8)
    shift=len(builder.data)
    builder.add(data)
    for slot in (3,5):
        field=root.field(slot)
        if field:builder.uoffset(field,field+buf.u32(field)+shift)
    for index,(offset,_) in enumerate(kept):
        builder.uoffset(vector+4+index*4,offset+shift)
    struct.pack_into('<I',builder.data,count_field+shift,count)
    result=bytes(builder.data)
    verify,_,written=read_structure(result)
    if len(written)!=len(kept):
        raise NativeWriteError('schema-18 纸张范围规范化校验失败')
    return result


def point_blob(points):
    """Encode linear cubics in the sample's float32 stroke command format."""
    if not points:raise NativeWriteError('空笔迹')
    if len(points)==1:points=points+(points[0],)
    if len(points)>65535:raise NativeWriteError('单条笔迹采样点过多')
    origin=points[0]
    output=bytearray(struct.pack('<BHBII',1,len(points),3,0,0))
    for a,b in zip(points,points[1:]):
        coords=(a[0]+(b[0]-a[0])/3-origin[0],a[1]+(b[1]-a[1])/3-origin[1],
                a[0]+2*(b[0]-a[0])/3-origin[0],a[1]+2*(b[1]-a[1])/3-origin[1],b[0]-origin[0],b[1]-origin[1])
        output.extend(struct.pack('<eeBBB6f',1.,1.,255,0,0,*coords))
    output.extend(struct.pack('<eeBB',1.,1.,255,0))
    return bytes(output)


def curve_blob(stroke):
    """Encode the displayed captured cubics, preserving them after reopening."""
    path=stroke.render_path;origin=stroke.points[0];records=[];index=0
    while index<path.elementCount():
        element=path.elementAt(index)
        if element.isMoveTo():command=3;vertices=(element,);index+=1
        elif element.isLineTo():command=2;vertices=(element,);index+=1
        else:
            if not element.isCurveTo() or index+2>=path.elementCount():
                raise NativeWriteError('笔迹曲线结构无效')
            command=0;vertices=tuple(path.elementAt(index+k) for k in range(3));index+=3
        coords=tuple(v for p in vertices for v in (p.x-origin[0],p.y-origin[1]))
        records.append(bytes((command,))+struct.pack('<'+'f'*len(coords),*coords)+struct.pack('<eeBB',1.,1.,255,0))
    if not 2<=len(records)<=65535:return point_blob(stroke.points)
    return struct.pack('<BH',1,len(records))+b''.join(records)


def outline_blob(points,origin):
    """Official nonattributed moveto/line encoding for a filled custom path."""
    if not points or len(points)>65535:raise NativeWriteError('笔迹轮廓采样点数量无效')
    return struct.pack('<BH',1,len(points))+b''.join(
        struct.pack('<B2f',7 if index==0 else 6,x-origin[0],y-origin[1])
        for index,(x,y) in enumerate(points))


def build_bundle(data,hidden,added,*,new_title=None,native_image_lock_events=None,native_image_deletions=None,native_image_layer_changes=None):
    from formats.ne_bundle_identity import repair_generated_namespaces
    data=repair_generated_namespaces(data)
    native_image_layer_changes=native_image_layer_changes or {}
    native_image_lock_events=native_image_lock_events or {}
    native_image_deletions=set(native_image_deletions or ())
    if not hidden and not added and new_title is None and not native_image_lock_events and not native_image_deletions and not native_image_layer_changes:return data
    buf,root,events=read_structure(data)
    version = buf.u16(root.field(7)) if root.field(7) and buf.valid(root.field(7), 2) else None
    # V18 has a legacy grouped-page layout. For newer versions, validate
    # actual page/event structure, not a hard-coded 19/20 version whitelist.
    # Unknown fields, event types and author layouts still abort in the
    # structural validator. This is NOT a promise of every future schema.
    if root.slots != 8 or root.field(2) or version is None or version < 18:
        raise NativeWriteError(f'NTB 结构版本 {version} 尚未验证笔迹写入，已停止保存以保护原数据')
    modern = version >= 19
    records = n.page_records(events,buf) if modern else []
    grouped_pdf_layout=(n.grouped_pdf_page_records(events,buf)
                        or n.mixed_grouped_pdf_page_records(events,buf))
    native_paper_layout=n.native_paper_page_records(events,buf,version)
    from formats.ne_paper_graph import paper_graph
    paper_layout=paper_graph(events,buf,version)
    group_count=(len(paper_layout['records']) if paper_layout else
                 len(grouped_pdf_layout['records'])
                 if grouped_pdf_layout else
                 len(native_paper_layout['records'])
                 if native_paper_layout else
                 n.document_page_count(events,buf) if modern else 0)
    # Imported PDF notes often use one type-3 event for *all* PDF pages.
    # Only the independently validated per-page layout may be rewritten for
    # structural edits; grouped PDFs can receive nonstructural pen events.
    grouped_pdf=grouped_pdf_layout is not None
    # Official non-PDF notes can group many numbered paper sheets into one
    # type-3 range.  Page refs still use (0,group_id,zero_based_index).
    # This is a non-destructive ink-only path, NEVER a page-rewrite approval.
    paper_signature=n.native_paper_range_signature(events,buf,version)
    grouped_paper=bool(native_paper_layout) or (
        modern and bool(records) and
        not any(r.get('pdf') is not None for r in records) and
        (group_count>len(records) or paper_signature is not None))
    grouped_pages=grouped_pdf or grouped_paper or bool(paper_layout)
    if version==18 and grouped_paper and native_paper_layout:
        data=_canonicalize_schema18_paper(
            data,native_paper_layout['group'],group_count)
        buf,root,events=read_structure(data)
    # IMPORTANT: Native *ink* saving is append-only. It does not remove,
    # reorder, recreate, or reinterpret any existing page or image event.
    # In contrast, validate_page_structure() is a strict validator for the
    # destructive page-rewrite pipeline. Using it here previously rejected
    # perfectly readable OFFICIAL NTBs containing type-4/14 page adjustments,
    # independent images, or unfamiliar event kinds. Keep both gates distinct.
    # The PDF-to-new-NTB limits belong to import_export.ne_official_pdf, never to this path.
    ev_field = root.field(6)
    if not ev_field or not buf.valid(ev_field,4):
        raise NativeWriteError('NTB 事件表不完整，已停止笔迹写入')
    ev_vector = ev_field + buf.u32(ev_field)
    if not buf.valid(ev_vector,4) or buf.u32(ev_vector) != len(events):
        raise NativeWriteError('NTB 存在无法完整读取的事件，已停止保存以避免丢失数据')
    page_ids = ([(native_paper_layout['group']
                  if native_paper_layout else paper_signature[0])]
                if grouped_paper and (native_paper_layout or paper_signature) else
                [r['id'] for r in records] if modern else [])
    if modern and (not page_ids or len(set(page_ids)) != len(page_ids) and not grouped_pages):
        raise NativeWriteError('NTB 页面标识不完整或重复，无法安全关联新笔迹')
    ids={};page_ranges=[];last_time=0
    for off,event in events:
        ref=event.field(0);typ=event.field(4)
        if not ref or not typ or not buf.valid(ref,8):raise NativeWriteError('事件缺少标识')
        actor,sequence=struct.unpack_from('<2I',data,ref)
        if n.is_asset_cloud_persisted(buf,event):continue
        if actor!=0 or sequence in ids:raise NativeWriteError('多作者或重复事件标识尚未验证写入')
        ids[sequence]=(off,event)
        stamp=event.field(1)
        if stamp and buf.valid(stamp,8):last_time=max(last_time,struct.unpack_from('<Q',data,stamp)[0])
        if data[typ]==3:
            payload=n.payload_table(buf,event);count=n.field_u32(buf,payload,2) if payload else None
            if count:page_ranges.append((sequence,count))
    if modern:
        if not page_ids:raise NativeWriteError('没有可用的页面标识')
        page_base,page_count=0,group_count if grouped_pages else len(page_ids)
    elif grouped_pdf:
        page_base,page_count=0,len(grouped_pdf_layout['records'])
    elif grouped_paper:
        page_base,page_count=0,group_count
    else:
        if len(page_ranges)!=1:raise NativeWriteError('页面分组结构尚未验证写入')
        page_base,page_count=page_ranges[0]
    for sequence in hidden:
        if sequence not in ids or data[ids[sequence][1].field(4)] not in (15,18):raise NativeWriteError('擦除目标不是笔迹或形状事件')
    if native_image_deletions:
        if len(native_image_deletions)>5000:raise NativeWriteError('单次隐藏原生图片数量过多')
        for ident in native_image_deletions:
            if type(ident) is not int or ident not in ids or data[ids[ident][1].field(4)]!=22:
                raise NativeWriteError('隐藏目标不是本 NTB 的原生图片事件')
        if native_image_deletions & n.hidden_image_event_ids(events,buf):
            raise NativeWriteError('原生图片已隐藏，不能重复删除')
    for page,stroke in added:
        if not 0<=page<page_count or not math.isfinite(stroke.width) or not 0<stroke.width<=1000:
            raise NativeWriteError('笔迹页码或宽度无效')
        if not stroke.points or not all(math.isfinite(v) and abs(v)<1e7 for p in stroke.points for v in p):
            raise NativeWriteError('笔迹坐标无效')
    # Place the relocated root and event vector before both new and old tables.
    builder=Builder();builder.data=bytearray(data[:root.offset+root.osize])
    count=len(events)+len(added)+len(native_image_lock_events)+len(native_image_layer_changes)+(1 if hidden or native_image_deletions else 0)+(1 if new_title is not None else 0)
    vector=builder.vector(b'\0'*(4*count),count)
    builder.uoffset(root.field(6),vector)
    sequence=max(ids)+1;timestamp=max(int(time.time()*1000),last_time+1)
    if added:
        # The preview puts new annotations above native pictures, including
        # pictures with an explicit zIndex greater than the wall-clock time.
        # Missing stroke zIndex falls back to its event timestamp on reopen.
        layers=n.native_image_event_state(events,buf)
        timestamp=max(timestamp,max((s.get('z_index',0) for s in layers.values()),default=0)+1)
        if timestamp+count>=2**64:
            raise NativeWriteError('图层编号已达到上限，无法在现有图片上方安全添加笔迹')
    new_offsets=[]
    def envelope(typ):
        nonlocal sequence,timestamp
        if sequence>=2**32:raise NativeWriteError('事件编号耗尽')
        table,fields=builder.table(6,{0:(struct.pack('<2I',0,sequence),4),1:(struct.pack('<Q',timestamp),8),4:(bytes([typ]),1),5:(b'\0'*4,4)})
        sequence+=1;timestamp+=1;new_offsets.append(table)
        return fields[5]
    if hidden or native_image_deletions:
        field=envelope(25)
        payload,fields=builder.table(4,{s:(b'\0'*4,4) for s in range(4)})
        builder.uoffset(field,payload)
        for slot in range(4):
            refs=sorted(set(hidden)|native_image_deletions) if slot==0 else []
            target=builder.vector(b''.join(struct.pack('<2I',0,v) for v in refs),len(refs))
            builder.uoffset(fields[slot],target)
    for page,stroke in added:
        field=envelope(15)
        color=bytes.fromhex(stroke.color.lstrip('#'))+bytes([round(max(0.,min(1.,stroke.opacity))*255)])
        page_ref=((0,*paper_layout['references'][page]) if paper_layout else
                  (0,*grouped_pdf_layout['references'][page])
                  if grouped_pdf else
                  ((0,page_ids[0],page) if grouped_pages else
                   ((0,page_ids[page],0) if modern else (0,page_base,page))))
        payload_fields={0:(struct.pack('<3I',*page_ref),4),1:(struct.pack('<2f',*stroke.points[0]),4),
                        4:(bytes([3 if stroke.tool=='tape' else 2 if stroke.is_highlighter else 0]),1),
                        7:(color,1),8:(struct.pack('<f',stroke.width),4),9:(b'\0'*4,4)}
        if stroke.tool=='tape':
            pattern=int(stroke.tape_pattern)
            if not 0<=pattern<=8:raise NativeWriteError('未知的遮字胶带样式')
            payload_fields.update({5:(bytes([1]),1),6:(bytes([pattern]),1)})
        # The iOS-validated handwritten stroke on a grouped PDF omitted
        # these optional trailing vectors. Do not invent them in that layout.
        if modern and not grouped_pages:
            payload_fields.update({10:(b'\0'*4,4),11:(b'\0'*4,4)})
        if stroke.outline is not None:payload_fields[10]=(b'\0'*4,4)
        # Grouped handwritten paper samples use ten-slot type-15 payloads;
        # match the source layout instead of synthesizing optional vectors.
        payload,fields=builder.table(12 if modern and not grouped_pages else 11 if stroke.outline is not None else 10,payload_fields)
        builder.uoffset(field,payload)
        blob=(curve_blob(stroke) if stroke.geometry_is_smoothed and not stroke.geometry_is_linear
              and stroke.outline is None else point_blob(stroke.points))
        target=builder.vector(blob,len(blob));builder.uoffset(fields[9],target)
        if 10 in fields:
            if stroke.outline is not None:
                ox,oy=stroke.points[0]
                custom=outline_blob(tuple((ox+x,oy+y) for x,y in stroke.outline),stroke.points[0])
                builder.uoffset(fields[10],builder.vector(custom,len(custom)))
            else:builder.uoffset(fields[10],builder.vector(b'',0))
        if 11 in fields:builder.uoffset(fields[11],builder.vector(b'',0))
    if native_image_lock_events:
        # T01/T02/T03: type-23(payload slot0 -> vector<struct(actor,id)>,
        # slot17 -> lock boolean). The observed official app uses 18 slots.
        # Never mutate existing type-22 slot20: it is historical baseline.
        original_state=n.native_image_event_state(events,buf)
        image_ids=set()
        for _,old_event in events:
            kind=old_event.field(4);ref=old_event.field(0)
            if kind and ref and buf.data[kind]==22 and buf.valid(ref,8):
                actor,ident=struct.unpack_from('<2I',data,ref)
                if actor==0:
                    if ident in image_ids:raise NativeWriteError('原生图片事件编号重复')
                    image_ids.add(ident)
        if len(native_image_lock_events)>5000:
            raise NativeWriteError('锁定图片数量超过安全限制')
        for ident,record in sorted(native_image_lock_events.items()):
            if type(ident) is not int or ident not in image_ids:
                raise NativeWriteError('锁定目标不是当前 NTB 的原生图片')
            if (not isinstance(record,dict) or set(record)!={'original','current'}
                or type(record['original']) is not bool or type(record['current']) is not bool
                or record['original']==record['current']):
                raise NativeWriteError('原生图片锁定修改记录不合法')
            if bool(original_state.get(ident,{}).get('locked',False))!=record['original']:
                raise NativeWriteError('图片锁定状态已改变，请重新打开源文件')
            target=envelope(23)
            payload,field=builder.table(18,{0:(b'\0'*4,4),17:(bytes([int(record['current'])]),1)})
            builder.uoffset(target,payload)
            target_vector=builder.vector(struct.pack('<2I',0,ident),1,alignment=4)
            builder.uoffset(field[0],target_vector)
    if native_image_layer_changes:
        states=n.native_image_event_state(events,buf)
        if len(native_image_layer_changes)>5000:raise NativeWriteError('图片层级修改数量过多')
        for ident,record in sorted(native_image_layer_changes.items()):
            if type(ident) is not int or ident not in ids or data[ids[ident][1].field(4)] not in (15,18,22):
                raise NativeWriteError('层级目标不是原生图片、文字或笔迹')
            payload=n.payload_table(buf,ids[ident][1])
            if not payload:raise NativeWriteError('层级目标缺少原生数据')
            if (set(record)!={'original','current'} or any(type(record[k]) is not int or not 0<=record[k]<2**64 for k in record)
                    or states.get(ident,{}).get('z_index',0)!=record['original']):
                raise NativeWriteError('原生图片层级基准不一致，请重新打开')
            if data[ids[ident][1].field(4)]==22:
                target=envelope(23)
                payload,fields=builder.table(18,{0:(b'\0'*4,4),9:(struct.pack('<Q',record['current']),8)})
                builder.uoffset(target,payload)
                refs=builder.vector(struct.pack('<2I',0,ident),1,alignment=4)
                builder.uoffset(fields[0],refs)
            else:
                target=envelope(24)
                payload,fields=builder.table(1,{0:(b'\0'*4,4)})
                builder.uoffset(target,payload)
                position_vector=builder.vector(b'\0'*4,1)
                builder.uoffset(fields[0],position_vector)
                item,_=builder.table(6,{0:(struct.pack('<2I',0,ident),4),5:(struct.pack('<Q',record['current']),8)})
                builder.uoffset(position_vector+4,item)
    if new_title is not None:
        from core.ne_titles import validate_note_title
        encoded = validate_note_title(new_title).encode('utf-8')
        # Notability title change: type-1 event with a one-field payload,
        # which points to a one-field table containing a FlatBuffers string.
        title_field = envelope(1)
        payload, payload_fields = builder.table(1, {0: (b'\0'*4, 4)})
        builder.uoffset(title_field, payload)
        wrapper, wrapper_fields = builder.table(1, {0: (b'\0'*4, 4)})
        builder.uoffset(payload_fields[0], wrapper)
        # FlatBuffers strings contain a byte-length, UTF-8, and a NUL.
        string_pos = builder.vector(encoded + b'\0', len(encoded))
        builder.uoffset(wrapper_fields[0], string_pos)
    builder.align(8);shift=len(builder.data);builder.add(data)
    # Only known root pointer fields are relocated; opaque inline fields retain
    # their bytes. Old signed vtable offsets remain valid after uniform shifting.
    for slot in (3,5):
        field=root.field(slot)
        if field:builder.uoffset(field,field+buf.u32(field)+shift)
    for i,(offset,_) in enumerate(events):builder.uoffset(vector+4+4*i,offset+shift)
    for i,offset in enumerate(new_offsets,len(events)):builder.uoffset(vector+4+4*i,offset)
    result=bytes(builder.data)
    verify,verified_root,written=read_structure(result)
    if len(written)!=count or result[shift:]!=data:raise NativeWriteError('写入后的结构校验失败')
    if not hidden<=n.hidden_stroke_event_ids(written,verify):raise NativeWriteError('擦除事件回读校验失败')
    if not native_image_deletions<=n.hidden_image_event_ids(written,verify):
        raise NativeWriteError('原生图片隐藏事件写入后校验失败')
    if native_image_lock_events:
        states=n.native_image_event_state(written,verify)
        if any(states.get(ident,{}).get('locked')!=record['current']
               for ident,record in native_image_lock_events.items()):
            raise NativeWriteError('原生图片状态事件写入后校验失败')
    if native_image_layer_changes:
        states=n.native_image_event_state(written,verify)
        if any(states.get(ident,{}).get('z_index')!=record['current'] for ident,record in native_image_layer_changes.items()):
            raise NativeWriteError('图片层级事件回读验证失败')
    if new_title is not None:
        from core.ne_titles import effective_title
        if effective_title(result,structure=(verify,verified_root,written))!=new_title:raise NativeWriteError('内部笔记标题写入校验失败')
    return result


from core.ne_save_work import save_operation


@save_operation
def write_ntb(source,target,hidden,added,progress=lambda value:None,cancelled=lambda:False,thumbnail=None,*,base_bundle=None,expected_bundle=None,page_plan=None,effective_target=None,effective_source=None,native_image_changes=None,native_image_lock_changes=None,native_image_deletions=None,native_image_layer_changes=None):
    """Stage and validate a complete ZIP before replacing the destination.

    The GUI keeps the opened bundle as its editing baseline across saves, so
    replaying full undo history never duplicates previously saved new strokes.
    expected_bundle is the last known on-disk bundle, checked before commit.
    """
    source,target=Path(source),Path(target)
    effective_target=Path(effective_target) if effective_target is not None else target
    identity_source=Path(effective_source) if effective_source is not None else source
    from formats.ne_loader import LoadCancelled
    def check(value):
        if cancelled():raise LoadCancelled()
        progress(value)
    check(0)
    combined_images = bool(page_plan is not None and (native_image_changes or
        native_image_lock_changes or native_image_deletions or native_image_layer_changes))
    if combined_images:
        # Image deltas refer to the opened document's stable page identities.
        # Only the checked per-page writer can preserve these while reordering.
        from formats.ne_pagewrite import validate_page_structure
        with ZipFile(source) as probe:
            from formats.ne_paper_graph import expand_paper_graph
            validate_page_structure(expand_paper_graph(base_bundle if base_bundle is not None
                                    else probe.read('noteBundle')))
    temporary=None
    if page_plan is not None:
        from formats.ne_grouped_pdf_range import (
            grouped_pdf_page_management_info,
            mixed_grouped_pdf_page_management_info,
            materialize_grouped_pdf_pages,
        )
        with ZipFile(source) as probe:
            checked=probe.read('noteBundle')
            if expected_bundle is not None and checked!=expected_bundle:
                raise NativeWriteError('文件已被外部修改，请重新打开后再保存')
            grouped_pdf_editable=grouped_pdf_page_management_info(
                checked,source)
            if grouped_pdf_editable is None:
                grouped_pdf_editable=mixed_grouped_pdf_page_management_info(
                    checked,source)
        if grouped_pdf_editable:
            return materialize_grouped_pdf_pages(
                source,target,page_plan,expected_bundle=expected_bundle,
                effective_target=effective_target,progress=progress,
                cancelled=cancelled)
        from formats.ne_grouped_paper import native_paper_info, save_native_paper, NativePaperError
        # Windows: do NOT invoke the commit writer inside ``with ZipFile``.
        # Python evaluates ``return writer(...)`` *before* __exit__ closes
        # the original NTB. An active read handle then blocks os.replace with
        # ERROR_SHARING_VIOLATION (32) or ERROR_ACCESS_DENIED (5).
        native_paper = False
        with ZipFile(source) as probe:
            checked = probe.read('noteBundle')
            if expected_bundle is not None and checked != expected_bundle:
                raise NativeWriteError('文件已被外部修改，请重新打开后再保存')
            from formats.ne_range_pages import range_page_info
            from formats.ne_paper_graph import paper_graph
            graph_buf,graph_root,graph_events=read_structure(checked)
            graph=paper_graph(graph_events,graph_buf,graph_buf.u16(graph_root.field(7)) if graph_root.field(7) else None)
            if range_page_info(checked) is None and graph is None:
                try:
                    native_paper_info(checked)
                except NativePaperError:
                    pass
                else:
                    native_paper = True
        # ALL ZipFile handles above have now been closed.
        if native_paper:
            return save_native_paper(source,target,page_plan,hidden,added,thumbnail,
                base_bundle=base_bundle,expected_bundle=expected_bundle,progress=progress,cancelled=cancelled,effective_target=effective_target,effective_source=identity_source)

        from formats.ne_grouped_pagesave import grouped_info,rebuild_grouped_pages,GroupedPageError
        grouped_pdf = False
        with ZipFile(source) as probe:
            checked = probe.read('noteBundle')
            if expected_bundle is not None and checked != expected_bundle:
                raise NativeWriteError('文件已被外部修改，请重新打开后再保存')
            try:
                grouped_info(probe,checked)
            except GroupedPageError:
                pass  # Non-grouped documents use original writer.
            else:
                grouped_pdf = True
        # The second probe must also be closed before committing a new NTB.
        if grouped_pdf:
            return rebuild_grouped_pages(source,target,page_plan,expected_bundle=expected_bundle,
                                         progress=progress,cancelled=cancelled,effective_target=effective_target)
    try:
        with ZipFile(source) as archive:
            current_bundle=archive.read('noteBundle')
            if expected_bundle is not None and current_bundle!=expected_bundle:
                raise NativeWriteError('文件已被其他程序修改，请重新打开后再保存；当前编辑仍保留。')
            baseline=current_bundle if base_bundle is None else base_bundle
            from formats.ne_bundle_identity import repair_generated_paper,repair_generated_paper_pattern
            baseline=repair_generated_paper(baseline,archive)
            baseline=repair_generated_paper_pattern(baseline,archive)
            from formats.ne_range_pages import expand_range_pages
            baseline=expand_range_pages(baseline)
            if page_plan is not None:
                from formats.ne_paper_graph import expand_paper_graph
                baseline=expand_paper_graph(baseline)
            if page_plan is not None and not combined_images:
                from formats.ne_pagewrite import rewrite_pages
                baseline=rewrite_pages(baseline,page_plan)
            # Unlock FIRST when a user unlocked a previously locked picture
            # and then moved/resized it. Preserve official type-22 baselines.
            unlocks={ident:v for ident,v in (native_image_lock_changes or {}).items() if not v['current']}
            locks={ident:v for ident,v in (native_image_lock_changes or {}).items() if v['current']}
            # A locked image can be unlocked, moved, then relocked before Save.
            # Its net lock delta is empty, but geometry still needs an unlock
            # event before patching and a relock event afterwards.
            if native_image_changes:
                state_buf,_,state_events=read_structure(baseline)
                image_state=n.native_image_event_state(state_events,state_buf)
                for ident in native_image_changes:
                    if ident not in (native_image_lock_changes or {}) and image_state.get(ident,{}).get('locked',False):
                        unlocks[ident]={'original':True,'current':False}
                        locks[ident]={'original':False,'current':True}
            if unlocks:
                baseline=build_bundle(baseline,set(),[],native_image_lock_events=unlocks)
            if native_image_changes:
                from images.ne_native_image_geometry import patch_image_rectangles
                baseline=patch_image_rectangles(baseline,native_image_changes)
            if combined_images:
                baseline=build_bundle(baseline,set(),[],native_image_lock_events=locks,
                    native_image_deletions=native_image_deletions,
                    native_image_layer_changes=native_image_layer_changes)
                from formats.ne_pagewrite import rewrite_pages
                baseline=rewrite_pages(baseline,page_plan)
                locks={}
                native_image_deletions=None
                native_image_layer_changes=None
            # A new filesystem name is a real Notability rename event, not an
            # edit of manifest.subjectName (which is the subject/category).
            # Save in place does not rename the note implicitly.
            new_title=effective_target.stem if identity_source.resolve()!=effective_target.resolve() else None
            bundle=build_bundle(baseline,hidden,added,new_title=new_title,native_image_lock_events=locks,
                                native_image_deletions=native_image_deletions,native_image_layer_changes=native_image_layer_changes)
            # Preserve all official assets and historical events on ordinary
            # Save / Save As. Pruning PDFs or orphan ZIP assets is destructive
            # and must ONLY run for an explicitly validated page-edit plan.
            # A filename change is never permission to rewrite the PDF tree.
            extra_assets={}
            obsolete=set()
            obsolete_indexes=set()
            with tempfile.TemporaryDirectory(prefix='ntb-pdfgc-',dir=target.parent) as pdf_work:
                if page_plan is not None:
                    # Page rewriting must be independently validated; normal
                    # Save As never enters this physical PDF pruning path.
                    # Do not silently delete/trim unknown page structures.
                    from formats.ne_pagewrite import validate_page_structure
                    modern_pages=False
                    try:
                        validate_page_structure(bundle)
                        modern_pages=True
                    except NativeWriteError:
                        if page_plan is not None:
                            raise  # a destructive edit requires checked mapping
                    if modern_pages:
                        from formats.ne_pdfgc import trim_embedded_pdf_pages, PdfPruneError
                        try:
                            bundle,extra_assets=trim_embedded_pdf_pages(archive,bundle,pdf_work,cancelled)
                        except (NativeWriteError,PdfPruneError) as exc:
                            raise NativeWriteError('PDF 底图物理裁剪失败，原文件未修改：'+str(exc)) from exc
                if extra_assets:
                    # The iOS PDF search index still points at the original PDF
                    # and original PDF page numbers. It is a derived cache and
                    # cannot safely be rewritten without its schema. Remove this
                    # now-stale PDF index rather than retain the entire old PDF.
                    obsolete_indexes={'index/ios/PDFIndex.fb'} & set(archive.namelist())
                if page_plan is not None or extra_assets:
                    from formats.ne_pagewrite import validate_page_structure
                    from core.ne_compact import assets_to_remove_after_save, CompactError
                    from formats.ne_grouped_pdf_range import normalize_grouped_pdf_pages
                    try:
                        removed_original=False
                        if page_plan is not None:
                            original_count=len(validate_page_structure(
                                expand_paper_graph(expand_range_pages(
                                    normalize_grouped_pdf_pages(current_bundle))))[3])
                            still_original={p['source'] for p in page_plan if p.get('source') is not None}
                            removed_original=bool(set(range(original_count)) - still_original)
                        if removed_original or extra_assets:
                            obsolete=assets_to_remove_after_save(archive,bundle,new_assets=extra_assets,ignored_metadata=obsolete_indexes)
                            obsolete.update(obsolete_indexes)
                    except (NativeWriteError,CompactError) as exc:
                        raise NativeWriteError('页面物理清理失败，原文件未修改：'+str(exc)) from exc
                if page_plan is not None:
                    from core.ne_bundle_compact import compact_bundle, BundleCompactError
                    try:bundle=compact_bundle(bundle)
                    except BundleCompactError as exc:
                        raise NativeWriteError('页面物理清理失败，原文件未修改：'+str(exc)) from exc
                fd,temporary=tempfile.mkstemp(prefix=target.name+'.',suffix='.tmp',dir=target.parent);os.close(fd)
                total=sum(info.file_size for info in archive.infolist())+sum(p.stat().st_size for p in extra_assets.values())
                copied=0
                with ZipFile(temporary,'w',allowZip64=True) as output:
                    output.comment=archive.comment
                    for info in archive.infolist():
                        check(int(95*copied/max(1,total)))
                        if info.filename in obsolete:
                            copied+=info.file_size
                            continue
                        if info.filename=='noteBundle':
                            output.writestr(copy.copy(info),bundle);copied+=info.file_size;continue
                        if info.filename=='thumbnail.png' and thumbnail is not None:
                            output.writestr(copy.copy(info),thumbnail);copied+=info.file_size;continue
                        with archive.open(info) as src,output.open(copy.copy(info),'w',force_zip64=True) as dst:
                            while True:
                                check(int(95*copied/max(1,total)))
                                chunk=src.read(4*1024*1024)
                                if not chunk:break
                                dst.write(chunk);copied+=len(chunk)
                    for name,path in extra_assets.items():
                        from zipfile import ZIP_STORED
                        from hashlib import sha512
                        digest=sha512()
                        with open(path,'rb') as src,output.open(name,'w',force_zip64=True) as dst:
                            while True:
                                check(int(95*copied/max(1,total)))
                                chunk=src.read(4*1024*1024)
                                if not chunk:break
                                digest.update(chunk);dst.write(chunk);copied+=len(chunk)
                        if digest.hexdigest()!=name[len('assets/'):-4]:
                            raise NativeWriteError('新 PDF 资源的 SHA-512 校验失败')
                with ZipFile(temporary) as output:
                    expected=[item.filename for item in archive.infolist() if item.filename not in obsolete]+list(extra_assets)
                    if output.namelist()!=expected:raise NativeWriteError('ZIP 成员不完整')
                    if output.read('noteBundle')!=bundle:raise NativeWriteError('noteBundle 写入验证失败')
                    # build_bundle already read back the native title; byte equality
                    # above proves the same checked bundle reached this archive.
                    # A structural save may compact the bundle afterwards.
                    if new_title is not None and page_plan is not None:
                        from core.ne_titles import effective_title
                        if effective_title(bundle)!=new_title:
                            raise NativeWriteError('另存为标题与内部存储名称不一致，已中止保存')
                    for info in archive.infolist():
                        if info.filename in obsolete or info.filename in ('noteBundle','thumbnail.png'):continue
                        other=output.getinfo(info.filename)
                        if (info.CRC,info.file_size)!=(other.CRC,other.file_size):raise NativeWriteError('素材校验失败')
                    if obsolete or extra_assets:
                        from core.ne_compact import _validate_staged_ntb
                        from formats.ne_pagewrite import validate_page_structure
                        expected_count=(len(page_plan) if page_plan is not None
                                        else len(validate_page_structure(bundle)[3]))
                        if not _validate_staged_ntb(temporary,expected_count):
                            raise NativeWriteError('物理删除后页面或资源映射校验失败，已停止保存')
                    if output.testzip() is not None:
                        raise NativeWriteError('ZIP CRC 校验失败，已停止保存')
        # The ZIPs must be closed before replacement, especially on Windows.
        with open(temporary,'r+b') as staged:
            staged.flush();os.fsync(staged.fileno())
        check(99)
        with ZipFile(source) as current:
            if current.read('noteBundle')!=current_bundle:
                raise NativeWriteError('保存期间文件被其他程序修改，已停止替换；当前编辑仍保留。')
        if cancelled():raise LoadCancelled()
        replace_with_retry(temporary,target);temporary=None
        progress(100)
        return bundle
    finally:
        if temporary is not None:
            try:os.unlink(temporary)
            except FileNotFoundError:pass
