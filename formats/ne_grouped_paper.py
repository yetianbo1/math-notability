"""Experimental, lossless-event grouped *native paper* page remapper.

Unlike imported PDF groups, native paper has no PDF backing. The visible
page lives in the third word of the event's three-word page reference.
For tested schema-20 notes, page edits rewrite only that index and the
range count, preserving stroke points, pressures, pictures and ZIP assets.
Never invoke from ordinary ink saving or from an unverified structure.
"""
from __future__ import annotations
import copy
import math
import os
from pathlib import Path
import struct
import tempfile
from core.ne_safecommit import replace_with_retry
from zipfile import ZipFile
import formats.ne_ntb as ntb
from formats.ne_native import Builder, NativeWriteError, build_bundle, read_structure

class NativePaperError(NativeWriteError):
    pass

PAGE_OBJECT_TYPES = frozenset((15, 18, 22))
SAFE_EVENT_TYPES = frozenset((1, 3, 7, 8, 12, 13, 14, 15, 17, 18, 22, 23, 24, 25))
# Type 18's command vector is in slot 5, rather than type 15's slot 9.
POINTER_FIELDS = {15: (9,10,11,13), 18: (5,), 22: (10,12,13,15)}
PAGE_FIELDS = {15: 0, 18: 0, 22: 2}


def native_paper_info(bundle):
    """Return original page count for a narrowly validated grouped-paper note."""
    b, root, events = read_structure(bundle)
    version = b.u16(root.field(7)) if root.field(7) else None
    if version not in (18, 20) or root.slots != 8 or root.field(2):
        raise NativePaperError('原生分组纸张只支持已验证的 schema-18/20 完整根表')
    count = ntb.document_page_count(events,b)
    pages = ntb.page_records(events,b)
    layout=ntb.native_paper_page_records(events,b,version)
    signature=ntb.native_paper_range_signature(events,b,version)
    template=ntb.document_paper_template(events,b)
    # Official empty A4 dots/blank/ruled: 2 range slots but one visible sheet.
    # Normalize *only* that observed source form. Rebuilt files carry a
    # standard type-3 terminator, so their range count is literal.
    one_sheet=ntb._known_single_sheet_paper(events,b,count,pages,template)
    visible_count=(1 if one_sheet else
                   len(layout['records']) if layout else count)
    canonical_schema18=(version==18 and layout is not None
                        and layout.get('marker_count')==0
                        and sum(1 for _,event in events
                                if event.field(4) and bundle[event.field(4)]==3)==1)
    if ((not signature and not canonical_schema18)
            or (signature and signature[1]!=count)
            or not (1<=visible_count<=10000)
            or any(p.get('pdf') is not None for p in pages)):
        raise NativePaperError('不是已知的原生纸张分组结构')
    # Validate every populated field in the new metadata-only blank family.
    if signature and template and template.get('kind')=='blank' and template.get('approximate'):
        from core.ne_bundle_compact import validate_bundle_schema
        validate_bundle_schema(bundle)
    group=signature[0] if signature else layout['group']
    kinds=[bundle[e.field(4)] if e.field(4) else -1 for _,e in events]
    if any(kind not in SAFE_EVENT_TYPES for kind in kinds):
        raise NativePaperError('包含未支持的事件类型，页面重排可能丢失资源')
    # Rule out arbitrary layout changes. The official dotted A4 sample has
    # one additional type-1 settings event. Rebuilt documents may have the
    # validated native type-3 range terminator (two total type-3 events).
    if 14 in kinds and not (kinds.count(3) in (1,2) and kinds.count(8)==1
                             and kinds.count(14)==1 and template
                             and ((template['kind']=='ruled' and
                                   (len(list(ntb._paper_styles(events,b)))==1 or ntb._known_a4_ruled_layout(events,b))) or
                                  (template['kind'] in ('dotted','blank') and kinds.count(1)>=2
                                   and (ntb._known_a4_dots_layout(events,b)
                                        if template['kind']=='dotted'
                                        else ntb._known_a4_blank_layout(events,b))))):
        raise NativePaperError('此页面设置结构尚未验证可重排，已拒绝修改')
    # A valid group can become blank after removing the last inked sheet.
    # Do not require an ink event in the rebuilt bundle.
    # v24 created a seven-slot envelope with an absent trailing field.
    # It carries exactly the same six known fields; populated extensions
    # remain unsupported. Accept it so existing new notes can be managed.
    if any(e.slots not in (6,7) or (e.slots==7 and e.field(6))
           or not e.field(0) or not b.valid(e.field(0),8) for _,e in events):
        raise NativePaperError('事件标识或事件布局不符合已验证结构')
    ids = [b.u32(e.field(0)+4) for _,e in events]
    event_ids = set(ids)
    if len(set(ids)) != len(ids) or any(b.u32(e.field(0))!=0 for _,e in events):
        raise NativePaperError('不支持重复事件标识或多作者事件')
    for _,e in events:
        kind=bundle[e.field(4)]
        if kind in (17,23,24) and not ntb.valid_layer_state_event(events,b,e):
            raise NativePaperError(f'图层状态事件类型 {kind} 的结构尚未验证')
        if kind in PAGE_OBJECT_TYPES:
            p=ntb.payload_table(b,e)
            at=p.field(PAGE_FIELDS[kind]) if p else 0
            if not at or not b.valid(at,12):
                raise NativePaperError('存在缺少页码的图片或笔迹事件')
            actor,content_group,page=struct.unpack_from('<III',bundle,at)
            if actor or content_group != group or page >= count:
                raise NativePaperError('图片或笔迹的页码不属于唯一纸张分组')
            for slot in POINTER_FIELDS[kind]:
                f=p.field(slot)
                if not f and (kind==22 or slot!=9):continue
                if not f or not b.valid(f,4) or not b.valid(f+b.u32(f),4):
                    # Official Python.ntb carries one type-22 placeholder
                    # without a content asset. Preserve its event unchanged,
                    # and refuse cloning this opaque placeholder.
                    if kind==22 and not f:continue
                    raise NativePaperError('内容对象存在损坏或未知资源引用')
        elif kind==3:
            p=ntb.payload_table(b,e)
            if not p:raise NativePaperError('页面范围事件缺少载荷')
            field=p.field(0)
            if field and b.valid(field,12):
                ref=struct.unpack_from('<3I',bundle,field)
                page_anchor=(ref[0]==0 and ref[1]==group
                             and ref[2]<count)
                marker_anchor=(ref[0]==0 and ref[1] in event_ids
                               and ref[2]==0)
                if not (page_anchor or marker_anchor):
                    raise NativePaperError('页面范围事件包含未知页面锚点')
    return visible_count


