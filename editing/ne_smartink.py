"""Centripetal cubic interpolation for newly captured ink."""
import math
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QPainterPath


def _add(a, b):
    return a[0] + b[0], a[1] + b[1]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _mul(a, factor):
    return a[0] * factor, a[1] * factor


def _reflected(endpoint, neighbour):
    return _sub(_mul(endpoint, 2.), neighbour)


def _neighbours(points, index, width):
    """Return four control inputs, treating a detected corner as a run boundary."""
    from editing.ne_inkcurve import _corner

    left, right = index - 1, index
    p1, p2 = points[left], points[right]
    p0 = (_reflected(p1, p2) if left == 0 or _corner(points, left, width)
          else points[left - 1])
    p3 = (_reflected(p2, p1) if right == len(points) - 1 or _corner(points, right, width)
          else points[right + 1])
    return p0, p1, p2, p3


def bezier_controls(p0, p1, p2, p3):
    """Return centripetal Catmull-Rom handles in Bezier form."""
    h0, h1, h2 = (math.sqrt(math.dist(a, b))
                   for a, b in ((p0, p1), (p1, p2), (p2, p3)))
    if not all(math.isfinite(h) and h > 1e-12 for h in (h0, h1, h2)):
        return None
    m1 = _mul(_add(
        _sub(_mul(_sub(p1, p0), 1. / h0),
             _mul(_sub(p2, p0), 1. / (h0 + h1))),
        _mul(_sub(p2, p1), 1. / h1)), h1)
    m2 = _mul(_add(
        _sub(_mul(_sub(p2, p1), 1. / h1),
             _mul(_sub(p3, p1), 1. / (h1 + h2))),
        _mul(_sub(p3, p2), 1. / h2)), h1)
    chord = _sub(p2, p1)
    length = math.hypot(*chord)
    if length <= 1e-12:
        return None
    direction = _mul(chord, 1. / length)
    limit = length * .75

    def bounded(handle):
        size = math.hypot(*handle)
        if size > limit:
            handle = _mul(handle, limit / size)
        # A handle that points backwards can create a loop at a sharp turn.
        if handle[0] * direction[0] + handle[1] * direction[1] <= 0:
            return 0., 0.
        return handle

    c1 = _add(p1, _mul(bounded(m1), 1. / 3.))
    c2 = _sub(p2, _mul(bounded(m2), 1. / 3.))
    return c1, c2


def append_smart_segment(path, points, index, width):
    """Append one interpolated segment in constant work."""
    if not 1 <= index < len(points):
        return
    controls = bezier_controls(*_neighbours(points, index, width))
    if controls is None:
        path.lineTo(QPointF(*points[index]))
        return
    path.cubicTo(QPointF(*controls[0]), QPointF(*controls[1]),
                 QPointF(*points[index]))


def smart_curve_path(points, width):
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(QPointF(*points[0]))
    for index in range(1, len(points)):
        append_smart_segment(path, points, index, width)
    return path


def smart_tail_bounds(points, width):
    """Bound the last few interpolated segments with their actual handles."""
    if not points:
        return QRectF()
    start = max(1, len(points) - 5)
    path = QPainterPath(QPointF(*points[start - 1]))
    for index in range(start, len(points)):
        append_smart_segment(path, points, index, width)
    margin = abs(width) / 2.
    bounds = (path.controlPointRect() if path.elementCount() > 1
              else QRectF(points[-1][0], points[-1][1], 0., 0.))
    return bounds.adjusted(-margin, -margin, margin, margin)
