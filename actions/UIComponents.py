"""Reusable configuration rows (GTK4/Adwaita) for plugin actions.

All rows follow the same pattern: they read `self.settings`, write to it
from `on_change()` and notify the parent action via `notify_parent()`.

Layout conventions (enforced through the UIComponentsBase helpers):
    - Outer container: MARGIN px on all sides, SPACING px between rows.
    - Row/section titles: bold label + section-reset button.
    - Switch rows: label (hexpand) + Gtk.Switch (valign CENTER).
    - Combo rows: label + Gtk.DropDown (hexpand).
    - Every setting can be reset: spins/colors/fonts individually,
      everything at once with the section-reset button.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Adw, GLib, Gdk, Pango
import cairo
import globals as gl

from .rendering.colors import rgba_to_hex, DEFAULT_GRADIENT_COLORS
from .rendering.text import FontDefaults

PCT_FORMAT_LABELS = [
    "Percentage (0%, 50%, 100%)",
    "Panoramic Percentage (-100%, 0%, +100%)",
    "Panoramic (-100, 0, +100)",
    "Crossfade (A 0, A=B, B 0)",
    "Crossfade B (L0, 100, R0)",
    "Disabled",
]


# ----------------------------------------------------------------------
# Module-level widget helpers (modern, non-deprecated GTK4 widgets)
# ----------------------------------------------------------------------

def save_or_del_in(settings, key, val, default):
    """Store `val` under `key` only when it differs from `default`.

    Keeps the settings dict clean: values equal to the default are removed.
    String comparison is case-insensitive (hex colours may differ in case).
    """
    if isinstance(val, str) and isinstance(default, str):
        same = val.lower() == default.lower()
    else:
        same = val == default
    if same:
        settings.pop(key, None)
    else:
        settings[key] = val


def make_dropdown(options, active_id=None, callback=None):
    """Create a Gtk.DropDown that emulates the id/label API of ComboBoxText.

    `options` is a list of (id, label) pairs.  The ids (stringified) are
    stored on the widget so dropdown_get_id()/dropdown_set_id() can map
    between the selected index and the logical id.
    """
    dd = Gtk.DropDown.new_from_strings([lbl for _, lbl in options])
    dd._option_ids = [str(oid) for oid, _ in options]
    if active_id is not None and str(active_id) in dd._option_ids:
        dd.set_selected(dd._option_ids.index(str(active_id)))
    dd._handler_id = dd.connect("notify::selected", callback) if callback else None
    return dd


def dropdown_get_id(dd):
    """Return the logical id of the selected dropdown item (or None)."""
    idx = dd.get_selected()
    if 0 <= idx < len(dd._option_ids):
        return dd._option_ids[idx]
    return None


def dropdown_set_id(dd, oid):
    """Select the dropdown item with the given logical id (handler blocked)."""
    oid = str(oid)
    if oid not in dd._option_ids:
        return
    if dd._handler_id:
        dd.handler_block(dd._handler_id)
    dd.set_selected(dd._option_ids.index(oid))
    if dd._handler_id:
        dd.handler_unblock(dd._handler_id)


def dropdown_set_options(dd, options, active_id=None):
    """Replace all dropdown options (handler blocked while rebuilding)."""
    if dd._handler_id:
        dd.handler_block(dd._handler_id)
    dd._option_ids = [str(oid) for oid, _ in options]
    dd.set_model(Gtk.StringList.new([lbl for _, lbl in options]))
    if active_id is not None and str(active_id) in dd._option_ids:
        dd.set_selected(dd._option_ids.index(str(active_id)))
    if dd._handler_id:
        dd.handler_unblock(dd._handler_id)


def make_color_button(hex_color, callback):
    """Create a Gtk.ColorDialogButton initialised to `hex_color`.

    The callback receives (button, pspec) via notify::rgba.
    """
    btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    btn.set_rgba(rgba)
    btn.connect("notify::rgba", callback)
    return btn


def set_color_button(btn, hex_color):
    """Set a colour button's value from a hex string."""
    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    btn.set_rgba(rgba)


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------

