import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Adw, GLib, Gdk, Pango
import globals as gl

from .PipeWireActionBase import PipeWireActionBase

class UIComponentsBase(Adw.PreferencesRow):
    def __init__(self, settings_dict, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        self.defaults_calc = {}
        if hasattr(self.parent, "get_calculated_defaults"):
            calc = self.parent.get_calculated_defaults()
            self.defaults_calc = calc[0] if isinstance(calc, tuple) else calc
        self._updating = False

    def create_color_button(self, settings_key, default_hex, on_change_cb):
        btn = Gtk.ColorButton()
        c = Gdk.RGBA()
        c.parse(self.settings.get(settings_key, default_hex))
        btn.set_rgba(c)
        btn.connect("color-set", on_change_cb)
        return btn

    def reset_val(self, key, spin):
        if key in self.settings:
            del self.settings[key]
        self._updating = True
        spin.set_value(self.defaults_calc.get(key, 0))
        self._updating = False
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            if "vol_a" in self.parent.last_state: self.parent.last_state["vol_a"] = -1
            if "vol_b" in self.parent.last_state: self.parent.last_state["vol_b"] = -1
        self.parent.draw_image()

    def save_or_del(self, key, val):
        if val == self.defaults_calc.get(key, 0):
            self.settings.pop(key, None)
        else:
            self.settings[key] = val

class CustomLabelRow(UIComponentsBase):
    def __init__(self, title_text, settings_dict, key_prefix, parent_action):
        super().__init__(settings_dict, parent_action)
        self.key_prefix = key_prefix

        defaults = gl.settings_manager.font_defaults
        def_color_rgba = Gdk.RGBA()
        def_color = defaults.get("font-color", [255, 255, 255, 255])
        def_color_rgba.red = def_color[0]/255.0
        def_color_rgba.green = def_color[1]/255.0
        def_color_rgba.blue = def_color[2]/255.0
        def_color_rgba.alpha = def_color[3]/255.0

        def_font_family = defaults.get("font-family", "Sans")
        def_font_size = defaults.get("font-size", 15)
        def_align = defaults.get("alignment", "center")
        
        if key_prefix == "pct":
            def_font_size = 22
            def_align = "right"
            
        def_font_desc_str = f"{def_font_family} {def_font_size}"

        def_out_color = defaults.get("outline-color", [0, 0, 0, 255])
        def_out_rgba = Gdk.RGBA()
        def_out_rgba.red = def_out_color[0]/255.0
        def_out_rgba.green = def_out_color[1]/255.0
        def_out_rgba.blue = def_out_color[2]/255.0
        def_out_rgba.alpha = def_out_color[3]/255.0

        def_out_width = int(defaults.get("outline-width", 2))
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)

        label = Gtk.Label(label=title_text, xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)

        self.text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.text_box)

        if key_prefix != "pct":
            self.entry = Gtk.Entry(hexpand=True, placeholder_text=self.parent.plugin_base.lm.get("config.label.placeholder"))
            if f"text_{key_prefix}" in self.settings:
                self.entry.set_text(self.settings[f"text_{key_prefix}"])
            self.entry.connect("changed", self.on_change)
            self.text_box.append(self.entry)
        else:
            self.entry = None
            self.pct_format_combo = Gtk.ComboBoxText(hexpand=True)
            self.pct_format_combo.append("0", "Porcentaje (0%, 50%, 100%)")
            self.pct_format_combo.append("1", "Panorámico Porcentaje (-100%, 0%, +100%)")
            self.pct_format_combo.append("2", "Panorámico (-100, 0, +100)")
            self.pct_format_combo.append("3", "Crossfade (A 0, A=B, B 0)")
            self.pct_format_combo.append("4", "Crossfade B (L0, 100, R0)")
            self.pct_format_combo.append("5", "Deshabilitado")
            
            val = str(self.settings.get("pct_format", 0))
            self.pct_format_combo.set_active_id(val)
            self.pct_format_combo.connect("changed", self.on_change)
            self.text_box.append(self.pct_format_combo)

        self.color_btn = self.create_color_button(f"color_{key_prefix}", f"#{int(def_color_rgba.red*255):02x}{int(def_color_rgba.green*255):02x}{int(def_color_rgba.blue*255):02x}", self.on_change)
        self.text_box.append(self.color_btn)

        self.font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.font_box)

        font_label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.font.title"), xalign=0, hexpand=True, margin_start=2)
        self.font_box.append(font_label)

        self.font_btn = Gtk.FontButton()
        if f"font_desc_{key_prefix}" in self.settings:
            self.font_btn.set_font_desc(Pango.FontDescription.from_string(self.settings[f"font_desc_{key_prefix}"]))
        else:
            self.font_btn.set_font_desc(Pango.FontDescription.from_string(def_font_desc_str))
        self.font_btn.connect("font-set", self.on_change)
        self.font_box.append(self.font_btn)

        self.align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.align_box)

        align_label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.align.title"), xalign=0, hexpand=True, margin_start=2)
        self.align_box.append(align_label)

        self.btn_left = Gtk.ToggleButton(icon_name="format-justify-left-symbolic", tooltip_text=self.parent.plugin_base.lm.get("config.align.left"))
        self.btn_center = Gtk.ToggleButton(icon_name="format-justify-center-symbolic", tooltip_text=self.parent.plugin_base.lm.get("config.align.center"))
        self.btn_right = Gtk.ToggleButton(icon_name="format-justify-right-symbolic", tooltip_text=self.parent.plugin_base.lm.get("config.align.right"))
        self.btn_center.set_group(self.btn_left)
        self.btn_right.set_group(self.btn_left)

        val_align = self.settings.get(f"align_{key_prefix}", def_align)
        if val_align == "left": self.btn_left.set_active(True)
        elif val_align == "right": self.btn_right.set_active(True)
        else: self.btn_center.set_active(True)

        self.btn_left.connect("toggled", self.on_change)
        self.btn_center.connect("toggled", self.on_change)
        self.btn_right.connect("toggled", self.on_change)

        self.align_box.append(self.btn_left)
        self.align_box.append(self.btn_center)
        self.align_box.append(self.btn_right)

        self.out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.out_box)

        out_width_label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.title"), xalign=0, hexpand=False, margin_start=2, margin_end=5)
        self.out_box.append(out_width_label)

        val_out_width = self.settings.get(f"outline_width_{key_prefix}", def_out_width)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(val_out_width)
        self.out_spin.connect("value-changed", self.on_change)
        self.out_box.append(self.out_spin)

        out_color_label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.color"), xalign=1, hexpand=True, margin_start=2, margin_end=5)
        self.out_box.append(out_color_label)

        self.out_color_btn = self.create_color_button(f"outline_color_{key_prefix}", f"#{int(def_out_rgba.red*255):02x}{int(def_out_rgba.green*255):02x}{int(def_out_rgba.blue*255):02x}", self.on_change)
        self.out_box.append(self.out_color_btn)

        # X, Y
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        


        x_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.x"), margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.x_spin.set_value(self.settings.get(f"pos_x_{key_prefix}", self.defaults_calc.get(f"pos_x_{key_prefix}", 0)))
        self.x_spin.connect("value-changed", self.on_change)
        
        btn_reset_x = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer X", css_classes=["circular", "flat"])
        btn_reset_x.connect("clicked", lambda *a: self.reset_val(f"pos_x_{key_prefix}", self.x_spin))
        
        y_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.y"), margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.y_spin.set_value(self.settings.get(f"pos_y_{key_prefix}", self.defaults_calc.get(f"pos_y_{key_prefix}", 0)))
        self.y_spin.connect("value-changed", self.on_change)
        
        btn_reset_y = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Y", css_classes=["circular", "flat"])
        btn_reset_y.connect("clicked", lambda *a: self.reset_val(f"pos_y_{key_prefix}", self.y_spin))
        
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(btn_reset_x)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        self.xy_box.append(btn_reset_y)
        
        # Width
        self.w_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.w_box)
        w_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.width"), margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.w_spin.set_value(self.settings.get(f"width_{key_prefix}", self.defaults_calc.get(f"width_{key_prefix}", 0)))
        self.w_spin.connect("value-changed", self.on_change)
        
        btn_reset_w = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Ancho", css_classes=["circular", "flat"])
        btn_reset_w.connect("clicked", lambda *a: self.reset_val(f"width_{key_prefix}", self.w_spin))
        
        self.w_box.append(w_lbl)
        self.w_box.append(self.w_spin)
        self.w_box.append(btn_reset_w)
        


    def on_change(self, *args):
        if getattr(self, "_updating", False): return
        if self.entry:
            self.settings[f"text_{self.key_prefix}"] = self.entry.get_text()
        elif self.key_prefix == "pct" and hasattr(self, "pct_format_combo"):
            self.settings["pct_format"] = int(self.pct_format_combo.get_active_id() or 0)

        rgba = self.color_btn.get_rgba()
        self.settings[f"color_{self.key_prefix}"] = PipeWireActionBase.rgba_to_hex(rgba)

        desc = self.font_btn.get_font_desc()
        self.settings[f"font_desc_{self.key_prefix}"] = desc.to_string()

        align = "center"
        if self.btn_left.get_active(): align = "left"
        elif self.btn_right.get_active(): align = "right"
        self.settings[f"align_{self.key_prefix}"] = align

        self.settings[f"outline_width_{self.key_prefix}"] = int(self.out_spin.get_value())

        rgba_out = self.out_color_btn.get_rgba()
        self.settings[f"outline_color_{self.key_prefix}"] = PipeWireActionBase.rgba_to_hex(rgba_out)
        
        self.save_or_del(f"pos_x_{self.key_prefix}", int(self.x_spin.get_value()))
        self.save_or_del(f"pos_y_{self.key_prefix}", int(self.y_spin.get_value()))
        self.save_or_del(f"width_{self.key_prefix}", int(self.w_spin.get_value()))

        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            if "vol_a" in self.parent.last_state: self.parent.last_state["vol_a"] = -1
            if "vol_b" in self.parent.last_state: self.parent.last_state["vol_b"] = -1
        self.parent.draw_image()