def _mapped_sources(plan,count):
    if not isinstance(plan,(list,tuple)) or not (1<=len(plan)<=10000):
        raise NativePaperError('页面数量无效')
    sources=[]
    for i,spec in enumerate(plan):
        if not isinstance(spec,dict) or spec.get('pdf') is not None:
            raise NativePaperError('纸张页面不能引用 PDF 底图')
        if spec.get('images') is None or 'strokes' not in spec:
            raise NativePaperError('缺少原生图片或笔迹快照')
        source=spec.get('source')
        if source is None:source=spec.get('background_source')
        if source is not None and (type(source) is not int or not 0<=source<count):
            raise NativePaperError(f'第 {i+1} 页原始引用无效')
        sources.append(source)
    return sources


def _copy_payload(builder,old_buf,old_payload,kind,old_index,new_index,shift_at):
    """Clone one table while leaving vector/asset objects in the preserved tail.

    Scalar widths are kept verbatim. Relative offsets are repointed to their
    original content AFTER shifting the source bundle as a complete suffix.
    """
    fields={}
    locations=sorted((old_payload.field(i),i) for i in range(old_payload.slots) if old_payload.field(i))
    for n,(where,slot) in enumerate(locations):
        end=locations[n+1][0] if n+1<len(locations) else old_payload.offset+old_payload.osize
        raw=bytes(old_buf.data[where:end])
        fields[slot]=(raw,4 if len(raw)>=4 and (where%4==0) else 1)
    table,refs=builder.table(old_payload.slots,fields)
    page_at=refs[PAGE_FIELDS[kind]]
    if len(fields[PAGE_FIELDS[kind]][0])<12:
        raise NativePaperError('无法克隆缺失页码的原生事件')
    struct.pack_into('<I',builder.data,page_at+8,new_index)
    pointers=[]
    for slot in POINTER_FIELDS[kind]:
        original=old_payload.field(slot)
        if not original:continue
        target=original+old_buf.u32(original)
        pointers.append((refs[slot],target))
    return table,pointers