class UIComponentsBase(Adw.PreferencesRow):
    """Base configuration rows: shared settings, defaults, resets and helpers."""

    MARGIN = 15
    SPACING = 6

    def __init__(self, settings_dict, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        self.defaults_calc = {}
        if hasattr(self.parent, "get_calculated_defaults"):
            calc = self.parent.get_calculated_defaults()
            self.defaults_calc = calc[0] if isinstance(calc, tuple) else calc
        self._updating = False
        # Reset registry: (restore_callable, keys_to_pop) per setting widget.
        self._resettables = []
        # Per-key static defaults (for keys not present in defaults_calc).
        self._defaults = {}
        # Size groups so spin labels align in columns across rows.
        self._sg_col = (Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL),
                        Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL))

    @property
    def lm(self):
        return self.parent.plugin_base.lm

    def _get_current_default(self, key, fallback=0):
        if hasattr(self.parent, "get_calculated_defaults"):
            calc = self.parent.get_calculated_defaults()
            dc = calc[0] if isinstance(calc, tuple) else calc
            return dc.get(key, fallback)
        return fallback

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------

    def build_main_box(self, title_text=None, margin=None, section_reset=True):
        """Create and attach the standard vertical container for the row.

        `margin=0` is used for rows embedded inside another row (the parent
        already provides the outer padding).  When a title is given, a
        section-reset button is placed at its right.
        """
        if margin is None:
            margin = self.MARGIN
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                      spacing=self.SPACING,
                      margin_start=margin, margin_end=margin,
                      margin_top=margin, margin_bottom=margin)
        self.set_child(box)
        if title_text:
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
            header.append(Gtk.Label(label=title_text, xalign=0, hexpand=True,
                                    css_classes=["bold"]))
            if section_reset:
                header.append(self._make_section_reset_button())
            box.append(header)
        return box

    def _make_section_reset_button(self):
        btn = Gtk.Button(icon_name="edit-undo-symbolic",
                         tooltip_text=self.lm.get("config.reset_all", "Reset all"),
                         css_classes=["circular", "flat"],
                         valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *a: self.reset_section())
        return btn

    @staticmethod
    def add_section_separator(box):
        """Append a separator with the standard section spacing."""
        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))

    @staticmethod
    def add_section_title(box, text):
        """Append a bold section title label."""
        box.append(Gtk.Label(label=text, xalign=0, margin_top=4, css_classes=["bold"]))

    # ------------------------------------------------------------------
    # Reset machinery
    # ------------------------------------------------------------------

    def _register(self, restore, keys=()):
        """Register a widget restore function and the setting keys it owns."""
        self._resettables.append((restore, tuple(keys)))

    def _reset_one(self, restore, keys=()):
        """Reset a single widget to its default and re-collect settings."""
        for k in keys:
            self.settings.pop(k, None)
        self._updating = True
        try:
            restore()
        finally:
            self._updating = False
        self.on_change()

    def reset_section(self):
        """Reset every registered widget in this row to its default value."""
        self._updating = True
        try:
            for restore, keys in self._resettables:
                for k in keys:
                    self.settings.pop(k, None)
                restore()
        finally:
            self._updating = False
        self.on_change()

    def update_dynamic_defaults(self):
        """Update widgets to new calculated defaults IF they are not explicitly set in settings."""
        self._updating = True
        try:
            for restore, keys in self._resettables:
                if not any(k in self.settings for k in keys):
                    restore()
        finally:
            self._updating = False

    def add_reset_button(self, box, restore, keys=(), label_text=""):
        """Append a small individual reset button bound to `restore`."""
        reset_word = self.lm.get("config.reset", "Reset")
        btn = Gtk.Button(icon_name="edit-undo-symbolic",
                         tooltip_text=f"{reset_word} {label_text}".strip(),
                         css_classes=["circular", "flat"],
                         valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *a: self._reset_one(restore, keys))
        box.append(btn)
        return btn

    # ------------------------------------------------------------------
    # Widget factory helpers (all register themselves for reset)
    # ------------------------------------------------------------------

    def create_switch_row(self, box, label_text, key, default, callback=None,
                          bold=False, section_reset=False):
        """Append a 'label + switch' row to `box` and return the Gtk.Switch."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        row.append(Gtk.Label(label=label_text, xalign=0, hexpand=True,
                             css_classes=["bold"] if bold else []))
        if section_reset:
            row.append(self._make_section_reset_button())
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        sw.set_active(self.settings.get(key, default))
        sw.connect("notify::active", callback or self.on_change)
        row.append(sw)
        box.append(row)
        self._defaults[key] = default
        self._register(lambda: sw.set_active(default), (key,))
        return sw

    def create_combo_row(self, box, label_text, options, active_id,
                         callback=None, key=None, default_id=None):
        """Append a 'label + dropdown' row to `box` and return the Gtk.DropDown."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        row.append(Gtk.Label(label=label_text, xalign=0))
        dd = make_dropdown(options, active_id, callback or self.on_change)
        dd.set_hexpand(True)
        row.append(dd)
        box.append(row)
        if default_id is None:
            default_id = options[0][0]
        keys = (key,) if key else ()
        self._register(lambda: dropdown_set_id(dd, default_id), keys)
        return dd

    def create_color_button(self, settings_key, default_hex, on_change_cb):
        """Create a colour button initialised from settings (registered for reset)."""
        btn = make_color_button(self.settings.get(settings_key, default_hex), on_change_cb)
        self._defaults[settings_key] = default_hex
        self._register(lambda: set_color_button(btn, default_hex), (settings_key,))
        return btn

    def create_spin_row(self, box, label_text, key, lo=-2000, hi=2000, step=1,
                        margin_start=0, with_reset=True, default=None,
                        label_hexpand=False):
        """Adds 'label + spin (+ reset)' to `box` and returns the SpinButton.

        The initial value comes from settings with fallback to `default` or
        the calculated defaults.  The label joins a per-column SizeGroup so
        spin columns align across rows.
        """
        default_val = default if default is not None else self._get_current_default(key, 0)
        lbl = Gtk.Label(label=label_text, xalign=0, margin_start=margin_start, margin_end=5,
                        hexpand=label_hexpand)
        if not label_hexpand:
            self._sg_col[0 if margin_start == 0 else 1].add_widget(lbl)
        box.append(lbl)
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_value(self.settings.get(key, default_val))
        spin.connect("value-changed", self.on_change)
        spin._lbl = lbl  # Attach label for dynamic text updates
        box.append(spin)
        if default is not None:
            self._defaults[key] = default
        restore = lambda: spin.set_value(default if default is not None else self._get_current_default(key, 0))
        self._register(restore, (key,))
        if with_reset:
            self.add_reset_button(box, restore, (key,), label_text)
        return spin

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def notify_parent(self):
        """Persist settings and force a complete redraw of the action."""
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "invalidate_render"):
            self.parent.invalidate_render()
        else:
            self.parent.draw_image()

    def save_or_del(self, key, val, default=None):
        """Saves the value only if it differs from the default (keeps settings clean)."""
        if default is None:
            if key in self._defaults:
                default = self._defaults[key]
            else:
                default = self._get_current_default(key, 0)
        save_or_del_in(self.settings, key, val, default)

    def on_change(self, *args):
        raise NotImplementedError


# ----------------------------------------------------------------------
# Composite colour widgets
# ----------------------------------------------------------------------

class GradientConfigBox(Gtk.Box):
    """Gradient selector: number of stops, colors and preview."""

    def __init__(self, prefix, settings, def_colors, on_change_cb, lm):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, margin_top=5)
        self.prefix = prefix
        self.settings = settings
        self.def_colors = def_colors
        self.on_change_cb = on_change_cb

        bgs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=5)
        bgs.append(Gtk.Label(label=lm.get("config.monitor.gradient_colors", "Colors"),
                             xalign=0, margin_end=10))
        self.cb_stops = make_dropdown([(i, str(i)) for i in range(2, 7)],
                                      self.settings.get(f"{prefix}_gradient_stops", 3),
                                      self._on_stops_changed)
        self.cb_stops.set_hexpand(True)
        bgs.append(self.cb_stops)
        self.append(bgs)

        bgc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.append(bgc)
        self.grad_btns = []
        for i in range(6):
            initial = self.settings.get(f"{prefix}_gradient_{i + 1}", self._def_color(i))
            btn = make_color_button(initial, self._on_color_set)
            self.grad_btns.append(btn)
            bgc.append(btn)

        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(-1, 15)
        self.preview.set_margin_top(5)
        self.preview.set_draw_func(self._on_draw_preview)
        self.append(self.preview)

        self.update_visibility()

    def _def_color(self, i):
        return self.def_colors[i] if i < len(self.def_colors) else "#ffffff"

    def _on_stops_changed(self, dd, pspec):
        self.update_visibility()
        self.on_change_cb()

    def _on_color_set(self, btn, pspec):
        self.preview.queue_draw()
        self.on_change_cb()

    def _stops(self):
        return int(dropdown_get_id(self.cb_stops) or 3)

    def update_visibility(self):
        stops = self._stops()
        for i in range(6):
            self.grad_btns[i].set_visible(i < stops)
        self.preview.queue_draw()

    def save_settings(self):
        save_or_del_in(self.settings, f"{self.prefix}_gradient_stops", self._stops(), 3)
        for i in range(6):
            save_or_del_in(self.settings, f"{self.prefix}_gradient_{i + 1}",
                           rgba_to_hex(self.grad_btns[i].get_rgba()), self._def_color(i))

    def reset_to_defaults(self):
        """Restore stops and colours to their defaults (no signals expected)."""
        dropdown_set_id(self.cb_stops, 3)
        for i in range(6):
            set_color_button(self.grad_btns[i], self._def_color(i))
        self.update_visibility()

    def _on_draw_preview(self, area, cr, width, height):
        stops = self._stops()
        lg = cairo.LinearGradient(0, 0, width, 0)
        for i in range(stops):
            rgba = self.grad_btns[i].get_rgba()
            lg.add_color_stop_rgba(i / (stops - 1), rgba.red, rgba.green, rgba.blue, rgba.alpha)
        cr.set_source(lg)
        cr.rectangle(0, 0, width, height)
        cr.fill()


