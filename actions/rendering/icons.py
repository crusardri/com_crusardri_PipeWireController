"""Carga, caché y tratamiento de iconos (PIL + tema GTK)."""
import io
import logging

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf
from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger(__name__)


class IconCache:
    """Caché FIFO acotada de iconos ya renderizados (imagen + paleta)."""

    def __init__(self, max_size=50):
        self._data = {}
        self._max = max_size

    def get(self, key):
        return self._data.get(key)

    def put(self, key, value):
        if key not in self._data and len(self._data) >= self._max:
            self._data.pop(next(iter(self._data)))
        self._data[key] = value


def load_icon_as_pil(icon_path, target_h):
    """Carga un icono (SVG/PNG/...) escalado a `target_h` como PIL RGBA.

    Usa GdkPixbuf (soporta SVG) y cae a PIL puro si falla.
    """
    try:
        pixbuf_info = GdkPixbuf.Pixbuf.get_file_info(icon_path)
        if pixbuf_info:
            w, h = pixbuf_info[1], pixbuf_info[2]
            target_w = int(w * (target_h / float(h))) if h > 0 else target_h
        else:
            target_w = target_h

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, target_w, target_h, True)
        success, buffer = pixbuf.save_to_bufferv("png", [], [])
        if success:
            return Image.open(io.BytesIO(buffer)).convert("RGBA")
    except Exception as e:
        log.debug("Error loading icon with GdkPixbuf: %s", e)

    try:
        pil_img = Image.open(icon_path).convert("RGBA")
        i_w = int(pil_img.width * (target_h / float(pil_img.height))) if pil_img.height > 0 else target_h
        return pil_img.resize((i_w, target_h), Image.Resampling.LANCZOS)
    except Exception as e:
        log.debug("Error resizing PIL image: %s", e)
        return Image.new("RGBA", (target_h, target_h), (0, 0, 0, 0))


def extract_palette(pil_img, num_colors=3):
    """Colores dominantes del icono como lista de hex (longitud `num_colors`)."""
    try:
        if pil_img.mode in ("RGBA", "LA") or (pil_img.mode == "P" and "transparency" in pil_img.info):
            img = pil_img.convert("RGBA")
            img.thumbnail((32, 32))
            colors = img.getcolors(4096)
            if colors:
                valid_colors = [(count, rgba) for count, rgba in colors if rgba[3] > 128]
                if valid_colors:
                    valid_colors.sort(key=lambda x: x[0], reverse=True)
                    palette = []
                    for _, rgba in valid_colors:
                        hex_c = f"#{rgba[0]:02x}{rgba[1]:02x}{rgba[2]:02x}"
                        if hex_c not in palette:
                            palette.append(hex_c)
                        if len(palette) >= num_colors:
                            break
                    while len(palette) < num_colors:
                        palette.append(palette[-1] if palette else "#ffffff")
                    return palette

        img = pil_img.convert("RGB")
        img.thumbnail((32, 32))
        q = img.quantize(colors=num_colors)
        pal = q.getpalette()
        res = []
        for i in range(num_colors):
            idx = i * 3
            if idx + 2 < len(pal):
                res.append(f"#{pal[idx]:02x}{pal[idx + 1]:02x}{pal[idx + 2]:02x}")
            else:
                res.append("#ffffff")
        return res
    except Exception as e:
        log.debug("Color extraction error: %s", e)
        return ["#ffffff"] * num_colors


def lookup_theme_icon(icon_name, size=48):
    """Busca un icono por nombre en el tema GTK; devuelve su ruta o None."""
    if not icon_name:
        return None
    try:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_info = theme.lookup_icon(icon_name, None, size, 1,
                                      Gtk.TextDirection.NONE, Gtk.IconLookupFlags.NONE)
        if icon_info:
            f = icon_info.get_file()
            if f:
                return f.get_path()
    except Exception as e:
        log.debug("Error finding icon %s: %s", icon_name, e)
    return None


def apply_outline(pil_img, width, rgba_color):
    """Devuelve el icono con un contorno sólido alrededor de sus zonas opacas."""
    r, g, b, _ = [int(c * 255) for c in rgba_color]
    alpha = pil_img.split()[3]
    expanded_alpha = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    outline_img = Image.new("RGBA", pil_img.size, (r, g, b, 255))
    outline_img.putalpha(expanded_alpha)
    outline_img.paste(pil_img, (0, 0), pil_img)
    return outline_img


def draw_mute_cross(base_img, x, y, w, h):
    """Tacha en rojo la zona del icono para indicar silencio."""
    d = ImageDraw.Draw(base_img)
    d.line([(x, y), (x + w, y + h)], fill=(255, 0, 0, 255), width=max(3, h // 10))
