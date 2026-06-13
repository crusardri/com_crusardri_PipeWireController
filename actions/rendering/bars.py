"""Volume bar, balance bar, and peak-monitor meter rendering.

BarRenderer is purely a VIEW: it never touches PulseAudio.  All values
(volumes, balance, dB readings) are passed in by the controller and drawn
onto an existing cairo context.
"""
import cairo
import math

from .colors import parse_color, darken, DEFAULT_GRADIENT_COLORS
from .shapes import rounded_rect, rounded_rect_custom

# dB range displayed by the monitor meter.
DB_FLOOR = -60.0
DB_CEIL = 0.0

# Bar style constants (stored in the settings as "bar_style").
STYLE_TWO_BARS = 0       # one bar per device, stacked (mixer mode only)
STYLE_ONE_BAR = 1        # single bar with no position marker
STYLE_ONE_BAR_TRIANGLE = 2  # single bar + triangle marker
STYLE_ONE_BAR_LINE = 3   # single bar + vertical line marker
STYLE_ONE_BAR_DOT = 4    # single bar + dot (circle) marker


def _db_to_pct(db):
    """Map a dB value to a 0.0-1.0 fill fraction within [DB_FLOOR, DB_CEIL]."""
    return max(0.0, min(1.0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))


class BarRenderer:
    """Draws the bars for a single frame.

    Geometry is computed once in __init__ so every drawing method shares
    the same x/y/w/h without re-reading settings individually.
    """

    def __init__(self, ctx, settings, defs, *, is_single_mode, is_monitor_active, palettes=None):
        """Initialise the renderer and resolve all geometry from settings.

        Args:
            ctx: Active cairo.Context to draw on.
            settings: Action settings dict.
            defs: Geometry defaults dict from get_calculated_defaults().
            is_single_mode: True when only one device is active.
            is_monitor_active: True when the peak monitor is running and visible.
            palettes: Dict {"a": [...], "b": [...]} of icon palette hex strings.
        """
        self.ctx = ctx
        self.settings = settings
        self.defs = defs
        self.palettes = palettes or {}

        # In single-mode the 2-bars style makes no sense, so fall back to 1-bar.
        style = int(settings.get("bar_style", STYLE_TWO_BARS))
        if is_single_mode and style == STYLE_TWO_BARS:
            style = STYLE_ONE_BAR
        self.style = style
        self.mon_bar_mode = int(settings.get("monitor_bar_mode", 0))
        self.invert = settings.get("bar_invert", False)

        # Determine whether the frame shows two vertically stacked bars.
        # This happens in dual-bar monitor mode or in classic 2-bars mixer mode.
        split = (is_monitor_active and self.mon_bar_mode != 0) or \
                (not is_monitor_active and not is_single_mode and style == STYLE_TWO_BARS)

        h = settings.get("bar_height", defs["bar_height"])
        if split:
            # Each of the two bars gets half the height minus a 2 px gap.
            h = max(1, (h - 2) // 2)
        self.h = h

        self.x = settings.get("bar_x", defs["bar_x"])
        self.base_y = settings.get("bar_y", defs["bar_y"])
        self.w = settings.get("bar_width", defs["bar_width"])
        rad = settings.get("bar_radius", defs["bar_radius"])
        if split:
            rad /= 2.0
        # Clamp radius so it never exceeds half the bar height (would look wrong).
        self.rad = min(rad, self.h / 2.0)

        # Pre-parse secondary colours used by several methods.
        self.c_over = parse_color(settings.get("bar_over_color", "#ff4b4b"))  # over-100% fill
        self.c_ind = parse_color(settings.get("bar_ind_color", "#FFFFFF"))    # marker indicator
        self.c_neu = parse_color(settings.get("bar_neu_color", "#808080"))    # balance neutral line

        # Per-corner rounding for background/fill/outline: (tl, tr, br, bl)
        # booleans, or None to round all four corners.  Used by the split
        # fader to draw a flat edge on the side shared with its partner key.
        self.corner_flags = None

        # Monitor meter sub-range: the peak/RMS dB fraction is remapped from
        # [fill_lo, fill_hi] to fill the whole bar.  Defaults span the full
        # range; the split fader sets 0-0.5 or 0.5-1.0 so each key shows its
        # half of one continuous meter (mirrors the volume bar's remap).
        self.fill_lo = 0.0
        self.fill_hi = 1.0

    # ------------------------------------------------------------------
    # Colour source helpers
    # ------------------------------------------------------------------

    def auto_color(self, prefix, suffix, index=0):
        """Derive a colour from the icon palette with optional darkening.

        Falls back to white when the palette does not have enough entries.
        The 'bar_bg' prefix darkens by 50% by default (vs 0% for the fill).
        """
        pal = self.palettes.get(suffix) or []
        base_color = pal[index] if len(pal) > index else "#ffffff"
        def_darken = 50 if prefix == "bar_bg" else 0
        pct = int(self.settings.get(f"{prefix}_auto_darken", def_darken))
        return darken(base_color, pct)

    def gradient_source(self, prefix, suffix, x, w, invert):
        """Build a cairo LinearGradient from the configured gradient stops.

        The gradient runs left→right (or right→left if `invert` is True).
        When the colour mode is 'auto gradient', the palette colours are used
        as stops instead of the user-configured ones.
        """
        if invert:
            lg = cairo.LinearGradient(x + w, 0, x, 0)
        else:
            lg = cairo.LinearGradient(x, 0, x + w, 0)

        cmode = int(self.settings.get(f"{prefix}_color_mode", 0))
        if cmode == 3 or (prefix == "monitor" and cmode == 4):
            # Auto gradient: use up to 3 palette colours as stops.
            stops = 3
            for i in range(stops):
                c = parse_color(self.auto_color(prefix, suffix, i))
                lg.add_color_stop_rgba(i / (stops - 1), *c)
        else:
            # Manual gradient: use user-configured colours.
            stops = int(self.settings.get(f"{prefix}_gradient_stops", 3))
            for i in range(stops):
                fallback = DEFAULT_GRADIENT_COLORS[i] if i < len(DEFAULT_GRADIENT_COLORS) else "#ffffff"
                c = parse_color(self.settings.get(f"{prefix}_gradient_{i + 1}", fallback))
                lg.add_color_stop_rgba(i / (stops - 1), *c)
        return lg

    def color_source(self, prefix, suffix, def_color, invert=None):
        """Return the appropriate cairo paint for the given settings prefix.

        Dispatches to solid, auto-solid, or gradient depending on `color_mode`.
        The returned value can be a (r, g, b, a) tuple or a cairo.LinearGradient.
        """
        cmode = int(self.settings.get(f"{prefix}_color_mode", 0))
        if cmode == 0:
            return parse_color(self.settings.get(f"{prefix}_color", def_color))
        if cmode == 2:
            return parse_color(self.auto_color(prefix, suffix, 0))
        if invert is None:
            invert = self.invert
        vx, vw = self._virtual_geometry(invert)
        return self.gradient_source(prefix, suffix, vx, vw, invert)

    def _set_source(self, color):
        """Apply `color` to the cairo context (tuple → set_source_rgba, gradient → set_source)."""
        if isinstance(color, cairo.LinearGradient):
            self.ctx.set_source(color)
        else:
            self.ctx.set_source_rgba(*color)

    # ------------------------------------------------------------------
    # Primitives (background, fill, outline)
    # ------------------------------------------------------------------

    def _bar_path(self, y):
        """Trace the bar's rounded-rect path honouring `corner_flags`."""
        if self.corner_flags is None:
            rounded_rect(self.ctx, self.x, y, self.w, self.h, self.rad)
        else:
            tl, tr, br, bl = (self.rad if f else 0 for f in self.corner_flags)
            rounded_rect_custom(self.ctx, self.x, y, self.w, self.h, tl, tr, br, bl)

    def background(self, y, suffix="a", invert=None):
        """Draw the bar background rectangle at vertical position `y`."""
        c_bg = self.color_source("bar_bg", suffix, "#424242", invert)
        self._bar_path(y)
        self._set_source(c_bg)
        self.ctx.fill()

    def fill(self, start_x, w, rad, color, y, corner_flags=None):
        """Draw a filled section of the bar.

        `rad` and `corner_flags` are ignored because the caller now clips
        the cairo context to the background's rounded rectangle path.
        """
        self.ctx.rectangle(start_x, y, w, self.h)
        self._set_source(color)
        self.ctx.fill()

    def outline(self, y):
        """Stroke the bar outline at vertical position `y` if outline width > 0."""
        out_w = self.settings.get("bar_out_width", self.defs.get("bar_out_width", 1))
        if out_w > 0:
            c = parse_color(self.settings.get("bar_out_color", self.defs.get("bar_out_color", "#000000")))
            self.ctx.set_source_rgba(*c)
            self.ctx.set_line_width(out_w)
            self._bar_path(y)
            self.ctx.stroke()



    def _meter_pct(self, db):
        """Map a dB value to a 0-1 fill fraction within the active sub-range."""
        p = _db_to_pct(db)
        lo, hi = self.fill_lo, self.fill_hi
        if hi <= lo:
            return p
        return max(0.0, min(1.0, (p - lo) / (hi - lo)))

    def _virtual_geometry(self, invert):
        """Calculate the virtual geometry spanning the full 0-100% meter range.
        
        For the split fader, the active sub-range (fill_lo to fill_hi) maps
        to the physical bar (self.x, self.w). The virtual bar covers the
        entire 0.0-1.0 range, potentially starting outside the physical key.
        Returns (virtual_left_edge_x, virtual_width).
        """
        span = self.fill_hi - self.fill_lo
        if span <= 0 or span >= 1.0:
            return self.x, self.w
            
        virt_w = self.w / span
        if invert:
            virt_right_edge = self.x + self.w + (self.fill_lo * virt_w)
            return virt_right_edge - virt_w, virt_w
        else:
            return self.x - (self.fill_lo * virt_w), virt_w

    def _volume_widths(self, vol_pct, virt_w):
        """Compute the normal-range and over-100% fill widths for a given volume."""
        active_w = int(virt_w * (min(vol_pct, 100.0) / 100.0))
        over_w = int(virt_w * (min(50.0, max(0.0, vol_pct - 100.0)) / 100.0))
        return active_w, over_w

    # ------------------------------------------------------------------
    # Complete bar types
    # ------------------------------------------------------------------

    def legacy_bar(self, y, suffix, vol_pct, invert):
        """Draw a classic per-device bar (used in '2 Bars' mixer style).

        `vol_pct` is None when the device is offline (only the background is drawn).
        Volumes above 100% are shown in the 'over' colour (default red).
        """
        self.background(y, suffix, invert)
        if vol_pct is not None:
            self.ctx.save()
            self._bar_path(y)
            self.ctx.clip()
            
            virt_left, virt_w = self._virtual_geometry(invert)
            c_bar = self.color_source("bar", suffix, "#FFFFFF", invert)
            active_w, over_w = self._volume_widths(vol_pct, virt_w)
            
            if invert:
                virt_right = virt_left + virt_w
                fill_start = virt_right - active_w
                over_start = virt_right - over_w
            else:
                fill_start = virt_left
                over_start = virt_left

            if active_w > 0:
                self.fill(fill_start, active_w, 0, c_bar, y)
            if over_w > 0:
                self.fill(over_start, over_w, 0, self.c_over, y)
            self.ctx.restore()
        self.outline(y)

    def single_bar(self, vol_pct):
        """Draw a single volume bar spanning 0-150% of the bar width.

        Returns the X pixel position of the volume marker edge so the
        caller can pass it to marker().
        """
        y = self.base_y
        self.background(y, "a", self.invert)
        c_bar = self.color_source("bar", "a", "#FFFFFF", self.invert)
        marker_x = self.x
        if vol_pct is not None:
            self.ctx.save()
            self._bar_path(y)
            self.ctx.clip()

            virt_left, virt_w = self._virtual_geometry(self.invert)
            active_w, over_w = self._volume_widths(vol_pct, virt_w)
            
            if self.invert:
                virt_right = virt_left + virt_w
                fill_start = virt_right - active_w
                over_start = virt_right - over_w
                marker_x = fill_start
            else:
                fill_start = virt_left
                over_start = virt_left
                marker_x = virt_left + active_w
                
            if active_w > 0:
                self.fill(fill_start, active_w, 0, c_bar, y)
            if over_w > 0:
                self.fill(over_start, over_w, 0, self.c_over, y)
                
            self.ctx.restore()
        self.outline(y)
        return marker_x

    def balance_bar(self, balance):
        """Draw a centred balance bar for the crossfader/mixer display.

        `balance` is 0-100: 0 = full A, 50 = equal, 100 = full B.
        The fill extends left from centre (towards A) or right (towards B).
        Returns the X pixel position of the marker edge.
        """
        y = self.base_y
        self.background(y, "a", self.invert)
        
        self.ctx.save()
        self._bar_path(y)
        self.ctx.clip()
        
        c_bar = self.color_source("bar", "a", "#FFFFFF", self.invert)
        center_x = self.x + self.w / 2.0

        if balance < 50:
            pct = (50.0 - balance) / 50.0
            fill_w = int((self.w / 2.0) * pct)
            fill_start = int(center_x - fill_w)
            marker_x = fill_start
        else:
            pct = (balance - 50.0) / 50.0
            fill_w = int((self.w / 2.0) * pct)
            fill_start = int(center_x)
            marker_x = int(center_x + fill_w)

        if fill_w > 0:
            self.fill(fill_start, fill_w, 0, c_bar, y)

        self.ctx.restore()

        # Draw the neutral centre line to show the equal-volume reference point.
        self.ctx.set_source_rgba(*self.c_neu)
        self.ctx.move_to(center_x, y)
        self.ctx.line_to(center_x, y + self.h)
        self.ctx.set_line_width(2)
        self.ctx.stroke()

        self.outline(y)
        return marker_x

    def marker(self, marker_x):
        """Draw a position marker (triangles or vertical line) at `marker_x`.

        Only active when the bar style is STYLE_ONE_BAR_TRIANGLE or
        STYLE_ONE_BAR_LINE.  Does nothing for other styles.
        """
        if self.style not in (STYLE_ONE_BAR_TRIANGLE, STYLE_ONE_BAR_LINE, STYLE_ONE_BAR_DOT):
            return
        ctx = self.ctx
        y = self.base_y
        out_w = self.settings.get("bar_out_width", self.defs.get("bar_out_width", 1))
        c_out = parse_color(self.settings.get("bar_out_color", self.defs.get("bar_out_color", "#000000")))

        if self.style == STYLE_ONE_BAR_TRIANGLE:
            # Draw two filled triangles: one above, one below the bar.
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
        elif self.style == STYLE_ONE_BAR_LINE:
            # Draw a vertical line spanning above and below the bar.
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
        elif self.style == STYLE_ONE_BAR_DOT:
            # Draw a circle marker exactly on the line
            radius = (self.h / 2) + 3
            ctx.arc(marker_x, y + self.h / 2, radius, 0, 2 * math.pi)
            ctx.set_source_rgba(*self.c_ind)
            ctx.fill_preserve()
            if out_w > 0:
                ctx.set_source_rgba(*c_out)
                ctx.set_line_width(max(1.0, out_w))
                ctx.stroke()
            else:
                ctx.new_path()

    # ------------------------------------------------------------------
    # Peak monitor meter
    # ------------------------------------------------------------------

    def monitor_meter(self, y, suffix, peak_db, rms_db):
        """Draw a VU-style peak meter at vertical position `y`.

        The fill width represents the peak level.  An optional RMS indicator
        line is drawn on top when 'monitor_show_rms' is enabled.

        Color modes:
            0 = solid colour
            1 = tricolor gradient (green/yellow/red with configurable thresholds)
            2 = manual gradient
            3 = auto solid (from icon palette)
            4 = auto gradient (from icon palette)
        """
        s = self.settings
        invert = s.get("monitor_invert", False)
        self.background(y, suffix, invert)

        cmode = int(s.get("monitor_color_mode", 0))
        fill_w = int(self.w * self._meter_pct(peak_db))
        if fill_w > 0:
            self.ctx.save()
            self._bar_path(y)
            self.ctx.clip()

            fill_x = (self.x + self.w - fill_w) if invert else self.x

            if cmode == 0:
                # Solid colour mode.
                self.fill(fill_x, fill_w, 0, parse_color(s.get("monitor_color_solid", "#ffffff")), y)
            elif cmode == 3:
                # Auto solid: first palette colour from the icon.
                self.fill(fill_x, fill_w, 0, parse_color(self.auto_color("monitor", suffix, 0)), y)
            else:
                self.ctx.rectangle(fill_x, y, fill_w, self.h)
                if cmode == 1:
                    lg = self._tricolor_gradient(invert)
                else:
                    vx, vw = self._virtual_geometry(invert)
                    lg = self.gradient_source("monitor", suffix, vx, vw, invert)
                self.ctx.set_source(lg)
                self.ctx.fill()
                
            self.ctx.restore()

        self.outline(y)

        if s.get("monitor_show_rms", False):
            self._rms_line(y, rms_db, invert)

    def _tricolor_gradient(self, invert):
        """Build a tricolor (green/yellow/red) LinearGradient for the monitor meter.

        The transition thresholds are configurable in dB
        (settings: 'monitor_threshold_mid', 'monitor_threshold_high').
        """
        s = self.settings
        vx, vw = self._virtual_geometry(invert)
        if invert:
            lg = cairo.LinearGradient(vx + vw, 0, vx, 0)
        else:
            lg = cairo.LinearGradient(vx, 0, vx + vw, 0)
        c_low = parse_color(s.get("monitor_color_low", "#00ff00"))
        c_mid = parse_color(s.get("monitor_color_mid", "#ffff00"))
        c_high = parse_color(s.get("monitor_color_high", "#ff0000"))
        pct_mid = _db_to_pct(float(s.get("monitor_threshold_mid", -20)))
        pct_high = _db_to_pct(float(s.get("monitor_threshold_high", -9)))
        # Sharp colour transitions: the 0.001 offset creates a hard edge
        # between each colour zone without visible blending.
        lg.add_color_stop_rgba(0.0, *c_low)
        lg.add_color_stop_rgba(max(0.0, pct_mid - 0.001), *c_low)
        lg.add_color_stop_rgba(pct_mid, *c_mid)
        lg.add_color_stop_rgba(max(0.0, pct_high - 0.001), *c_mid)
        lg.add_color_stop_rgba(pct_high, *c_high)
        return lg

    def _rms_line(self, y, rms_db, invert):
        """Draw a vertical RMS indicator line on top of the peak fill.

        Drawn with an optional outline for visibility against any background.
        """
        s = self.settings
        pct_rms = self._meter_pct(rms_db)
        # Clamp the line position one pixel inset from the bar edges.
        if invert:
            rms_x = self.x + self.w - int(self.w * pct_rms)
            rms_x = min(self.x + self.w - 1, max(self.x + 1, rms_x))
        else:
            rms_x = self.x + int(self.w * pct_rms)
            rms_x = min(self.x + self.w - 2, max(self.x, rms_x - 1))

        rms_out_w = float(s.get("monitor_rms_out_width", 1.0))
        if rms_out_w > 0:
            # Draw the outline stroke before the coloured inner stroke.
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
