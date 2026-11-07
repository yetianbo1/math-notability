"""Promote legacy Viewer overlays to official native CreateBlock images.

The checked schema is recorded in core.ne_bundle_schema (CreateBlock/ImageAsset/
AssetMetadata). Existing tables and resource references remain byte-for-byte
in a relocated suffix, as in the native ink writer.
"""
import hashlib
import struct
import time

import formats.ne_ntb as n
from formats.ne_native import Builder,NativeWriteError,read_structure


def _page_refs(buf,root,events,count):
    version=buf.u16(root.field(7)) if root.field(7) else None
    if root.slots!=8 or root.field(2) or version is None or version<18:
        raise NativeWriteError('此 NTB 页面版本尚未验证原生图片写入')
    layout=n.grouped_pdf_page_records(events,buf) or n.mixed_grouped_pdf_page_records(events,buf)
    from formats.ne_paper_graph import paper_graph
    layout=paper_graph(events,buf,version) or layout
    if layout:
        refs=[(0,*ref) for ref in layout['references']]
    else:
        layout=n.native_paper_page_records(events,buf,version)
        signature=n.native_paper_range_signature(events,buf,version)
        records=n.page_records(events,buf)
        allocated=n.document_page_count(events,buf)
        if layout:
            refs=[(0,r['id'],r.get('page_index',0)) for r in layout['records']]
        elif signature:
            group,total=signature
            template=n.document_paper_template(events,buf)
            total=1 if n._known_single_sheet_paper(events,buf,total,records,template) else total
            refs=[(0,group,index) for index in range(total)]
        elif version>=19 and records and allocated<=len(records):
            refs=[(0,r['id'],0) for r in records]
        else:
            ranges=[(buf.u32(e.field(0)+4),n.field_u32(buf,n.payload_table(buf,e),2))
                    for _,e in events if buf.data[e.field(4)]==3
                    and n.field_u32(buf,n.payload_table(buf,e),2)]
            if len(ranges)!=1:
                raise NativeWriteError('无法确定插入图片的原生页面引用')
            group,total=ranges[0]
            refs=[(0,group,index) for index in range(total)]
    if len(refs)!=count or len(set(refs))!=count:
        raise NativeWriteError('图片页数与原生页面引用不一致')
    return refs


