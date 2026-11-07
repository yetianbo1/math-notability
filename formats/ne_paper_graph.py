"""Resolve modern non-PDF notebooks containing several CreatePage ranges."""
import struct
import formats.ne_ntb as n


def paper_graph(events, buf, version):
    if version != 20:
        return None
    creations=[]
    ranges=0
    for _, event in events:
        if not event.field(4) or buf.data[event.field(4)] != 3:
            continue
        payload=n.payload_table(buf,event)
        if payload and payload.field(1):
            return None
        count=(n.field_u32(buf,payload,2) if payload else None) or 1
        if not 1 <= count <= 10000 or sum(x[1] for x in creations)+count>10000:
            return None
        ranges+=count>1
        creations.append((buf.u32(event.field(0)+4),count))
    # Preserve the existing specialized adapters for modern paper templates.
    # Legacy custom-size paper uses real marker-created trailing sheets.
    if not ranges or (ranges==1 and n.document_paper_template(events,buf) is not None
                      and all(n.payload_table(buf,e) is None or not n.payload_table(buf,e).field(0)
                              for _,e in events if e.field(4) and buf.data[e.field(4)]==3)):
        return None
    if ranges==1 and n.document_paper_template(events,buf) is not None:
        # Do not change the previously validated Python/A4 terminator families.
        template=n.document_paper_template(events,buf)
        if not template.get('legacy_paper'):
            # Blank document-level paper can create a genuine trailing sheet
            # with a parent anchor after its range. Keep legacy terminator
            # families on their specialized adapters.
            kinds={buf.data[e.field(4)] for _,e in events if e.field(4)}
            anchored=any(n.payload_table(buf,e) and n.payload_table(buf,e).field(0)
                         for _,e in events if e.field(4) and buf.data[e.field(4)]==3)
            if (template.get('kind')!='blank' or not anchored or not kinds<={1,3,4,15,18,22,23,24,25}
                    or n._compact_supported_paper_type(events,buf) is not None
                    or n._known_a4_blank_layout(events,buf)):
                return None
    width,height=n.document_page_size(events,buf) or (595.,842.)
    records=[dict(id=ident,event_id=ident,page_index=index,pdf=None,width=width,height=height)
             for ident,count in creations for index in range(count)]
    from formats.ne_page_model import resolve_page_order,PageModelError
    try:
        return resolve_page_order(events,buf,records)
    except PageModelError:
        return None


from core.ne_save_work import checked_once, checkpoint


@checked_once
def expand_paper_graph(data):
    """Materialize only page identities; all native content keeps its bytes."""
    from formats.ne_native import read_structure,Builder,NativeWriteError
    b,r,events=read_structure(data)
    layout=paper_graph(events,b,b.u16(r.field(7)) if r.field(7) else None)
    if layout is None:
        return data
    # Prove that every field we relocate has its documented schema.
    from core.ne_bundle_compact import validate_bundle_schema
    validate_bundle_schema(data)
    maximum=max(b.u32(e.field(0)+4) for _,e in events)
    mapping={ref:maximum+1+i for i,ref in enumerate(layout['references'])}
    if max(mapping.values())>=2**32:
        raise NativeWriteError('页面事件编号超过上限')
    retained=[(off,e) for off,e in events if data[e.field(4)] not in (3,4)]
    builder=Builder();builder.data=bytearray(data[:r.offset+r.osize])
    count=len(retained)+len(mapping)
    vector=builder.vector(bytes(count*4),count);builder.uoffset(r.field(6),vector)
    pages=[];previous=None
    for record in layout['records']:
        ident=mapping[record['id'],record['page_index']]
        env,ef=builder.table(6,{0:(struct.pack('<2I',0,ident),4),4:(b'\x03',1),5:(bytes(4),4)})
        fields={1:(bytes(4),4)}
        if previous is not None:fields[0]=(struct.pack('<3I',0,previous,0),4)
        payload,pf=builder.table(2,fields);builder.uoffset(ef[5],payload)
        bg,_=builder.table(5,{3:(struct.pack('<2f',record['width'],record['height']),4)})
        builder.uoffset(pf[1],bg);pages.append(env);previous=ident
    builder.align(8);shift=len(builder.data);builder.add(data)
    for slot in (3,5):
        field=r.field(slot)
        if field:builder.uoffset(field,field+b.u32(field)+shift)
    for _,event in retained:
        kind=data[event.field(4)];payload=n.payload_table(b,event)
        if kind in (15,18,22):
            ref=payload.field(2 if kind==22 else 0) if payload else 0
            if not ref or not b.valid(ref,12):raise NativeWriteError('原生对象缺少页面归属')
            actor,owner,index=struct.unpack_from('<3I',data,ref)
            if actor or (owner,index) not in mapping:raise NativeWriteError('原生对象引用未知页面')
            struct.pack_into('<3I',builder.data,ref+shift,0,mapping[owner,index],0)
        elif kind==24:
            raise NativeWriteError('多分组页面包含位置修改，尚不能安全展开')
        elif kind==25 and payload and any(payload.field(i) and b.u32(payload.field(i)+b.u32(payload.field(i))) for i in (2,3)):
            raise NativeWriteError('多分组页面包含页面可见性历史，尚不能安全展开')
    for i,offset in enumerate(pages+[off+shift for off,_ in retained]):
        builder.uoffset(vector+4+i*4,offset)
    result=bytes(builder.data)
    from formats.ne_pagewrite import validate_page_structure
    if len(validate_page_structure(result)[3])!=len(mapping):
        raise NativeWriteError('多分组页面展开校验失败')
    return result
