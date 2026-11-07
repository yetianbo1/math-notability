"""Validate true native sheet geometry separate from long SVG canvas size."""
from __future__ import annotations
import math


def page_size(width, height):
    """Validate dimensions without rounding or substituting a paper preset."""
    try:
        width, height = float(width), float(height)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('页面尺寸无效') from exc
    if not all(math.isfinite(v) and 0 < v <= 10000 for v in (width, height)):
        raise ValueError('页面尺寸须大于 0 且不超过 10000 pt')
    return width, height


def thumbnail_scale(width, height):
    """Bound preview allocation even for large custom sheets."""
    width, height = page_size(width, height)
    return min(.5, 768 / max(width, height))


def pdf_page_size(document_width, crop_width, crop_height):
    """Official PDF pages fit the document width, preserving crop aspect."""
    crop_width, crop_height = page_size(crop_width, crop_height)
    return page_size(document_width, float(document_width) * crop_height / crop_width)


def native_sheet_height(root, document_height: float) -> float:
    """Use explicit native page height when present; never use total SVG height.

    Notability NTB SVG data-page-height records the *single paper sheet*.
    The outer SVG height often equals that value times the number of sheets.
    """
    explicit=root.get('data-page-height')
    if explicit is None:
        return float(document_height)
    try:
        height=float(explicit)
    except (TypeError,ValueError,OverflowError) as exc:
        raise ValueError('原生笔记的单页高度无效') from exc
    if not math.isfinite(height) or not 0 < height <= 10000:
        raise ValueError('原生笔记的单页高度超出安全范围')
    return height