def embed_images(data,pages,assets):
    """Return (native bundle, official ZIP assets), without private overlays."""
    if not any(page['images'] for page in pages):return data,{}
    buf,root,events=read_structure(data)
    refs=_page_refs(buf,root,events,len(pages))
    page_by_ref={ref:page for page,ref in enumerate(refs)}
    state=n.native_image_event_state(events,buf)
    ids=set();last_time=0;native=[[] for _ in pages]
    for _,event in events:
        ref=event.field(0)
        if not ref or not buf.valid(ref,8):raise NativeWriteError('原生事件缺少编号')
        actor,ident=struct.unpack_from('<2I',data,ref)
        if actor or ident in ids:raise NativeWriteError('多作者或重复事件编号尚未验证图片写入')
        ids.add(ident)
        stamp=event.field(1)
        timestamp=struct.unpack_from('<Q',data,stamp)[0] if stamp else 0
        last_time=max(last_time,timestamp)
        kind=data[event.field(4)]
        if kind not in (15,18,22):continue
        payload=n.payload_table(buf,event)
        at=payload.field(2 if kind==22 else 0) if payload else 0
        if not at or not buf.valid(at,12):raise NativeWriteError('原生内容缺少页面引用')
        page=page_by_ref.get(struct.unpack_from('<3I',data,at))
        if page is None:continue
        slot={15:14,18:12,22:9}[kind]
        z=payload.field(slot)
        z=struct.unpack_from('<Q',data,z)[0] if z else timestamp
        native[page].append((ident,state.get(ident,{}).get('z_index',z)))
    encoded={};image_sizes={}
    import fitz
    for digest,blob in assets.items():
        pixmap=fitz.Pixmap(blob)
        if not blob.startswith(b'\x89PNG\r\n\x1a\n') or pixmap.width<=0 or pixmap.height<=0:
            raise NativeWriteError('插入图片不是有效 PNG')
        content_hash=hashlib.sha512(blob).digest()
        name='assets/'+content_hash.hex()+'.png'
        encoded[name]=blob
        image_sizes[digest]=(content_hash,pixmap.width,pixmap.height,len(blob))
    operations=[];promoted=[]
    for page,record in enumerate(pages):
        by_id={image['id']:image for image in record['images']}
        pivot=record['stack'].index('ink')
        below=record['stack'][:pivot];above=record['stack'][pivot+1:]
        existing=native[page]
        low=min((z for _,z in existing),default=len(below))
        high=max((z for _,z in existing),default=len(below))
        if low<len(below) or high+len(above)>=2**64:
            # Make space around the existing native scene while preserving
            # its relative order. Unknown/opaque native tables are untouched.
            for rank,(ident,_) in enumerate(sorted(existing,key=lambda v:(v[1],v[0]))):
                operations.append(('layer',ident,len(below)+rank))
            low=len(below);high=low+max(0,len(existing)-1)
        for index,identifier in enumerate(below):
            operations.append(('image',page,by_id[identifier],low-len(below)+index))
        for index,identifier in enumerate(above):
            operations.append(('image',page,by_id[identifier],high+index+1))
    # Locked placements get the same verified ModifyBlock lock event as
    # the regular native-image editor, rather than an inferred inline flag.
    count=len(events)+len(operations)+sum(bool(image.get('locked')) for page in pages for image in page['images'])
    builder=Builder();builder.data=bytearray(data[:root.offset+root.osize])
    vector=builder.vector(bytes(4*count),count)
    builder.uoffset(root.field(6),vector)
    offsets=[];sequence=max(ids)+1;timestamp=max(int(time.time()*1000),last_time+1)
    def envelope(kind):
        nonlocal sequence,timestamp
        if sequence>=2**32:raise NativeWriteError('原生事件编号耗尽')
        ident=sequence;sequence+=1
        event,slots=builder.table(6,{0:(struct.pack('<2I',0,ident),4),
            1:(struct.pack('<Q',timestamp),8),4:(bytes((kind,)),1),5:(bytes(4),4)})
        timestamp+=1;offsets.append(event)
        return ident,slots[5]
    def string(text):
        blob=text.encode('utf-8')
        return builder.vector(blob+b'\0',len(blob))
    for operation in operations:
        if operation[0]=='layer':
            _,ident,z=operation
            _,target=envelope(24)
            table,slots=builder.table(1,{0:(bytes(4),4)})
            builder.uoffset(target,table)
            positions=builder.vector(bytes(4),1);builder.uoffset(slots[0],positions)
            position,_=builder.table(6,{0:(struct.pack('<2I',0,ident),4),5:(struct.pack('<Q',z),8)})
            builder.uoffset(positions+4,position)
            continue
        _,page,image,z=operation
        content_hash,width,height,size=image_sizes[image['asset']]
        ident,target=envelope(22)
        table,slots=builder.table(12,{
            0:(b'\x01',1),2:(struct.pack('<3I',*refs[page]),4),
            3:(struct.pack('<2f',image['x'],image['y']),4),
            6:(struct.pack('<2f',image['width'],image['height']),4),
            9:(struct.pack('<Q',z),8),10:(bytes(4),4),
            11:(struct.pack('<4f',0,0,width,height),4),
        })
        builder.uoffset(target,table)
        asset,asset_slots=builder.table(2,{0:(bytes(4),4),1:(struct.pack('<2f',width,height),4)})
        builder.uoffset(slots[10],asset)
        metadata,metadata_slots=builder.table(4,{0:(content_hash,4),1:(bytes(4),4),2:(bytes(4),4),3:(struct.pack('<I',size),4)})
        builder.uoffset(asset_slots[0],metadata)
        builder.uoffset(metadata_slots[1],string('Image.png'))
        builder.uoffset(metadata_slots[2],string('image/png'))
        promoted.append((ident,image))
        if image.get('locked'):
            _,target=envelope(23)
            table,slots=builder.table(18,{0:(bytes(4),4),17:(b'\x01',1)})
            builder.uoffset(target,table)
            builder.uoffset(slots[0],builder.vector(struct.pack('<2I',0,ident),1,alignment=4))
    builder.align(8);shift=len(builder.data);builder.add(data)
    for slot in (3,5):
        field=root.field(slot)
        if field:builder.uoffset(field,field+buf.u32(field)+shift)
    for index,(offset,_) in enumerate(events):builder.uoffset(vector+4+4*index,offset+shift)
    for index,offset in enumerate(offsets,len(events)):builder.uoffset(vector+4+4*index,offset)
    result=bytes(builder.data)
    verify,_,written=read_structure(result)
    if len(written)!=count or result[shift:]!=data:
        raise NativeWriteError('原生图片事件保存校验失败')
    states=n.native_image_event_state(written,verify)
    for ident,image in promoted:
        if bool(states.get(ident,{}).get('locked',False))!=bool(image.get('locked')):
            raise NativeWriteError('原生图片锁定状态校验失败')
    return result,encoded
