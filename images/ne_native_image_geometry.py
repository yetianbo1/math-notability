"""Validated native image geometry patching, including type-24 history.

This is intentionally NOT a writer for type-23 lock/unlock events, deletion,
new pictures or z-order events. It modifies existing float32 values only.
Validated on the user's official 16.13.2 image-state samples.
"""
from __future__ import annotations
import math
import struct
import formats.ne_ntb as n
from images.ne_image_resize import has_original_aspect


class NativeImageGeometryError(ValueError):
    pass


def _image_geometry(buf, event):
    t=n.payload_table(buf,event)
    origin=t.field(3) if t else 0
    size=t.field(6) if t else 0
    scale=t.field(5) if t else 0
    if not origin or not size or not buf.valid(origin,8) or not buf.valid(size,8):
        raise NativeImageGeometryError('原生图片缺少位置或基础尺寸字段')
    xy=struct.unpack_from('<2f',buf.data,origin)
    base=struct.unpack_from('<2f',buf.data,size)
    scale_values=(struct.unpack_from('<2f',buf.data,scale)
                  if scale and buf.valid(scale,8) else (1.,1.))
    if (not all(math.isfinite(v) for v in (*xy,*base,*scale_values))
            or min(*base,*scale_values)<=0):
        raise NativeImageGeometryError('原生图片几何数据无效')
    return (xy,base,scale_values,origin,size,scale if scale and buf.valid(scale,8) else None)


def _effective(buf,event,edits):
    xy,base,scale,origin,size,scale_pos=_image_geometry(buf,event)
    if 'position' in edits:xy=edits['position'][0]
    if 'scale' in edits:scale=edits['scale'][0]
    return (xy[0],xy[1],base[0]*scale[0],base[1]*scale[1])


def _near(a,b):
    return abs(float(a)-float(b))<=max(.002,abs(float(b))*.00001)


def _page_reference(buf,events,index):
    """Resolve a visible page to the existing native ID/range part."""
    from formats.ne_paper_graph import paper_graph
    root=n.table_at(buf,buf.u32(0))
    layout=paper_graph(events,buf,buf.u16(root.field(7)) if root.field(7) else None)
    if layout:
        if type(index) is not int or not 0<=index<len(layout['references']):
            raise NativeImageGeometryError('图片目标页超出范围')
        return (0,*layout['references'][index])
    if type(index) is not int or index<0:raise NativeImageGeometryError('图片目标页无效')
    records=n.page_records(events,buf)
    count=n.document_page_count(events,buf)
    signature=n.native_paper_range_signature(events,buf)
    if signature or (len(records)==1 and count>1):
        if index>=count:raise NativeImageGeometryError('图片目标页超出范围')
        return (0,signature[0] if signature else records[0]['id'],index)
    if index>=len(records):raise NativeImageGeometryError('图片目标页超出范围')
    return (0,records[index]['id'],0)


def _patch_page(buf,events,event,ident,change,changed):
    original=_page_reference(buf,events,change['original_page'])
    target=_page_reference(buf,events,change['current_page'])
    payload=n.payload_table(buf,event);field=payload.field(2)
    if not field or not buf.valid(field,12) or struct.unpack_from('<3I',buf.data,field)!=original:
        raise NativeImageGeometryError('图片原始页面归属不一致，请重新打开')
    struct.pack_into('<3I',changed,field,*target)
    # Historical absolute positions can carry a page reference as well.
    # Retarget those records together so official replay cannot move it back.
    for _,op in events:
        kind=buf.data[op.field(4)] if op.field(4) else -1
        payload=n.payload_table(buf,op)
        if kind==24 and payload:
            for _,record in n.table_vector(buf,payload,0):
                ref=record.field(0)
                if not ref or not buf.valid(ref,8) or struct.unpack_from('<2I',buf.data,ref)!=(0,ident):continue
                page=record.field(1)
                if page:
                    if not buf.valid(page,12):raise NativeImageGeometryError('图片历史页面引用损坏')
                    struct.pack_into('<3I',changed,page,*target)
        elif kind==23 and payload and payload.field(2):
            ref=payload.field(0)
            if not ref or not buf.valid(ref,4):raise NativeImageGeometryError('图片历史引用无效')
            vector=ref+buf.u32(ref)
            if not buf.valid(vector,4) or not buf.valid(vector+4,8*buf.u32(vector)):
                raise NativeImageGeometryError('图片历史引用无效')
            refs=[struct.unpack_from('<2I',buf.data,vector+4+i*8) for i in range(buf.u32(vector))]
            if (0,ident) in refs:
                # A shared ModifyBlock page field cannot move just one target.
                raise NativeImageGeometryError('图片含共享页面修改历史，暂不能安全跨页移动')
    return target