class CustomIconRow(UIComponentsBase):
    def __init__(self, settings_dict, parent_action, suffix=""):
        super().__init__(settings_dict, parent_action)
        self.suffix = f"_{suffix}" if suffix else ""
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        
        label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.icon.format"), xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
        # Row 0: Show Icon toggle
        self.toggle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=10)
        lbl_show = Gtk.Label(label=self.parent.plugin_base.lm.get("config.show_icon.title", "Show Icon"), xalign=0, hexpand=True)
        self.switch_show = Gtk.Switch()
        self.switch_show.set_valign(Gtk.Align.CENTER)
        self.switch_show.set_active(self.settings.get(f"show_icon{self.suffix}", True))
        self.switch_show.connect("notify::active", self.on_show_changed)
        
        self.toggle_box.append(lbl_show)
        self.toggle_box.append(self.switch_show)
        self.main_box.append(self.toggle_box)
        
        # Row 1: File Button
        self.file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.file_box)
        
        self.btn_file = Gtk.Button(label=self.parent.plugin_base.lm.get("config.icon.select"))
        self.btn_file.connect("clicked", self.on_btn_file_clicked)
        self.file_box.append(self.btn_file)
        
        self.btn_clear = Gtk.Button(icon_name="user-trash-symbolic", margin_start=5)
        self.btn_clear.connect("clicked", self.on_btn_clear_clicked)
        self.file_box.append(self.btn_clear)
        
        self.lbl_file = Gtk.Label(label=self.settings.get(f"icon_path{self.suffix}", self.parent.plugin_base.lm.get("config.icon.default")), margin_start=10)
        self.lbl_file.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_file.set_max_width_chars(20)
        self.file_box.append(self.lbl_file)
        self.wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.wh_box)
        


        h_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.height"), margin_end=5)
        self.h_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.h_spin.set_value(self.settings.get(f"icon_height{self.suffix}", self.defaults_calc.get(f"icon_height{self.suffix}", 48)))
        self.h_spin.connect("value-changed", self.on_change)
        
        btn_reset_h = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Alto", css_classes=["circular", "flat"])
        btn_reset_h.connect("clicked", lambda *a: self.reset_val(f"icon_height{self.suffix}", self.h_spin))
        
        self.wh_box.append(h_lbl)
        self.wh_box.append(self.h_spin)
        self.wh_box.append(btn_reset_h)
        
        # Row 3: Outline
        self.out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.out_box)
        
        out_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.width", "Outline Width"), margin_end=5)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get(f"icon_out_width{self.suffix}", 1))
        self.out_spin.connect("value-changed", self.on_change)
        
        out_color_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.color", "Outline Color"), margin_start=15, margin_end=5)
        self.out_color_btn = self.create_color_button(f"icon_out_color{self.suffix}", "#000000", self.on_change)
        
        self.out_box.append(out_lbl)
        self.out_box.append(self.out_spin)
        self.out_box.append(out_color_lbl)
        self.out_box.append(self.out_color_btn)

        # Row 4: X, Y
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        
        x_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.x"), margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.x_spin.set_value(self.settings.get(f"icon_x{self.suffix}", self.defaults_calc.get(f"icon_x{self.suffix}", 0)))
        self.x_spin.connect("value-changed", self.on_change)
        
        btn_reset_x = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer X", css_classes=["circular", "flat"])
        btn_reset_x.connect("clicked", lambda *a: self.reset_val(f"icon_x{self.suffix}", self.x_spin))
        
        y_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.y"), margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.y_spin.set_value(self.settings.get(f"icon_y{self.suffix}", self.defaults_calc.get(f"icon_y{self.suffix}", 0)))
        self.y_spin.connect("value-changed", self.on_change)
        
        btn_reset_y = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Y", css_classes=["circular", "flat"])
        btn_reset_y.connect("clicked", lambda *a: self.reset_val(f"icon_y{self.suffix}", self.y_spin))
        
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(btn_reset_x)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        self.xy_box.append(btn_reset_y)
        
        self._updating = False


    def on_btn_file_clicked(self, btn):
        media_path = self.settings.get(f"icon_path{self.suffix}", "")
        GLib.idle_add(gl.app.let_user_select_asset, media_path, self.on_media_selected)
        
    def on_btn_clear_clicked(self, btn):
        self.settings[f"icon_path{self.suffix}"] = ""
        self.lbl_file.set_label(self.parent.plugin_base.lm.get("config.icon.default"))
        self.parent.set_settings(self.settings)
        self.parent.draw_image()

    def on_media_selected(self, path):
        if path is not None:
            self.settings[f"icon_path{self.suffix}"] = path
            self.lbl_file.set_label(path)
            self.parent.set_settings(self.settings)
            self.parent.draw_image()
            
    def on_show_changed(self, switch, pspec):
        self.settings[f"show_icon{self.suffix}"] = switch.get_active()
        self.parent.set_settings(self.settings)
        self.parent.draw_image()
            
    def on_change(self, *args):
        if getattr(self, "_updating", False): return
        
        self.save_or_del(f"icon_height{self.suffix}", int(self.h_spin.get_value()))
        self.save_or_del(f"icon_x{self.suffix}", int(self.x_spin.get_value()))
        self.save_or_del(f"icon_y{self.suffix}", int(self.y_spin.get_value()))
        self.save_or_del(f"icon_out_width{self.suffix}", int(self.out_spin.get_value()))
        
        rgba_out = self.out_color_btn.get_rgba()
        self.settings[f"icon_out_color{self.suffix}"] = PipeWireActionBase.rgba_to_hex(rgba_out)
        
        self.parent.set_settings(self.settings)
        self.parent.draw_image()

