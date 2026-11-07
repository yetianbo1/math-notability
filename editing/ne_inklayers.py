"""Identify the visible portions of NTB ink whose geometry crosses page bounds.

Notability's page-ownership event and its drawing coordinates are separate:
a stroke owned by page N can extend onto page N+1. Do not throw away the
continuation when rendering/exporting page N+1.

This is intentionally independent of Qt, so it can be tested without a GUI.
"""
import math
import re

from lxml import etree

_TOKEN = re.compile(r'[MLC]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?')
_ARITY = {'M': 2, 'L': 2, 'C': 6}


def path_y_bounds(path_data):
    """Conservative y-bounds for the absolute M/L/C paths produced by formats.ne_ntb.

    Bezier control points are included (the curve cannot lie beyond their
    convex hull), so no visible continuation is accidentally omitted.
    Malformed/unknown paths are not classified as cross-page ink.
    """
    tokens = _TOKEN.findall(path_data)
    ys = []
    i = 0
    while i < len(tokens):
        command = tokens[i]
        i += 1
        count = _ARITY.get(command)
        if not count or i + count > len(tokens):
            return None
        values = tokens[i:i + count]
        if any(token in _ARITY for token in values):
            return None
        try:
            nums = [float(token) for token in values]
        except ValueError:
            return None
        if not all(math.isfinite(v) for v in nums):
            return None
        ys.extend(nums[1::2])
        i += count
    return (min(ys), max(ys)) if ys else None


def map_cross_page_ink(ink, page_height, page_count, *, path_bounds=None):
    """Return {target_source_page: [(owner_source_page, svg_bytes), ...]}.

    Targets refer to original NTB page identity, not the current display index.
    Deleted pages therefore cannot leak ink into unrelated adjacent pages.
    """
    if not math.isfinite(page_height) or page_height <= 0 or page_count < 0:
        return {}
    result = {}
    for owner, records in ink.items():
        if not isinstance(owner, int) or owner < 0 or owner >= page_count:
            continue
        for xml_bytes in records:
            try:
                if path_bounds is not None and xml_bytes in path_bounds:
                    bounds = path_bounds[xml_bytes]
                else:
                    element = etree.fromstring(xml_bytes)
                    bounds = path_y_bounds(element.get('d', ''))
            except (TypeError, ValueError, etree.XMLSyntaxError):
                continue
            if bounds is None:
                continue
            ymin, ymax = bounds
            low = max(0, int(math.floor(ymin / page_height)))
            high = min(page_count - 1, int(math.floor(ymax / page_height)))
            # Only append to pages whose *interior* the path may intersect.
            for target in range(low, high + 1):
                if target == owner or ymin >= (target + 1) * page_height or ymax <= target * page_height:
                    continue
                result.setdefault(target, []).append((owner, xml_bytes))
    return result