class ColorModeSelector(Gtk.Box):
    """Color mode selector (solid / gradient / automatic by icon)."""

    def __init__(self, prefix, title, settings, lm, def_color, def_colors, on_change_cb, def_darken=50):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.prefix = prefix
        self.settings = settings
        self.def_color = def_color
        self.def_darken = def_darken
        self.on_change_cb = on_change_cb

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=5)
        self.append(row)
        row.append(Gtk.Label(label=title, xalign=0, margin_end=10, hexpand=True))

        self.cb_mode = make_dropdown(
            [(0, lm.get("config.monitor.color_solid", "Solid")),
             (1, lm.get("config.monitor.color_gradient", "Gradient")),
             (2, lm.get("config.monitor.solid_auto", "Automatic Solid (Icon)")),
             (3, lm.get("config.monitor.gradient_auto", "Automatic Gradient (Icon)"))],
            self.settings.get(f"{prefix}_color_mode", 0), self._on_mode_changed)
        row.append(self.cb_mode)

        self.box_solid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_start=10)
        row.append(self.box_solid)
        self.btn_solid = make_color_button(self.settings.get(f"{prefix}_color", def_color),
                                           self.on_change_cb)
        self.box_solid.append(self.btn_solid)

        self.box_auto = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_top=5)
        self.append(self.box_auto)
        self.box_auto.append(Gtk.Label(label=lm.get("config.monitor.darken_auto", "Auto Darken %:"),
                                       margin_end=5))
        self.spin_darken = Gtk.SpinButton.new_with_range(0, 100, 5)
        self.spin_darken.set_value(self.settings.get(f"{prefix}_auto_darken", def_darken))
        self.spin_darken.connect("value-changed", self.on_change_cb)
        self.box_auto.append(self.spin_darken)

        self.grad_box = GradientConfigBox(prefix, settings, def_colors, on_change_cb, lm)
        self.append(self.grad_box)

        self.update_visibility()

    def _on_mode_changed(self, dd, pspec):
        self.update_visibility()
        self.on_change_cb()

    def update_visibility(self):
        m = int(dropdown_get_id(self.cb_mode) or 0)
        self.box_solid.set_visible(m == 0)
        self.grad_box.set_visible(m == 1)
        self.box_auto.set_visible(m in (2, 3))

    def save_settings(self):
        save_or_del_in(self.settings, f"{self.prefix}_color_mode",
                       int(dropdown_get_id(self.cb_mode) or 0), 0)
        save_or_del_in(self.settings, f"{self.prefix}_auto_darken",
                       int(self.spin_darken.get_value()), self.def_darken)
        save_or_del_in(self.settings, f"{self.prefix}_color",
                       rgba_to_hex(self.btn_solid.get_rgba()), self.def_color)
        self.grad_box.save_settings()

    def reset_to_defaults(self):
        """Restore mode, darken, solid colour and gradient to their defaults."""
        dropdown_set_id(self.cb_mode, 0)
        self.spin_darken.set_value(self.def_darken)
        set_color_button(self.btn_solid, self.def_color)
        self.grad_box.reset_to_defaults()
        self.update_visibility()


# ----------------------------------------------------------------------
# Row classes
# ----------------------------------------------------------------------

class CustomLabelRow(UIComponentsBase):
    """Configuration of a text block: content, font, color, alignment,
    outline, position and width. Reused for name, percentage and carousel."""

    def __init__(self, title_text, settings_dict, key_prefix, parent_action,
                 show_text_input=True, embedded=False):
        super().__init__(settings_dict, parent_action)
        self.key_prefix = key_prefix
        lm = self.lm
        fd = FontDefaults.from_global()

        is_pct = key_prefix == "pct"
        self._def_align = "right" if is_pct else fd.align
        self._def_font_desc = f"{fd.family} {22 if is_pct else fd.size}"
        self._def_color = fd.color
        self._def_out_width = fd.outline_width
        self._def_out_color = fd.outline_color

        # Embedded rows live inside another row that already provides padding.
        self.main_box = self.build_main_box(title_text, margin=0 if embedded else None)

        self.entry = None
        self.pct_format_combo = None
        if show_text_input:
            text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            self.main_box.append(text_box)
            if is_pct:
                self.pct_format_combo = make_dropdown(
                    [(fmt_id, lm.get(f"config.pct_format.{fmt_id}", label))
                     for fmt_id, label in enumerate(PCT_FORMAT_LABELS)],
                    self.settings.get("pct_format", 0), self.on_change)
                self.pct_format_combo.set_hexpand(True)
                text_box.append(self.pct_format_combo)
                self._register(lambda: dropdown_set_id(self.pct_format_combo, 0),
                               ("pct_format",))
            else:
                self.entry = Gtk.Entry(hexpand=True,
                                       placeholder_text=lm.get("config.label.placeholder"))
                if f"text_{key_prefix}" in self.settings:
                    self.entry.set_text(self.settings[f"text_{key_prefix}"])
                self.entry.connect("changed", self.on_change)
                text_box.append(self.entry)
                self._register(lambda: self.entry.set_text(""), (f"text_{key_prefix}",))

        # Font and color
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.main_box.append(font_box)
        font_box.append(Gtk.Label(label=lm.get("config.font.title"), xalign=0,
                                  hexpand=True, margin_start=2))
        self.font_btn = Gtk.FontDialogButton(dialog=Gtk.FontDialog())
        self.font_btn.set_font_desc(Pango.FontDescription.from_string(
            self.settings.get(f"font_desc_{key_prefix}", self._def_font_desc)))
        self.font_btn.connect("notify::font-desc", self.on_change)
        font_box.append(self.font_btn)
        restore_font = lambda: self.font_btn.set_font_desc(
            Pango.FontDescription.from_string(self._def_font_desc))
        self._register(restore_font, (f"font_desc_{key_prefix}",))
        self.add_reset_button(font_box, restore_font, (f"font_desc_{key_prefix}",),
                              lm.get("config.font.title"))

        self.color_btn = self.create_color_button(f"color_{key_prefix}", fd.color, self.on_change)
        self.color_btn.set_margin_start(5)
        font_box.append(self.color_btn)
        self.add_reset_button(font_box,
                              lambda: set_color_button(self.color_btn, self._def_color),
                              (f"color_{key_prefix}",))

        # Alignment
        align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(align_box)
        align_box.append(Gtk.Label(label=lm.get("config.align.title"), xalign=0,
                                   hexpand=True, margin_start=2))
        self.btn_left = Gtk.ToggleButton(icon_name="format-justify-left-symbolic",
                                         tooltip_text=lm.get("config.align.left"))
        self.btn_center = Gtk.ToggleButton(icon_name="format-justify-center-symbolic",
                                           tooltip_text=lm.get("config.align.center"))
        self.btn_right = Gtk.ToggleButton(icon_name="format-justify-right-symbolic",
                                          tooltip_text=lm.get("config.align.right"))
        self.btn_center.set_group(self.btn_left)
        self.btn_right.set_group(self.btn_left)

        self._set_align_buttons(self.settings.get(f"align_{key_prefix}", self._def_align))
        for btn in (self.btn_left, self.btn_center, self.btn_right):
            btn.connect("toggled", self.on_change)
            align_box.append(btn)
        self._register(lambda: self._set_align_buttons(self._def_align),
                       (f"align_{key_prefix}",))

        # Outline
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.main_box.append(out_box)
        out_box.append(Gtk.Label(label=lm.get("config.outline.title"), xalign=0,
                                 margin_start=2))
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get(f"outline_width_{key_prefix}",
                                                  self._def_out_width))
        self.out_spin.connect("value-changed", self.on_change)
        out_box.append(self.out_spin)
        restore_out_w = lambda: self.out_spin.set_value(self._def_out_width)
        self._register(restore_out_w, (f"outline_width_{key_prefix}",))
        self.add_reset_button(out_box, restore_out_w, (f"outline_width_{key_prefix}",))

        out_box.append(Gtk.Label(label=lm.get("config.outline.color"), xalign=1,
                                 hexpand=True, margin_end=5))
        self.out_color_btn = self.create_color_button(f"outline_color_{key_prefix}",
                                                      fd.outline_color, self.on_change)
        out_box.append(self.out_color_btn)
        self.add_reset_button(out_box,
                              lambda: set_color_button(self.out_color_btn, self._def_out_color),
                              (f"outline_color_{key_prefix}",))

        # Position and width
        xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(xy_box)
        self.x_spin = self.create_spin_row(xy_box, lm.get("config.pos.x"), f"pos_x_{key_prefix}")
        self.y_spin = self.create_spin_row(xy_box, lm.get("config.pos.y"), f"pos_y_{key_prefix}",
                                           margin_start=10)

        w_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(w_box)
        self.w_spin = self.create_spin_row(w_box, lm.get("config.size.width"), f"width_{key_prefix}")

    def _set_align_buttons(self, align):
        if align == "left":
            self.btn_left.set_active(True)
        elif align == "right":
            self.btn_right.set_active(True)
        else:
            self.btn_center.set_active(True)

    def on_change(self, *args):
        if self._updating:
            return
        p = self.key_prefix
        if self.entry is not None:
            self.save_or_del(f"text_{p}", self.entry.get_text(), "")
        elif self.pct_format_combo is not None:
            self.save_or_del("pct_format", int(dropdown_get_id(self.pct_format_combo) or 0), 0)

        self.save_or_del(f"color_{p}", rgba_to_hex(self.color_btn.get_rgba()), self._def_color)
        font_desc = self.font_btn.get_font_desc()
        if font_desc is not None:
            self.save_or_del(f"font_desc_{p}", font_desc.to_string(), self._def_font_desc)

        align = "center"
        if self.btn_left.get_active():
            align = "left"
        elif self.btn_right.get_active():
            align = "right"
        self.save_or_del(f"align_{p}", align, self._def_align)

        self.save_or_del(f"outline_width_{p}", int(self.out_spin.get_value()),
                         self._def_out_width)
        self.save_or_del(f"outline_color_{p}", rgba_to_hex(self.out_color_btn.get_rgba()),
                         self._def_out_color)

        self.save_or_del(f"pos_x_{p}", int(self.x_spin.get_value()))
        self.save_or_del(f"pos_y_{p}", int(self.y_spin.get_value()))
        self.save_or_del(f"width_{p}", int(self.w_spin.get_value()))
        self.notify_parent()


