"""Primitivas de dibujo cairo."""
import math

HALF_PI = math.pi / 2


def rounded_rect(cr, x, y, w, h, r):
    rounded_rect_custom(cr, x, y, w, h, r, r, r, r)


def rounded_rect_custom(cr, x, y, w, h, tl, tr, br, bl):
    """Rectángulo con radio independiente por esquina (0 = esquina recta)."""
    cr.new_sub_path()
    if tr > 0:
        cr.arc(x + w - tr, y + tr, tr, -HALF_PI, 0)
    else:
        cr.line_to(x + w, y)

    if br > 0:
        cr.arc(x + w - br, y + h - br, br, 0, HALF_PI)
    else:
        cr.line_to(x + w, y + h)

    if bl > 0:
        cr.arc(x + bl, y + h - bl, bl, HALF_PI, math.pi)
    else:
        cr.line_to(x, y + h)

    if tl > 0:
        cr.arc(x + tl, y + tl, tl, math.pi, -HALF_PI)
    else:
        cr.line_to(x, y)
    cr.close_path()
