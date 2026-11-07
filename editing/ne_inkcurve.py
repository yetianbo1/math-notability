"""Local interpolating cubics shared by capture previews and committed ink."""
import math
from PyQt6.QtCore import QPointF,QRectF
from PyQt6.QtGui import QPainterPath


def _corner(points,index,width):
    if not 0<index<len(points)-1:return False
    def turn(i):
        a,b,c=points[i-1:i+2]
        u=(b[0]-a[0],b[1]-a[1]);v=(c[0]-b[0],c[1]-b[1])
        left,right=math.hypot(*u),math.hypot(*v)
        return math.atan2(u[0]*v[1]-u[1]*v[0],u[0]*v[0]+u[1]*v[1]),left,right
    angle,left,right=turn(index)
    if abs(angle)<=math.pi/3 or min(left,right)<=max(2.,min(4.,abs(width)*.25)):return False
    if abs(angle)>=math.pi/2:return True
    curvature=angle/(left+right)
    # Consecutive turns of comparable curvature form an arc, even when a
    # fast pointer supplies very few samples. An isolated turn stays sharp.
    for neighbour in (index-1,index+1):
        if not 0<neighbour<len(points)-1:continue
        turn_angle,a,b=turn(neighbour)
        if min(a,b)<1e-9:continue
        other=turn_angle/(a+b)
        if other*curvature>0 and .5<abs(other/curvature)<2.:return False
    return True


def _endpoint(edge,neighbour,edge_length,other_length,corner):
    tangent=tuple(v/edge_length for v in edge)
    if other_length<1e-9:return tangent
    if corner:return tangent
    h,k=edge_length,other_length
    return tuple(tangent[i]+(tangent[i]-neighbour[i]/k)*h/(h+k) for i in (0,1))


def _tangent(points,index,width):
    here=points[index]
    left=points[index-1] if index else None
    right=points[index+1] if index+1<len(points) else None
    a=math.dist(left,here) if left is not None else 0.
    b=math.dist(here,right) if right is not None else 0.
    if a<1e-9 and b<1e-9:return (0.,0.)
    if a<1e-9:
        edge=tuple(right[k]-here[k] for k in (0,1))
        other=points[index+2] if index+2<len(points) else right
        return _endpoint(edge,tuple(other[k]-right[k] for k in (0,1)),b,math.dist(other,right),_corner(points,index+1,width))
    if b<1e-9:
        edge=tuple(here[k]-left[k] for k in (0,1))
        other=points[index-2] if index>=2 else left
        return _endpoint(edge,tuple(left[k]-other[k] for k in (0,1)),a,math.dist(other,left),_corner(points,index-1,width))
    u=tuple(here[k]-left[k] for k in (0,1))
    v=tuple(right[k]-here[k] for k in (0,1))
    # A genuine corner has a cusp; never loop or round across its vertex.
    if _corner(points,index,width):
        return (0.,0.)
    ta,tb=a,b
    return tuple((u[k]*tb/ta+v[k]*ta/tb)/(ta+tb) for k in (0,1))


def append_curve(path,points,index,width):
    """Append one segment using a bounded neighbourhood of adjacent samples."""
    a,b=points[index-1:index+1]
    length=math.dist(a,b)
    if length<1e-9:
        path.lineTo(*b)
        return
    chord=tuple(b[k]-a[k] for k in (0,1))
    scale=length/3
    handles=[]
    for vertex in (index-1,index):
        tangent=_tangent(points,vertex,width)
        handle=tuple(v*scale for v in tangent)
        size=math.hypot(*handle)
        if sum(x*y for x,y in zip(handle,chord))<=0:handle=(0.,0.)
        elif size>length*.45:handle=tuple(v*length*.45/size for v in handle)
        handles.append(handle)
    first=tuple(a[k]+handles[0][k] for k in (0,1))
    last=tuple(b[k]-handles[1][k] for k in (0,1))
    path.cubicTo(QPointF(*first),QPointF(*last),QPointF(*b))


def curve_path(points,width):
    path=QPainterPath()
    if points:
        path.moveTo(*points[0])
        for index in range(1,len(points)):append_curve(path,points,index,width)
    return path


def tail_bounds(points,width,smooth=True,smart=False):
    """Bound only the mutable tail using its actual curve control handles."""
    if smart:
        from editing.ne_smartink import smart_tail_bounds
        return smart_tail_bounds(points,width)
    start=max(1,len(points)-4)
    path=QPainterPath(QPointF(*points[start-1]))
    for index in range(start,len(points)):
        if smooth:append_curve(path,points,index,width)
        else:path.lineTo(*points[index])
    margin=abs(width)/2
    # Qt reports an empty rectangle at the origin for a move-only path.
    bounds=path.controlPointRect() if path.elementCount()>1 else QRectF(*points[-1],0.,0.)
    return bounds.adjusted(-margin,-margin,margin,margin)
