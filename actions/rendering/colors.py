"""Utilidades de color compartidas entre renderizado y UI de configuración."""

DEFAULT_GRADIENT_COLORS = ["#00ff00", "#ffff00", "#ff0000", "#00ffff", "#ffff00", "#ff00ff"]


def parse_color(hex_str):
    """'#rrggbb' o '#rrggbbaa' -> tupla (r, g, b, a) en rango 0.0-1.0."""
    hex_str = (hex_str or "").lstrip("#")
    if len(hex_str) in (6, 8):
        try:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            a = int(hex_str[6:8], 16) / 255.0 if len(hex_str) == 8 else 1.0
            return (r, g, b, a)
        except ValueError:
            pass
    return (1, 1, 1, 1)


def rgba_to_hex(rgba):
    """Gdk.RGBA -> '#rrggbb'."""
    return f"#{int(rgba.red * 255):02x}{int(rgba.green * 255):02x}{int(rgba.blue * 255):02x}"


def darken(hex_color, pct):
    """Oscurece un color '#rrggbb' un porcentaje (0-100)."""
    if pct <= 0:
        return hex_color
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    f = max(0.0, 1.0 - (pct / 100.0))
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"