class CustomBarRow(UIComponentsBase):
    def __init__(self, settings_dict, parent_action):
        super().__init__(settings_dict, parent_action)
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        
        label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.format"), xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
        lbl_warn = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.style.warning", "The '2 Bars' mode will only appear when the mixer mode is enabled."), xalign=0, margin_bottom=6)
        lbl_warn.add_css_class("dim-label")
        lbl_warn.set_wrap(True)
        self.main_box.append(lbl_warn)
        
        # Style Box
        self.style_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=6)
        self.main_box.append(self.style_box)
        
        style_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.style", "Style"), xalign=0, margin_end=10)
        self.style_box.append(style_lbl)
        
        self.style_combo = Gtk.ComboBoxText(hexpand=True)
        self.style_combo.append("0", self.parent.plugin_base.lm.get("config.bar.style.2bars", "2 Bars"))
        self.style_combo.append("1", self.parent.plugin_base.lm.get("config.bar.style.1bar", "1 Bar"))
        self.style_combo.append("2", self.parent.plugin_base.lm.get("config.bar.style.1bar_tri", "1 Bar with Triangle"))
        self.style_combo.append("3", self.parent.plugin_base.lm.get("config.bar.style.1bar_line", "1 Bar with Line"))
        self.style_combo.set_active_id(str(self.settings.get("bar_style", 0)))
        self.style_combo.connect("changed", self.on_change)
        self.style_box.append(self.style_combo)
        
        # Color Box (Base)
        self.color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.color_box)
        
        color_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.color"), margin_end=5)
        self.color_box.append(color_lbl)
        
        self.color_btn = self.create_color_button("bar_color", "#FFFFFF", self.on_change)
        self.color_box.append(self.color_btn)
        
        # Background Color
        bg_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.background"), margin_start=15, margin_end=5)
        self.color_box.append(bg_lbl)
        self.bg_color_btn = self.create_color_button("bar_bg_color", "#424242", self.on_change)
        self.color_box.append(self.bg_color_btn)

        # Over-limit Color
        over_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.over100"), margin_start=15, margin_end=5)
        self.color_box.append(over_lbl)
        self.over_color_btn = self.create_color_button("bar_over_color", "#ff4b4b", self.on_change)
        self.color_box.append(self.over_color_btn)

        # Colors Box 2
        self.color_box_2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.color_box_2)
        
        ind_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.ind_color", "Indicator Color"), margin_end=5)
        self.color_box_2.append(ind_lbl)
        
        self.ind_color_btn = self.create_color_button("bar_ind_color", "#FFFFFF", self.on_change)
        self.color_box_2.append(self.ind_color_btn)
        
        neu_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.neu_color", "Neutral Color"), margin_start=15, margin_end=5)
        self.color_box_2.append(neu_lbl)
        
        self.neu_color_btn = self.create_color_button("bar_neu_color", "#808080", self.on_change)
        self.color_box_2.append(self.neu_color_btn)

        # Invert Box
        self.invert_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.invert_box)
        
        invert_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.invert", "Invert Bar"), xalign=0, margin_end=10, hexpand=True)
        self.invert_box.append(invert_lbl)
        
        self.sw_invert = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_invert.set_active(self.settings.get("bar_invert", False))
        self.sw_invert.connect("notify::active", self.on_change)
        self.invert_box.append(self.sw_invert)

        # Outline Box
        self.out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.out_box)
        
        out_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.width", "Ancho del contorno"), margin_end=5)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get("bar_out_width", self.defaults_calc.get("bar_out_width", 1)))
        self.out_spin.connect("value-changed", self.on_change)
        
        out_color_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.color", "Color del contorno"), margin_start=15, margin_end=5)
        self.out_color_btn = self.create_color_button("bar_out_color", self.defaults_calc.get("bar_out_color", "#000000"), self.on_change)
        self.out_box.append(out_lbl)
        self.out_box.append(self.out_spin)
        self.out_box.append(out_color_lbl)
        self.out_box.append(self.out_color_btn)

        # Row W, H
        self.wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.wh_box)
            
        self._updating = True
        
        w_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.width"), margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.w_spin.set_value(self.settings.get("bar_width", self.defaults_calc.get("bar_width", 0)))
        self.w_spin.connect("value-changed", self.on_change)
        btn_reset_w = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Ancho", css_classes=["circular", "flat"])
        btn_reset_w.connect("clicked", lambda *a: self.reset_val("bar_width", self.w_spin))
        
        h_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.height"), margin_start=10, margin_end=5)
        self.h_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.h_spin.set_value(self.settings.get("bar_height", self.defaults_calc.get("bar_height", 0)))
        self.h_spin.connect("value-changed", self.on_change)
        btn_reset_h = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Alto", css_classes=["circular", "flat"])
        btn_reset_h.connect("clicked", lambda *a: self.reset_val("bar_height", self.h_spin))
        self.wh_box.append(w_lbl)
        self.wh_box.append(self.w_spin)
        self.wh_box.append(btn_reset_w)
        self.wh_box.append(h_lbl)
        self.wh_box.append(self.h_spin)
        self.wh_box.append(btn_reset_h)
        
        # Row X, Y
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        
        x_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.x"), margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.x_spin.set_value(self.settings.get("bar_x", self.defaults_calc.get("bar_x", 0)))
        self.x_spin.connect("value-changed", self.on_change)
        btn_reset_x = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer X", css_classes=["circular", "flat"])
        btn_reset_x.connect("clicked", lambda *a: self.reset_val("bar_x", self.x_spin))
        
        y_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.y"), margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-2000, 2000, 1)
        self.y_spin.set_value(self.settings.get("bar_y", self.defaults_calc.get("bar_y", 0)))
        self.y_spin.connect("value-changed", self.on_change)
        btn_reset_y = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Y", css_classes=["circular", "flat"])
        btn_reset_y.connect("clicked", lambda *a: self.reset_val("bar_y", self.y_spin))
        
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(btn_reset_x)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        self.xy_box.append(btn_reset_y)

        # Row Radius
        self.rad_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.rad_box)
        
        rad_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.radius"), margin_end=5)
        self.rad_spin = Gtk.SpinButton.new_with_range(-2000, 100, 1)
        self.rad_spin.set_value(self.settings.get("bar_radius", self.defaults_calc.get("bar_radius", 0)))
        self.rad_spin.connect("value-changed", self.on_change)
        btn_reset_rad = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Restablecer Radio", css_classes=["circular", "flat"])
        btn_reset_rad.connect("clicked", lambda *a: self.reset_val("bar_radius", self.rad_spin))
        
        self.rad_box.append(rad_lbl)
        self.rad_box.append(self.rad_spin)
        self.rad_box.append(btn_reset_rad)
        
        self._updating = False

    def reset_val(self, key, spin):
        if key in self.settings:
            del self.settings[key]
        self._updating = True
        spin.set_value(self.defaults_calc.get(key, 0))
        self._updating = False
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            if "vol_a" in self.parent.last_state: self.parent.last_state["vol_a"] = -1
            if "vol_b" in self.parent.last_state: self.parent.last_state["vol_b"] = -1
        self.parent.draw_image()

    def on_change(self, *args):
        if getattr(self, "_updating", False): return
        
        if hasattr(self, "style_combo"):
            self.settings["bar_style"] = int(self.style_combo.get_active_id() or 0)
            
        rgba = self.color_btn.get_rgba()
        self.settings["bar_color"] = PipeWireActionBase.rgba_to_hex(rgba)
        
        rgba_bg = self.bg_color_btn.get_rgba()
        self.settings["bar_bg_color"] = PipeWireActionBase.rgba_to_hex(rgba_bg)

        rgba_over = self.over_color_btn.get_rgba()
        self.settings["bar_over_color"] = PipeWireActionBase.rgba_to_hex(rgba_over)
        
        self.settings["bar_invert"] = self.sw_invert.get_active()

        if hasattr(self, "ind_color_btn"):
            rgba_ind = self.ind_color_btn.get_rgba()
            self.settings["bar_ind_color"] = PipeWireActionBase.rgba_to_hex(rgba_ind)
            
        if hasattr(self, "neu_color_btn"):
            rgba_neu = self.neu_color_btn.get_rgba()
            self.settings["bar_neu_color"] = PipeWireActionBase.rgba_to_hex(rgba_neu)

        rgba_out = self.out_color_btn.get_rgba()
        self.settings["bar_out_color"] = PipeWireActionBase.rgba_to_hex(rgba_out)
        self.settings["bar_out_width"] = int(self.out_spin.get_value())

        self.save_or_del("bar_width", int(self.w_spin.get_value()))
        self.save_or_del("bar_height", int(self.h_spin.get_value()))
        self.save_or_del("bar_x", int(self.x_spin.get_value()))
        self.save_or_del("bar_y", int(self.y_spin.get_value()))
        self.save_or_del("bar_radius", int(self.rad_spin.get_value()))
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            if "vol_a" in self.parent.last_state: self.parent.last_state["vol_a"] = -1
            if "vol_b" in self.parent.last_state: self.parent.last_state["vol_b"] = -1
        self.parent.draw_image()

