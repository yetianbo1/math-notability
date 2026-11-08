"""Rule-based classroom shapes as polyline ink, in NTB page coordinates.

Qt-free geometry so a whole shape can be unit-tested without a window. A shape
is one closed (or two-point) polyline stroke; keeping it as ordinary ink means
selection, moving, corner scaling, erasing, undo and NTB write-back all keep
working without inventing a new native object format.
"""
from __future__ import annotations

import math

KINDS = ('line', 'circle', 'triangle', 'rectangle')
LABELS = {'line': '直线', 'circle': '圆', 'triangle': '三角', 'rectangle': '矩形'}
CIRCLE_SEGMENTS = 96
MIN_SIZE = 2.0
MIN_LINE = 1.0


def clamp_point(point, page_size):
    """Keep a dragged pointer inside the paper without losing its direction."""
    x, y = float(point[0]), float(point[1])
    pw, ph = (float(v) for v in page_size)
    if not all(math.isfinite(v) for v in (x, y, pw, ph)) or pw <= 0 or ph <= 0:
        raise ValueError('页面尺寸无效')
    return max(0.0, min(pw, x)), max(0.0, min(ph, y))


def _regular_box(kind, dx, dy, left, right, top, bottom):
    """Square / circle / equilateral-triangle constraint on a dragged box."""
    if kind == 'triangle':
        height = (right - left) * math.sqrt(3.0) / 2.0
        if dy >= 0:
            bottom = top + height
        else:
            top = bottom - height
    else:
        side = max(right - left, bottom - top)
        if dx >= 0:
            right = left + side
        else:
            left = right - side
        if dy >= 0:
            bottom = top + side
        else:
            top = bottom - side
    return left, right, top, bottom


def shape_points(kind, start, end, *, shift=False, page_size=None):
    """Return the polyline for one drag from ``start`` to ``end``.

    ``shift`` snaps a line to 45 degrees, or a box shape to its regular form
    (square, circle, equilateral triangle). ``page_size`` clamps every vertex
    to the paper so a fast drag past the edge cannot write ink off-page.
    """
    if kind not in KINDS:
        raise ValueError('不支持的图形')
    sx, sy = (float(v) for v in start)
    ex, ey = (float(v) for v in end)
    if not all(math.isfinite(v) for v in (sx, sy, ex, ey)):
        raise ValueError('图形坐标无效')
    if page_size is not None:
        sx, sy = clamp_point((sx, sy), page_size)
        ex, ey = clamp_point((ex, ey), page_size)
    dx, dy = ex - sx, ey - sy

    if shift and kind == 'line':
        length = math.hypot(dx, dy)
        if length < MIN_LINE:
            raise ValueError('图形尺寸太小')
        angle = round(math.atan2(dy, dx) / (math.pi / 4.0)) * (math.pi / 4.0)
        ex, ey = sx + length * math.cos(angle), sy + length * math.sin(angle)
        if page_size is not None:
            ex, ey = clamp_point((ex, ey), page_size)
        dx, dy = ex - sx, ey - sy
    if kind == 'line':
        if math.hypot(dx, dy) < MIN_LINE:
            raise ValueError('图形尺寸太小')
        return ((sx, sy), (ex, ey))

    left, right = sorted((sx, ex))
    top, bottom = sorted((sy, ey))
    if shift:
        left, right, top, bottom = _regular_box(kind, dx, dy, left, right, top, bottom)
    width, height = right - left, bottom - top
    if width < MIN_SIZE or height < MIN_SIZE:
        raise ValueError('图形尺寸太小')
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0

    if kind == 'rectangle':
        points = ((left, top), (right, top), (right, bottom), (left, bottom), (left, top))
    elif kind == 'circle':
        rx, ry = width / 2.0, height / 2.0
        points = [(cx + rx * math.cos(math.tau * i / CIRCLE_SEGMENTS),
                   cy + ry * math.sin(math.tau * i / CIRCLE_SEGMENTS))
                  for i in range(CIRCLE_SEGMENTS)]
        points.append(points[0])
        points = tuple(points)
    else:
        # Isosceles triangle inscribed in the dragged box: apex at the middle
        # of the first edge, base along the opposite edge. Shift makes it
        # equilateral by fixing the height at width * sqrt(3) / 2.
        if dy >= 0:
            apex, base = (cx, top), ((right, bottom), (left, bottom))
        else:
            apex, base = (cx, bottom), ((left, top), (right, top))
        points = (apex, base[0], base[1], apex)

    if page_size is not None:
        points = tuple(clamp_point(p, page_size) for p in points)
    return points
