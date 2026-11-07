"""Vector erasure: subtract swept nib outlines, retain independent pieces."""
from dataclasses import replace
from PyQt6.QtCore import QPointF,Qt
from PyQt6.QtGui import QPainterPath,QPainterPathStroker,QTransform


def swept_outline(a,b,width):
    path=QPainterPath()
    if a==b:
        path.addEllipse(QPointF(*a),width/2,width/2)
        return path
    path.moveTo(*a);path.lineTo(*b)
    return stroke_outline(path,width)


def stroke_outline(path,width):
    nib=QPainterPathStroker();nib.setWidth(width)
    nib.setCapStyle(Qt.PenCapStyle.RoundCap);nib.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return nib.createStroke(path)


def tape_outline(path,width):
    """Fixed vertical elliptical nib, matching the official tape appearance."""
    if not path.elementCount():return QPainterPath()
    normalized=QTransform.fromScale(4.,1.).map(path)
    if normalized.elementCount()==1:
        e=normalized.elementAt(0)
        result=QPainterPath();result.addEllipse(QPointF(e.x,e.y),width/2,width/2)
    else:
        result=stroke_outline(normalized,width).simplified()
        # Qt can leave cap-shaped holes when a captured path reverses very
        # near its endpoints. Explicit nib stamps restore the swept end caps.
        first=normalized.elementAt(0);last=normalized.elementAt(normalized.elementCount()-1)
        for e in (first,last):
            cap=QPainterPath();cap.addEllipse(QPointF(e.x,e.y),width/2,width/2)
            result=result.united(cap)
    return QTransform.fromScale(.25,1.).map(result).simplified()


def visible_outline(stroke,smooth=True):
    if stroke.outline is not None:return stroke.fill_path
    if stroke.tool=='tape':
        if smooth:return tape_outline(stroke.render_path,stroke.width)
        path=QPainterPath(QPointF(*stroke.points[0]))
        for p in stroke.points[1:]:path.lineTo(*p)
        return tape_outline(path,stroke.width)
    if len(stroke.points)==1 or all(p==stroke.points[0] for p in stroke.points):
        return swept_outline(stroke.points[0],stroke.points[0],stroke.width)
    if smooth:return stroke_outline(stroke.render_path,stroke.width)
    path=QPainterPath(QPointF(*stroke.points[0]))
    for point in stroke.points[1:]:path.lineTo(*point)
    return stroke_outline(path,stroke.width)


def has_area(path):
    # Rewound hole polygons contain doubled bridge edges. They have zero area
    # but Qt's intersects() alone can still report those edges as a hit.
    for polygon in path.toFillPolygons():
        if len(polygon)<3:continue
        ox,oy=polygon[0].x(),polygon[0].y()
        points=[(p.x()-ox,p.y()-oy) for p in polygon]
        if abs(sum(p[0]*q[1]-q[0]*p[1] for p,q in zip(points,points[1:])))>1e-8:return True
    return False


def filled_intersects(first,second):
    if not first.intersects(second):return False
    scale=QTransform.fromScale(8,8)
    return has_area(scale.map(first).intersected(scale.map(second)))


def subtract(stroke,eraser,smooth=True):
    original=visible_outline(stroke,smooth)
    if not original.intersects(eraser):return None
    scale=QTransform.fromScale(8,8)
    scaled=scale.map(original)
    cut=scale.map(eraser)
    if not has_area(scaled.intersected(cut)):return None
    remaining=QTransform.fromScale(1/8,1/8).map(scaled.subtracted(cut))
    if remaining==original:return None
    fragments=[]
    # Rewinding keeps enclosed holes with their outer connected component.
    for polygon in remaining.toFillPolygons(QTransform.fromScale(8,8)):
        points=tuple((p.x()/8,p.y()/8) for p in polygon)
        if len(points)<4:continue
        area=abs(sum(p[0]*q[1]-q[0]*p[1] for p,q in zip(points,points[1:])))/2
        if area<1e-8:continue
        ox,oy=points[0]
        fragments.append(replace(stroke,points=points,source_id=None,
            outline=tuple((x-ox,y-oy) for x,y in points),
            geometry_is_smoothed=True,geometry_is_linear=True))
    return fragments
