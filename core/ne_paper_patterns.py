"""Deterministic, preview-only rendering geometry for Notability backgrounds.

Units are native PDF points (1/72 inch). Never overwrite noteBundle's style
metadata or infer a native-paper style from document dimensions alone.
"""
from __future__ import annotations

import math

PATTERN_COLOR = '#a3b7d3'  # Sampled from paired official PDF.
# Median of the unmarked top margin in the supplied official yellow-paper
# thumbnail (2026-11-05). The native style stores preset 2, not custom RGBA.
YELLOW_PAPER_COLOR = '#fbf4c8'
DEFAULT_PITCH_PT = 36.0
VERIFIED_LEVELS_PT = {1: 14.173235893249512, 3: 28.346471786499023, 4: 36.0}


def estimate_pitch_for_preview(level: int) -> tuple[float, bool]:
    """Return spacing for a *visual-only* 1..10 selector.

    Native NTB files already contain their true pitch and MUST NOT use this
    estimate. Unverified levels are approximate, not Notability-native data.
    """
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 10:
        raise ValueError('纸张间距档位必须是 1～10 的整数')
    if level in VERIFIED_LEVELS_PT:
        return VERIFIED_LEVELS_PT[level], True
    return 2.5 * (level + 1) * 72.0 / 25.4, False


def preview_pattern(kind: str, width: float, height: float, pitch: float | None = None):
    """Create safe preview specification for a *known* kind.

    Unknown kinds have no fabricated pattern. Caller can render a plain white
    fallback; do not assert that white is the original unknown background.
    """
    if kind not in ('blank', 'ruled', 'dotted'):
        return None
    if not (math.isfinite(width) and math.isfinite(height) and
            0 < width <= 10000 and 0 < height <= 10000):
        return None
    if kind == 'blank':
        return {'kind': 'blank', 'approximate': False}
    if pitch is None or not math.isfinite(pitch) or not (10 <= pitch <= 100):
        return None
    result = {'kind': kind, 'pitch': float(pitch), 'color': PATTERN_COLOR,
              'approximate': True}
    if kind == 'ruled':
        result['margin'] = 36.0
    else:
        # Official PDF dot grid is centred horizontally; first row at pitch.
        result['x_origin'] = max(0.0, (width - math.floor(width / pitch) * pitch) / 2)
        result['radius'] = 1.0
    return result


def ruled_segments(width: float, height: float, pitch: float, margin: float = 36.0):
    """Generate horizontal lines in pt for a known ruled-paper template."""
    if not (all(map(math.isfinite, (width, height, pitch, margin))) and
            10 <= pitch <= 100 and width > 2 * margin >= 0 and height > 0):
        return
    for row in range(1, min(2001, math.ceil(height / pitch) + 1)):
        y = row * pitch
        if y >= height: break
        yield margin, y, width - margin, y


def dotted_centres(width: float, height: float, pitch: float, origin: float | None = None):
    """Generate centre points; supports native precision, not raster stretching."""
    if not (all(map(math.isfinite, (width, height, pitch))) and
            10 <= pitch <= 100 and width > 0 and height > 0):
        return
    if origin is None:
        origin = (width - math.floor(width / pitch) * pitch) / 2
    if not math.isfinite(origin) or not 0 <= origin < pitch:
        return
    for col in range(min(2000, math.ceil((width - origin) / pitch) + 1)):
        x = origin + col * pitch
        if x < 0 or x > width: continue
        for row in range(1, min(2001, math.ceil(height / pitch) + 1)):
            y = row * pitch
            if y >= height: break
            yield x, y
