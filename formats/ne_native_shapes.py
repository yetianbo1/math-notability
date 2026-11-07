"""Render documented CreateShape geometry without rewriting native definitions."""
import math
import struct
import formats.ne_ntb as n


def shape_strokes(events,b,page_indices,page_height):
    hidden=n.hidden_stroke_event_ids(events,b)
    moves=n.stroke_position_overrides(events,b)
    result=[]
    for _,event in events:
        if not event.field(4) or b.data[event.field(4)]!=18:continue
        ident=b.u32(event.field(0)+4)
        if ident in hidden:continue
        p=n.payload_table(b,event)
        if not p or not p.field(0) or not p.field(1) or not p.field(4):continue
        ref=struct.unpack_from('<3I',b.data,p.field(0))
        origin=struct.unpack_from('<2f',b.data,p.field(1))
        if ident in moves:ref,origin=moves[ident]
        page=page_indices.get((ref[1],ref[2]),page_indices.get(ref[1]))
        if ref[0] or page is None:continue
        scale=struct.unpack_from('<2f',b.data,p.field(3)) if p.field(3) else (1.,1.)
        angle=b.f32(p.field(2)) if p.field(2) else 0.
        width=b.f32(p.field(10)) if p.field(10) else 4.
        if not all(math.isfinite(v) for v in (*origin,*scale,angle,width)) or not 0<width<=1000:continue
        definition=n.child_table(b,p,5)
        if not definition:continue
        kind=b.data[p.field(4)];closed=False;arrow=False
        def point(slot,default=(0.,0.)):
            field=definition.field(slot)
            return struct.unpack_from('<2f',b.data,field) if field and b.valid(field,8) else default
        if kind==1:
            start,end=point(0),point(3)
            c1,c2=point(1,start),point(2,end)
            if definition.field(1) or definition.field(2):
                points=[tuple((1-t)**3*start[j]+3*(1-t)**2*t*c1[j]+3*(1-t)*t*t*c2[j]+t**3*end[j]
                              for j in (0,1)) for t in (i/64 for i in range(65))]
            else:points=[start,end]
            arrow=bool(definition.field(4) and b.data[definition.field(4)]==1)
        elif kind==2:
            field=definition.field(0)
            if not field:continue
            vector=field+b.u32(field)
            if not b.valid(vector,4):continue
            count=b.u32(vector)
            if not 2<=count<=10000 or not b.valid(vector+4,count*8):continue
            points=[struct.unpack_from('<2f',b.data,vector+4+i*8) for i in range(count)]
            points.append(points[0]);closed=True
        elif kind==3:
            if definition.field(0) and b.data[definition.field(0)]!=0:continue
            w,h=point(1)
            if min(w,h)<=0 or max(w,h)>100000:continue
            points=[(w/2+w/2*math.cos(t),h/2+h/2*math.sin(t))
                    for t in (i*math.tau/128 for i in range(129))]
            closed=True
        else:continue
        if not all(math.isfinite(v) for xy in points for v in xy):continue
        cos,sin=math.cos(angle),math.sin(angle)
        def transform(xy):
            x,y=xy[0]*scale[0],xy[1]*scale[1]
            return origin[0]+cos*x-sin*y,origin[1]+sin*x+cos*y+page*page_height
        points=[transform(xy) for xy in points]
        def path(points):
            return 'M '+' L '.join(f'{x:.3f} {y:.3f}' for x,y in points)
        color=tuple(b.data[p.field(9):p.field(9)+4]) if p.field(9) else (0,0,0,255)
        tool_code=b.data[p.field(6)] if p.field(6) else 0
        tool='tape' if tool_code==3 else 'highlighter' if tool_code==2 else 'pen'
        pattern=b.data[p.field(8)] if p.field(8) else 3
        # Filled native shapes keep a separate fill and border with shared ID.
        if closed and p.field(11):
            fill=tuple(b.data[p.field(11):p.field(11)+4])
            if fill[3]:result.append((page,path(points),width,fill,ident,tool,True,pattern))
        style=b.data[p.field(7)] if p.field(7) else 1
        if style in (2,3):
            # Explicit segments survive both the SVG view and editable ink model.
            dash=max(.25,width*(3 if style==2 else .1));gap=max(.25,width*2)
            phase=0.;draw=True;parts=[]
            for a,z in zip(points,points[1:]):
                length=math.dist(a,z)
                if length<1e-9:continue
                at=0.
                while at<length-1e-9:
                    step=min((dash if draw else gap)-phase,length-at)
                    if draw:
                        u=tuple(a[j]+(z[j]-a[j])*at/length for j in (0,1))
                        v=tuple(a[j]+(z[j]-a[j])*(at+step)/length for j in (0,1))
                        parts.append(path([u,v]))
                    at+=step;phase+=step
                    if phase>=(dash if draw else gap)-1e-9:phase=0.;draw=not draw
            d=' '.join(parts)
        else:d=path(points)
        if arrow:
            end=points[-1];prev=next((q for q in reversed(points[:-1]) if math.dist(q,end)>1e-6),points[0])
            direction=math.atan2(end[1]-prev[1],end[0]-prev[0]);length=max(5.,width*5)
            wings=[(end[0]-length*math.cos(direction+s*.45),end[1]-length*math.sin(direction+s*.45)) for s in (-1,1)]
            d+=' '+path([wings[0],end,wings[1]])
        if d and color[3]:result.append((page,d,width,color,ident,tool,False,pattern))
    return result
