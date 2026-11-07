"""Uniform corner scaling for a selected ink group, in NTB page coordinates.

Qt-free geometry so the whole transform can be unit-tested without a window.
The interaction mirrors image resizing: the corner opposite the dragged one
stays fixed, aspect ratio is preserved and the result never leaves the page.
All Stroke geometry fields are transformed by the same factor so native outline
ink stays aligned with its displayed outline.
"""
from __future__ import annotations

import math
from dataclasses import replace

CORNERS = ('nw', 'ne', 'sw', 'se')
MIN_FACTOR = 0.25
MAX_FACTOR = 4.0
HANDLE_TOLERANCE_PX = 10.0


def opposite_corner(corner):
    if corner not in CORNERS:
        raise ValueError('不支持的缩放控制点')
    return {'nw': 'se', 'ne': 'sw', 'sw': 'ne', 'se': 'nw'}[corner]


def rect_corners(x, y, width, height):
    return {'nw': (x, y), 'ne': (x + width, y),
            'sw': (x, y + height), 'se': (x + width, y + height)}


def scaled_bounds(bounds, corner, point, page_size,
                  min_factor=MIN_FACTOR, max_factor=MAX_FACTOR):
    """Scale a selection rectangle about its opposite corner.

    bounds: (x, y, width, height) in page coordinates.
    point: pointer position in page coordinates.
    page_size: (page_width, page_height) in page coordinates.
    Returns (x, y, width, height, anchor_x, anchor_y, factor).
    """
    x, y, width, height = (float(v) for v in bounds)
    px, py = (float(v) for v in point)
    page_w, page_h = (float(v) for v in page_size)
    values = (x, y, width, height, px, py, page_w, page_h)
    if not all(math.isfinite(v) for v in values) or width <= 0 or height <= 0 \
            or page_w <= 0 or page_h <= 0:
        raise ValueError('选区或页面尺寸无效')
    if corner not in CORNERS:
        raise ValueError('不支持的缩放控制点')

    corners = rect_corners(x, y, width, height)
    anchor_x, anchor_y = corners[opposite_corner(corner)]
    dir_x = 1 if corner.endswith('e') else -1
    dir_y = 1 if corner.startswith('s') else -1
    vx = corners[corner][0] - anchor_x
    vy = corners[corner][1] - anchor_y
    diagonal2 = vx * vx + vy * vy
    if diagonal2 <= 0:
        raise ValueError('选区尺寸无效')
    # Normalized projection onto the original corner diagonal. Unlike an
    # aspect-ratio projection this stays stable for very flat selections
    # (a single handwritten line can be 50:1), where a 1px vertical move
    # would otherwise swing the factor to its limits.
    factor = ((px - anchor_x) * vx + (py - anchor_y) * vy) / diagonal2
    if not math.isfinite(factor):
        raise ValueError('缩放比例无效')

    max_w = (page_w - anchor_x) if dir_x == 1 else anchor_x
    max_h = (page_h - anchor_y) if dir_y == 1 else anchor_y
    page_factor = min(max_w / width, max_h / height)
    if factor > 1.0 and math.isfinite(page_factor):
        factor = min(factor, max(1.0, page_factor))
    factor = max(min_factor, min(max_factor, factor))

    new_w, new_h = width * factor, height * factor
    new_x = anchor_x if dir_x == 1 else anchor_x - new_w
    new_y = anchor_y if dir_y == 1 else anchor_y - new_h
    # An anchor sitting outside the page (wide strokes near an edge) can still
    # push a clamped result out; nudge it back without changing the size.
    new_x = max(0.0, min(page_w - new_w, new_x))
    new_y = max(0.0, min(page_h - new_h, new_y))
    return new_x, new_y, new_w, new_h, anchor_x, anchor_y, factor


def scale_point(point, anchor, factor):
    ax, ay = anchor
    return ax + (point[0] - ax) * factor, ay + (point[1] - ay) * factor


def scale_stroke(stroke, anchor, factor):
    """Transform points, native outline offsets and pen width together."""
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('缩放比例无效')
    points = tuple(scale_point(p, anchor, factor) for p in stroke.points)
    outline = (tuple((dx * factor, dy * factor) for dx, dy in stroke.outline)
               if stroke.outline is not None else None)
    return replace(stroke, points=points, outline=outline,
                   width=stroke.width * factor)


def scale_strokes(strokes, indices, anchor, factor):
    """Return a new stroke sequence with only the selected entries scaled."""
    selected = set(indices)
    return tuple(scale_stroke(s, anchor, factor) if i in selected else s
                 for i, s in enumerate(strokes))