def remap_grouped_paper_bundle(data,plan,*,hidden=(),added=(),new_title=None):
    """Create a new event vector and map every original native page object.

    Object contents (point-vector bytes, embedded picture hashes, placements)
    are preserved; copies get fresh op IDs and envelopes. No source mutation.
    """
    count=native_paper_info(data)
    source_by_new=_mapped_sources(plan,count)
    # Native paper type-1 settings are document-wide. The UI guards this too,
    # but the writer must not silently discard an independently selected
    # background when called from a script or an older GUI build.
    check_buf,_,check_events=read_structure(data)
    source_template=ntb.document_paper_template(check_events,check_buf) or {}
    source_kind=source_template.get('kind','blank')
    # The supplied Python notebook allocates all content sheets in its range.
    # Its extra anchor-only CreatePage is a real trailing blank in Desktop,
    # not a terminator. A rewritten range already allocates exactly len(plan).
    dimensions=ntb.document_page_size(check_events,check_buf)
    python_range=(source_kind=='ruled' and dimensions is not None
                  and abs(dimensions[0]-567.)<.01
                  and abs(dimensions[1]-744.187561)<.01)
    for spec in plan:
        if (spec.get('source') is None and spec.get('background_kind') is not None
                and spec['background_kind'] != source_kind):
            raise NativePaperError('原生底页属于文档级设置，当前不能安全保存混合空白/横线/点状页面')
    destinations={old:[] for old in range(count)}
    for new,old in enumerate(source_by_new):
        if old is not None:destinations[old].append(new)
    b,root,events=read_structure(data)
    root_version=b.u16(root.field(7)) if root.field(7) else None
    original_state=ntb.native_image_event_state(events,b)
    original_by_id={b.u32(e.field(0)+4):e for _,e in events}
    from formats.ne_native_text import text_records
    known_text={x['event_id'] for x in text_records(events,b)}
    text_slots={8:2,12:2,13:7}
    text_events={}
    for off,event in events:
        kind=data[event.field(4)]
        if kind in text_slots:
            payload=ntb.payload_table(b,event);ref=payload.field(text_slots[kind]) if payload else 0
            if ref:
                text_events[off]=b.u32(ref+4)
    existing=[];copies=[];patches=[]
    max_id=max(original_by_id)
    last_time=max((struct.unpack_from('<Q',data,e.field(1))[0] for _,e in events if e.field(1) and b.valid(e.field(1),8)),default=0)
    for off,e in events:
        kind=data[e.field(4)]
        if kind in PAGE_OBJECT_TYPES:
            pt=ntb.payload_table(b,e)
            old=b.u32(pt.field(PAGE_FIELDS[kind])+8)
            target=destinations[old]
            if not target:continue
            patches.append((pt.field(PAGE_FIELDS[kind])+8,target[0]))
            existing.append(off)
            for extra in target[1:]:
                state=original_state.get(b.u32(e.field(0)+4),{})
                if 'position' in state or 'scale' in state:
                    raise NativePaperError('复制页面含历史位置或缩放变换，暂不能安全克隆；请撤销该页面复制操作')
                if kind==22 and not pt.field(10) and b.u32(e.field(0)+4) not in known_text:
                    raise NativePaperError('复制页面含未知图片占位事件，无法安全克隆')
                copies.append((e,pt,kind,old,extra))
        else:
            if off in text_events:
                block=original_by_id.get(text_events[off])
                if block is not None and data[block.field(4)]==22:
                    block_payload=ntb.payload_table(b,block)
                    old=b.u32(block_payload.field(2)+8)
                    if not destinations[old]:continue
            if kind==3:
                pt=ntb.payload_table(b,e)
                if root_version==18:
                    # Schema 18 marker pages are historical page nodes. A
                    # structural save canonicalizes all visible pages into one
                    # checked range whose count is exactly len(plan).
                    if pt and ntb.field_u32(b,pt,2) is not None:
                        existing.append(off)
                    continue
                anchor=pt.field(0) if pt else 0
                if anchor and b.valid(anchor,12):
                    old=b.u32(anchor+8)
                    actor,owner,index=struct.unpack_from('<3I',data,anchor)
                    if owner != ntb.native_paper_range_signature(events,b)[0]:
                        # Schema-18 can append a second terminal marker that
                        # anchors to the preceding marker event. It represents
                        # page history, not another paper sheet. Preserve its
                        # scalar reference because marker IDs are immutable.
                        if (pt.slots!=1 or actor!=0 or index!=0
                                or owner not in original_by_id):
                            raise NativePaperError('页面终止标记包含未知事件锚点')
                    else:
                        target=destinations[old]
                        if pt.slots==1 and actor==0 and old==count-1:
                            if python_range:continue
                            # Official native paper ends with one range
                            # terminator referring to the LAST logical sheet.
                            # Retarget it when the last source page changes.
                            patches.append((anchor+8,len(plan)-1))
                        elif not target:
                            # Unknown page anchors must never be guessed.
                            raise NativePaperError('未知页面锚点引用了已删除页面，已停止保存')
                        else:
                            patches.append((anchor+8,target[0]))
            existing.append(off)
    starter=next(e for _,e in events if data[e.field(4)]==3 and ntb.field_u32(b,ntb.payload_table(b,e),2))
    count_pos=ntb.payload_table(b,starter).field(2)
    if not count_pos:raise NativePaperError('纸张分组没有可写页数')
    patches.append((count_pos,len(plan)))
    # Keep root/vtable valid and rebuild just the pointer vector. Source bundle
    # is appended bit-for-bit; only explicitly checked scalar references change.
    template=ntb.document_paper_template(events,b)
    has_terminal=sum(data[e.field(4)]==3 for _,e in events)>=2
    add_terminal=(root_version!=18 and template is not None and (template.get('kind') in ('dotted','blank') or
                 (template.get('kind') == 'ruled' and (ntb._known_a4_ruled_layout(events,b)
                  or ntb._compact_supported_paper_type(events,b) == 'ruled')))
                 and not has_terminal
                 and not (source_kind=='blank' and source_template.get('approximate')
                          and ntb._compact_supported_paper_type(check_events,check_buf) is None
                          and not ntb._known_a4_blank_layout(check_events,check_buf)
                          and {check_buf.data[e.field(4)] for _,e in check_events if e.field(4)}<={1,3,15,18,22,23,24,25}))
    # The native page-range terminator is an observed official schema-20
    # convention. It makes a newly inserted blank second sheet distinct
    # from the original "2 allocated / 1 visible" A4 dot/blank/ruled samples.
    if add_terminal and len(plan)>10000:raise NativePaperError('页数超出原生范围编码')
    builder=Builder()
    builder.data=bytearray(data[:root.offset+root.osize])
    text_copy_count=sum(sum(ident==b.u32(e.field(0)+4) for ident in text_events.values())
                        for e,_,kind,_,_ in copies if kind==22)
    event_count=len(existing)+len(copies)+text_copy_count+int(add_terminal)
    vector=builder.vector(b'\0'*(4*event_count),event_count)
    builder.uoffset(root.field(6),vector)
    staged_copies=[]
    copied_ids={}
    block_copies=[]
    for e,old_payload,kind,old_page,new_page in copies:
        max_id+=1;last_time+=1
        if max_id>=2**32:raise NativePaperError('事件编号超过上限')
        env,ene_fields=builder.table(6,{0:(struct.pack('<2I',0,max_id),4),1:(struct.pack('<Q',last_time),8),4:(bytes((kind,)),1),5:(b'\0'*4,4)})
        pt,pointers=_copy_payload(builder,b,old_payload,kind,old_page,new_page,0)
        builder.uoffset(ene_fields[5],pt)
        staged_copies.append((env,pointers))
        if kind in (15,22):copied_ids[max_id]=(b.u32(e.field(0)+4),kind)
        if kind==22 and b.u32(e.field(0)+4) in known_text:
            block_copies.append((b.u32(e.field(0)+4),max_id))
    # Text is owned by its block ID, not directly by the page. Clone the
    # InsertString and formatting events and retarget that ownership too.
    from core.ne_bundle_schema import SCHEMA,PAYLOADS
    for old_block,new_block in block_copies:
        for off,event in events:
            if text_events.get(off)!=old_block:continue
            kind=data[event.field(4)];old_payload=ntb.payload_table(b,event)
            max_id+=1;last_time+=1
            env,ef=builder.table(6,{0:(struct.pack('<2I',0,max_id),4),1:(struct.pack('<Q',last_time),8),4:(bytes((kind,)),1),5:(b'\0'*4,4)})
            builder.align(2);vt=builder.add(data[old_payload.vtable:old_payload.vtable+old_payload.vsize])
            while len(builder.data)%8!=old_payload.offset%8:builder.data.append(0)
            payload=builder.add(data[old_payload.offset:old_payload.offset+old_payload.osize])
            struct.pack_into('<i',builder.data,payload,payload-vt)
            builder.uoffset(ef[5],payload)
            ref=payload+old_payload.field(text_slots[kind])-old_payload.offset
            struct.pack_into('<2I',builder.data,ref,0,new_block)
            pointers=[]
            for slot,field_kind in enumerate(SCHEMA[PAYLOADS[kind]]):
                field=old_payload.field(slot)
                if field and field_kind[0]!='scalar':
                    pointers.append((payload+field-old_payload.offset,field+b.u32(field)))
            staged_copies.append((env,pointers))
    terminal_offset=None
    if add_terminal:
        max_id+=1;last_time+=1
        group=ntb.native_paper_range_signature(events,b)[0]
        terminal_offset,terminal_fields=builder.table(6,{
            0:(struct.pack('<2I',0,max_id),4),
            1:(struct.pack('<Q',last_time),8),
            4:(b'\x03',1),
            5:(b'\0'*4,4),
        })
        terminal_payload,_=builder.table(1,{0:(struct.pack('<3I',0,group,len(plan)-1),4)})
        builder.uoffset(terminal_fields[5],terminal_payload)
    builder.align(8)
    shift=len(builder.data)
    builder.add(data)
    for slot in (3,5):
        field=root.field(slot)
        if field:builder.uoffset(field,field+b.u32(field)+shift)
    for pos,val in patches:struct.pack_into('<I',builder.data,pos+shift,val)
    for i,offset in enumerate(existing):builder.uoffset(vector+4+i*4,offset+shift)
    for i,(env,pointers) in enumerate(staged_copies,len(existing)):
        for field,target in pointers:builder.uoffset(field,target+shift)
        builder.uoffset(vector+4+i*4,env)
    if terminal_offset is not None:
        builder.uoffset(vector+4+4*(len(existing)+len(staged_copies)),terminal_offset)
    result=bytes(builder.data)
    # State events still target the old IDs. Fresh page copies must inherit
    # the effective lock/layer/visibility state instead of their type-22/15
    # creation defaults (which can also resurrect erased source objects).
    cb,_,ce=read_structure(result)
    copy_state=ntb.native_image_event_state(ce,cb)
    locks={};layers={};hidden_copies=set();deleted_copies=set()
    original_hidden=ntb.hidden_stroke_event_ids(events,b)
    original_deleted=ntb.hidden_image_event_ids(events,b)
    for new_id,(old_id,kind) in copied_ids.items():
        old=original_state.get(old_id,{});new=copy_state.get(new_id,{})
        if kind==22 and bool(old.get('locked',False))!=bool(new.get('locked',False)):
            locks[new_id]=dict(original=bool(new.get('locked',False)),current=bool(old.get('locked',False)))
        if old.get('z_index',0)!=new.get('z_index',0):
            layers[new_id]=dict(original=new.get('z_index',0),current=old.get('z_index',0))
        if kind==15 and old_id in original_hidden:hidden_copies.add(new_id)
        if kind==22 and old_id in original_deleted:deleted_copies.add(new_id)
    if locks or layers or hidden_copies or deleted_copies:
        result=build_bundle(result,hidden_copies,[],native_image_lock_events=locks,
                            native_image_layer_changes=layers,native_image_deletions=deleted_copies)
    # Apply user pen/eraser edits AFTER index remapping: added strokes are
    # already addressed to their final display page indices.
    if hidden or added or new_title is not None:
        result=build_bundle(result,set(hidden),list(added),new_title=new_title)
    if native_paper_info(result)!=len(plan):
        raise NativePaperError('页面重建后页数回读验证失败')
    check,_,items=read_structure(result)
    observed={check.u32(p.field(PAGE_FIELDS[result[e.field(4)]])+8)
              for _,e in items if result[e.field(4)] in PAGE_OBJECT_TYPES
              for p in [ntb.payload_table(check,e)]}
    if not observed.issubset(set(range(len(plan)))):
        raise NativePaperError('重建后图片或笔迹存在越界页码')
    return result