class CustomIconRow(UIComponentsBase):
    """Icon configuration: visibility, file, size, outline and position."""

    def __init__(self, settings_dict, parent_action, suffix=""):
        super().__init__(settings_dict, parent_action)
        self.suffix = f"_{suffix}" if suffix else ""
        lm = self.lm

        self.main_box = self.build_main_box(lm.get("config.icon.format"))

        # Show icon: everything below is greyed out when disabled.
        self.switch_show = self.create_switch_row(
            self.main_box, lm.get("config.show_icon.title", "Show Icon"),
            f"show_icon{self.suffix}", True, callback=self.on_show_changed)
        self._register(lambda: self.settings_container.set_sensitive(True))

        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                          spacing=self.SPACING)
        self.main_box.append(self.settings_container)

        # File
        file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.settings_container.append(file_box)
        self.btn_file = Gtk.Button(label=lm.get("config.icon.select"))
        self.btn_file.connect("clicked", self.on_btn_file_clicked)
        file_box.append(self.btn_file)
        self.btn_clear = Gtk.Button(icon_name="user-trash-symbolic", margin_start=5)
        self.btn_clear.connect("clicked", self.on_btn_clear_clicked)
        file_box.append(self.btn_clear)
        self.lbl_file = Gtk.Label(label=self.settings.get(f"icon_path{self.suffix}",
                                                          lm.get("config.icon.default")),
                                  margin_start=10)
        self.lbl_file.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_file.set_max_width_chars(20)
        file_box.append(self.lbl_file)
        self._register(lambda: self.lbl_file.set_label(lm.get("config.icon.default")),
                       (f"icon_path{self.suffix}",))

        # Height
        wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.settings_container.append(wh_box)
        self.h_spin = self.create_spin_row(wh_box, lm.get("config.size.height"),
                                           f"icon_height{self.suffix}")

        # Outline
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.settings_container.append(out_box)
        out_box.append(Gtk.Label(label=lm.get("config.outline.width", "Outline Width"),
                                 margin_end=5))
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get(f"icon_out_width{self.suffix}", 1))
        self.out_spin.connect("value-changed", self.on_change)
        out_box.append(self.out_spin)
        restore_out_w = lambda: self.out_spin.set_value(1)
        self._register(restore_out_w, (f"icon_out_width{self.suffix}",))
        self.add_reset_button(out_box, restore_out_w, (f"icon_out_width{self.suffix}",))

        out_box.append(Gtk.Label(label=lm.get("config.outline.color", "Outline Color"),
                                 margin_start=10, margin_end=5))
        self.out_color_btn = self.create_color_button(f"icon_out_color{self.suffix}",
                                                      "#000000", self.on_change)
        out_box.append(self.out_color_btn)
        self.add_reset_button(out_box,
                              lambda: set_color_button(self.out_color_btn, "#000000"),
                              (f"icon_out_color{self.suffix}",))

        # Position
        xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.settings_container.append(xy_box)
        self.x_spin = self.create_spin_row(xy_box, lm.get("config.pos.x"), f"icon_x{self.suffix}")
        self.y_spin = self.create_spin_row(xy_box, lm.get("config.pos.y"), f"icon_y{self.suffix}",
                                           margin_start=10)

        self.settings_container.set_sensitive(self.switch_show.get_active())

    def on_btn_file_clicked(self, btn):
        media_path = self.settings.get(f"icon_path{self.suffix}", "")
        GLib.idle_add(gl.app.let_user_select_asset, media_path, self.on_media_selected)

    def on_btn_clear_clicked(self, btn):
        self.settings[f"icon_path{self.suffix}"] = ""
        self.lbl_file.set_label(self.lm.get("config.icon.default"))
        self.notify_parent()

    def on_media_selected(self, path):
        if path is not None:
            self.settings[f"icon_path{self.suffix}"] = path
            self.lbl_file.set_label(path)
            self.notify_parent()

    def on_show_changed(self, switch, pspec):
        if self._updating:
            return
        self.save_or_del(f"show_icon{self.suffix}", switch.get_active(), True)
        self.settings_container.set_sensitive(switch.get_active())
        self.notify_parent()

    def on_change(self, *args):
        if self._updating:
            return
        self.save_or_del(f"show_icon{self.suffix}", self.switch_show.get_active(), True)
        self.settings_container.set_sensitive(self.switch_show.get_active())
        self.save_or_del(f"icon_height{self.suffix}", int(self.h_spin.get_value()))
        self.save_or_del(f"icon_x{self.suffix}", int(self.x_spin.get_value()))
        self.save_or_del(f"icon_y{self.suffix}", int(self.y_spin.get_value()))
        self.save_or_del(f"icon_out_width{self.suffix}", int(self.out_spin.get_value()), 1)
        self.save_or_del(f"icon_out_color{self.suffix}",
                         rgba_to_hex(self.out_color_btn.get_rgba()), "#000000")
        self.notify_parent()


