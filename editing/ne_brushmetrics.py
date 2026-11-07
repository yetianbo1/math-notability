"""Brush cursor geometry in logical widget pixels, matched to saved page units."""


def brush_preview_radii(brush_width, displayed_width, displayed_height, page_width, page_height):
    """Return x/y preview radii for the width used by the actual ink painter.

    Ink strokes are specified in *page* units and scaled independently per axis
    when rendered. Qt widget coordinates are logical pixels; devicePixelRatio
    must not be applied a second time to cursor geometry.
    """
    width = float(brush_width)
    dw, dh = float(displayed_width), float(displayed_height)
    pw, ph = float(page_width), float(page_height)
    if width <= 0 or dw <= 0 or dh <= 0 or pw <= 0 or ph <= 0:
        raise ValueError('Brush width and both page dimensions must be positive')
    return (width * dw / pw / 2.0, width * dh / ph / 2.0)
