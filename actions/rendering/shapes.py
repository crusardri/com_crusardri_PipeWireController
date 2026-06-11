"""Cairo drawing primitives for rounded rectangles."""
import math

# Precomputed constant: π/2 radians (90°), used for arc direction arguments.
HALF_PI = math.pi / 2


def rounded_rect(cr, x, y, w, h, r):
    """Draw a rounded rectangle with a uniform corner radius on all four corners."""
    rounded_rect_custom(cr, x, y, w, h, r, r, r, r)


def rounded_rect_custom(cr, x, y, w, h, tl, tr, br, bl):
    """Draw a rounded rectangle with an independent radius for each corner.

    Corners with radius 0 are drawn as sharp 90° angles.
    Corner order: top-left (tl), top-right (tr), bottom-right (br), bottom-left (bl).

    The path is closed and ready to be filled or stroked by the caller.
    """
    cr.new_sub_path()

    # Top-right corner: arc from top edge (−π/2) to right edge (0).
    if tr > 0:
        cr.arc(x + w - tr, y + tr, tr, -HALF_PI, 0)
    else:
        cr.line_to(x + w, y)

    # Bottom-right corner: arc from right edge (0) to bottom edge (π/2).
    if br > 0:
        cr.arc(x + w - br, y + h - br, br, 0, HALF_PI)
    else:
        cr.line_to(x + w, y + h)

    # Bottom-left corner: arc from bottom edge (π/2) to left edge (π).
    if bl > 0:
        cr.arc(x + bl, y + h - bl, bl, HALF_PI, math.pi)
    else:
        cr.line_to(x, y + h)

    # Top-left corner: arc from left edge (π) to top edge (−π/2 = 3π/2).
    if tl > 0:
        cr.arc(x + tl, y + tl, tl, math.pi, -HALF_PI)
    else:
        cr.line_to(x, y)

    cr.close_path()
