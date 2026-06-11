"""Icon loading, caching, and post-processing (PIL + GTK theme lookup)."""
import io
import logging

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf
from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger(__name__)


class IconCache:
    """Bounded FIFO cache for rendered icon images.

    Entries are (PIL image, colour palette) pairs keyed by a string composed
    of the icon path, target height, outline width, and outline colour.
    The oldest entry is evicted when the cache is full.
    """

    def __init__(self, max_size=50):
        self._data = {}
        self._max = max_size

    def get(self, key):
        """Return the cached value for `key`, or None if not present."""
        return self._data.get(key)

    def put(self, key, value):
        """Insert `key` → `value`, evicting the oldest entry when at capacity."""
        if key not in self._data and len(self._data) >= self._max:
            # Remove the first key in insertion order (FIFO).
            self._data.pop(next(iter(self._data)))
        self._data[key] = value


def load_icon_as_pil(icon_path, target_h):
    """Load an icon file and return it as a PIL RGBA image scaled to `target_h`.

    GdkPixbuf is tried first because it supports SVG and preserves aspect ratio.
    Falls back to PIL for any format GdkPixbuf cannot handle.
    Returns a transparent square image on complete failure.
    """
    try:
        # Query the image dimensions to compute a proportional target width.
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

    # PIL fallback: works for raster formats not supported by GdkPixbuf.
    try:
        pil_img = Image.open(icon_path).convert("RGBA")
        i_w = int(pil_img.width * (target_h / float(pil_img.height))) if pil_img.height > 0 else target_h
        return pil_img.resize((i_w, target_h), Image.Resampling.LANCZOS)
    except Exception as e:
        log.debug("Error resizing PIL image: %s", e)
        return Image.new("RGBA", (target_h, target_h), (0, 0, 0, 0))


def extract_palette(pil_img, num_colors=3):
    """Extract the `num_colors` most dominant colours from `pil_img`.

    For images with transparency, pixels with alpha < 128 are ignored.
    Returns a list of '#rrggbb' hex strings; the list is always exactly
    `num_colors` long (padded with the last colour if needed).
    """
    try:
        if pil_img.mode in ("RGBA", "LA") or (pil_img.mode == "P" and "transparency" in pil_img.info):
            img = pil_img.convert("RGBA")
            img.thumbnail((32, 32))  # Downsample before counting for speed.
            colors = img.getcolors(4096)
            if colors:
                # Keep only sufficiently opaque pixels.
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
                    # Pad with the last found colour if fewer than requested.
                    while len(palette) < num_colors:
                        palette.append(palette[-1] if palette else "#ffffff")
                    return palette

        # For fully opaque images, use quantisation.
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
    """Look up an icon by name in the current GTK icon theme.

    Returns the filesystem path to the icon file, or None if not found.
    Used to resolve application icons from their 'application.icon_name' property.
    """
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
    """Return `pil_img` with a solid-colour outline around its opaque regions.

    Expands the alpha channel with a max-filter (morphological dilation) to
    create the outline shape, then composites the original on top.
    """
    r, g, b, _ = [int(c * 255) for c in rgba_color]
    alpha = pil_img.split()[3]
    # Dilate the alpha mask by `width` pixels to form the outline silhouette.
    expanded_alpha = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    outline_img = Image.new("RGBA", pil_img.size, (r, g, b, 255))
    outline_img.putalpha(expanded_alpha)
    # Paste the original icon on top to restore interior pixels.
    outline_img.paste(pil_img, (0, 0), pil_img)
    return outline_img


def draw_mute_cross(base_img, x, y, w, h):
    """Draw a red diagonal line across the icon region to indicate mute state."""
    d = ImageDraw.Draw(base_img)
    d.line([(x, y), (x + w, y + h)], fill=(255, 0, 0, 255), width=max(3, h // 10))
