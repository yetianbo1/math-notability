"""Aspect-locked, page-bounded image resizing in NTB page coordinates.

Qt-free geometry shared by mouse gestures and tests. This geometric
helper is also used for validated native type-22 image movement and resizing.
"""
from __future__ import annotations
import math

CORNERS = ('nw', 'ne', 'sw', 'se')


def has_original_aspect(original_width, original_height, proposed_width, proposed_height):
    """True if a resize uses one common width/height scale factor.

    The baseline is the displayed image rectangle BEFORE resizing. This works
    for official native image histories and Viewer-owned images alike. A small
    relative tolerance admits normal float32 NTB round-trip rounding only.
    """
    values = (original_width, original_height, proposed_width, proposed_height)
    try:
        ow, oh, nw, nh = map(float, values)
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(v) and v > 0 for v in (ow, oh, nw, nh)):
        return False
    return math.isclose(nw / ow, nh / oh, rel_tol=2e-5, abs_tol=2e-7)


def image_corners(x, y, width, height):
    return {'nw': (x, y), 'ne': (x + width, y),
            'sw': (x, y + height), 'se': (x + width, y + height)}


def resize_corner(original, corner, point, bounds, min_side=10.):
    """Keep the opposite corner fixed, preserve aspect and stay inside page.

    original: (x,y,width,height), point: pointer in page coordinates,
    bounds: (page_width,page_height). Never crosses the fixed corner.
    """
    if corner not in CORNERS:
        raise ValueError('不支持的缩放控制点')
    x, y, width, height = map(float, original)
    px, py = map(float, point)
    page_width, page_height = map(float, bounds)
    if (not all(math.isfinite(v) for v in (x,y,width,height,px,py,page_width,page_height))
            or width <= 0 or height <= 0 or page_width <= 0 or page_height <= 0):
        raise ValueError('图片尺寸或坐标无效')
    anchor_corner = {'nw':'se', 'ne':'sw', 'sw':'ne', 'se':'nw'}[corner]
    anchor_x, anchor_y = image_corners(x,y,width,height)[anchor_corner]
    dir_x = 1 if corner.endswith('e') else -1
    dir_y = 1 if corner.startswith('s') else -1
    ratio = width / height
    # Project the pointer onto the locked-aspect diagonal.
    dx = (px-anchor_x)*dir_x
    dy = (py-anchor_y)*dir_y
    wanted_w = (dx + ratio*dy) * .5
    max_w = (page_width-anchor_x) if dir_x == 1 else anchor_x
    max_h = (page_height-anchor_y) if dir_y == 1 else anchor_y
    limit = min(max_w, max_h*ratio, 1e6)
    if limit <= 0:
        raise ValueError('图片已到达页面边界')
    floor = min(limit, max(float(min_side), float(min_side)*ratio))
    new_w = max(floor, min(limit, wanted_w))
    new_h = new_w / ratio
    new_x = anchor_x if dir_x == 1 else anchor_x-new_w
    new_y = anchor_y if dir_y == 1 else anchor_y-new_h
    return new_x,new_y,new_w,new_h
