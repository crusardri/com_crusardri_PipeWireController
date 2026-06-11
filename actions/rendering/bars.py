"""Renderizado de las barras de volumen, balance y monitor de picos.

BarRenderer es puramente VISTA: no toca PulseAudio, recibe los valores
(volúmenes, balance, dB) ya leídos por el controlador.
"""
import cairo

from .colors import parse_color, darken, DEFAULT_GRADIENT_COLORS
from .shapes import rounded_rect, rounded_rect_custom

DB_FLOOR = -60.0
DB_CEIL = 0.0

# Estilos de barra (setting "bar_style")
STYLE_TWO_BARS = 0
STYLE_ONE_BAR = 1
STYLE_ONE_BAR_TRIANGLE = 2
STYLE_ONE_BAR_LINE = 3


def _db_to_pct(db):
    return max(0.0, min(1.0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))


class BarRenderer:
    """Dibuja las barras de un frame; la geometría se calcula una sola vez."""

    def __init__(self, ctx, settings, defs, *, is_single_mode, is_monitor_active, palettes=None):
        self.ctx = ctx
        self.settings = settings
        self.defs = defs
        self.palettes = palettes or {}

        style = int(settings.get("bar_style", STYLE_TWO_BARS))
        if is_single_mode and style == STYLE_TWO_BARS:
            style = STYLE_ONE_BAR
        self.style = style
        self.mon_bar_mode = int(settings.get("monitor_bar_mode", 0))
        self.invert = settings.get("bar_invert", False)

        # Si el frame muestra dos barras apiladas, cada una ocupa media altura.
        split = (is_monitor_active and self.mon_bar_mode != 0) or \
                (not is_monitor_active and not is_single_mode and style == STYLE_TWO_BARS)

        h = settings.get("bar_height", defs["bar_height"])
        if split:
            h = max(1, (h - 2) // 2)
        self.h = h

        self.x = settings.get("bar_x", defs["bar_x"])
        self.base_y = settings.get("bar_y", defs["bar_y"])
        self.w = settings.get("bar_width", defs["bar_width"])
        rad = settings.get("bar_radius", defs["bar_radius"])
        if split:
            rad /= 2.0
        self.rad = min(rad, self.h / 2.0)

        self.c_over = parse_color(settings.get("bar_over_color", "#ff4b4b"))
        self.c_ind = parse_color(settings.get("bar_ind_color", "#FFFFFF"))
        self.c_neu = parse_color(settings.get("bar_neu_color", "#808080"))

    # ---------- fuentes de color ----------

    def auto_color(self, prefix, suffix, index=0):
        """Color derivado de la paleta del icono, con oscurecido configurable."""
        pal = self.palettes.get(suffix) or []
        base_color = pal[index] if len(pal) > index else "#ffffff"
        def_darken = 50 if prefix == "bar_bg" else 0
        pct = int(self.settings.get(f"{prefix}_auto_darken", def_darken))
        return darken(base_color, pct)

    def gradient_source(self, prefix, suffix, x, w, invert):
        if invert:
            lg = cairo.LinearGradient(x + w, 0, x, 0)
        else:
            lg = cairo.LinearGradient(x, 0, x + w, 0)

        cmode = int(self.settings.get(f"{prefix}_color_mode", 0))
        if cmode == 3 or (prefix == "monitor" and cmode == 4):
            stops = 3
            for i in range(stops):
                c = parse_color(self.auto_color(prefix, suffix, i))
                lg.add_color_stop_rgba(i / (stops - 1), *c)
        else:
            stops = int(self.settings.get(f"{prefix}_gradient_stops", 3))
            for i in range(stops):
                fallback = DEFAULT_GRADIENT_COLORS[i] if i < len(DEFAULT_GRADIENT_COLORS) else "#ffffff"
                c = parse_color(self.settings.get(f"{prefix}_gradient_{i + 1}", fallback))
                lg.add_color_stop_rgba(i / (stops - 1), *c)
        return lg

    def color_source(self, prefix, suffix, def_color, invert=None):
        """Color sólido, automático o degradado según el modo configurado."""
        cmode = int(self.settings.get(f"{prefix}_color_mode", 0))
        if cmode == 0:
            return parse_color(self.settings.get(f"{prefix}_color", def_color))
        if cmode == 2:
            return parse_color(self.auto_color(prefix, suffix, 0))
        if invert is None:
            invert = self.invert
        return self.gradient_source(prefix, suffix, self.x, self.w, invert)

    def _set_source(self, color):
        if isinstance(color, cairo.LinearGradient):
            self.ctx.set_source(color)
        else:
            self.ctx.set_source_rgba(*color)

    # ---------- primitivas ----------

    def background(self, y, suffix="a", invert=None):
        c_bg = self.color_source("bar_bg", suffix, "#424242", invert)
        rounded_rect(self.ctx, self.x, y, self.w, self.h, self.rad)
        self._set_source(c_bg)
        self.ctx.fill()

    def fill(self, start_x, w, rad, color, y, corner_flags=None):
        if corner_flags is None:
            tl = tr = br = bl = rad
        else:
            tl, tr, br, bl = (rad if f else 0 for f in corner_flags)
        rounded_rect_custom(self.ctx, start_x, y, w, self.h, tl, tr, br, bl)
        self._set_source(color)
        self.ctx.fill()

    def outline(self, y):
        out_w = self.settings.get("bar_out_width", self.defs.get("bar_out_width", 1))
        if out_w > 0:
            c = parse_color(self.settings.get("bar_out_color", self.defs.get("bar_out_color", "#000000")))
            self.ctx.set_source_rgba(*c)
            self.ctx.set_line_width(out_w)
            rounded_rect(self.ctx, self.x, y, self.w, self.h, self.rad)
            self.ctx.stroke()

    @staticmethod
    def _fill_rad(rad, w):
        return rad if w > rad * 2 else w / 2

    def _volume_widths(self, vol_pct):
        """Anchos de relleno normal (0-100%) y de exceso (100-150%)."""
        active_w = min(self.w, int(self.w * (min(vol_pct, 100.0) / 100.0)))
        over_w = int(self.w * (min(50.0, max(0.0, vol_pct - 100.0)) / 100.0))
        return active_w, over_w

    # ---------- barras completas ----------

    def legacy_bar(self, y, suffix, vol_pct, invert):
        """Barra clásica por dispositivo (estilo '2 Bars'). vol_pct None = sin dispositivo."""
        self.background(y, suffix, invert)
        if vol_pct is not None:
            c_bar = self.color_source("bar", suffix, "#FFFFFF", invert)
            active_w, over_w = self._volume_widths(vol_pct)
            if active_w > 0:
                start_x = self.x if not invert else self.x + self.w - active_w
                self.fill(start_x, active_w, self._fill_rad(self.rad, active_w), c_bar, y)
            if over_w > 0:
                start_x = self.x if not invert else self.x + self.w - over_w
                self.fill(start_x, over_w, self._fill_rad(self.rad, over_w), self.c_over, y)
        self.outline(y)

    def single_bar(self, vol_pct):
        """Barra única 0-150%. Devuelve la X del marcador."""
        y = self.base_y
        self.background(y, "a", self.invert)
        c_bar = self.color_source("bar", "a", "#FFFFFF", self.invert)
        marker_x = self.x
        if vol_pct is not None:
            active_w, over_w = self._volume_widths(vol_pct)
            if self.invert:
                fill_start = self.x + self.w - active_w
                over_start = self.x + self.w - over_w
                marker_x = fill_start
            else:
                fill_start = self.x
                over_start = self.x
                marker_x = self.x + active_w
            if active_w > 0:
                self.fill(fill_start, active_w, self._fill_rad(self.rad, active_w), c_bar, y)
            if over_w > 0:
                self.fill(over_start, over_w, self._fill_rad(self.rad, over_w), self.c_over, y)
        self.outline(y)
        return marker_x

    def balance_bar(self, balance):
        """Barra de balance centrada (0=A, 50=centro, 100=B). Devuelve la X del marcador."""
        y = self.base_y
        self.background(y, "a", self.invert)
        c_bar = self.color_source("bar", "a", "#FFFFFF", self.invert)
        center_x = self.x + self.w / 2.0

        if balance < 50:
            pct = (50.0 - balance) / 50.0
            fill_w = int((self.w / 2.0) * pct)
            fill_start = int(center_x - fill_w)
            marker_x = fill_start
            corner_flags = (True, False, False, True)  # recta hacia el centro
        else:
            pct = (balance - 50.0) / 50.0
            fill_w = int((self.w / 2.0) * pct)
            fill_start = int(center_x)
            marker_x = int(center_x + fill_w)
            corner_flags = (False, True, True, False)

        if fill_w > 0:
            self.fill(fill_start, fill_w, self._fill_rad(self.rad, fill_w), c_bar, y, corner_flags)

        # línea neutral central
        self.ctx.set_source_rgba(*self.c_neu)
        self.ctx.move_to(center_x, y)
        self.ctx.line_to(center_x, y + self.h)
        self.ctx.set_line_width(2)
        self.ctx.stroke()

        self.outline(y)
        return marker_x

    def marker(self, marker_x):
        """Indicador de posición (triángulos o línea) según el estilo activo."""
        if self.style not in (STYLE_ONE_BAR_TRIANGLE, STYLE_ONE_BAR_LINE):
            return
        ctx = self.ctx
        y = self.base_y
        out_w = self.settings.get("bar_out_width", self.defs.get("bar_out_width", 1))
        c_out = parse_color(self.settings.get("bar_out_color", self.defs.get("bar_out_color", "#000000")))

        if self.style == STYLE_ONE_BAR_TRIANGLE:
            tri_w = tri_h = 8
            ctx.move_to(marker_x, y - 2)
            ctx.line_to(marker_x - tri_w / 2, y - 2 - tri_h)
            ctx.line_to(marker_x + tri_w / 2, y - 2 - tri_h)
            ctx.close_path()
            ctx.move_to(marker_x, y + self.h + 2)
            ctx.line_to(marker_x - tri_w / 2, y + self.h + 2 + tri_h)
            ctx.line_to(marker_x + tri_w / 2, y + self.h + 2 + tri_h)
            ctx.close_path()
            ctx.set_source_rgba(*self.c_ind)
            ctx.fill_preserve()
            if out_w > 0:
                ctx.set_source_rgba(*c_out)
                ctx.set_line_width(out_w)
                ctx.stroke()
            else:
                ctx.new_path()
        else:
            if out_w > 0:
                ctx.set_source_rgba(*c_out)
                ctx.move_to(marker_x, y - 4)
                ctx.line_to(marker_x, y + self.h + 4)
                ctx.set_line_width(3 + out_w * 2)
                ctx.stroke()
            ctx.set_source_rgba(*self.c_ind)
            ctx.move_to(marker_x, y - 4)
            ctx.line_to(marker_x, y + self.h + 4)
            ctx.set_line_width(3)
            ctx.stroke()

    def monitor_meter(self, y, suffix, peak_db, rms_db):
        """Vúmetro: pico relleno + indicador RMS opcional."""
        s = self.settings
        invert = s.get("monitor_invert", False)
        self.background(y, suffix, invert)

        cmode = int(s.get("monitor_color_mode", 0))
        fill_w = int(self.w * _db_to_pct(peak_db))
        if fill_w > 0:
            rad_fill = self._fill_rad(self.rad, fill_w)
            fill_x = (self.x + self.w - fill_w) if invert else self.x

            if cmode == 0:
                self.fill(fill_x, fill_w, rad_fill, parse_color(s.get("monitor_color_solid", "#ffffff")), y)
            elif cmode == 3:
                self.fill(fill_x, fill_w, rad_fill, parse_color(self.auto_color("monitor", suffix, 0)), y)
            else:
                rounded_rect_custom(self.ctx, fill_x, y, fill_w, self.h,
                                    rad_fill, rad_fill, rad_fill, rad_fill)
                if cmode == 1:
                    lg = self._tricolor_gradient(invert)
                else:  # cmode 2 o 4: degradado manual o automático
                    lg = self.gradient_source("monitor", suffix, self.x, self.w, invert)
                self.ctx.set_source(lg)
                self.ctx.fill()

        self.outline(y)

        if s.get("monitor_show_rms", False):
            self._rms_line(y, rms_db, invert)

    def _tricolor_gradient(self, invert):
        s = self.settings
        if invert:
            lg = cairo.LinearGradient(self.x + self.w, 0, self.x, 0)
        else:
            lg = cairo.LinearGradient(self.x, 0, self.x + self.w, 0)
        c_low = parse_color(s.get("monitor_color_low", "#00ff00"))
        c_mid = parse_color(s.get("monitor_color_mid", "#ffff00"))
        c_high = parse_color(s.get("monitor_color_high", "#ff0000"))
        pct_mid = _db_to_pct(float(s.get("monitor_threshold_mid", -20)))
        pct_high = _db_to_pct(float(s.get("monitor_threshold_high", -9)))
        lg.add_color_stop_rgba(0.0, *c_low)
        lg.add_color_stop_rgba(max(0.0, pct_mid - 0.001), *c_low)
        lg.add_color_stop_rgba(pct_mid, *c_mid)
        lg.add_color_stop_rgba(max(0.0, pct_high - 0.001), *c_mid)
        lg.add_color_stop_rgba(pct_high, *c_high)
        return lg

    def _rms_line(self, y, rms_db, invert):
        s = self.settings
        pct_rms = _db_to_pct(rms_db)
        if invert:
            rms_x = self.x + self.w - int(self.w * pct_rms)
            rms_x = min(self.x + self.w - 1, max(self.x + 1, rms_x))
        else:
            rms_x = self.x + int(self.w * pct_rms)
            rms_x = min(self.x + self.w - 2, max(self.x, rms_x - 1))

        rms_out_w = float(s.get("monitor_rms_out_width", 1.0))
        if rms_out_w > 0:
            self.ctx.set_source_rgba(*parse_color(s.get("monitor_rms_out_color", "#000000")))
            self.ctx.set_line_width(2.0 + (rms_out_w * 2))
            self.ctx.move_to(rms_x, y)
            self.ctx.line_to(rms_x, y + self.h)
            self.ctx.stroke()

        self.ctx.set_source_rgba(*parse_color(s.get("monitor_rms_color", "#FFFFFF")))
        self.ctx.set_line_width(2.0)
        self.ctx.move_to(rms_x, y)
        self.ctx.line_to(rms_x, y + self.h)
        self.ctx.stroke()
