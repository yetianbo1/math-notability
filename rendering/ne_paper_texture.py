"""Bounded, deterministic preview grain for the verified yellow-paper preset.

The official NTB stores a preset identifier, without an embedded paper bitmap.
Use a procedural approximation calibrated to its clean thumbnail margin; never
reuse a notebook thumbnail as a background or include its handwriting.
"""
from functools import lru_cache

import numpy as np
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QBrush, QColor, QImage, QTransform

from core.ne_paper_patterns import YELLOW_PAPER_COLOR


@lru_cache(maxsize=1)
def _yellow_grain():
    # A 128-point tile at two pixels per point. Wrap smoothing at the edges
    # so repetition has no seams; randomness is independent of zoom and page.
    rng = np.random.default_rng(20261105)
    noise = rng.normal(size=(256, 256))
    for _ in range(8):
        noise = (noise + np.roll(noise, 1, 0) + np.roll(noise, -1, 0)
                 + np.roll(noise, 1, 1) + np.roll(noise, -1, 1)) / 5
    noise = (noise - noise.mean()) / noise.std()
    fine = rng.normal(size=noise.shape)
    grain = noise * .95 + fine * .4
    rgb = np.array(QColor(YELLOW_PAPER_COLOR).getRgb()[:3])
    channels = np.array((1., 1., 1.25))
    pixels = np.empty((256, 256, 4), dtype=np.uint8)
    pixels[:, :, :3] = np.clip(np.rint(rgb + grain[:, :, None] * channels), 0, 255)
    pixels[:, :, 3] = 255
    return QImage(pixels.data, 256, 256, 256 * 4, QImage.Format.Format_RGBA8888).copy()


def paint_paper_texture(painter, template, target, page_size):
    """Paint in paper coordinates so thumbnails, zoom and PDF share the grain."""
    if not template or template.get('texture') != 'yellow-paper':
        return
    width, height = page_size
    painter.save()
    try:
        painter.translate(target.x(), target.y())
        painter.scale(target.width() / width, target.height() / height)
        brush = QBrush(_yellow_grain())
        brush.setTransform(QTransform.fromScale(.5, .5))
        painter.fillRect(QRectF(0, 0, width, height), brush)
    finally:
        painter.restore()