class DeviceConfigGroup(Adw.PreferencesGroup):
    def __init__(self, parent_action, suffix=""):
        super().__init__()
        self.parent_action = parent_action
        self.suffix_str = f"_{suffix}" if suffix else ""
        self.suffix = suffix
        
        lm = self.parent_action.plugin_base.lm
        settings = self.parent_action.get_settings()
        
        # Type
        self.type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        type_lbl = Gtk.Label(label=lm.get("config.type.title", "Device Type"), xalign=0, margin_end=10, hexpand=True)
        self.type_combo = Gtk.ComboBoxText(hexpand=False)
        self.type_combo.append("sink", lm.get("config.type.sink", "Sink (Output)"))
        self.type_combo.append("source", lm.get("config.type.source", "Source (Input)"))
        self.type_combo.append("application", lm.get("config.type.application", "Application"))
        
        device_type = settings.get(f"device_type{self.suffix_str}", "sink")
        self.type_combo.set_active_id(device_type)
        self.type_combo.connect("changed", self.on_type_changed)
        
        self.type_box.append(type_lbl)
        self.type_box.append(self.type_combo)
        self.add(self.type_box)
        
        # Device
        self.device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        device_lbl = Gtk.Label(label=lm.get("config.device.title", "Device"), xalign=0, margin_end=10, hexpand=True)
        self.device_combo = Gtk.ComboBoxText(hexpand=False)
        self.device_combo.set_size_request(200, -1)
        self.device_combo.connect("changed", self.on_device_changed)
        
        self.device_box.append(device_lbl)
        self.device_box.append(self.device_combo)
        self.add(self.device_box)
        
        # Auto-index
        self.auto_index_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        ai_lbl = Gtk.Label(label=lm.get("config.auto_index.title", "Auto-index #"), xalign=0, margin_end=10, hexpand=True)
        ai_lbl.set_tooltip_text(lm.get("config.auto_index.subtitle", "0 = disabled. Used for apps"))
        self.auto_index_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=settings.get(f"auto_index{self.suffix_str}", 0), lower=0, upper=10, step_increment=1))
        self.auto_index_spin.connect("value-changed", self.on_auto_index_changed)
        self.auto_index_box.append(ai_lbl)
        self.auto_index_box.append(self.auto_index_spin)
        self.add(self.auto_index_box)

        # Limit
        self.limit_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=5)
        lim_lbl = Gtk.Label(label=lm.get("config.limit.title", "Volume Limit (%)"), xalign=0, margin_end=10, hexpand=True)
        self.limit_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=min(150.0, float(settings.get(f"volume_limit{self.suffix_str}", 100))), lower=0, upper=150, step_increment=5))
        self.limit_spin.connect("value-changed", self.on_limit_changed)
        self.limit_box.append(lim_lbl)
        self.limit_box.append(self.limit_spin)
        self.add(self.limit_box)
        
        self.update_device_model()

    def update_device_model(self):
        settings = self.parent_action.get_settings()
        device_type = settings.get(f"device_type{self.suffix_str}", "sink")
        selected_target = settings.get(f"device_name{self.suffix_str}", "")
        
        pulse = self.parent_action.get_pulse()
        if not pulse:
            return
            
        self.device_combo.handler_block_by_func(self.on_device_changed)
        self.device_combo.remove_all()
        
        if device_type == "application":
            self.device_combo.append("Auto", self.parent_action.plugin_base.lm.get("config.device.auto", "Auto"))
            default_id = "Auto"
        else:
            self.device_combo.append("default", self.parent_action.plugin_base.lm.get("config.device.default", "Default"))
            default_id = "default"
        
        items = []
        try:
            with self.parent_action.plugin_base.pulse_lock:
                if device_type == "sink":
                    items = pulse.sink_list()
                elif device_type == "source":
                    items = [d for d in pulse.source_list() if not getattr(d, 'name', '').endswith('.monitor') and not getattr(d, 'description', '').startswith('Monitor of')]
                else:
                    items = self.parent_action.get_active_applications()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Error updating device model: %s", e)

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
                desc = getattr(dev, 'description', getattr(dev, 'name', 'Unknown'))
                name = getattr(dev, 'name', '')
                self.device_combo.append(name, desc)
                if selected_target == name:
                    selected_id = name
                    found = True

        if selected_target and not found and selected_target != default_id:
            lm = self.parent_action.plugin_base.lm
            not_available_text = lm.get("config.device.not_available", "Not Available")
            self.device_combo.append(selected_target, f"{selected_target} ({not_available_text})")
            selected_id = selected_target

        self.device_combo.set_active_id(selected_id)
        self.device_combo.handler_unblock_by_func(self.on_device_changed)
        
        if device_type == "application":
            self.auto_index_box.set_visible(True)
        else:
            self.auto_index_box.set_visible(False)

    def on_type_changed(self, combo):
        settings = self.parent_action.get_settings()
        t = combo.get_active_id() or "sink"
        
        settings[f"device_type{self.suffix_str}"] = t
        settings[f"device_name{self.suffix_str}"] = ""
        self.parent_action.set_settings(settings)
        self.update_device_model()
        self.parent_action.draw_image()

    def on_device_changed(self, combo):
        settings = self.parent_action.get_settings()
        active_id = combo.get_active_id()
        
        if active_id == "default" or not active_id:
            settings[f"device_name{self.suffix_str}"] = ""
        else:
            settings[f"device_name{self.suffix_str}"] = active_id
            
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()

    def on_auto_index_changed(self, spin):
        settings = self.parent_action.get_settings()
        settings[f"auto_index{self.suffix_str}"] = int(spin.get_value())
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()

    def on_limit_changed(self, spin):
        settings = self.parent_action.get_settings()
        settings[f"volume_limit{self.suffix_str}"] = int(spin.get_value())
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()

