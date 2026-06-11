"""Reusable configuration rows (GTK4/Adwaita) for plugin actions.

All rows follow the same pattern: they read `self.settings`, write to it
from `on_change()` and notify the parent action via `notify_parent()`.
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


class UIComponentsBase(Adw.PreferencesRow):
    """Base configuration rows: shared settings, defaults and helpers."""

    def __init__(self, settings_dict, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        self.defaults_calc = {}
        if hasattr(self.parent, "get_calculated_defaults"):
            calc = self.parent.get_calculated_defaults()
            self.defaults_calc = calc[0] if isinstance(calc, tuple) else calc
        self._updating = False

    @property
    def lm(self):
        return self.parent.plugin_base.lm

    def notify_parent(self):
        """Persist settings and force a complete redraw of the action."""
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "invalidate_render"):
            self.parent.invalidate_render()
        else:
            self.parent.draw_image()

    def create_color_button(self, settings_key, default_hex, on_change_cb):
        btn = Gtk.ColorButton()
        c = Gdk.RGBA()
        c.parse(self.settings.get(settings_key, default_hex))
        btn.set_rgba(c)
        btn.connect("color-set", on_change_cb)
        return btn

    def create_spin_row(self, box, label_text, key, lo=-2000, hi=2000, step=1,
                        margin_start=0, with_reset=True):
        """Adds 'label + spin (+ reset)' to `box` and returns the SpinButton.

        The initial value comes from settings with fallback to calculated defaults.
        """
        box.append(Gtk.Label(label=label_text, margin_start=margin_start, margin_end=5))
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_value(self.settings.get(key, self.defaults_calc.get(key, 0)))
        spin.connect("value-changed", self.on_change)
        box.append(spin)
        if with_reset:
            reset_word = self.lm.get("config.reset", "Reset")
            btn = Gtk.Button(icon_name="edit-undo-symbolic",
                             tooltip_text=f"{reset_word} {label_text}",
                             css_classes=["circular", "flat"])
            btn.connect("clicked", lambda *a: self.reset_val(key, spin))
            box.append(btn)
        return spin

    def reset_val(self, key, spin):
        """Removes the override and restores the calculated default."""
        self.settings.pop(key, None)
        self._updating = True
        spin.set_value(self.defaults_calc.get(key, 0))
        self._updating = False
        self.notify_parent()

    def save_or_del(self, key, val):
        """Saves the value only if it differs from the default (keeps settings clean)."""
        if val == self.defaults_calc.get(key, 0):
            self.settings.pop(key, None)
        else:
            self.settings[key] = val

    def on_change(self, *args):
        raise NotImplementedError


class GradientConfigBox(Gtk.Box):
    """Gradient selector: number of stops, colors and preview."""

    def __init__(self, prefix, settings, def_colors, on_change_cb, lm):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, margin_top=5)
        self.prefix = prefix
        self.settings = settings
        self.on_change_cb = on_change_cb

        bgs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=5)
        bgs.append(Gtk.Label(label=lm.get("config.monitor.gradient_colors", "Colors"),
                             xalign=0, margin_end=10))
        self.cb_stops = Gtk.ComboBoxText(hexpand=True)
        for i in range(2, 7):
            self.cb_stops.append(str(i), str(i))
        self.cb_stops.set_active_id(str(self.settings.get(f"{prefix}_gradient_stops", 3)))
        self.cb_stops.connect("changed", self._on_stops_changed)
        bgs.append(self.cb_stops)
        self.append(bgs)

        bgc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.append(bgc)
        self.grad_btns = []
        for i in range(6):
            btn = Gtk.ColorButton()
            c = Gdk.RGBA()
            c.parse(self.settings.get(f"{prefix}_gradient_{i + 1}",
                                      def_colors[i] if i < len(def_colors) else "#ffffff"))
            btn.set_rgba(c)
            btn.connect("color-set", self._on_color_set)
            self.grad_btns.append(btn)
            bgc.append(btn)

        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(-1, 15)
        self.preview.set_margin_top(5)
        self.preview.set_draw_func(self._on_draw_preview)
        self.append(self.preview)

        self.update_visibility()

    def _on_stops_changed(self, combo):
        self.update_visibility()
        self.on_change_cb()

    def _on_color_set(self, btn):
        self.preview.queue_draw()
        self.on_change_cb()

    def update_visibility(self):
        stops = int(self.cb_stops.get_active_id() or 3)
        for i in range(6):
            self.grad_btns[i].set_visible(i < stops)
        self.preview.queue_draw()

    def save_settings(self):
        self.settings[f"{self.prefix}_gradient_stops"] = int(self.cb_stops.get_active_id() or 3)
        for i in range(6):
            self.settings[f"{self.prefix}_gradient_{i + 1}"] = rgba_to_hex(self.grad_btns[i].get_rgba())

    def _on_draw_preview(self, area, cr, width, height):
        stops = int(self.cb_stops.get_active_id() or 3)
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
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, margin_bottom=10)
        self.prefix = prefix
        self.settings = settings
        self.on_change_cb = on_change_cb

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=5)
        self.append(row)
        row.append(Gtk.Label(label=title, xalign=0, margin_end=10, hexpand=True))

        self.cb_mode = Gtk.ComboBoxText()
        self.cb_mode.append("0", lm.get("config.monitor.color_solid", "Solid"))
        self.cb_mode.append("1", lm.get("config.monitor.color_gradient", "Gradient"))
        self.cb_mode.append("2", lm.get("config.monitor.solid_auto", "Automatic Solid (Icon)"))
        self.cb_mode.append("3", lm.get("config.monitor.gradient_auto", "Automatic Gradient (Icon)"))
        self.cb_mode.set_active_id(str(self.settings.get(f"{prefix}_color_mode", 0)))
        self.cb_mode.connect("changed", self._on_mode_changed)
        row.append(self.cb_mode)

        self.box_solid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_start=10)
        row.append(self.box_solid)
        self.btn_solid = Gtk.ColorButton()
        c = Gdk.RGBA()
        c.parse(self.settings.get(f"{prefix}_color", def_color))
        self.btn_solid.set_rgba(c)
        self.btn_solid.connect("color-set", self.on_change_cb)
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

    def _on_mode_changed(self, combo):
        self.update_visibility()
        self.on_change_cb()

    def update_visibility(self):
        m = int(self.cb_mode.get_active_id() or 0)
        self.box_solid.set_visible(m == 0)
        self.grad_box.set_visible(m == 1)
        self.box_auto.set_visible(m in (2, 3))

    def save_settings(self):
        self.settings[f"{self.prefix}_color_mode"] = int(self.cb_mode.get_active_id() or 0)
        self.settings[f"{self.prefix}_auto_darken"] = int(self.spin_darken.get_value())
        self.settings[f"{self.prefix}_color"] = rgba_to_hex(self.btn_solid.get_rgba())
        self.grad_box.save_settings()


class CustomLabelRow(UIComponentsBase):
    """Configuration of a text block: content, font, color, alignment,
    outline, position and width. Reused for name, percentage and carousel."""

    def __init__(self, title_text, settings_dict, key_prefix, parent_action, show_text_input=True):
        super().__init__(settings_dict, parent_action)
        self.key_prefix = key_prefix
        lm = self.lm
        fd = FontDefaults.from_global()

        is_pct = key_prefix == "pct"
        def_align = "right" if is_pct else fd.align
        def_font_desc_str = f"{fd.family} {22 if is_pct else fd.size}"

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        self.main_box.append(Gtk.Label(label=title_text, xalign=0, margin_bottom=3,
                                       css_classes=["bold"]))

        self.entry = None
        self.pct_format_combo = None
        if show_text_input:
            text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            self.main_box.append(text_box)
            if is_pct:
                self.pct_format_combo = Gtk.ComboBoxText(hexpand=True)
                for fmt_id, label in enumerate(PCT_FORMAT_LABELS):
                    self.pct_format_combo.append(str(fmt_id),
                                                 lm.get(f"config.pct_format.{fmt_id}", label))
                self.pct_format_combo.set_active_id(str(self.settings.get("pct_format", 0)))
                self.pct_format_combo.connect("changed", self.on_change)
                text_box.append(self.pct_format_combo)
            else:
                self.entry = Gtk.Entry(hexpand=True,
                                       placeholder_text=lm.get("config.label.placeholder"))
                if f"text_{key_prefix}" in self.settings:
                    self.entry.set_text(self.settings[f"text_{key_prefix}"])
                self.entry.connect("changed", self.on_change)
                text_box.append(self.entry)

        # Font and color
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(font_box)
        font_box.append(Gtk.Label(label=lm.get("config.font.title"), xalign=0,
                                  hexpand=True, margin_start=2))
        self.font_btn = Gtk.FontButton()
        self.font_btn.set_font_desc(Pango.FontDescription.from_string(
            self.settings.get(f"font_desc_{key_prefix}", def_font_desc_str)))
        self.font_btn.connect("font-set", self.on_change)
        font_box.append(self.font_btn)
        self.color_btn = self.create_color_button(f"color_{key_prefix}", fd.color, self.on_change)
        self.color_btn.set_margin_start(10)
        font_box.append(self.color_btn)

        # Alignment
        align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
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

        val_align = self.settings.get(f"align_{key_prefix}", def_align)
        if val_align == "left":
            self.btn_left.set_active(True)
        elif val_align == "right":
            self.btn_right.set_active(True)
        else:
            self.btn_center.set_active(True)

        for btn in (self.btn_left, self.btn_center, self.btn_right):
            btn.connect("toggled", self.on_change)
            align_box.append(btn)

        # Outline
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(out_box)
        out_box.append(Gtk.Label(label=lm.get("config.outline.title"), xalign=0,
                                 hexpand=False, margin_start=2, margin_end=5))
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get(f"outline_width_{key_prefix}", fd.outline_width))
        self.out_spin.connect("value-changed", self.on_change)
        out_box.append(self.out_spin)
        out_box.append(Gtk.Label(label=lm.get("config.outline.color"), xalign=1,
                                 hexpand=True, margin_start=2, margin_end=5))
        self.out_color_btn = self.create_color_button(f"outline_color_{key_prefix}",
                                                      fd.outline_color, self.on_change)
        out_box.append(self.out_color_btn)

        # Position and width
        xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(xy_box)
        self.x_spin = self.create_spin_row(xy_box, lm.get("config.pos.x"), f"pos_x_{key_prefix}")
        self.y_spin = self.create_spin_row(xy_box, lm.get("config.pos.y"), f"pos_y_{key_prefix}",
                                           margin_start=10)

        w_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(w_box)
        self.w_spin = self.create_spin_row(w_box, lm.get("config.size.width"), f"width_{key_prefix}")

    def on_change(self, *args):
        if self._updating:
            return
        if self.entry is not None:
            self.settings[f"text_{self.key_prefix}"] = self.entry.get_text()
        elif self.pct_format_combo is not None:
            self.settings["pct_format"] = int(self.pct_format_combo.get_active_id() or 0)

        self.settings[f"color_{self.key_prefix}"] = rgba_to_hex(self.color_btn.get_rgba())
        self.settings[f"font_desc_{self.key_prefix}"] = self.font_btn.get_font_desc().to_string()

        align = "center"
        if self.btn_left.get_active():
            align = "left"
        elif self.btn_right.get_active():
            align = "right"
        self.settings[f"align_{self.key_prefix}"] = align

        self.settings[f"outline_width_{self.key_prefix}"] = int(self.out_spin.get_value())
        self.settings[f"outline_color_{self.key_prefix}"] = rgba_to_hex(self.out_color_btn.get_rgba())

        self.save_or_del(f"pos_x_{self.key_prefix}", int(self.x_spin.get_value()))
        self.save_or_del(f"pos_y_{self.key_prefix}", int(self.y_spin.get_value()))
        self.save_or_del(f"width_{self.key_prefix}", int(self.w_spin.get_value()))
        self.notify_parent()


class CustomIconRow(UIComponentsBase):
    """Icon configuration: visibility, file, size, outline and position."""

    def __init__(self, settings_dict, parent_action, suffix=""):
        super().__init__(settings_dict, parent_action)
        self.suffix = f"_{suffix}" if suffix else ""
        lm = self.lm

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        self.main_box.append(Gtk.Label(label=lm.get("config.icon.format"), xalign=0,
                                       margin_bottom=3, css_classes=["bold"]))

        # Show icon
        toggle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=10)
        toggle_box.append(Gtk.Label(label=lm.get("config.show_icon.title", "Show Icon"),
                                    xalign=0, hexpand=True))
        self.switch_show = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch_show.set_active(self.settings.get(f"show_icon{self.suffix}", True))
        self.switch_show.connect("notify::active", self.on_show_changed)
        toggle_box.append(self.switch_show)
        self.main_box.append(toggle_box)

        # File
        file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(file_box)
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

        # Height
        wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(wh_box)
        self.h_spin = self.create_spin_row(wh_box, lm.get("config.size.height"),
                                           f"icon_height{self.suffix}")

        # Outline
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(out_box)
        out_box.append(Gtk.Label(label=lm.get("config.outline.width", "Outline Width"), margin_end=5))
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get(f"icon_out_width{self.suffix}", 1))
        self.out_spin.connect("value-changed", self.on_change)
        out_box.append(self.out_spin)
        out_box.append(Gtk.Label(label=lm.get("config.outline.color", "Outline Color"),
                                 margin_start=15, margin_end=5))
        self.out_color_btn = self.create_color_button(f"icon_out_color{self.suffix}",
                                                      "#000000", self.on_change)
        out_box.append(self.out_color_btn)

        # Position
        xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(xy_box)
        self.x_spin = self.create_spin_row(xy_box, lm.get("config.pos.x"), f"icon_x{self.suffix}")
        self.y_spin = self.create_spin_row(xy_box, lm.get("config.pos.y"), f"icon_y{self.suffix}",
                                           margin_start=10)

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
        self.settings[f"show_icon{self.suffix}"] = switch.get_active()
        self.notify_parent()

    def on_change(self, *args):
        if self._updating:
            return
        self.save_or_del(f"icon_height{self.suffix}", int(self.h_spin.get_value()))
        self.save_or_del(f"icon_x{self.suffix}", int(self.x_spin.get_value()))
        self.save_or_del(f"icon_y{self.suffix}", int(self.y_spin.get_value()))
        self.save_or_del(f"icon_out_width{self.suffix}", int(self.out_spin.get_value()))
        self.settings[f"icon_out_color{self.suffix}"] = rgba_to_hex(self.out_color_btn.get_rgba())
        self.notify_parent()


class CustomBarRow(UIComponentsBase):
    """Bar configuration: style, colors, inversion, outline and geometry."""

    def __init__(self, settings_dict, parent_action):
        super().__init__(settings_dict, parent_action)
        lm = self.lm

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        self.main_box.append(Gtk.Label(label=lm.get("config.bar.format"), xalign=0,
                                       margin_bottom=3, css_classes=["bold"]))

        lbl_warn = Gtk.Label(label=lm.get("config.bar.style.warning",
                                          "The '2 Bars' mode will only appear when the mixer mode is enabled."),
                             xalign=0, margin_bottom=6)
        lbl_warn.add_css_class("dim-label")
        lbl_warn.set_wrap(True)
        self.main_box.append(lbl_warn)

        # Style
        style_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=6)
        self.main_box.append(style_box)
        style_box.append(Gtk.Label(label=lm.get("config.bar.style", "Style"), xalign=0, margin_end=10))
        self.style_combo = Gtk.ComboBoxText(hexpand=True)
        self.style_combo.append("0", lm.get("config.bar.style.2bars", "2 Bars"))
        self.style_combo.append("1", lm.get("config.bar.style.1bar", "1 Bar"))
        self.style_combo.append("2", lm.get("config.bar.style.1bar_tri", "1 Bar with Triangle"))
        self.style_combo.append("3", lm.get("config.bar.style.1bar_line", "1 Bar with Line"))
        self.style_combo.set_active_id(str(self.settings.get("bar_style", 0)))
        self.style_combo.connect("changed", self.on_change)
        style_box.append(self.style_combo)

        # Main colors
        self.bar_color_sel = ColorModeSelector(
            "bar", lm.get("config.bar.color", "Bar Color"), self.settings, lm,
            "#ffffff", DEFAULT_GRADIENT_COLORS, self.on_change, def_darken=0)
        self.main_box.append(self.bar_color_sel)

        self.bg_color_sel = ColorModeSelector(
            "bar_bg", lm.get("config.bar.background", "Background Color"), self.settings, lm,
            "#424242", DEFAULT_GRADIENT_COLORS, self.on_change, def_darken=50)
        self.main_box.append(self.bg_color_sel)

        # Secondary colors
        color_box_2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(color_box_2)
        color_box_2.append(Gtk.Label(label=lm.get("config.bar.over100"), margin_end=5))
        self.over_color_btn = self.create_color_button("bar_over_color", "#ff4b4b", self.on_change)
        color_box_2.append(self.over_color_btn)
        color_box_2.append(Gtk.Label(label=lm.get("config.bar.ind_color", "Indicator Color"),
                                     margin_start=15, margin_end=5))
        self.ind_color_btn = self.create_color_button("bar_ind_color", "#FFFFFF", self.on_change)
        color_box_2.append(self.ind_color_btn)
        self.neu_lbl = Gtk.Label(label=lm.get("config.bar.neu_color", "Neutral Color"),
                                 margin_start=15, margin_end=5)
        color_box_2.append(self.neu_lbl)
        self.neu_color_btn = self.create_color_button("bar_neu_color", "#808080", self.on_change)
        color_box_2.append(self.neu_color_btn)

        # Invert
        invert_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(invert_box)
        invert_box.append(Gtk.Label(label=lm.get("config.bar.invert", "Invert Bar"),
                                    xalign=0, margin_end=10, hexpand=True))
        self.sw_invert = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_invert.set_active(self.settings.get("bar_invert", False))
        self.sw_invert.connect("notify::active", self.on_change)
        invert_box.append(self.sw_invert)

        # Outline
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(out_box)
        out_box.append(Gtk.Label(label=lm.get("config.outline.width", "Outline Width"),
                                 margin_end=5))
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get("bar_out_width",
                                                  self.defaults_calc.get("bar_out_width", 1)))
        self.out_spin.connect("value-changed", self.on_change)
        out_box.append(self.out_spin)
        out_box.append(Gtk.Label(label=lm.get("config.outline.color", "Outline Color"),
                                 margin_start=15, margin_end=5))
        self.out_color_btn = self.create_color_button(
            "bar_out_color", self.defaults_calc.get("bar_out_color", "#000000"), self.on_change)
        out_box.append(self.out_color_btn)

        # Geometry
        wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(wh_box)
        self.w_spin = self.create_spin_row(wh_box, lm.get("config.size.width"), "bar_width")
        self.h_spin = self.create_spin_row(wh_box, lm.get("config.size.height"), "bar_height",
                                           margin_start=10)

        xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(xy_box)
        self.x_spin = self.create_spin_row(xy_box, lm.get("config.pos.x"), "bar_x")
        self.y_spin = self.create_spin_row(xy_box, lm.get("config.pos.y"), "bar_y", margin_start=10)

        rad_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(rad_box)
        self.rad_spin = self.create_spin_row(rad_box, lm.get("config.size.radius"), "bar_radius",
                                             hi=100)

        self.update_neu_visibility()

    def update_neu_visibility(self):
        """The neutral color only applies to the balance bar (dual + '1 Bar' style)."""
        is_single_mode = not self.settings.get("dual_mode", False)
        style = int(self.style_combo.get_active_id() or 0)
        visible = not is_single_mode and style == 1
        self.neu_lbl.set_visible(visible)
        self.neu_color_btn.set_visible(visible)

    def on_change(self, *args):
        if self._updating:
            return
        self.settings["bar_style"] = int(self.style_combo.get_active_id() or 0)
        self.update_neu_visibility()

        self.bar_color_sel.save_settings()
        self.bg_color_sel.save_settings()

        self.settings["bar_over_color"] = rgba_to_hex(self.over_color_btn.get_rgba())
        self.settings["bar_ind_color"] = rgba_to_hex(self.ind_color_btn.get_rgba())
        self.settings["bar_neu_color"] = rgba_to_hex(self.neu_color_btn.get_rgba())
        self.settings["bar_invert"] = self.sw_invert.get_active()
        self.settings["bar_out_color"] = rgba_to_hex(self.out_color_btn.get_rgba())
        self.settings["bar_out_width"] = int(self.out_spin.get_value())

        for key, spin in (("bar_width", self.w_spin), ("bar_height", self.h_spin),
                          ("bar_x", self.x_spin), ("bar_y", self.y_spin),
                          ("bar_radius", self.rad_spin)):
            self.save_or_del(key, int(spin.get_value()))
        self.notify_parent()


class DeviceConfigGroup(Adw.PreferencesGroup):
    """Device selector: type, specific device, auto-index and limit."""

    def __init__(self, parent_action, suffix=""):
        super().__init__()
        self.parent_action = parent_action
        self.suffix_str = f"_{suffix}" if suffix else ""

        lm = self.parent_action.plugin_base.lm
        settings = self.parent_action.get_settings()

        # Type
        type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        type_box.append(Gtk.Label(label=lm.get("config.type.title", "Device Type"),
                                  xalign=0, margin_end=10, hexpand=True))
        self.type_combo = Gtk.ComboBoxText(hexpand=False)
        self.type_combo.append("sink", lm.get("config.type.sink", "Sink (Output)"))
        self.type_combo.append("source", lm.get("config.type.source", "Source (Input)"))
        self.type_combo.append("application", lm.get("config.type.application", "Application"))
        self.type_combo.set_active_id(settings.get(f"device_type{self.suffix_str}", "sink"))
        self.type_combo.connect("changed", self.on_type_changed)
        type_box.append(self.type_combo)
        self.add(type_box)

        # Device
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        device_box.append(Gtk.Label(label=lm.get("config.device.title", "Device"),
                                    xalign=0, margin_end=10, hexpand=True))
        self.device_combo = Gtk.ComboBoxText(hexpand=False)
        self.device_combo.set_size_request(200, -1)
        self.device_combo.connect("changed", self.on_device_changed)
        device_box.append(self.device_combo)
        self.add(device_box)

        # Auto-index
        self.auto_index_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        ai_lbl = Gtk.Label(label=lm.get("config.auto_index.title", "Auto-index #"),
                           xalign=0, margin_end=10, hexpand=True)
        ai_lbl.set_tooltip_text(lm.get("config.auto_index.subtitle", "0 = disabled. Used for apps"))
        self.auto_index_box.append(ai_lbl)
        self.auto_index_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=settings.get(f"auto_index{self.suffix_str}", 0), lower=0, upper=10, step_increment=1))
        self.auto_index_spin.connect("value-changed", self.on_auto_index_changed)
        self.auto_index_box.append(self.auto_index_spin)
        self.add(self.auto_index_box)

        # Volume limit
        limit_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        limit_box.append(Gtk.Label(label=lm.get("config.limit.title", "Volume Limit (%)"),
                                   xalign=0, margin_end=10, hexpand=True))
        self.limit_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=min(150.0, float(settings.get(f"volume_limit{self.suffix_str}", 100))),
            lower=0, upper=150, step_increment=5))
        self.limit_spin.connect("value-changed", self.on_limit_changed)
        limit_box.append(self.limit_spin)
        self.add(limit_box)

        self.update_device_model()

    def update_device_model(self):
        settings = self.parent_action.get_settings()
        device_type = settings.get(f"device_type{self.suffix_str}", "sink")
        selected_target = settings.get(f"device_name{self.suffix_str}", "")

        svc = self.parent_action.pulse_service
        if svc is None:
            return
        lm = self.parent_action.plugin_base.lm

        self.device_combo.handler_block_by_func(self.on_device_changed)
        self.device_combo.remove_all()

        if device_type == "application":
            default_id = "Auto"
            self.device_combo.append("Auto", lm.get("config.device.auto", "Auto"))
            items = svc.get_active_applications()
        elif device_type == "sink":
            default_id = "default"
            self.device_combo.append("default", lm.get("config.device.default", "Default"))
            items = svc.sink_list()
        else:
            default_id = "default"
            self.device_combo.append("default", lm.get("config.device.default", "Default"))
            items = [d for d in svc.source_list()
                     if not getattr(d, "name", "").endswith(".monitor")
                     and not getattr(d, "description", "").startswith("Monitor of")]

        selected_id = default_id
        found = False
        if device_type == "application":
            for app_name in items:
                self.device_combo.append(app_name, app_name)
                if selected_target == app_name:
                    selected_id = app_name
                    found = True
        else:
            for dev in items:
                desc = getattr(dev, "description", getattr(dev, "name", "Unknown"))
                name = getattr(dev, "name", "")
                self.device_combo.append(name, desc)
                if selected_target == name:
                    selected_id = name
                    found = True

        if selected_target and not found and selected_target != default_id:
            not_available = lm.get("config.device.not_available", "Not Available")
            self.device_combo.append(selected_target, f"{selected_target} ({not_available})")
            selected_id = selected_target

        self.device_combo.set_active_id(selected_id)
        self.device_combo.handler_unblock_by_func(self.on_device_changed)
        self.auto_index_box.set_visible(device_type == "application")

    def _save(self, key, value):
        settings = self.parent_action.get_settings()
        settings[key] = value
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()

    def on_type_changed(self, combo):
        settings = self.parent_action.get_settings()
        settings[f"device_type{self.suffix_str}"] = combo.get_active_id() or "sink"
        settings[f"device_name{self.suffix_str}"] = ""
        self.parent_action.set_settings(settings)
        self.update_device_model()
        self.parent_action.draw_image()

    def on_device_changed(self, combo):
        active_id = combo.get_active_id()
        value = "" if (active_id == "default" or not active_id) else active_id
        self._save(f"device_name{self.suffix_str}", value)

    def on_auto_index_changed(self, spin):
        self._save(f"auto_index{self.suffix_str}", int(spin.get_value()))

    def on_limit_changed(self, spin):
        self._save(f"volume_limit{self.suffix_str}", int(spin.get_value()))


class VolumeMonitorConfigRow(UIComponentsBase):
    """Peak monitor configuration: activation, modes, colors and timing."""

    def __init__(self, settings, parent):
        super().__init__(settings, parent)
        lm = self.lm

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5,
                                margin_top=5, margin_bottom=5, margin_start=10, margin_end=10)
        self.set_child(self.main_box)

        # Activation
        box_enable = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=10)
        box_enable.append(Gtk.Label(label=lm.get("config.monitor.enable", "Enable Volume Monitor"),
                                    xalign=0, hexpand=True, css_classes=["bold"]))
        self.sw_enable = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_enable.set_active(self.settings.get("monitor_enabled", False))
        self.sw_enable.connect("notify::active", self.on_enable_change)
        box_enable.append(self.sw_enable)
        self.main_box.append(box_enable)

        lbl_warning = Gtk.Label(label=lm.get("config.monitor.app_warning",
                                             "Monitor does not support individual applications. "
                                             "It will monitor the entire output device instead."),
                                xalign=0, wrap=True, css_classes=["dim-label"])
        lbl_warning.set_margin_bottom(10)
        self.main_box.append(lbl_warning)

        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.main_box.append(self.settings_container)

        # Bar mode
        box_bar_mode = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        box_bar_mode.append(Gtk.Label(label=lm.get("config.monitor.bar_mode", "Bar Mode"),
                                      xalign=0, margin_end=10))
        self.cb_bar_mode = Gtk.ComboBoxText(hexpand=True)
        self.cb_bar_mode.append("0", lm.get("config.monitor.bar_single", "Single Bar"))
        self.cb_bar_mode.append("1", lm.get("config.monitor.bar_dual", "Dual Bar (Stereo)"))
        self.cb_bar_mode.set_active_id(str(self.settings.get("monitor_bar_mode", 0)))
        self.cb_bar_mode.connect("changed", self.on_change)
        box_bar_mode.append(self.cb_bar_mode)
        self.settings_container.append(box_bar_mode)

        # Color mode
        box_color_mode = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        box_color_mode.append(Gtk.Label(label=lm.get("config.monitor.color_mode", "Color Mode"),
                                        xalign=0, margin_end=10))
        self.cb_color_mode = Gtk.ComboBoxText(hexpand=True)
        self.cb_color_mode.append("0", lm.get("config.monitor.color_solid", "Solid"))
        self.cb_color_mode.append("1", lm.get("config.monitor.color_tricolor", "Tricolor"))
        self.cb_color_mode.append("2", lm.get("config.monitor.color_gradient", "Gradient"))
        self.cb_color_mode.append("3", lm.get("config.monitor.solid_auto", "Automatic Solid (Icon)"))
        self.cb_color_mode.append("4", lm.get("config.monitor.gradient_auto", "Automatic Gradient (Icon)"))
        self.cb_color_mode.set_active_id(str(self.settings.get("monitor_color_mode", 0)))
        self.cb_color_mode.connect("changed", self.on_color_mode_change)
        box_color_mode.append(self.cb_color_mode)
        self.settings_container.append(box_color_mode)

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

        # Solid
        self.box_solid.append(Gtk.Label(label=lm.get("config.monitor.color", "Color"),
                                        xalign=0, hexpand=True))
        self.btn_solid = self.create_color_button("monitor_color_solid", "#ffffff", self.on_change)
        self.box_solid.append(self.btn_solid)

        # Tricolor
        self.btn_tri_low = self.create_color_button("monitor_color_low", "#00ff00", self.on_change)
        self.btn_tri_mid = self.create_color_button("monitor_color_mid", "#ffff00", self.on_change)
        self.btn_tri_high = self.create_color_button("monitor_color_high", "#ff0000", self.on_change)
        self.spin_threshold_mid = self._create_threshold_spin("monitor_threshold_mid", -20)
        self.spin_threshold_high = self._create_threshold_spin("monitor_threshold_high", -9)

        self._add_tri_row(lm.get("config.monitor.color_low", "Low"), self.btn_tri_low)
        self._add_tri_row(lm.get("config.monitor.color_mid", "Mid"), self.btn_tri_mid,
                          self.spin_threshold_mid)
        self._add_tri_row(lm.get("config.monitor.color_high", "High"), self.btn_tri_high,
                          self.spin_threshold_high)

        # Gradient
        self.grad_config = GradientConfigBox("monitor", self.settings, DEFAULT_GRADIENT_COLORS,
                                             self.on_change, lm)
        self.box_gradient.append(self.grad_config)

        # Timing
        bfps = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=5)
        bfps.append(Gtk.Label(label=lm.get("config.monitor.fps", "Update Rate (FPS)"),
                              xalign=0, margin_end=10))
        self.cb_fps = Gtk.ComboBoxText(hexpand=True)
        for f in (1, 2, 5, 10, 15, 20):
            self.cb_fps.append(str(f), str(f))
        self.cb_fps.set_active_id(str(self.settings.get("monitor_fps", 10)))
        self.cb_fps.connect("changed", self.on_change)
        bfps.append(self.cb_fps)
        self.settings_container.append(bfps)

        lbl_fps_warn = Gtk.Label(label=lm.get("config.monitor.fps.warning",
                                              "A high FPS rate increases CPU usage"),
                                 xalign=0, margin_top=2, margin_bottom=5)
        lbl_fps_warn.add_css_class("dim-label")
        lbl_fps_warn.set_wrap(True)
        self.settings_container.append(lbl_fps_warn)

        bdel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        bdel.append(Gtk.Label(label=lm.get("config.monitor.delay", "Switch Delay (s)"),
                              xalign=0, hexpand=True))
        self.spin_delay = Gtk.SpinButton.new_with_range(1, 60, 1)
        self.spin_delay.set_value(self.settings.get("monitor_delay", 5))
        self.spin_delay.connect("value-changed", self.on_change)
        bdel.append(self.spin_delay)
        self.settings_container.append(bdel)

        # Show dB
        bdb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        bdb.append(Gtk.Label(label=lm.get("config.monitor.show_db", "Show Decibels"),
                             xalign=0, hexpand=True))
        self.sw_db = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_db.set_active(self.settings.get("monitor_show_db", False))
        self.sw_db.connect("notify::active", self.on_change)
        bdb.append(self.sw_db)
        self.settings_container.append(bdb)

        # RMS Indicator
        brms_sw = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        brms_sw.append(Gtk.Label(label=lm.get("config.monitor.show_rms", "Show Average Indicator"),
                                 xalign=0, hexpand=True))
        self.sw_rms = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_rms.set_active(self.settings.get("monitor_show_rms", False))
        self.sw_rms.connect("notify::active", self.on_rms_change)
        brms_sw.append(self.sw_rms)
        self.settings_container.append(brms_sw)

        self.brms_options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.brms_options.append(Gtk.Label(label=lm.get("config.monitor.rms_color", "Indicator Color"),
                                           margin_end=5))
        self.btn_rms_color = self.create_color_button("monitor_rms_color", "#FFFFFF", self.on_change)
        self.brms_options.append(self.btn_rms_color)
        self.settings_container.append(self.brms_options)

        self.brms_options2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.brms_options2.append(Gtk.Label(label=lm.get("config.outline.width", "Outline Width"),
                                            margin_end=5))
        self.spin_rms_out = Gtk.SpinButton.new_with_range(0, 10, 1)
        self.spin_rms_out.set_value(self.settings.get("monitor_rms_out_width", 1.0))
        self.spin_rms_out.connect("value-changed", self.on_change)
        self.brms_options2.append(self.spin_rms_out)
        self.brms_options2.append(Gtk.Label(label=lm.get("config.outline.color", "Outline Color"),
                                            margin_start=15, margin_end=5))
        self.btn_rms_out_color = self.create_color_button("monitor_rms_out_color", "#000000",
                                                          self.on_change)
        self.brms_options2.append(self.btn_rms_out_color)
        self.settings_container.append(self.brms_options2)

        # Invert barra
        binv = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=5)
        binv.append(Gtk.Label(label=lm.get("config.monitor.invert", "Invert Bar"),
                              xalign=0, hexpand=True))
        self.sw_mon_inv = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_mon_inv.set_active(self.settings.get("monitor_invert", False))
        self.sw_mon_inv.connect("notify::active", self.on_change)
        binv.append(self.sw_mon_inv)
        self.settings_container.append(binv)

        self.update_visibility()

    def _create_threshold_spin(self, key, default):
        spin = Gtk.SpinButton.new_with_range(-100, 0, 1)
        spin.set_value(self.settings.get(key, default))
        spin.connect("value-changed", self.on_change)
        return spin

    def _add_tri_row(self, label, btn, thresh_spin=None):
        r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        r.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        if thresh_spin is not None:
            r.append(Gtk.Label(label=self.lm.get("config.monitor.threshold", "Threshold (dB)"),
                               margin_start=10, margin_end=5))
            r.append(thresh_spin)
        r.append(btn)
        self.box_tricolor.append(r)

    def on_enable_change(self, *args):
        self.on_change()
        self.update_visibility()

    def on_color_mode_change(self, *args):
        self.on_change()
        self.update_visibility()

    def on_rms_change(self, *args):
        self.on_change()
        self.update_visibility()

    def update_visibility(self):
        self.settings_container.set_sensitive(self.sw_enable.get_active())
        cmode = int(self.cb_color_mode.get_active_id() or 0)
        self.box_solid.set_visible(cmode == 0)
        self.box_tricolor.set_visible(cmode == 1)
        self.box_gradient.set_visible(cmode == 2)
        self.box_auto.set_visible(cmode in (3, 4))
        self.brms_options.set_sensitive(self.sw_rms.get_active())
        self.brms_options2.set_sensitive(self.sw_rms.get_active())

    def on_change(self, *args):
        if self._updating:
            return
        s = self.settings
        s["monitor_enabled"] = self.sw_enable.get_active()
        s["monitor_bar_mode"] = int(self.cb_bar_mode.get_active_id() or 0)
        s["monitor_color_mode"] = int(self.cb_color_mode.get_active_id() or 0)
        s["monitor_auto_darken"] = int(self.spin_monitor_darken.get_value())
        s["monitor_color_solid"] = rgba_to_hex(self.btn_solid.get_rgba())
        s["monitor_color_low"] = rgba_to_hex(self.btn_tri_low.get_rgba())
        s["monitor_color_mid"] = rgba_to_hex(self.btn_tri_mid.get_rgba())
        s["monitor_color_high"] = rgba_to_hex(self.btn_tri_high.get_rgba())
        s["monitor_threshold_mid"] = int(self.spin_threshold_mid.get_value())
        s["monitor_threshold_high"] = int(self.spin_threshold_high.get_value())
        self.grad_config.save_settings()
        s["monitor_fps"] = int(self.cb_fps.get_active_id() or 10)
        s["monitor_delay"] = int(self.spin_delay.get_value())
        s["monitor_show_db"] = self.sw_db.get_active()
        s["monitor_show_rms"] = self.sw_rms.get_active()
        s["monitor_rms_color"] = rgba_to_hex(self.btn_rms_color.get_rgba())
        s["monitor_rms_out_color"] = rgba_to_hex(self.btn_rms_out_color.get_rgba())
        s["monitor_rms_out_width"] = float(self.spin_rms_out.get_value())
        s["monitor_invert"] = self.sw_mon_inv.get_active()
        self.notify_parent()


class CarouselConfigRow(UIComponentsBase):
    """Carousel configuration: activation, device filters and texts."""

    def __init__(self, settings_dict, parent_action):
        super().__init__(settings_dict, parent_action)
        lm = self.lm

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15,
                                spacing=10)
        self.set_child(self.main_box)

        box_enable, self.sw_enable = self._create_toggle(
            "carousel_enabled", lm.get("config.carousel.enable", "Enable Device Carousel"), False)
        self.main_box.append(box_enable)
        self.main_box.append(Gtk.Separator(margin_top=5, margin_bottom=5))

        toggles = [
            ("carousel_show_sinks", "config.carousel.show_sinks", False),
            ("carousel_show_sources", "config.carousel.show_sources", False),
            ("carousel_show_apps", "config.carousel.show_apps", True),
            ("carousel_show_default_sink", "config.carousel.default_sink", True),
            ("carousel_show_default_source", "config.carousel.default_source", False),
        ]
        self.toggle_switches = {}
        for key, label_key, default in toggles:
            box, sw = self._create_toggle(key, lm.get(label_key, label_key), default)
            self.toggle_switches[key] = sw
            self.main_box.append(box)

        self.main_box.append(Gtk.Separator(margin_top=10, margin_bottom=10))

        self.name_row = CustomLabelRow(lm.get("config.carousel.name_format", "Name Format"),
                                       settings_dict, "carousel_name", parent_action,
                                       show_text_input=False)
        self.name_row.set_activatable(False)
        self.main_box.append(self.name_row)

        self.main_box.append(Gtk.Separator(margin_top=10, margin_bottom=10))

        self.pct_row = CustomLabelRow(lm.get("config.carousel.pct_format", "Volume Format"),
                                      settings_dict, "carousel_pct", parent_action,
                                      show_text_input=False)
        self.pct_row.set_activatable(False)
        self.main_box.append(self.pct_row)

    def _create_toggle(self, key, label_text, default):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.append(Gtk.Label(label=label_text, xalign=0, hexpand=True))
        sw = Gtk.Switch()
        sw.set_active(self.settings.get(key, default))
        sw.connect("notify::active", self.on_change)
        box.append(sw)
        return box, sw

    def on_change(self, *args):
        self.settings["carousel_enabled"] = self.sw_enable.get_active()
        for key, sw in self.toggle_switches.items():
            self.settings[key] = sw.get_active()
        self.parent.set_settings(self.settings)
