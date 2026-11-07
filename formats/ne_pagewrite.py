"""Checked modern page snapshot rewrite; unknown layouts fail closed."""
import math,struct,time
from formats.ne_native import Builder,NativeWriteError,read_structure
import formats.ne_ntb as n


from core.ne_save_work import checked_once, checkpoint


@checked_once
def validate_page_structure(data):
    b,r,events=read_structure(data)
    version=b.u16(r.field(7)) if r.field(7) and b.valid(r.field(7),2) else None
    if r.slots!=8 or r.field(2) or version is None or version < 18:
        raise NativeWriteError(f'NTB 结构版本 {version} 的页面修改尚未验证；仍可阅读，支持的笔迹编辑功能不受影响。')
    # A *single* official PDF-import page event may allocate many PDF pages.
    # The existing per-page delete/reorder writer assumes one event per page;
    # it would destroy this valid grouped document if we let it through.
    grouped=n.document_page_count(events,b)
    number_of_page_events=sum(1 for _,e in events if e.field(4) and data[e.field(4)]==3)
    if number_of_page_events==1 and grouped>1:
        template=n.document_paper_template(events,b)
        if template and template.get('kind') in ('dotted','blank'):
            raise NativeWriteError('此 NTB 使用原生 A4 点状纸或纯空白纸分组结构，不能使用逐页事件写入器；'
                                   '请使用已验证结构指纹的原生分组纸张安全写入器。')
        raise NativeWriteError('此 NTB 使用分组页面结构（1 个事件管理多页）：'
                               '可阅读、画笔批注和保存笔迹；安全的原生页面删除/重排仍待官方验证，'
                               '请不要强制使用逐页事件算法。')
    # Later releases using this exact known event layout can be processed;
    # extensions or unexpected event shapes are rejected below.
    seen=set();pages=[]
    if any(data[e.field(4)] in (13,14,18,20) for _,e in events if e.field(4)):
        from core.ne_bundle_compact import validate_bundle_schema
        validate_bundle_schema(data)  # Reject unknown populated shape/group fields.
    for _,e in events:
        checkpoint()
        if not e.field(0) or not e.field(4):raise NativeWriteError('事件标识缺失')
        actor,seq=struct.unpack_from('<2I',data,e.field(0))
        if n.is_asset_cloud_persisted(b,e):
            identity=(actor,seq)
            if identity in seen:raise NativeWriteError('重复的服务器素材确认事件')
            seen.add(identity)
            continue
        if actor or seq in seen:raise NativeWriteError('页面管理暂不支持多作者或重复事件标识')
        seen.add(seq);typ=data[e.field(4)];p=n.payload_table(b,e)
        if typ not in (1,3,4,7,8,12,13,14,15,17,18,20,22,23,24,25):raise NativeWriteError(f'包含尚未验证页面重排的事件类型 {typ}')
        if typ==14:
            from formats.ne_native_text import validate_page_clear_style
            validate_page_clear_style(b,e)
        if typ==4:
            if (e.slots>6 or e.field(2) or e.field(3) or not p or p.slots>4
                    or any(p.field(i) for i in (2,3))):
                raise NativeWriteError('页面修改包含尚未适配的背景或书签状态')
        if typ in (17,23,24) and not n.valid_layer_state_event(events,b,e):
            raise NativeWriteError(f'图层状态事件类型 {typ} 的结构尚未验证')
        if typ==7:
            # Official InsertChar: the Taylor fixture contains only a main
            # text-body newline (unicodeScalar=10), with no location/textField
            # references. Preserve this exact shape; do not enable arbitrary
            # text operations or page-anchored text boxes for rewriting.
            location=p.field(0) if p else 0
            text_ids={b.u32(item.field(0)+4) for _,item in events
                      if item.field(4) and data[item.field(4)]==7 and item.field(0)}
            valid_location=(not location or (b.valid(location,12)
                            and b.u32(location)==0 and b.u32(location+4) in text_ids
                            and b.u32(location+8)==0))
            if (e.slots!=6 or e.field(2) or e.field(3) or not p
                    or p.slots!=2 or not valid_location or not p.field(1)
                    or not b.valid(p.field(1),4) or b.u32(p.field(1))!=10):
                raise NativeWriteError('文本事件含未验证的内容或引用，禁止页面重排')
        if typ in (3,22) and (e.slots>6 or e.field(2) or e.field(3)):
            raise NativeWriteError('页面或图片事件含未解析的扩展字段')
        if typ==3:
            if p is not None and p.slots>2:raise NativeWriteError('页面结构尚未支持')
            pages.append(seq)
        if typ==22:
            if not p or any(p.field(i) and i not in (0,2,3,4,6,9,10,11,20) for i in range(p.slots)):
                raise NativeWriteError('图片结构含未解析的页面引用')
            # CreateBlock.zIndex is an inline uint64, also emitted by our
            # image inserter. Cloning the payload preserves it verbatim.
            if p.field(9) and not b.valid(p.field(9),8):
                raise NativeWriteError('图片层级字段不完整')
    if not pages:raise NativeWriteError('没有可管理的页面')
    records=n.page_records(events,b)
    if len(records)==len(pages):pages=[record['id'] for record in records]
    # Official schema-20 per-page notes can use a bare type-3 event rather
    # than the two-slot insertion payload. This is distinct from grouped
    # paper/PDF ranges. Prove every native image/ink reference resolves to a
    # real independent page before enabling destructive operations.
    creators={b.u32(e.field(0)+4):e for _,e in events if data[e.field(4)]==3}
    page_payloads=[n.payload_table(b,creators[ident]) for ident in pages]
    if any(p is None for p in page_payloads):
        # User-supplied 16.13.2 43-page document: the first page is a
        # payloadless type-3 marker, all subsequent pages insert after their
        # immediately preceding page via a one-slot anchor. Do not generalize
        # this approval to arbitrary mixtures or undocumented anchors.
        if (version!=20 or page_payloads[0] is not None
                or any(p is None or p.slots!=1 or not p.field(0)
                       for p in page_payloads[1:])):
            raise NativeWriteError('裸页面标记与锚定页面结构尚未匹配已知逐页格式')
        for previous,p in zip(pages,page_payloads[1:]):
            field=p.field(0)
            if not b.valid(field,12) or struct.unpack_from('<3I',data,field)!=(0,previous,0):
                raise NativeWriteError('页面插入锚点不是已验证的顺序关联')
        records=n.page_records(events,b)
        if [record['id'] for record in records]!=pages:
            raise NativeWriteError('原生逐页顺序与事件索引不一致')
        known=set(pages)
        for _,event in events:
            kind=data[event.field(4)]
            if kind not in (15,18,22):continue
            payload=n.payload_table(b,event)
            ref=payload.field(2 if kind==22 else 0) if payload else 0
            if not ref or not b.valid(ref,12):
                raise NativeWriteError('原生笔迹或图片缺少完整页面归属')
            actor,page_id,page_part=struct.unpack_from('<3I',data,ref)
            if actor!=0 or page_id not in known or page_part!=0:
                raise NativeWriteError('发现无法安全重映射的笔迹或图片页面引用')
            if kind==22 and payload.slots>=21 and payload.field(20):
                flag=payload.field(20)
                if not b.valid(flag,1) or data[flag] not in (0,1):
                    raise NativeWriteError('图片存在未知状态值，禁止页面破坏性编辑')
    from formats.ne_page_model import resolve_page_order, PageModelError
    try:
        layout=resolve_page_order(events,b,n.page_records(events,b,resolve=False))
    except PageModelError as exc:
        raise NativeWriteError(str(exc)) from exc
    pages=[record['id'] for record in layout['records']]
    return b,r,events,pages


