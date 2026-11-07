"""Checked handwritten range plus real trailing-page expansion.

The supplied inequality note creates two pages in one CreatePage, followed by
another CreatePage anchored to the last member. The latter creates a real blank
page. This narrow adapter normalizes that family to the existing per-page writer.
"""
import struct
import formats.ne_ntb as n


def range_page_info(data, *, structure=None):
    b,root,events=structure if structure is not None else (n.Buffer(data),None,None)
    if structure is None:root=n.table_at(b,b.u32(0)) if b.valid(0,4) else None
    if (not root or root.slots!=8 or root.field(2) or not root.field(7)
            or b.u16(root.field(7))!=20):return None
    if events is None:events=n.table_vector(b,root,6)
    kinds=[]; ids=[]; creators=[]
    for _,e in events:
        if (e.slots!=6 or not e.field(0) or not b.valid(e.field(0),8)
                or b.u32(e.field(0)) or not e.field(4)
                or e.field(2) or e.field(3)):return None
        kind=data[e.field(4)];kinds.append(kind);ids.append(b.u32(e.field(0)+4))
        if kind not in (1,3,7,15):return None
        if kind==3:creators.append(e)
        if kind==7:
            p=n.payload_table(b,e)
            if (not p or p.slots!=2 or p.field(0) or not p.field(1)
                    or not b.valid(p.field(1),4) or b.u32(p.field(1))!=10):return None
    if len(set(ids))!=len(ids) or kinds.count(1)!=1 or kinds.count(7)!=1 or len(creators)!=2:
        return None
    first,last=creators
    p=n.payload_table(b,first);tail=n.payload_table(b,last)
    if (not p or p.slots!=3 or p.field(0) or p.field(1) or not p.field(2)
            or not b.valid(p.field(2),4) or not tail or tail.slots!=1
            or not tail.field(0) or not b.valid(tail.field(0),12)):return None
    count=b.u32(p.field(2));group=b.u32(first.field(0)+4)
    if not 2<=count<10000 or struct.unpack_from('<3I',data,tail.field(0))!=(0,group,count-1):
        return None
    used=set()
    for _,e in events:
        if data[e.field(4)]!=15:continue
        ink=n.payload_table(b,e);ref=ink.field(0) if ink else 0
        if not ref or not b.valid(ref,12):return None
        actor,owner,index=struct.unpack_from('<3I',data,ref)
        if actor or owner!=group or index>=count:return None
        used.add(index)
    # Establish the content-bearing range; do not infer display trimming for
    # other notebooks with multiple preallocated empty sheets.
    if used!=set(range(count)):return None
    styles=list(n._paper_styles(events,b))
    if len(styles)!=1:return None
    w,h,paper,style=styles[0]
    if (abs(w-567.)>.01 or abs(h-744.187561)>.01 or not style
            or not style.field(0) or data[style.field(0)]!=1
            or not style.field(5) or data[style.field(5)]!=13):return None
    return {'count':count+1,'range_count':count,'group':group,
            'tail':b.u32(last.field(0)+4),'width':w,'height':h}


def expand_range_pages(data):
    """Preserve ink vectors/settings while giving every sheet its own page ID."""
    info=range_page_info(data)
    if info is None:return data
    from formats.ne_native import Builder, read_structure, NativeWriteError
    b,root,events=read_structure(data)
    maximum=max(b.u32(e.field(0)+4) for _,e in events)
    page_ids=[info['group']]+list(range(maximum+1,maximum+info['range_count']))+[info['tail']]
    if max(page_ids)>=2**32:raise NativeWriteError('页面事件编号超过上限')
    globals_=[off for off,e in events if data[e.field(4)] in (1,7)]
    content=[off for off,e in events if data[e.field(4)]==15]
    builder=Builder();builder.data=bytearray(data[:root.offset+root.osize])
    count=len(globals_)+len(page_ids)+len(content)
    vector=builder.vector(bytes(4*count),count);builder.uoffset(root.field(6),vector)
    timestamp=max((struct.unpack_from('<Q',data,e.field(1))[0]
                   for _,e in events if e.field(1)),default=0)
    pages=[]
    for index,ident in enumerate(page_ids):
        env,fields=builder.table(6,{0:(struct.pack('<2I',0,ident),4),
            1:(struct.pack('<Q',timestamp+index+1),8),4:(b'\x03',1),5:(bytes(4),4)})
        pf={1:(bytes(4),4)}
        if index:pf[0]=(struct.pack('<3I',0,page_ids[index-1],0),4)
        payload,refs=builder.table(2,pf);builder.uoffset(fields[5],payload)
        background,_=builder.table(5,{3:(struct.pack('<2f',info['width'],info['height']),4)})
        builder.uoffset(refs[1],background);pages.append(env)
    builder.align(8);shift=len(builder.data);builder.add(data)
    for slot in (3,5):
        field=root.field(slot)
        if field:builder.uoffset(field,field+b.u32(field)+shift)
    for off,e in events:
        if data[e.field(4)]!=15:continue
        p=n.payload_table(b,e);ref=p.field(0);old_index=b.u32(ref+8)
        struct.pack_into('<3I',builder.data,ref+shift,0,page_ids[old_index],0)
    for index,offset in enumerate([o+shift for o in globals_]+pages+[o+shift for o in content]):
        builder.uoffset(vector+4+4*index,offset)
    result=bytes(builder.data)
    from formats.ne_pagewrite import validate_page_structure
    if validate_page_structure(result)[3]!=page_ids:
        raise NativeWriteError('原生分组页面展开校验失败')
    return result
