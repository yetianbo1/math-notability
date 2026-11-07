"""Non-destructive, bounded stroke-centreline smoothing for previews and exports.

No modification is made to the serialized Notability noteBundle. The same
smoother is used for original SVG paths and for editable/live pen strokes.
"""
from __future__ import annotations

import math
import re

_NUMBER = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
_TOKEN_RE = re.compile(r"[MLC]|" + _NUMBER)
_ALLOWED_SEPARATORS = re.compile(r"[\s,]*")
_MAX_POINTS = 5000


def smooth_points(points, width=2.0, strength=0.85):
    """Suppress small high-frequency wobble without shifting endpoints/corners.

    The location of each sample is constrained to a small radius around the
    original centreline, so text glyphs and intentional hooks stay recognizable.
    """
    pts = [tuple(map(float, p)) for p in points]
    n = len(pts)
    if n < 5 or n > _MAX_POINTS or not all(
        math.isfinite(v) for point in pts for v in point
    ):
        return pts
    strength = float(strength)
    if not math.isfinite(strength):
        return pts
    strength = min(1.0, max(0.0, strength))
    if strength <= 0:
        return pts
    width = float(width)
    if not math.isfinite(width):
        return pts
    width = abs(width)
    cap = max(0.28, min(1.5, width * 0.5))
    # Two gentle 5-tap passes remove pixel-scale jitter, with a hard bound
    # on cumulative displacement relative to the ORIGINAL geometry.
    result = pts
    for _ in range(2):
        next_points = list(result)
        for i in range(2, n - 2):
            left = result[i-1]; here = result[i]; right = result[i+1]
            u = (here[0]-left[0], here[1]-left[1])
            v = (right[0]-here[0], right[1]-here[1])
            ulen = math.hypot(*u); vlen = math.hypot(*v)
            # Do not smooth distinct intentional sharp corners.
            if ulen > 1.e-8 and vlen > 1.e-8:
                cosangle = max(-1., min(1., (u[0]*v[0]+u[1]*v[1])/(ulen*vlen)))
                if cosangle < .50 and min(ulen, vlen) > max(2.0, width * .65):  # deliberate large corner
                    continue
            weighted = (
                (result[i-2][0] + 4*left[0] + 6*here[0] +
                 4*right[0] + result[i+2][0]) / 16.,
                (result[i-2][1] + 4*left[1] + 6*here[1] +
                 4*right[1] + result[i+2][1]) / 16.
            )
            candidate = (here[0] + strength*(weighted[0]-here[0]),
                         here[1] + strength*(weighted[1]-here[1]))
            dx = candidate[0]-pts[i][0]; dy = candidate[1]-pts[i][1]
            distance = math.hypot(dx, dy)
            if distance > cap:
                factor = cap / distance
                candidate = (pts[i][0]+dx*factor, pts[i][1]+dy*factor)
            next_points[i] = candidate
        result = next_points
    return result


def _tokens(path):
    if not path or len(path) > 500_000:
        return None
    tokens = []
    pos = 0
    for match in _TOKEN_RE.finditer(path):
        if not _ALLOWED_SEPARATORS.fullmatch(path[pos:match.start()]):
            return None
        tokens.append(match.group())
        pos = match.end()
    if not _ALLOWED_SEPARATORS.fullmatch(path[pos:]):
        return None
    return tokens


def sample_svg_path(path, step=1.5):
    """Read the SVG M/L/C absolute path subset emitted by formats.ne_ntb.

    Returns None for any unsupported command, malformed input or overly long
    path. No best-guess transformation of unexpected file format is attempted.
    """
    tokens = _tokens(path)
    if not tokens:
        return None
    groups = []
    points = []
    i = 0
    current = None
    while i < len(tokens):
        command = tokens[i]
        i += 1
        count = {'M': 2, 'L': 2, 'C': 6}.get(command)
        if count is None or i + count > len(tokens):
            return None
        data = tokens[i:i+count]
        i += count
        if any(k in 'MLC' for k in data):
            return None
        try:
            nums = list(map(float, data))
        except ValueError:
            return None
        if not all(map(math.isfinite, nums)):
            return None
        if command == 'M':
            if points:
                groups.append(points)
            current = (nums[0], nums[1])
            points = [current]
            continue
        if current is None:
            return None
        if command == 'L':
            endpoint = (nums[0], nums[1])
            length = math.dist(current, endpoint)
            steps = max(1, min(256, math.ceil(length/step)))
            for j in range(1, steps+1):
                t = j/steps
                points.append((current[0]+(endpoint[0]-current[0])*t,
                               current[1]+(endpoint[1]-current[1])*t))
            current = endpoint
        else:
            a = current
            b = (nums[0], nums[1])
            c = (nums[2], nums[3])
            d = (nums[4], nums[5])
            control_length = math.dist(a,b)+math.dist(b,c)+math.dist(c,d)
            steps = max(2, min(256, math.ceil(control_length/step)))
            for j in range(1,steps+1):
                t = j/steps
                q = 1-t
                points.append((
                    q**3*a[0] + 3*q*q*t*b[0] + 3*q*t*t*c[0] + t**3*d[0],
                    q**3*a[1] + 3*q*q*t*b[1] + 3*q*t*t*c[1] + t**3*d[1]))
            current = d
        if len(points) + sum(map(len, groups)) > _MAX_POINTS:
            return None
    if points:
        groups.append(points)
    return groups or None


def smooth_svg_path(path, width=2.0, strength=0.85):
    """Return a new display-only SVG path d; preserve unsupported input."""
    # SVG source from NTB may already contain long, carefully curved paths.
    # Never smooth a short stroke or unsupported syntax.
    groups = sample_svg_path(path)
    if not groups:
        return path
    if max(map(len, groups)) < 5:
        return path
    parts = []
    for group in groups:
        group = smooth_points(group, width, strength)
        parts.append(f'M{group[0][0]:.5f},{group[0][1]:.5f}')
        for x,y in group[1:]:
            parts.append(f'L{x:.5f},{y:.5f}')
    return ' '.join(parts)