class VolumeMonitorConfigRow(UIComponentsBase):
    def __init__(self, settings, parent):
        super().__init__(settings, parent)
        
        lm = self.parent.plugin_base.lm
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin_top=5, margin_bottom=5, margin_start=10, margin_end=10)
        self.set_child(self.main_box)
        
        # 1. Enable switch
        box_enable = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_bottom=10)
        lbl_enable = Gtk.Label(label=lm.get("config.monitor.enable", "Enable Volume Monitor"), xalign=0, hexpand=True, css_classes=["bold"])
        self.sw_enable = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_enable.set_active(self.settings.get("monitor_enabled", False))
        self.sw_enable.connect("notify::active", self.on_enable_change)
        box_enable.append(lbl_enable)
        box_enable.append(self.sw_enable)
        self.main_box.append(box_enable)
        
        self.lbl_warning = Gtk.Label(label=lm.get("config.monitor.app_warning", "Monitor does not support individual applications. It will monitor the entire output device instead."), xalign=0, wrap=True, css_classes=["dim-label"])
        self.lbl_warning.set_margin_bottom(10)
        self.main_box.append(self.lbl_warning)
        
        self.settings_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.main_box.append(self.settings_container)
        
        # 2. Bar Mode
        box_bar_mode = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        lbl_bar_mode = Gtk.Label(label=lm.get("config.monitor.bar_mode", "Bar Mode"), xalign=0, margin_end=10)
        self.cb_bar_mode = Gtk.ComboBoxText(hexpand=True)
        self.cb_bar_mode.append("0", lm.get("config.monitor.bar_single", "Single Bar"))
        self.cb_bar_mode.append("1", lm.get("config.monitor.bar_dual", "Dual Bar (Stereo)"))
        self.cb_bar_mode.set_active_id(str(self.settings.get("monitor_bar_mode", 0)))
        self.cb_bar_mode.connect("changed", self.on_change)
        box_bar_mode.append(lbl_bar_mode)
        box_bar_mode.append(self.cb_bar_mode)
        self.settings_container.append(box_bar_mode)
        
        # 3. Color Mode
        box_color_mode = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        lbl_color_mode = Gtk.Label(label=lm.get("config.monitor.color_mode", "Color Mode"), xalign=0, margin_end=10)
        self.cb_color_mode = Gtk.ComboBoxText(hexpand=True)
        self.cb_color_mode.append("0", lm.get("config.monitor.color_solid", "Solid"))
        self.cb_color_mode.append("1", lm.get("config.monitor.color_tricolor", "Tricolor"))
        self.cb_color_mode.append("2", lm.get("config.monitor.color_gradient", "Gradient"))
        self.cb_color_mode.set_active_id(str(self.settings.get("monitor_color_mode", 0)))
        self.cb_color_mode.connect("changed", self.on_color_mode_change)
        box_color_mode.append(lbl_color_mode)
        box_color_mode.append(self.cb_color_mode)
        self.settings_container.append(box_color_mode)
        
        self.box_solid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.box_tricolor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        self.box_gradient = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        self.settings_container.append(self.box_solid)
        self.settings_container.append(self.box_tricolor)
        self.settings_container.append(self.box_gradient)
        
        # Solid
        lbl_solid = Gtk.Label(label=lm.get("config.monitor.color", "Color"), xalign=0, hexpand=True)
        self.btn_solid = self.create_color_button("monitor_color_solid", "#ffffff", self.on_change)
        self.box_solid.append(lbl_solid)
        self.box_solid.append(self.btn_solid)
        
        # Tricolor
        self.btn_tri_low = self.create_color_button("monitor_color_low", "#00ff00", self.on_change)
        self.btn_tri_mid = self.create_color_button("monitor_color_mid", "#ffff00", self.on_change)
        self.btn_tri_high = self.create_color_button("monitor_color_high", "#ff0000", self.on_change)
        
        def add_tri_row(label, btn, thresh_key=None, def_thresh=0):
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            r.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            if thresh_key:
                r.append(Gtk.Label(label=lm.get("config.monitor.threshold", "Threshold (dB)"), margin_start=10, margin_end=5))
                spin = Gtk.SpinButton.new_with_range(-100, 0, 1)
                spin.set_value(self.settings.get(thresh_key, def_thresh))
                spin.connect("value-changed", self.on_change)
                setattr(self, f"spin_{thresh_key}", spin)
                r.append(spin)
            r.append(btn)
            self.box_tricolor.append(r)
            
        add_tri_row(lm.get("config.monitor.color_low", "Low"), self.btn_tri_low)
        add_tri_row(lm.get("config.monitor.color_mid", "Mid"), self.btn_tri_mid, "monitor_threshold_mid", -20)
        add_tri_row(lm.get("config.monitor.color_high", "High"), self.btn_tri_high, "monitor_threshold_high", -9)
        
        # Gradient
        bgs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        bgs.append(Gtk.Label(label=lm.get("config.monitor.gradient_colors", "Colors"), xalign=0, margin_end=10))
        self.cb_grad_stops = Gtk.ComboBoxText(hexpand=True)
        for i in range(2, 7):
            self.cb_grad_stops.append(str(i), str(i))
        self.cb_grad_stops.set_active_id(str(self.settings.get("monitor_gradient_stops", 3)))
        self.cb_grad_stops.connect("changed", self.on_grad_stops_change)
        bgs.append(self.cb_grad_stops)
        self.box_gradient.append(bgs)
        
        bgc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, hexpand=True)
        self.box_gradient.append(bgc)
        
        default_colors = ["#00ff00", "#ffff00", "#ff0000", "#00ffff", "#ffff00", "#ff00ff"]
        self.grad_btns = []
        for i in range(6):
            btn = self.create_color_button(f"monitor_gradient_{i+1}", default_colors[i], self.on_change)
            self.grad_btns.append(btn)
            setattr(self, f"btn_g{i+1}", btn)
            bgc.append(btn)
            
        import cairo
        self.preview_area = Gtk.DrawingArea()
        self.preview_area.set_size_request(-1, 20)
        self.preview_area.set_draw_func(self.on_draw_preview)
        self.box_gradient.append(self.preview_area)
        
        # 4. Timing
        bfps = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=5)
        bfps.append(Gtk.Label(label=lm.get("config.monitor.fps", "Update Rate (FPS)"), xalign=0, margin_end=10))
        self.cb_fps = Gtk.ComboBoxText(hexpand=True)
        for f in [1, 2, 5, 10, 15, 20]: self.cb_fps.append(str(f), str(f))
        self.cb_fps.set_active_id(str(self.settings.get("monitor_fps", 10)))
        self.cb_fps.connect("changed", self.on_change)
        bfps.append(self.cb_fps)
        self.settings_container.append(bfps)
        
        lbl_fps_warn = Gtk.Label(label=lm.get("config.monitor.fps.warning", "A high FPS rate increases CPU usage"), 
                                 xalign=0, margin_top=2, margin_bottom=5)
        lbl_fps_warn.add_css_class("dim-label")
        lbl_fps_warn.set_wrap(True)
        self.settings_container.append(lbl_fps_warn)
        
        bdel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        bdel.append(Gtk.Label(label=lm.get("config.monitor.delay", "Switch Delay (s)"), xalign=0, hexpand=True))
        self.spin_delay = Gtk.SpinButton.new_with_range(1, 60, 1)
        self.spin_delay.set_value(self.settings.get("monitor_delay", 5))
        self.spin_delay.connect("value-changed", self.on_change)
        bdel.append(self.spin_delay)
        self.settings_container.append(bdel)
        
        # 5. Show dB
        bdb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        bdb.append(Gtk.Label(label=lm.get("config.monitor.show_db", "Show Decibels"), xalign=0, hexpand=True))
        self.sw_db = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_db.set_active(self.settings.get("monitor_show_db", False))
        self.sw_db.connect("notify::active", self.on_change)
        bdb.append(self.sw_db)
        self.settings_container.append(bdb)
        
        # 6. Show RMS (Average indicator)
        brms_sw = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        brms_sw.append(Gtk.Label(label=lm.get("config.monitor.show_rms", "Show Average Indicator"), xalign=0, hexpand=True))
        
        self.sw_rms = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_rms.set_active(self.settings.get("monitor_show_rms", False))
        self.sw_rms.connect("notify::active", self.on_rms_change)
        brms_sw.append(self.sw_rms)
        self.settings_container.append(brms_sw)
        
        self.brms_options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        
        rms_color_lbl = Gtk.Label(label=lm.get("config.monitor.rms_color", "Indicator Color"), margin_end=5)
        self.btn_rms_color = self.create_color_button("monitor_rms_color", "#FFFFFF", self.on_change)
        
        self.brms_options.append(rms_color_lbl)
        self.brms_options.append(self.btn_rms_color)
        
        self.brms_options2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        
        rms_out_w_lbl = Gtk.Label(label=lm.get("config.outline.width", "Outline Width"), margin_end=5)
        self.spin_rms_out = Gtk.SpinButton.new_with_range(0, 10, 1)
        self.spin_rms_out.set_value(self.settings.get("monitor_rms_out_width", 1.0))
        self.spin_rms_out.connect("value-changed", self.on_change)
        
        rms_out_color_lbl = Gtk.Label(label=lm.get("config.outline.color", "Outline Color"), margin_start=15, margin_end=5)
        self.btn_rms_out_color = self.create_color_button("monitor_rms_out_color", "#000000", self.on_change)
        
        self.brms_options2.append(rms_out_w_lbl)
        self.brms_options2.append(self.spin_rms_out)
        self.brms_options2.append(rms_out_color_lbl)
        self.brms_options2.append(self.btn_rms_out_color)
        
        self.settings_container.append(self.brms_options)
        self.settings_container.append(self.brms_options2)
        
        # 7. Invert Bar
        binv = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=5)
        binv.append(Gtk.Label(label=lm.get("config.monitor.invert", "Invert Bar"), xalign=0, hexpand=True))
        
        self.sw_mon_inv = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_mon_inv.set_active(self.settings.get("monitor_invert", False))
        self.sw_mon_inv.connect("notify::active", self.on_change)
        binv.append(self.sw_mon_inv)
        self.settings_container.append(binv)
        
        self.update_visibility()
        
    def on_enable_change(self, *args):
        self.on_change()
        self.update_visibility()

    def on_color_mode_change(self, *args):
        self.on_change()
        self.update_visibility()
        
    def on_rms_change(self, *args):
        self.on_change()
        self.update_visibility()
        
    def on_grad_stops_change(self, *args):
        self.on_change()
        stops = int(self.cb_grad_stops.get_active_id() or 3)
        for i in range(6):
            self.grad_btns[i].set_visible(i < stops)
        self.preview_area.queue_draw()
        
    def update_visibility(self):
        self.settings_container.set_sensitive(self.sw_enable.get_active())
        cmode = int(self.cb_color_mode.get_active_id() or 0)
        self.box_solid.set_visible(cmode == 0)
        self.box_tricolor.set_visible(cmode == 1)
        self.box_gradient.set_visible(cmode == 2)
        
        if hasattr(self, "brms_options"):
            self.brms_options.set_sensitive(self.sw_rms.get_active())
            if hasattr(self, "brms_options2"):
                self.brms_options2.set_sensitive(self.sw_rms.get_active())
        if cmode == 2: self.on_grad_stops_change()
            
    def on_draw_preview(self, area, cr, width, height):
        import cairo
        stops = int(self.cb_grad_stops.get_active_id() or 3)
        lg = cairo.LinearGradient(0, 0, width, 0)
        
        for i in range(stops):
            btn = self.grad_btns[i]
            c = PipeWireActionBase._parse_color(None, PipeWireActionBase.rgba_to_hex(btn.get_rgba()))
            lg.add_color_stop_rgba(i / (stops - 1), *c)
            
        cr.set_source(lg)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        
    def on_change(self, *args):
        if self._updating: return
        self.settings["monitor_enabled"] = self.sw_enable.get_active()
        self.settings["monitor_bar_mode"] = int(self.cb_bar_mode.get_active_id() or 0)
        self.settings["monitor_color_mode"] = int(self.cb_color_mode.get_active_id() or 0)
        self.settings["monitor_color_solid"] = PipeWireActionBase.rgba_to_hex(self.btn_solid.get_rgba())
        self.settings["monitor_color_low"] = PipeWireActionBase.rgba_to_hex(self.btn_tri_low.get_rgba())
        self.settings["monitor_color_mid"] = PipeWireActionBase.rgba_to_hex(self.btn_tri_mid.get_rgba())
        self.settings["monitor_color_high"] = PipeWireActionBase.rgba_to_hex(self.btn_tri_high.get_rgba())
        self.settings["monitor_threshold_mid"] = int(self.spin_monitor_threshold_mid.get_value()) if hasattr(self, "spin_monitor_threshold_mid") else -20
        self.settings["monitor_threshold_high"] = int(self.spin_monitor_threshold_high.get_value()) if hasattr(self, "spin_monitor_threshold_high") else -9
        self.settings["monitor_gradient_stops"] = int(self.cb_grad_stops.get_active_id() or 3)
        for i in range(6):
            self.settings[f"monitor_gradient_{i+1}"] = PipeWireActionBase.rgba_to_hex(self.grad_btns[i].get_rgba())
        self.settings["monitor_fps"] = int(self.cb_fps.get_active_id() or 10)
        self.settings["monitor_delay"] = int(self.spin_delay.get_value())
        self.settings["monitor_show_db"] = self.sw_db.get_active()
        self.settings["monitor_show_rms"] = self.sw_rms.get_active()
        self.settings["monitor_rms_color"] = PipeWireActionBase.rgba_to_hex(self.btn_rms_color.get_rgba())
        self.settings["monitor_rms_out_color"] = PipeWireActionBase.rgba_to_hex(self.btn_rms_out_color.get_rgba())
        self.settings["monitor_rms_out_width"] = float(self.spin_rms_out.get_value())
        self.settings["monitor_invert"] = getattr(self, "sw_mon_inv", self.sw_rms).get_active() if hasattr(self, 'sw_mon_inv') else False
        if hasattr(self, "preview_area"):
            self.preview_area.queue_draw()
        self.parent.set_settings(self.settings)
        self.parent.draw_image()