class CustomBarRow(UIComponentsBase):
    """Bar configuration: style, colors, inversion, outline and geometry."""

    def __init__(self, settings_dict, parent_action, hide_dual_style=False,
                 absolute_geometry=False):
        super().__init__(settings_dict, parent_action)
        lm = self.lm
        # `hide_dual_style` is used by actions that can never run a mixer
        # (e.g. Device Control): the '2 Bars' option and its warning are dropped.
        self.hide_dual_style = hide_dual_style
        # `absolute_geometry` stores the geometry spins as literal pixel values
        # (Device Control) instead of deleting them when they equal the
        # calculated default.  Without it a value equal to the default would be
        # an "alias" for auto-calculation and could shift when the default does.
        self.absolute_geometry = absolute_geometry

        self.main_box = self.build_main_box(lm.get("config.bar.format"))

        if not hide_dual_style:
            lbl_warn = Gtk.Label(label=lm.get("config.bar.style.warning",
                                              "The '2 Bars' mode will only appear when the mixer mode is enabled."),
                                 xalign=0)
            lbl_warn.add_css_class("dim-label")
            lbl_warn.set_wrap(True)
            self.main_box.append(lbl_warn)

        # Style ('2 Bars' is only offered when a mixer is possible).
        style_options = [(1, lm.get("config.bar.style.1bar", "1 Bar")),
                         (2, lm.get("config.bar.style.1bar_tri", "1 Bar with Triangle")),
                         (3, lm.get("config.bar.style.1bar_line", "1 Bar with Line")),
                         (4, lm.get("config.bar.style.1bar_dot", "1 Bar with Dot"))]
        if not hide_dual_style:
            style_options.insert(0, (0, lm.get("config.bar.style.2bars", "2 Bars")))
        default_style = 1 if hide_dual_style else 0
        self.style_combo = self.create_combo_row(
            self.main_box, lm.get("config.bar.style", "Style"),
            style_options,
            self.settings.get("bar_style", default_style), key="bar_style")

        # Main colors
        self.bar_color_sel = ColorModeSelector(
            "bar", lm.get("config.bar.color", "Bar Color"), self.settings, lm,
            "#ffffff", DEFAULT_GRADIENT_COLORS, self.on_change, def_darken=0)
        self.main_box.append(self.bar_color_sel)
        self._register(self.bar_color_sel.reset_to_defaults)

        self.bg_color_sel = ColorModeSelector(
            "bar_bg", lm.get("config.bar.background", "Background Color"), self.settings, lm,
            "#424242", DEFAULT_GRADIENT_COLORS, self.on_change, def_darken=50)
        self.main_box.append(self.bg_color_sel)
        self._register(self.bg_color_sel.reset_to_defaults)

        # Secondary colors
        color_box_2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.main_box.append(color_box_2)
        color_box_2.append(Gtk.Label(label=lm.get("config.bar.over100"), margin_end=5))
        self.over_color_btn = self.create_color_button("bar_over_color", "#ff4b4b", self.on_change)
        color_box_2.append(self.over_color_btn)
        self.add_reset_button(color_box_2,
                              lambda: set_color_button(self.over_color_btn, "#ff4b4b"),
                              ("bar_over_color",))
        color_box_2.append(Gtk.Label(label=lm.get("config.bar.ind_color", "Indicator Color"),
                                     margin_start=10, margin_end=5))
        self.ind_color_btn = self.create_color_button("bar_ind_color", "#FFFFFF", self.on_change)
        color_box_2.append(self.ind_color_btn)
        self.add_reset_button(color_box_2,
                              lambda: set_color_button(self.ind_color_btn, "#FFFFFF"),
                              ("bar_ind_color",))
        self.neu_lbl = Gtk.Label(label=lm.get("config.bar.neu_color", "Neutral Color"),
                                 margin_start=10, margin_end=5)
        color_box_2.append(self.neu_lbl)
        self.neu_color_btn = self.create_color_button("bar_neu_color", "#808080", self.on_change)
        color_box_2.append(self.neu_color_btn)

        # Invert
        self.sw_invert = self.create_switch_row(
            self.main_box, lm.get("config.bar.invert", "Invert Bar"), "bar_invert", False)

        # Outline
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.main_box.append(out_box)
        out_box.append(Gtk.Label(label=lm.get("config.outline.width", "Outline Width"),
                                 margin_end=5))
        def_out_w = self.defaults_calc.get("bar_out_width", 1)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get("bar_out_width", def_out_w))
        self.out_spin.connect("value-changed", self.on_change)
        out_box.append(self.out_spin)
        restore_out_w = lambda: self.out_spin.set_value(def_out_w)
        self._register(restore_out_w, ("bar_out_width",))
        self.add_reset_button(out_box, restore_out_w, ("bar_out_width",))

        out_box.append(Gtk.Label(label=lm.get("config.outline.color", "Outline Color"),
                                 margin_start=10, margin_end=5))
        def_out_c = self.defaults_calc.get("bar_out_color", "#000000")
        self.out_color_btn = self.create_color_button("bar_out_color", def_out_c, self.on_change)
        out_box.append(self.out_color_btn)
        self.add_reset_button(out_box,
                              lambda: set_color_button(self.out_color_btn, def_out_c),
                              ("bar_out_color",))

        # Geometry
        wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(wh_box)
        self.w_spin = self.create_spin_row(wh_box, lm.get("config.size.width"), "bar_width")
        self.h_spin = self.create_spin_row(wh_box, lm.get("config.size.height"), "bar_height",
                                           margin_start=10)

        xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(xy_box)
        self.x_spin = self.create_spin_row(xy_box, lm.get("config.pos.x"), "bar_x")
        self.y_spin = self.create_spin_row(xy_box, lm.get("config.pos.y"), "bar_y", margin_start=10)

        rad_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(rad_box)
        self.rad_spin = self.create_spin_row(rad_box, lm.get("config.size.radius"), "bar_radius",
                                             hi=100)

        self.update_neu_visibility()
        self.update_geometry_labels()

    def update_geometry_labels(self):
        """Swap geometry labels for vertical faders to match user expectation."""
        is_vert = (self.settings.get("icon_style") == "fader_v" and 
                   self.settings.get("control_action", "set") == "adjust")
        
        lm = self.lm
        w_text = lm.get("config.size.height", "Height") if is_vert else lm.get("config.size.width", "Width")
        h_text = lm.get("config.size.width", "Width") if is_vert else lm.get("config.size.height", "Height")
        x_text = lm.get("config.pos.y", "Pos Y") if is_vert else lm.get("config.pos.x", "Pos X")
        y_text = lm.get("config.pos.x", "Pos X") if is_vert else lm.get("config.pos.y", "Pos Y")
        
        if hasattr(self.w_spin, "_lbl"):
            self.w_spin._lbl.set_label(w_text)
            self.h_spin._lbl.set_label(h_text)
            self.x_spin._lbl.set_label(x_text)
            self.y_spin._lbl.set_label(y_text)

    def update_neu_visibility(self):
        """The neutral color only applies to the balance bar (dual + '1 Bar' style)."""
        is_single_mode = not self.settings.get("dual_mode", False)
        style = int(dropdown_get_id(self.style_combo) or 0)
        visible = not is_single_mode and style == 1
        self.neu_lbl.set_visible(visible)
        self.neu_color_btn.set_visible(visible)

    def on_change(self, *args):
        if self._updating:
            return
        default_style = 1 if self.hide_dual_style else 0
        self.save_or_del("bar_style", int(dropdown_get_id(self.style_combo) or default_style),
                         default_style)
        self.update_neu_visibility()

        self.bar_color_sel.save_settings()
        self.bg_color_sel.save_settings()

        self.save_or_del("bar_over_color", rgba_to_hex(self.over_color_btn.get_rgba()))
        self.save_or_del("bar_ind_color", rgba_to_hex(self.ind_color_btn.get_rgba()))
        self.save_or_del("bar_neu_color", rgba_to_hex(self.neu_color_btn.get_rgba()), "#808080")
        self.save_or_del("bar_invert", self.sw_invert.get_active(), False)
        self.save_or_del("bar_out_color", rgba_to_hex(self.out_color_btn.get_rgba()))
        self.save_or_del("bar_out_width", int(self.out_spin.get_value()))

        for key, spin in (("bar_width", self.w_spin), ("bar_height", self.h_spin),
                          ("bar_x", self.x_spin), ("bar_y", self.y_spin),
                          ("bar_radius", self.rad_spin)):
            if self.absolute_geometry:
                # Store the literal pixel value; never collapse to "auto".
                self.settings[key] = int(spin.get_value())
            else:
                self.save_or_del(key, int(spin.get_value()))
        self.notify_parent()


