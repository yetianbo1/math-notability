"""Resolve the observed single-author official page sequence.

Page identity is (CreatePage ID, sheet index). Sequence positions have their
own IDs: ModifyPage inserts new positions while old positions remain anchors.
Deletion is a separate last-operation-wins register. Never use archive vector
order, client time, or a page reference's sheet index as an insertion side.
"""
import struct


class PageModelError(ValueError):
    pass


def resolve_page_order(events, buf, records):
    import formats.ne_ntb as n

    def reference(field):
        if not field or not buf.valid(field,12):
            raise PageModelError('页面引用不完整')
        actor,owner,index=struct.unpack_from('<3I',buf.data,field)
        if actor:
            raise PageModelError('多作者页面图尚未验证')
        return owner,index

    def vector(payload, slot):
        field=payload.field(slot) if payload else 0
        if not field:return []
        if not buf.valid(field,4):raise PageModelError('页面引用向量不完整')
        start=field+buf.u32(field)
        if not buf.valid(start,4):raise PageModelError('页面引用向量不完整')
        count=buf.u32(start)
        if count>100_000 or not buf.valid(start+4,count*12):
            raise PageModelError('页面引用向量越界')
        return [reference(start+4+i*12) for i in range(count)]

    by_ref={}
    for record in records:
        ref=record['id'],record.get('page_index',0)
        if ref in by_ref:raise PageModelError('重复页面身份')
        by_ref[ref]=record
    if not by_ref:raise PageModelError('没有页面')
    nodes={};positions={};deletes={};seen=set();modifications=[]
    for _,event in events:
        kind=event.field(4)
        if not kind or not buf.valid(kind,1) or buf.data[kind] not in (3,4,25):continue
        kind=buf.data[kind]
        field=event.field(0)
        if not field or not buf.valid(field,8):raise PageModelError('页面操作缺少身份')
        actor,op=struct.unpack_from('<2I',buf.data,field)
        if actor or op in seen:raise PageModelError('多作者或重复页面操作尚未验证')
        seen.add(op)
        payload=n.payload_table(buf,event)
        if kind==3:
            parent=reference(payload.field(0)) if payload and payload.field(0) else None
            count=n.field_u32(buf,payload,2) if payload else None
            count=1 if count is None else count
            if not 1<=count<=100_000:raise PageModelError('页面范围无效')
            for index in range(count):
                ref=op,index
                if ref not in by_ref:raise PageModelError('页面创建范围与显示模型不一致')
                nodes[ref]=(parent,ref)
                if ref not in positions or positions[ref][0]<op:positions[ref]=ref
        elif kind==4:
            if payload is None:raise PageModelError('页面修改缺少负载')
            # Background replacements need a separate geometry/asset adapter.
            if any(payload.field(i) for i in range(2,payload.slots)):
                raise PageModelError('页面背景、书签或扩展修改尚未验证')
            refs=vector(payload,0)
            if not refs or len(set(refs))!=len(refs):raise PageModelError('页面移动目标无效')
            if any(ref not in by_ref for ref in refs):raise PageModelError('页面修改引用不存在的页面')
            move=n.child_table(buf,payload,1)
            if payload.field(1) and move is None:raise PageModelError('页面移动位置损坏')
            if move is not None:
                parent=reference(move.field(0)) if move.field(0) else None
                for index,ref in enumerate(refs):
                    node=op,index
                    nodes[node]=(parent,ref)
                    if ref not in positions or positions[ref][0]<op:positions[ref]=node
                modifications.append(op)
        else:
            if payload and any(payload.field(i) for i in range(4,payload.slots)):
                raise PageModelError('页面可见性事件包含未知扩展')
            for slot,deleted in ((2,True),(3,False)):
                for ref in vector(payload,slot):
                    if ref not in by_ref:raise PageModelError('页面删除引用不存在的页面')
                    if ref not in deletes or deletes[ref][0]<op:deletes[ref]=(op,deleted)
    children={None:[]}
    for node in nodes:children[node]=[]
    for node,(parent,_) in nodes.items():
        if parent not in children:raise PageModelError('页面位置锚点不存在')
        children[parent].append(node)
    # Official SeqId orders larger operation IDs first, but indexes within
    # one InsertMany in ascending order. All sheets share the same parent.
    for siblings in children.values():siblings.sort(key=lambda v:(-v[0],v[1]))
    ordered=[];visited=set();pending=list(reversed(children[None]))
    while pending:
        node=pending.pop()
        if node in visited:raise PageModelError('页面位置图存在循环')
        visited.add(node);ref=nodes[node][1]
        if positions[ref]==node and not deletes.get(ref,(0,False))[1]:ordered.append(ref)
        pending.extend(reversed(children[node]))
    if len(visited)!=len(nodes):raise PageModelError('页面位置图存在循环或不可达节点')
    if not ordered:raise PageModelError('笔记没有可见页面')
    return dict(records=[by_ref[ref] for ref in ordered],references=ordered,
                deleted_references=sorted(ref for ref,(_,deleted) in deletes.items() if deleted),
                has_moves=bool(modifications))