def rewrite_pages(data,plan):
    b,r,events,page_ids=validate_page_structure(data)
    if not plan:raise NativeWriteError('笔记至少保留一页')
    sources=[p['source'] for p in plan if p['source'] is not None]
    if len(set(sources))!=len(sources) or any(not isinstance(i,int) or not 0<=i<len(page_ids) for i in sources):
        raise NativeWriteError('原始页面映射无效')
    for p in plan:
        if not all(math.isfinite(v) and 0<v<=10000 for v in (p['width'],p['height'])):raise NativeWriteError('页面尺寸无效')
        bg=p.get('background_source')
        if bg is not None and (not isinstance(bg,int) or not 0<=bg<len(page_ids)):raise NativeWriteError('页面背景引用无效')
    by_id={b.u32(e.field(0)+4):e for _,e in events if b.u32(e.field(0))==0};kept_ids={page_ids[i] for i in sources}
    retained=[]
    for off,e in events:
        typ=data[e.field(4)]
        if typ in (3,4):continue
        if typ in (15,18,22):
            p=n.payload_table(b,e);ref=p.field(2 if typ==22 else 0) if p else 0
            if not ref or b.u32(ref+4) not in kept_ids:continue
        retained.append((off,e))
    # Native drawing groups are entity-reference vectors, not page objects.
    # Remove members deleted with their page and then empty nested groups.
    groups={}
    for off,event in retained:
        if data[event.field(4)]!=20:continue
        payload=n.payload_table(b,event);field=payload.field(0)
        vector_start=field+b.u32(field);length=b.u32(vector_start)
        members=[struct.unpack_from('<2I',data,vector_start+4+i*8) for i in range(length)]
        if any(actor for actor,_ in members):raise NativeWriteError('多作者图形组尚未验证')
        groups[b.u32(event.field(0)+4)]=(off,event,members)
    alive={b.u32(event.field(0)+4) for _,event in retained}
    while True:
        empty={ident for ident,(_,_,members) in groups.items()
               if ident in alive and not any(member in alive for _,member in members)}
        if not empty:break
        alive-=empty
    retained=[(off,event) for off,event in retained if b.u32(event.field(0)+4) in alive]
    hidden=n.hidden_stroke_event_ids(events,b)
    ink_by_page={ident:[] for ident in page_ids}
    for _,event in events:
        ident=b.u32(event.field(0)+4)
        if data[event.field(4)] not in (15,18) or ident in hidden:continue
        payload=n.payload_table(b,event)
        ref=payload.field(0) if payload else 0
        if ref and b.u32(ref+4) in ink_by_page:ink_by_page[b.u32(ref+4)].append(ident)
    copies=[]
    for page in plan:
        ids=[]
        if page['source'] is None and page.get('background_source') is not None:
            # Preserve opaque native ink too. Parsed ink is cloned only when
            # its complete visible geometry still matches the captured source.
            eligible=page.get('native_copy_ids',set())
            modeled=page.get('native_model_ids',set())
            ids=[ident for ident in ink_by_page[page_ids[page['background_source']]]
                 if ident in eligible or ('native_model_ids' in page and ident not in modeled)]
        copies.append(ids)
    count=len(retained)+len(plan)+sum(len(p['images']) for p in plan if p['source'] is None)+sum(map(len,copies))
    builder=Builder();builder.data=bytearray(data[:r.offset+r.osize])
    vector=builder.vector(b'\0'*(count*4),count);builder.uoffset(r.field(6),vector)
    fixups=[];new_pages=[];new_images=[];sequence=max(by_id)+1
    copied_locks={};copied_layers={}
    timestamp=max(int(time.time()*1000),max((struct.unpack_from('<Q',data,e.field(1))[0] for _,e in events if e.field(1)),default=0)+1)
    def clone(table):
        builder.align(2);vt=builder.add(data[table.vtable:table.vtable+table.vsize])
        while len(builder.data)%8!=table.offset%8:builder.data.append(0)
        start=builder.add(data[table.offset:table.offset+table.osize]);struct.pack_into('<i',builder.data,start,start-vt)
        return start,{s:start+table.field(s)-table.offset for s in range(table.slots) if table.field(s)}
    def envelope(typ):
        nonlocal sequence,timestamp
        if sequence>=2**32:raise NativeWriteError('事件编号耗尽')
        seq=sequence;sequence+=1;timestamp+=1
        off,fields=builder.table(6,{0:(struct.pack('<2I',0,seq),4),1:(struct.pack('<Q',timestamp),8),4:(bytes([typ]),1),5:(b'\0'*4,4)})
        return seq,off,fields
    # A structural rewrite is a resolved snapshot. Old position operations
    # must not rearrange it again, and page tombstones must not hide newly
    # retained identities. Preserve entity hide/restore vectors verbatim.
    visibility_offsets={}
    for ident,(offset,event,members) in groups.items():
        if ident not in alive:continue
        remaining=[member for member in members if member[1] in alive]
        if remaining==members:continue
        new_event,fields=clone(event)
        payload,refs=builder.table(1,{0:(bytes(4),4)})
        builder.uoffset(fields[5],payload)
        member_vector=builder.vector(b''.join(struct.pack('<2I',*ref) for ref in remaining),len(remaining))
        builder.uoffset(refs[0],member_vector)
        visibility_offsets[offset]=new_event
    for offset,event in retained:
        if data[event.field(4)]!=25:continue
        payload=n.payload_table(b,event)
        if payload is None or not any(payload.field(i) for i in (2,3)):continue
        if payload.slots>4:raise NativeWriteError('页面可见性事件含未知扩展')
        new_event,fields=clone(event)
        values={slot:(bytes(4),4) for slot in (0,1) if payload.field(slot)}
        replacement,refs=builder.table(2,values)
        builder.uoffset(fields[5],replacement)
        for slot,field in refs.items():
            old=payload.field(slot);fixups.append((field,old+b.u32(old)))
        visibility_offsets[offset]=new_event
    previous=None
    for page,ink_ids in zip(plan,copies):
        source=page['source'];background=None
        if source is not None:
            seq=page_ids[source];e=by_id[seq];off,fields=clone(e);p=n.payload_table(b,e)
        else:seq,off,fields=envelope(3)
        background_source=source if source is not None else page.get('background_source')
        if background_source is not None:
            p=n.payload_table(b,by_id[page_ids[background_source]])
            if p and p.field(1):background=p.field(1)+b.u32(p.field(1))
        new_pages.append(off)
        pf={1:(b'\0'*4,4)}
        if previous is not None:pf[0]=(struct.pack('<3I',0,previous,0),4)
        payload,pp=builder.table(2,pf);builder.uoffset(fields[5],payload)
        if background is not None:fixups.append((pp[1],background))
        else:
            bg,_=builder.table(5,{3:(struct.pack('<2f',page['width'],page['height']),4)})
            builder.uoffset(pp[1],bg)
        if source is None:
            for ink_id in ink_ids:
                original=by_id[ink_id]
                original_payload=n.payload_table(b,original)
                if not original_payload or original_payload.slots>20:
                    raise NativeWriteError('笔迹扩展字段未知，无法无损复制')
                kind=data[original.field(4)]
                _,io,ef=envelope(kind);new_images.append(io)
                ip,ipf=clone(original_payload);builder.uoffset(ef[5],ip)
                struct.pack_into('<3I',builder.data,ipf[0],0,seq,0)
                # Official CreateInk schema: center/custom/fill paths and
                # styleMap are vectors; every other field is inline scalar.
                for slot in ((5,) if kind==18 else (9,10,11,13)):
                    field=original_payload.field(slot)
                    if field:fixups.append((ipf[slot],field+b.u32(field)))
            for image in page['images']:
                image_id=int(image['data-event-id']);original=by_id.get(image_id)
                if original is None or data[original.field(4)]!=22:raise NativeWriteError('图片原事件缺失')
                image_seq,io,ef=envelope(22);new_images.append(io)
                original_payload=n.payload_table(b,original);ip,ipf=clone(original_payload);builder.uoffset(ef[5],ip)
                struct.pack_into('<3I',builder.data,ipf[2],0,seq,0)
                # A clipboard page is a snapshot, not an alias of the source
                # event. Its geometry may predate a subsequent source undo.
                if all('data-'+key in image for key in ('x','y','width','height')):
                    x,y,w,h=(float(image['data-'+key]) for key in ('x','y','width','height'))
                    if not all(math.isfinite(v) for v in (x,y,w,h)) or min(w,h)<=0:
                        raise NativeWriteError('复制图片的快照坐标无效')
                    if 3 not in ipf or 6 not in ipf:raise NativeWriteError('复制图片缺少几何字段')
                    struct.pack_into('<2f',builder.data,ipf[3],x,y)
                    struct.pack_into('<2f',builder.data,ipf[6],w,h)
                if 'data-locked' in image:
                    copied_locks[image_seq]={'current':str(image['data-locked']).lower() in ('true','1')}
                if 'data-z-index' in image:
                    copied_layers[image_seq]={'current':int(image['data-z-index'])}
                fixups.append((ipf[10],original_payload.field(10)+b.u32(original_payload.field(10))))
        previous=seq
    builder.align(8);shift=len(builder.data);builder.add(data)
    for field,target in fixups:builder.uoffset(field,target+shift)
    for slot in (3,5):
        field=r.field(slot)
        if field:builder.uoffset(field,field+b.u32(field)+shift)
    # Keep global settings first, then pages in visible order, then their content.
    globals_=[off+shift for off,e in retained if data[e.field(4)] in (1,7,8,12)]
    content=[visibility_offsets.get(off,off+shift) for off,e in retained if data[e.field(4)] not in (1,7,8,12)]
    for i,off in enumerate(globals_+new_pages+content+new_images):builder.uoffset(vector+4+4*i,off)
    result=bytes(builder.data);vb,_,ve,vpages=validate_page_structure(result)
    records=n.page_records(ve,vb)
    if len(vpages)!=len(plan) or len(ve)!=count:raise NativeWriteError('页面写入校验失败')
    original_records=n.page_records(events,b)
    for page,record in zip(plan,records):
        background_source=page['source'] if page['source'] is not None else page.get('background_source')
        expected=original_records[background_source]['pdf'] if background_source is not None else None
        if record['pdf']!=expected:raise NativeWriteError('页面底图写入校验失败')
    if copied_locks or copied_layers:
        from formats.ne_native import build_bundle
        state=n.native_image_event_state(ve,vb)
        copied_locks={ident:dict(original=bool(state.get(ident,{}).get('locked',False)),current=record['current'])
                      for ident,record in copied_locks.items()
                      if bool(state.get(ident,{}).get('locked',False))!=record['current']}
        copied_layers={ident:dict(original=state.get(ident,{}).get('z_index',0),current=record['current'])
                       for ident,record in copied_layers.items()}
        result=build_bundle(result,set(),[],native_image_lock_events=copied_locks,
                            native_image_layer_changes=copied_layers)
        validate_page_structure(result)
    return result