class DeviceConfigGroup(UIComponentsBase):
    """Device selector: type, specific device, auto-index and limit."""

    def __init__(self, settings_dict, parent_action, suffix=""):
        super().__init__(settings_dict, parent_action)
        self.suffix_str = f"_{suffix}" if suffix else ""
        lm = self.lm

        self.main_box = self.build_main_box()

        # Type
        type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        type_box.append(Gtk.Label(label=lm.get("config.type.title", "Device Type"),
                                  xalign=0, hexpand=True))
        self.type_dd = make_dropdown(
            [("sink", lm.get("config.type.sink", "Sink (Output)")),
             ("source", lm.get("config.type.source", "Source (Input)")),
             ("application", lm.get("config.type.application", "Application"))],
            self.settings.get(f"device_type{self.suffix_str}", "sink"),
            self.on_type_changed)
        type_box.append(self.type_dd)
        self.main_box.append(type_box)

        # Device
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        device_box.append(Gtk.Label(label=lm.get("config.device.title", "Device"),
                                    xalign=0, hexpand=True))
        self.device_dd = make_dropdown([], None, self.on_device_changed)
        self.device_dd.set_size_request(200, -1)
        device_box.append(self.device_dd)
        self.main_box.append(device_box)

        # Auto-index
        self.auto_index_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                      spacing=10, hexpand=True)
        ai_lbl_text = lm.get("config.auto_index.title", "Auto-index #")
        self.auto_index_spin = self.create_spin_row(
            self.auto_index_box, ai_lbl_text, f"auto_index{self.suffix_str}",
            lo=0, hi=10, default=0, label_hexpand=True)
        self.auto_index_box.get_first_child().set_tooltip_text(
            lm.get("config.auto_index.subtitle", "0 = disabled. Used for apps"))
        self.main_box.append(self.auto_index_box)

        # Volume limit
        limit_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        self.limit_spin = self.create_spin_row(
            limit_box, lm.get("config.limit.title", "Volume Limit (%)"),
            f"volume_limit{self.suffix_str}", lo=0, hi=150, step=5, default=100,
            label_hexpand=True)
        self.main_box.append(limit_box)

        self.update_device_model()

    def update_device_model(self):
        """Rebuild the device dropdown for the currently selected type."""
        device_type = self.settings.get(f"device_type{self.suffix_str}", "sink")
        selected_target = self.settings.get(f"device_name{self.suffix_str}", "")

        svc = self.parent.pulse_service
        if svc is None:
            return
        lm = self.lm

        if device_type == "application":
            default_id = "Auto"
            options = [("Auto", lm.get("config.device.auto", "Auto"))]
            options += [(name, name) for name in svc.get_active_applications()]
        elif device_type == "sink":
            default_id = "default"
            options = [("default", lm.get("config.device.default", "Default"))]
            options += [(d.name, getattr(d, "description", d.name)) for d in svc.sink_list()]
        else:
            default_id = "default"
            options = [("default", lm.get("config.device.default", "Default"))]
            options += [(d.name, getattr(d, "description", d.name))
                        for d in svc.source_list()
                        if not getattr(d, "name", "").endswith(".monitor")
                        and not getattr(d, "description", "").startswith("Monitor of")]

        known_ids = {oid for oid, _ in options}
        if selected_target and selected_target not in known_ids and selected_target != default_id:
            not_available = lm.get("config.device.not_available", "Not Available")
            options.append((selected_target, f"{selected_target} ({not_available})"))

        selected_id = selected_target if selected_target else default_id
        dropdown_set_options(self.device_dd, options, selected_id)
        self.auto_index_box.set_visible(device_type == "application")

    def on_type_changed(self, dd, pspec):
        if self._updating:
            return
        self.save_or_del(f"device_type{self.suffix_str}",
                         dropdown_get_id(dd) or "sink", "sink")
        self.save_or_del(f"device_name{self.suffix_str}", "", "")
        self.update_device_model()
        self.notify_parent()

    def on_device_changed(self, dd, pspec):
        if self._updating:
            return
        active_id = dropdown_get_id(dd)
        value = "" if (active_id == "default" or not active_id) else active_id
        self.save_or_del(f"device_name{self.suffix_str}", value, "")
        self.notify_parent()

    def on_change(self, *args):
        if self._updating:
            return
        self.save_or_del(f"auto_index{self.suffix_str}", int(self.auto_index_spin.get_value()))
        self.save_or_del(f"volume_limit{self.suffix_str}", int(self.limit_spin.get_value()))
        self.notify_parent()


