import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Adw, GLib, Gdk, Pango
import globals as gl

class CustomLabelRow(Adw.PreferencesRow):
    def __init__(self, title_text, settings_dict, key_prefix, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.key_prefix = key_prefix
        self.parent = parent_action

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

        self.color_btn = Gtk.ColorButton()
        if f"color_{key_prefix}" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings[f"color_{key_prefix}"])
            self.color_btn.set_rgba(c)
        else:
            self.color_btn.set_rgba(def_color_rgba)
        self.color_btn.connect("color-set", self.on_change)
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

        self.out_color_btn = Gtk.ColorButton()
        if f"outline_color_{key_prefix}" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings[f"outline_color_{key_prefix}"])
            self.out_color_btn.set_rgba(c)
        else:
            self.out_color_btn.set_rgba(def_out_rgba)
        self.out_color_btn.connect("color-set", self.on_change)
        self.out_box.append(self.out_color_btn)

        # X, Y
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        
        self.defaults_calc = {}
        if hasattr(self.parent, "get_calculated_defaults"):
            self.defaults_calc = self.parent.get_calculated_defaults()

        self._updating = True

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
        
        self._updating = False

    def reset_val(self, key, spin):
        if key in self.settings:
            del self.settings[key]
        self._updating = True
        spin.set_value(self.defaults_calc.get(key, 0))
        self._updating = False
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            self.parent.last_state["vol"] = -1
        self.parent.draw_image()


    def on_change(self, *args):
        if getattr(self, "_updating", False): return
        if self.entry:
            self.settings[f"text_{self.key_prefix}"] = self.entry.get_text()

        rgba = self.color_btn.get_rgba()
        self.settings[f"color_{self.key_prefix}"] = f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"

        desc = self.font_btn.get_font_desc()
        self.settings[f"font_desc_{self.key_prefix}"] = desc.to_string()

        align = "center"
        if self.btn_left.get_active(): align = "left"
        elif self.btn_right.get_active(): align = "right"
        self.settings[f"align_{self.key_prefix}"] = align

        self.settings[f"outline_width_{self.key_prefix}"] = int(self.out_spin.get_value())

        rgba_out = self.out_color_btn.get_rgba()
        self.settings[f"outline_color_{self.key_prefix}"] = f"#{int(rgba_out.red*255):02x}{int(rgba_out.green*255):02x}{int(rgba_out.blue*255):02x}"
        
        def save_or_del(key, val):
            if val == self.defaults_calc.get(key, 0):
                self.settings.pop(key, None)
            else:
                self.settings[key] = val

        save_or_del(f"pos_x_{self.key_prefix}", int(self.x_spin.get_value()))
        save_or_del(f"pos_y_{self.key_prefix}", int(self.y_spin.get_value()))
        save_or_del(f"width_{self.key_prefix}", int(self.w_spin.get_value()))

        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            self.parent.last_state["vol"] = -1
        self.parent.draw_image()

