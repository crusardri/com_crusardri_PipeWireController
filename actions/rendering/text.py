"""Renderizado de texto compartido por el mixer y el carrusel."""
import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo
import cairo

from .colors import parse_color


class FontDefaults:
    """Valores tipográficos por defecto de StreamController, normalizados."""

    def __init__(self, defaults: dict):
        self.color = self._hex(defaults.get("font-color"), "#FFFFFF")
        self.outline_color = self._hex(defaults.get("outline-color"), "#000000")
        self.outline_width = int(defaults.get("outline-width", 2))
        self.align = defaults.get("alignment", "center")
        self.family = defaults.get("font-family", "Sans")
        self.size = int(defaults.get("font-size", 15))
        self.desc = f"{self.family} {self.size}"

    @staticmethod
    def _hex(val, fallback):
        if val:
            return f"#{val[0]:02x}{val[1]:02x}{val[2]:02x}"
        return fallback

    @classmethod
    def from_global(cls):
        import globals as gl
        return cls(gl.settings_manager.font_defaults)


def draw_text_section(ctx, settings, key_suffix, text, font_defaults, *,
                      defs=None, default_y=0, default_font_desc=None,
                      margin=0, default_max_w=0, anchor_bottom=False):
    """Dibuja un bloque de texto configurable (fuente, color, alineación,
    contorno, posición y ancho máximo con elipsis).

    Cada propiedad se resuelve en cascada: settings -> defs -> defaults.
    Con `anchor_bottom` la Y indica el borde inferior del texto.
    """
    defs = defs or {}
    fd = font_defaults

    align = settings.get(f"align_{key_suffix}", defs.get(f"align_{key_suffix}", fd.align))
    out_width = int(settings.get(f"outline_width_{key_suffix}", fd.outline_width))
    c_out = parse_color(settings.get(f"outline_color_{key_suffix}", fd.outline_color))
    c_text = parse_color(settings.get(f"color_{key_suffix}", fd.color))

    curr_font = settings.get(f"font_desc_{key_suffix}", default_font_desc or fd.desc)
    desc = Pango.FontDescription.from_string(curr_font) if curr_font else Pango.FontDescription()

    layout = PangoCairo.create_layout(ctx)
    layout.set_font_description(desc)
    layout.set_text(text, -1)

    max_w = settings.get(f"width_{key_suffix}", defs.get(f"width_{key_suffix}", default_max_w))
    layout.set_width(max_w * Pango.SCALE)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    w_pango, h_pango = layout.get_pixel_size()

    base_x = settings.get(f"pos_x_{key_suffix}", defs.get(f"pos_x_{key_suffix}", margin))
    y_val = settings.get(f"pos_y_{key_suffix}", defs.get(f"pos_y_{key_suffix}", default_y))
    y_pos = y_val - h_pango if anchor_bottom else y_val

    if align == "left":
        x = base_x
    elif align == "right":
        x = base_x + max_w - w_pango
    else:
        x = base_x + int((max_w - w_pango) / 2)

    if out_width > 0:
        ctx.move_to(x, y_pos)
        PangoCairo.layout_path(ctx, layout)
        ctx.set_source_rgba(*c_out)
        ctx.set_line_width(out_width * 2)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.stroke()

    ctx.set_source_rgba(*c_text)
    ctx.move_to(x, y_pos)
    PangoCairo.show_layout(ctx, layout)


def draw_centered_text(ctx, text, width, height, color=(1, 1, 1, 1)):
    """Texto simple centrado en el lienzo (mensajes de estado)."""
    layout = PangoCairo.create_layout(ctx)
    layout.set_text(text, -1)
    w, h = layout.get_pixel_size()
    ctx.set_source_rgba(*color)
    ctx.move_to((width - w) // 2, (height - h) // 2)
    PangoCairo.show_layout(ctx, layout)


def draw_anchored_text(ctx, text, font_desc, color, x, bottom_y):
    """Texto anclado por su esquina inferior izquierda."""
    layout = PangoCairo.create_layout(ctx)
    layout.set_font_description(Pango.FontDescription.from_string(font_desc))
    layout.set_text(text, -1)
    _, h = layout.get_pixel_size()
    ctx.move_to(x, bottom_y - h)
    ctx.set_source_rgba(*color)
    PangoCairo.show_layout(ctx, layout)