class VolumeMonitorBarRow(UIComponentsBase):
    """Peak monitor configuration: bar mode, colors, db."""
    def __init__(self, settings, parent):
        super().__init__(settings, parent)
        lm = self.lm
        self.main_box = self.build_main_box()
        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.SPACING)
        self.main_box.append(self.settings_container)

        # Bar mode
        self.cb_bar_mode = self.create_combo_row(
            self.settings_container, lm.get("config.monitor.bar_mode", "Bar Mode"),
            [(0, lm.get("config.monitor.bar_single", "Single Bar")),
             (1, lm.get("config.monitor.bar_dual", "Dual Bar (Stereo)"))],
            self.settings.get("monitor_bar_mode", 0), key="monitor_bar_mode")

        # Color mode
        self.cb_color_mode = self.create_combo_row(
            self.settings_container, lm.get("config.monitor.color_mode", "Color Mode"),
            [(0, lm.get("config.monitor.color_solid", "Solid")),
             (1, lm.get("config.monitor.color_tricolor", "Tricolor")),
             (2, lm.get("config.monitor.color_gradient", "Gradient")),
             (3, lm.get("config.monitor.solid_auto", "Automatic Solid (Icon)")),
             (4, lm.get("config.monitor.gradient_auto", "Automatic Gradient (Icon)"))],
            self.settings.get("monitor_color_mode", 0), callback=self.on_color_mode_change,
            key="monitor_color_mode")

        self.box_solid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.box_tricolor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        self.box_gradient = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        self.box_auto = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        for box in (self.box_solid, self.box_tricolor, self.box_gradient, self.box_auto):
            self.settings_container.append(box)

        # Auto (darkened)
        self.box_auto.append(Gtk.Label(label=lm.get("config.monitor.darken_auto", "Auto Darken %:"),
                                       margin_end=10, xalign=0, hexpand=True))
        self.spin_monitor_darken = Gtk.SpinButton.new_with_range(0, 100, 5)
        self.spin_monitor_darken.set_value(self.settings.get("monitor_auto_darken", 0))
        self.spin_monitor_darken.connect("value-changed", self.on_change)
        self.box_auto.append(self.spin_monitor_darken)
        self._register(lambda: self.spin_monitor_darken.set_value(0), ("monitor_auto_darken",))

        # Solid
        self.box_solid.append(Gtk.Label(label=lm.get("config.monitor.color", "Color"),
                                        xalign=0, hexpand=True))
        self.btn_solid = self.create_color_button("monitor_color_solid", "#ffffff", self.on_change)
        self.box_solid.append(self.btn_solid)
        self.add_reset_button(self.box_solid,
                              lambda: set_color_button(self.btn_solid, "#ffffff"),
                              ("monitor_color_solid",))

        # Tricolor
        self.btn_tri_low = self.create_color_button("monitor_color_low", "#00ff00", self.on_change)
        self.btn_tri_mid = self.create_color_button("monitor_color_mid", "#ffff00", self.on_change)
        self.btn_tri_high = self.create_color_button("monitor_color_high", "#ff0000", self.on_change)
        self.spin_threshold_mid = self._create_threshold_spin("monitor_threshold_mid", -20)
        self.spin_threshold_high = self._create_threshold_spin("monitor_threshold_high", -9)

        self._add_tri_row(lm.get("config.monitor.color_low", "Low"),
                          self.btn_tri_low, "monitor_color_low", "#00ff00")
        self._add_tri_row(lm.get("config.monitor.color_mid", "Mid"),
                          self.btn_tri_mid, "monitor_color_mid", "#ffff00",
                          self.spin_threshold_mid)
        self._add_tri_row(lm.get("config.monitor.color_high", "High"),
                          self.btn_tri_high, "monitor_color_high", "#ff0000",
                          self.spin_threshold_high)

        # Gradient
        self.grad_config = GradientConfigBox("monitor", self.settings, DEFAULT_GRADIENT_COLORS,
                                             self.on_change, lm)
        self.box_gradient.append(self.grad_config)
        self._register(self.grad_config.reset_to_defaults)

        # Invert bar
        self.sw_mon_inv = self.create_switch_row(
            self.settings_container, lm.get("config.monitor.invert", "Invert Bar"),
            "monitor_invert", False)

        self.add_section_separator(self.settings_container)

        # RMS Indicator
        self.sw_rms = self.create_switch_row(
            self.settings_container, lm.get("config.monitor.show_rms", "Show Average Indicator"),
            "monitor_show_rms", False, callback=self.on_rms_change)

        self.brms_options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.brms_options.append(Gtk.Label(label=lm.get("config.monitor.rms_color", "Indicator Color"),
                                           margin_end=5))
        self.btn_rms_color = self.create_color_button("monitor_rms_color", "#FFFFFF", self.on_change)
        self.brms_options.append(self.btn_rms_color)
        self.add_reset_button(self.brms_options,
                              lambda: set_color_button(self.btn_rms_color, "#FFFFFF"),
                              ("monitor_rms_color",))
        self.settings_container.append(self.brms_options)

        self.brms_options2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.brms_options2.append(Gtk.Label(label=lm.get("config.outline.width", "Outline Width"),
                                            margin_end=5))
        self.spin_rms_out = Gtk.SpinButton.new_with_range(0, 10, 1)
        self.spin_rms_out.set_value(self.settings.get("monitor_rms_out_width", 1.0))
        self.spin_rms_out.connect("value-changed", self.on_change)
        self.brms_options2.append(self.spin_rms_out)
        self._register(lambda: self.spin_rms_out.set_value(1.0), ("monitor_rms_out_width",))
        self.brms_options2.append(Gtk.Label(label=lm.get("config.outline.color", "Outline Color"),
                                            margin_start=10, margin_end=5))
        self.btn_rms_out_color = self.create_color_button("monitor_rms_out_color", "#000000",
                                                          self.on_change)
        self.brms_options2.append(self.btn_rms_out_color)
        self.add_reset_button(self.brms_options2,
                              lambda: set_color_button(self.btn_rms_out_color, "#000000"),
                              ("monitor_rms_out_color",))
        self.settings_container.append(self.brms_options2)

        self.update_visibility()

    def _create_threshold_spin(self, key, default):
        spin = Gtk.SpinButton.new_with_range(-100, 0, 1)
        spin.set_value(self.settings.get(key, default))
        spin.connect("value-changed", self.on_change)
        self._defaults[key] = default
        self._register(lambda: spin.set_value(default), (key,))
        return spin

    def _add_tri_row(self, label, btn, color_key, def_color, thresh_spin=None):
        r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        r.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        if thresh_spin is not None:
            r.append(Gtk.Label(label=self.lm.get("config.monitor.threshold", "Threshold (dB)"),
                               margin_start=10, margin_end=5))
            r.append(thresh_spin)
        r.append(btn)
        self.add_reset_button(r, lambda: set_color_button(btn, def_color), (color_key,))
        self.box_tricolor.append(r)

    def on_color_mode_change(self, *args):
        if self._updating:
            return
        self.on_change()
        self.update_visibility()

    def on_rms_change(self, *args):
        if self._updating:
            return
        self.on_change()
        self.update_visibility()

    def reset_section(self):
        super().reset_section()
        self.update_visibility()

    def update_visibility(self):
        cmode = int(dropdown_get_id(self.cb_color_mode) or 0)
        self.box_solid.set_visible(cmode == 0)
        self.box_tricolor.set_visible(cmode == 1)
        self.box_gradient.set_visible(cmode == 2)
        self.box_auto.set_visible(cmode in (3, 4))
        self.brms_options.set_sensitive(self.sw_rms.get_active())
        self.brms_options2.set_sensitive(self.sw_rms.get_active())

    def on_change(self, *args):
        if self._updating:
            return
        self.save_or_del("monitor_bar_mode", int(dropdown_get_id(self.cb_bar_mode) or 0), 0)
        self.save_or_del("monitor_color_mode", int(dropdown_get_id(self.cb_color_mode) or 0), 0)
        self.save_or_del("monitor_auto_darken", int(self.spin_monitor_darken.get_value()), 0)
        self.save_or_del("monitor_color_solid", rgba_to_hex(self.btn_solid.get_rgba()))
        self.save_or_del("monitor_color_low", rgba_to_hex(self.btn_tri_low.get_rgba()))
        self.save_or_del("monitor_color_mid", rgba_to_hex(self.btn_tri_mid.get_rgba()))
        self.save_or_del("monitor_color_high", rgba_to_hex(self.btn_tri_high.get_rgba()))
        self.save_or_del("monitor_threshold_mid", int(self.spin_threshold_mid.get_value()))
        self.save_or_del("monitor_threshold_high", int(self.spin_threshold_high.get_value()))
        self.grad_config.save_settings()
        self.save_or_del("monitor_show_rms", self.sw_rms.get_active(), False)
        self.save_or_del("monitor_rms_color", rgba_to_hex(self.btn_rms_color.get_rgba()))
        self.save_or_del("monitor_rms_out_color", rgba_to_hex(self.btn_rms_out_color.get_rgba()))
        self.save_or_del("monitor_rms_out_width", float(self.spin_rms_out.get_value()), 1.0)
        self.save_or_del("monitor_invert", self.sw_mon_inv.get_active(), False)
        self.notify_parent()