def patch_image_rectangles(bundle:bytes,changes:dict)->bytes:
    """Apply verified edits to the FINAL visible geometry of native images.

    Type-24 transforms supersede the type-22 origin / scale. For an existing
    transform, patch its final float32 fields rather than the type-22 values
    which the official app would overwrite on replay. Locked type-23/slot20 images
    are never changed. Reject a stale baseline or unknown original image ID.
    """
    if not changes:return bundle
    if len(changes)>5000:raise NativeImageGeometryError('原生图片数量超出安全上限')
    buf=n.Buffer(bundle)
    root=n.table_at(buf,buf.u32(0)) if buf.valid(0,4) else None
    if root is None:raise NativeImageGeometryError('NTB 事件根表无效')
    events=n.table_vector(buf,root,6)
    state=n.native_image_event_state(events,buf)
    changed=bytearray(bundle)
    matched=set()
    for _,event in events:
        pos=event.field(4);ref=event.field(0)
        if not (pos and ref and buf.valid(pos,1) and buf.valid(ref,8)) or buf.data[pos]!=22:
            continue
        actor,ident=struct.unpack_from('<2I',bundle,ref)
        if actor!=0 or ident not in changes:continue
        if ident in matched:raise NativeImageGeometryError('图片事件编号重复')
        info=state.get(ident,{})
        if info.get('locked'):
            raise NativeImageGeometryError('图片已在官方 Notability 中锁定，不能写入位置或大小')
        expected=changes[ident]
        if set(expected) not in ({'original','current'},{'original','current','original_page','current_page'}):
            raise NativeImageGeometryError('图片修改记录不完整')
        original,current=expected['original'],expected['current']
        if len(original)!=4 or len(current)!=4:
            raise NativeImageGeometryError('图片坐标必须有四项')
        if (not all(math.isfinite(float(v)) for v in (*original,*current))
                or not all(_near(a,b) for a,b in zip(_effective(buf,event,info),original))):
            raise NativeImageGeometryError('图片变换历史与原始编辑基准不一致，请重新打开')
        x,y,w,h=map(float,current)
        # Last-resort safety boundary: even a hand-built change dictionary
        # must not write non-uniform native image scaling to the noteBundle.
        if not has_original_aspect(original[2],original[3],w,h):
            raise NativeImageGeometryError('图片缩放必须保持原始宽高比例，不能拉伸变形')
        if not (0<=x<=100000 and 0<=y<=100000 and 0.1<=w<=100000 and 0.1<=h<=100000):
            raise NativeImageGeometryError('图片坐标或尺寸超出安全范围')
        base_xy,base_size,old_scale,origin_pos,size_pos,native_scale_pos=_image_geometry(buf,event)
        point_pos=info['position'][1] if 'position' in info else origin_pos
        # Last type-24 scale, otherwise type-22 scale, otherwise type-22 base size.
        scale_pos=(info['scale'][1] if 'scale' in info else native_scale_pos)
        struct.pack_into('<2f',changed,point_pos,x,y)
        if scale_pos is not None:
            sx,sy=w/base_size[0],h/base_size[1]
            if not (0.001<=sx<=1000 and 0.001<=sy<=1000):
                raise NativeImageGeometryError('图片缩放比例超出安全范围')
            struct.pack_into('<2f',changed,scale_pos,sx,sy)
        else:
            struct.pack_into('<2f',changed,size_pos,w,h)
        if 'current_page' in expected:_patch_page(buf,events,event,ident,expected,changed)
        matched.add(ident)
    if matched!=set(changes):
        raise NativeImageGeometryError('找不到要修改的原生图片')
    verify=n.Buffer(changed)
    root=n.table_at(verify,verify.u32(0))
    if root is None:raise NativeImageGeometryError('修改后的 FlatBuffers 无效')
    verified=n.table_vector(verify,root,6)
    replays=n.native_image_event_state(verified,verify)
    counts={}
    for _,ev in verified:
        typ=ev.field(4);ref=ev.field(0)
        if not typ or not ref or not verify.valid(ref,8) or verify.data[typ]!=22:continue
        actor,ident=struct.unpack_from('<2I',changed,ref)
        if actor!=0 or ident not in matched:continue
        rect=_effective(verify,ev,replays.get(ident,{}))
        if not all(_near(a,b) for a,b in zip(rect,changes[ident]['current'])):
            raise NativeImageGeometryError('原生图片修改后回放校验失败')
        if 'current_page' in changes[ident]:
            field=n.payload_table(verify,ev).field(2)
            if struct.unpack_from('<3I',changed,field)!=_page_reference(verify,verified,changes[ident]['current_page']):
                raise NativeImageGeometryError('原生图片跨页写入回读失败')
        counts[ident]=counts.get(ident,0)+1
    if counts!={ident:1 for ident in matched}:
        raise NativeImageGeometryError('原生图片回读数量不匹配')
    return bytes(changed)