class CustomIconRow(Adw.PreferencesRow):
    def __init__(self, settings_dict, parent_action, suffix=""):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        self.suffix = f"_{suffix}" if suffix else ""
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        
        label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.icon.format"), xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
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
        self.file_box.append(self.lbl_file)
        
        # Row 2: H
        self.wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.wh_box)
        
        self.defaults_calc = {}
        if hasattr(self.parent, "get_calculated_defaults"):
            self.defaults_calc = self.parent.get_calculated_defaults()

        self._updating = True

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
        
        out_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.width", "Ancho del contorno"), margin_end=5)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get(f"icon_out_width{self.suffix}", 1))
        self.out_spin.connect("value-changed", self.on_change)
        
        out_color_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.color", "Color del contorno"), margin_start=15, margin_end=5)
        self.out_color_btn = Gtk.ColorButton()
        if f"icon_out_color{self.suffix}" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings[f"icon_out_color{self.suffix}"])
            self.out_color_btn.set_rgba(c)
        else:
            c = Gdk.RGBA()
            c.parse("#000000")
            self.out_color_btn.set_rgba(c)
        self.out_color_btn.connect("color-set", self.on_change)
        
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

    def reset_val(self, key, spin):
        if key in self.settings:
            del self.settings[key]
        self._updating = True
        spin.set_value(self.defaults_calc.get(key, 0))
        self._updating = False
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            self.parent.last_state["vol"] = -1
        self.parent.draw_image()
        
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
            
    def on_change(self, *args):
        if getattr(self, "_updating", False): return
        
        def save_or_del(key, val):
            if val == self.defaults_calc.get(key):
                self.settings.pop(key, None)
            else:
                self.settings[key] = val

        save_or_del(f"icon_height{self.suffix}", int(self.h_spin.get_value()))
        save_or_del(f"icon_x{self.suffix}", int(self.x_spin.get_value()))
        save_or_del(f"icon_y{self.suffix}", int(self.y_spin.get_value()))
        save_or_del(f"icon_out_width{self.suffix}", int(self.out_spin.get_value()))
        
        rgba_out = self.out_color_btn.get_rgba()
        self.settings[f"icon_out_color{self.suffix}"] = f"#{int(rgba_out.red*255):02x}{int(rgba_out.green*255):02x}{int(rgba_out.blue*255):02x}"
        
        self.parent.set_settings(self.settings)
        self.parent.draw_image()

class CustomBarRow(Adw.PreferencesRow):
    def __init__(self, settings_dict, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        
        self.defaults_calc = {}
        if hasattr(self.parent, "get_calculated_defaults"):
            self.defaults_calc = self.parent.get_calculated_defaults()
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        
        label = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.format"), xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
        # Color Box (Base)
        self.color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.color_box)
        
        color_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.color"), margin_end=5)
        self.color_box.append(color_lbl)
        
        self.color_btn = Gtk.ColorButton()
        if "bar_color" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings["bar_color"])
            self.color_btn.set_rgba(c)
        else:
            c = Gdk.RGBA()
            c.parse("#FFFFFF")
            self.color_btn.set_rgba(c)
        self.color_btn.connect("color-set", self.on_change)
        self.color_box.append(self.color_btn)
        
        # Background Color
        bg_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.background"), margin_start=15, margin_end=5)
        self.color_box.append(bg_lbl)
        self.bg_color_btn = Gtk.ColorButton()
        if "bar_bg_color" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings["bar_bg_color"])
            self.bg_color_btn.set_rgba(c)
        else:
            c = Gdk.RGBA()
            c.parse("#424242")
            self.bg_color_btn.set_rgba(c)
        self.bg_color_btn.connect("color-set", self.on_change)
        self.color_box.append(self.bg_color_btn)

        # Over-limit Color
        over_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.bar.over100"), margin_start=15, margin_end=5)
        self.color_box.append(over_lbl)
        self.over_color_btn = Gtk.ColorButton()
        if "bar_over_color" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings["bar_over_color"])
            self.over_color_btn.set_rgba(c)
        else:
            c = Gdk.RGBA()
            c.parse("#ff4b4b")
            self.over_color_btn.set_rgba(c)
        self.over_color_btn.connect("color-set", self.on_change)
        self.color_box.append(self.over_color_btn)

        # Outline Box
        self.out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.out_box)
        
        out_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.width", "Ancho del contorno"), margin_end=5)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(self.settings.get("bar_out_width", self.defaults_calc.get("bar_out_width", 1)))
        self.out_spin.connect("value-changed", self.on_change)
        
        out_color_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.outline.color", "Color del contorno"), margin_start=15, margin_end=5)
        self.out_color_btn = Gtk.ColorButton()
        if "bar_out_color" in self.settings:
            c = Gdk.RGBA()
            c.parse(self.settings["bar_out_color"])
            self.out_color_btn.set_rgba(c)
        else:
            c = Gdk.RGBA()
            c.parse(self.defaults_calc.get("bar_out_color", "#000000"))
            self.out_color_btn.set_rgba(c)
        self.out_color_btn.connect("color-set", self.on_change)
        
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
            self.parent.last_state["vol"] = -1
        self.parent.draw_image()

    def on_change(self, *args):
        if getattr(self, "_updating", False): return
        rgba = self.color_btn.get_rgba()
        self.settings["bar_color"] = f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"
        
        rgba_bg = self.bg_color_btn.get_rgba()
        self.settings["bar_bg_color"] = f"#{int(rgba_bg.red*255):02x}{int(rgba_bg.green*255):02x}{int(rgba_bg.blue*255):02x}"

        rgba_over = self.over_color_btn.get_rgba()
        self.settings["bar_over_color"] = f"#{int(rgba_over.red*255):02x}{int(rgba_over.green*255):02x}{int(rgba_over.blue*255):02x}"

        rgba_out = self.out_color_btn.get_rgba()
        self.settings["bar_out_color"] = f"#{int(rgba_out.red*255):02x}{int(rgba_out.green*255):02x}{int(rgba_out.blue*255):02x}"
        self.settings["bar_out_width"] = int(self.out_spin.get_value())

        def save_or_del(key, val):
            if val == self.defaults_calc.get(key, 0):
                self.settings.pop(key, None)
            else:
                self.settings[key] = val

        save_or_del("bar_width", int(self.w_spin.get_value()))
        save_or_del("bar_height", int(self.h_spin.get_value()))
        save_or_del("bar_x", int(self.x_spin.get_value()))
        save_or_del("bar_y", int(self.y_spin.get_value()))
        save_or_del("bar_radius", int(self.rad_spin.get_value()))
        self.parent.set_settings(self.settings)
        if hasattr(self.parent, "last_state"):
            self.parent.last_state["vol"] = -1
        self.parent.draw_image()