def save_native_paper(source,target,plan,hidden,added,thumbnail=None,*,base_bundle=None,expected_bundle=None,progress=lambda _:None,cancelled=lambda:False,effective_target=None,effective_source=None):
    """Transactional ZIP save; reclaim only provably unreferenced native images."""
    from formats.ne_loader import LoadCancelled
    source,target=Path(source),Path(target)
    if cancelled():raise LoadCancelled()
    with ZipFile(source) as archive:
        current=archive.read('noteBundle')
        if expected_bundle is not None and current!=expected_bundle:
            raise NativePaperError('源笔记已被其他程序修改，请重新打开')
        old_info=source.stat()
        title_target=Path(effective_target) if effective_target is not None else target
        identity_source=Path(effective_source) if effective_source is not None else source
        new_title=title_target.stem if identity_source.resolve()!=title_target.resolve() else None
        # PageEdits normally reports the entire content of a newly copied
        # page as 'added'. But this writer preserves/copies the ORIGINAL native
        # FlatBuffers pen events. Do not append the same ink a second time.
        from pages.ne_pages import ink_snapshot_digest
        filtered_added=[]
        copied_pages=set()
        for new_index,spec in enumerate(plan):
            if spec.get('source') is None and spec.get('background_source') is not None:
                signature=spec.get('native_copy_signature')
                if (not signature or spec.get('native_copy_from_edited_page')
                    or ink_snapshot_digest(spec['strokes'])!=signature):
                    raise NativePaperError('复制后修改的原生页面尚不能无损保存；请撤销该修改或另存为 PDF')
                copied_pages.add(new_index)
        for page,stroke in added:
            if page not in copied_pages:filtered_added.append((page,stroke))
        # Undo/history changes are relative to the opened bundle, including
        # after an ordinary save. Replaying them on disk duplicates saved ink
        # and prevents undo from removing it.
        baseline=current if base_bundle is None else base_bundle
        from formats.ne_bundle_identity import repair_generated_paper,repair_generated_paper_pattern
        baseline=repair_generated_paper(baseline,archive)
        baseline=repair_generated_paper_pattern(baseline,archive)
        rebuilt=remap_grouped_paper_bundle(baseline,plan,hidden=hidden,added=filtered_added,new_title=new_title)
        rebuild_buf,rebuild_root,_=read_structure(rebuilt)
        rebuild_version=(rebuild_buf.u16(rebuild_root.field(7))
                         if rebuild_root.field(7) else None)
        # The garbage compactor only models the modern nested schema. Schema
        # 18 keeps opaque archived objects; preserve those bytes verbatim.
        if rebuild_version is not None and rebuild_version>=19:
            from core.ne_bundle_compact import compact_bundle
            rebuilt=compact_bundle(rebuilt)
        from formats.ne_nativepaper_gc import (orphan_images_after_page_edit,
                                       validate_native_images, NativePaperGCError)
        try:
            obsolete=orphan_images_after_page_edit(archive,current,rebuilt,plan)
        except NativePaperGCError as exc:
            raise NativePaperError('页面物理素材清理失败，原文件未修改：'+str(exc)) from exc
        target.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.nv-paper-',suffix='.tmp',dir=target.parent)
        os.close(fd)
        try:
            with ZipFile(tmp,'w',allowZip64=True) as output:
                output.comment=archive.comment
                for info in archive.infolist():
                    if cancelled():raise LoadCancelled()
                    if info.filename in obsolete:continue
                    if info.filename=='noteBundle':output.writestr(copy.copy(info),rebuilt)
                    elif info.filename=='thumbnail.png' and thumbnail is not None:output.writestr(copy.copy(info),thumbnail)
                    else:
                        with archive.open(info) as reader,output.open(copy.copy(info),'w',force_zip64=True) as writer:
                            while True:
                                chunk=reader.read(1024*1024)
                                if not chunk:break
                                writer.write(chunk)
            progress(90)
            with ZipFile(tmp) as checked:
                if checked.testzip() is not None:raise NativePaperError('保存文件的 ZIP CRC 校验失败')
                if checked.read('noteBundle')!=rebuilt:raise NativePaperError('保存文件的笔迹事件不一致')
                expected_names=[item.filename for item in archive.infolist() if item.filename not in obsolete]
                if checked.namelist()!=expected_names:raise NativePaperError('保存后的 ZIP 资源列表异常')
                validate_native_images(checked,rebuilt)
                for info in archive.infolist():
                    if info.filename not in ('noteBundle','thumbnail.png') and info.filename not in obsolete:
                        copyinfo=checked.getinfo(info.filename)
                        if (copyinfo.CRC,copyinfo.file_size)!=(info.CRC,info.file_size):
                            raise NativePaperError('图片或索引资源在保存中意外发生变化')
            with open(tmp,'r+b') as f:f.flush();os.fsync(f.fileno())
            if cancelled():raise LoadCancelled()
            if (source.stat().st_size,source.stat().st_mtime_ns)!=(old_info.st_size,old_info.st_mtime_ns):
                raise NativePaperError('原笔记在保存期间发生变化，未覆盖')
        except BaseException:
            if os.path.exists(tmp):os.unlink(tmp)
            raise
    try:
        replace_with_retry(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    progress(100)
    return rebuilt