class VolumeMonitorSettingsRow(UIComponentsBase):
    """Peak monitor configuration: FPS and Switch Delay."""
    def __init__(self, settings, parent, show_db_switch=True):
        super().__init__(settings, parent)
        lm = self.lm
        # `show_db_switch` lets actions that provide their own dB toggle
        # (e.g. Device Control) drop the duplicate here.
        self.show_db_switch = show_db_switch
        self.main_box = self.build_main_box()
        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.SPACING)
        self.main_box.append(self.settings_container)

        self.cb_fps = self.create_combo_row(
            self.settings_container, lm.get("config.monitor.fps", "Update Rate (FPS)"),
            [(f, str(f)) for f in (1, 2, 5, 10, 15, 20)],
            self.settings.get("monitor_fps", 10), key="monitor_fps", default_id=10)

        lbl_fps_warn = Gtk.Label(label=lm.get("config.monitor.fps.warning",
                                              "A high FPS rate increases CPU usage"),
                                 xalign=0)
        lbl_fps_warn.add_css_class("dim-label")
        lbl_fps_warn.set_wrap(True)
        self.settings_container.append(lbl_fps_warn)

        self.sw_db = None
        if show_db_switch:
            self.add_section_separator(self.settings_container)
            # Show dB
            self.sw_db = self.create_switch_row(
                self.settings_container, lm.get("config.monitor.show_db", "Show Decibels"),
                "monitor_show_db", False)

        self.add_section_separator(self.settings_container)

        bdel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        self.spin_delay = self.create_spin_row(
            bdel, lm.get("config.monitor.delay", "Switch Delay (s)"), "monitor_delay",
            lo=1, hi=60, default=5, label_hexpand=True)
        self.settings_container.append(bdel)

    def on_change(self, *args):
        if self._updating:
            return
        self.save_or_del("monitor_fps", int(dropdown_get_id(self.cb_fps) or 10), 10)
        self.save_or_del("monitor_delay", int(self.spin_delay.get_value()))
        if self.sw_db is not None:
            self.save_or_del("monitor_show_db", self.sw_db.get_active(), False)
        self.notify_parent()


class CarouselSettingsRow(UIComponentsBase):
    """Carousel configuration: device filters and switch delay."""
    def __init__(self, settings_dict, parent_action):
        super().__init__(settings_dict, parent_action)
        lm = self.lm
        self.main_box = self.build_main_box()
        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.SPACING)
        self.main_box.append(self.settings_container)

        toggles = [
            ("carousel_show_sinks", "config.carousel.show_sinks", False),
            ("carousel_show_sources", "config.carousel.show_sources", False),
            ("carousel_show_apps", "config.carousel.show_apps", True),
            ("carousel_show_default_sink", "config.carousel.default_sink", True),
            ("carousel_show_default_source", "config.carousel.default_source", False),
        ]
        self.toggle_switches = {}
        for key, label_key, default in toggles:
            self.toggle_switches[key] = self.create_switch_row(
                self.settings_container, lm.get(label_key, label_key), key, default)

        self.add_section_separator(self.settings_container)

        bdel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        self.spin_delay = self.create_spin_row(
            bdel, lm.get("config.monitor.delay", "Switch Delay (s)"), "carousel_delay",
            lo=1, hi=60, default=10, label_hexpand=True)
        self.settings_container.append(bdel)

    def on_change(self, *args):
        if self._updating:
            return
        for key, sw in self.toggle_switches.items():
            self.save_or_del(key, sw.get_active())
        self.save_or_del("carousel_delay", int(self.spin_delay.get_value()))
        self.notify_parent()


class CarouselIconsRow(UIComponentsBase):
    """Carousel configuration: icon layout."""
    def __init__(self, settings_dict, parent_action):
        super().__init__(settings_dict, parent_action)
        lm = self.lm
        self.main_box = self.build_main_box()
        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.SPACING)
        self.main_box.append(self.settings_container)

        self.count_combo = self.create_combo_row(
            self.settings_container, lm.get("config.carousel.icon_count", "Visible Icons"),
            [(c, str(c)) for c in (3, 5, 7)],
            int(self.settings.get("carousel_count", self.defaults_calc.get("carousel_count", 5))),
            key="carousel_count", default_id=5)

        tier_titles = [
            (1, lm.get("config.carousel.tier.primary", "Primary Icon (centre)")),
            (2, lm.get("config.carousel.tier.secondary", "Secondary Icons")),
            (3, lm.get("config.carousel.tier.tertiary", "Tertiary Icons")),
        ]
        lbl_size = lm.get("config.carousel.icon_size", "Size")
        lbl_opacity = lm.get("config.carousel.icon_opacity", "Opacity %")
        self.tier_spins = {}
        for tier, title in tier_titles:
            self.add_section_separator(self.settings_container)
            self.settings_container.append(Gtk.Label(label=title, xalign=0,
                                                     css_classes=["dim-label"]))
            row_size = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            self.tier_spins[f"carousel_size_{tier}"] = self.create_spin_row(
                row_size, lbl_size, f"carousel_size_{tier}", 4, 512)
            self.tier_spins[f"carousel_opacity_{tier}"] = self.create_spin_row(
                row_size, lbl_opacity, f"carousel_opacity_{tier}", 0, 100, margin_start=10)
            self.settings_container.append(row_size)

            row_pos = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            self.tier_spins[f"carousel_x_{tier}"] = self.create_spin_row(
                row_pos, lm.get("config.pos.x", "X"), f"carousel_x_{tier}")
            self.tier_spins[f"carousel_y_{tier}"] = self.create_spin_row(
                row_pos, lm.get("config.pos.y", "Y"), f"carousel_y_{tier}", margin_start=10)
            self.settings_container.append(row_pos)

    def on_change(self, *args):
        if self._updating:
            return
        self.save_or_del("carousel_count", int(dropdown_get_id(self.count_combo) or 5))
        for key, spin in self.tier_spins.items():
            self.save_or_del(key, int(spin.get_value()))
        self.notify_parent()