class DeviceConfigGroup(Adw.PreferencesGroup):
    def __init__(self, parent_action, title, suffix=""):
        super().__init__(title=title)
        self.parent_action = parent_action
        self.suffix_str = f"_{suffix}" if suffix else ""
        self.suffix = suffix
        
        lm = self.parent_action.plugin_base.lm
        settings = self.parent_action.get_settings()
        
        self.type_row = Adw.ComboRow(title=lm.get("config.type.title", "Device Type"))
        type_model = Gtk.StringList()
        type_model.append("Sink (Output)")
        type_model.append("Source (Input)")
        type_model.append("Application")
        self.type_row.set_model(type_model)
        
        device_type = settings.get(f"device_type{self.suffix_str}", "sink")
        if device_type == "source":
            self.type_row.set_selected(1)
        elif device_type == "application":
            self.type_row.set_selected(2)
        else:
            self.type_row.set_selected(0)
            
        self.type_row.connect("notify::selected-item", self.on_type_changed)
        self.add(self.type_row)
        
        self.device_row = Adw.ComboRow(title=lm.get("config.device.title", "Device"))
        self.device_row.connect("notify::selected-item", self.on_device_changed)
        self.add(self.device_row)
        
        self.auto_index_row = Adw.SpinRow(
            title=lm.get("config.auto_index.title", "Auto-index #"),
            subtitle=lm.get("config.auto_index.subtitle", "0 = disabled. Used for apps"),
            adjustment=Gtk.Adjustment(value=settings.get(f"auto_index{self.suffix_str}", 0), lower=0, upper=10, step_increment=1)
        )
        self.auto_index_row.connect("notify::value", self.on_auto_index_changed)
        self.add(self.auto_index_row)

        self.limit_row = Adw.SpinRow(
            title=lm.get("config.volume_limit.title", "Volume Limit (%)"),
            adjustment=Gtk.Adjustment(value=min(150.0, float(settings.get(f"volume_limit{self.suffix_str}", 100))), lower=0, upper=150, step_increment=5)
        )
        self.limit_row.connect("notify::value", self.on_limit_changed)
        self.add(self.limit_row)
        
        self.show_icon_row = Adw.SwitchRow(
            title=lm.get("config.show_icon.title", "Mostrar Icono")
        )
        self.show_icon_row.set_active(settings.get(f"show_icon{self.suffix_str}", True))
        self.show_icon_row.connect("notify::active", self.on_show_icon_changed)
        self.add(self.show_icon_row)
        
        self.update_device_model()

    def update_device_model(self):
        settings = self.parent_action.get_settings()
        device_type = settings.get(f"device_type{self.suffix_str}", "sink")
        selected_target = settings.get(f"device_name{self.suffix_str}", "")
        
        pulse = self.parent_action.get_pulse()
        if not pulse:
            return
            
        dev_model = Gtk.StringList()
        dev_model.append(self.parent_action.plugin_base.lm.get("config.device.default", "Default"))
        
        items = []
        try:
            if device_type == "sink":
                items = pulse.sink_list()
            elif device_type == "source":
                items = [d for d in pulse.source_list() if not getattr(d, 'name', '').endswith('.monitor') and not getattr(d, 'description', '').startswith('Monitor of')]
            else:
                items = self.parent_action.get_active_applications()
        except Exception:
            pass

        selected_idx = 0
        
        if device_type == "application":
            for i, app_name in enumerate(items):
                dev_model.append(app_name)
                if selected_target == app_name:
                    selected_idx = i + 1
        else:
            for i, dev in enumerate(items):
                desc = getattr(dev, 'description', getattr(dev, 'name', 'Unknown'))
                dev_model.append(desc)
                if selected_target == getattr(dev, 'name', ''):
                    selected_idx = i + 1

        self.device_row.set_model(dev_model)
        self.device_row.set_selected(selected_idx)
        
        if device_type == "application":
            self.auto_index_row.set_visible(True)
        else:
            self.auto_index_row.set_visible(False)

    def on_type_changed(self, combo, pspec):
        settings = self.parent_action.get_settings()
        idx = combo.get_selected()
        t = "sink"
        if idx == 1: t = "source"
        elif idx == 2: t = "application"
        
        settings[f"device_type{self.suffix_str}"] = t
        settings[f"device_name{self.suffix_str}"] = ""
        self.parent_action.set_settings(settings)
        self.update_device_model()
        self.parent_action.draw_image()

    def on_device_changed(self, combo, pspec):
        settings = self.parent_action.get_settings()
        device_type = settings.get(f"device_type{self.suffix_str}", "sink")
        idx = combo.get_selected()
        
        if idx == 0:
            settings[f"target_device{self.suffix_str}"] = ""
            self.parent_action.set_settings(settings)
            self.parent_action.draw_image()
            return
            
        pulse = self.parent_action.get_pulse()
        if not pulse:
            return
            
        try:
            if device_type == "sink":
                items = pulse.sink_list()
            elif device_type == "source":
                items = [d for d in pulse.source_list() if not getattr(d, 'name', '').endswith('.monitor') and not getattr(d, 'description', '').startswith('Monitor of')]
            else:
                items = self.parent_action.get_active_applications()
                
            if idx - 1 < len(items):
                if device_type == "application":
                    settings[f"device_name{self.suffix_str}"] = items[idx - 1]
                else:
                    settings[f"device_name{self.suffix_str}"] = getattr(items[idx - 1], 'name', '')
            
            self.parent_action.set_settings(settings)
            self.parent_action.draw_image()
        except Exception as e:
            pass

    def on_auto_index_changed(self, spin, pspec):
        settings = self.parent_action.get_settings()
        settings[f"auto_index{self.suffix_str}"] = spin.get_value()
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()

    def on_limit_changed(self, spin, pspec):
        settings = self.parent_action.get_settings()
        settings[f"volume_limit{self.suffix_str}"] = min(150.0, spin.get_value())
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()

    def on_show_icon_changed(self, switch, pspec):
        settings = self.parent_action.get_settings()
        settings[f"show_icon{self.suffix_str}"] = switch.get_active()
        self.parent_action.set_settings(settings)
        self.parent_action.draw_image()
