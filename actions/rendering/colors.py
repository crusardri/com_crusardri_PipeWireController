"""Shared color utilities used by both the renderer and the configuration UI."""

# Default gradient stop colors used when no custom gradient is configured.
# Order: green → yellow → red → cyan → yellow → magenta.
DEFAULT_GRADIENT_COLORS = ["#00ff00", "#ffff00", "#ff0000", "#00ffff", "#ffff00", "#ff00ff"]


def parse_color(hex_str):
    """Parse a CSS hex color string into a (r, g, b, a) float tuple.

    Accepts '#rrggbb' (opaque) and '#rrggbbaa' (with alpha).
    Each component is in the range 0.0-1.0 for use with cairo.
    Returns white (1, 1, 1, 1) for any invalid input.
    """
    hex_str = (hex_str or "").lstrip("#")
    if len(hex_str) in (6, 8):
        try:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            # Alpha defaults to fully opaque when the string is 6 digits.
            a = int(hex_str[6:8], 16) / 255.0 if len(hex_str) == 8 else 1.0
            return (r, g, b, a)
        except ValueError:
            pass
    return (1, 1, 1, 1)


def rgba_to_hex(rgba):
    """Convert a Gdk.RGBA object to a '#rrggbb' hex string.

    Used when saving color values from GTK color buttons to the settings dict.
    """
    return f"#{int(rgba.red * 255):02x}{int(rgba.green * 255):02x}{int(rgba.blue * 255):02x}"


def darken(hex_color, pct):
    """Return a darkened version of `hex_color` by `pct` percent (0-100).

    A `pct` of 0 returns the original color unchanged.
    A `pct` of 100 returns black.
    Used for the 'Auto Color' bar mode to distinguish foreground from background.
    """
    if pct <= 0:
        return hex_color
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    # Scale each channel towards zero by the darkening factor.
    f = max(0.0, 1.0 - (pct / 100.0))
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"
