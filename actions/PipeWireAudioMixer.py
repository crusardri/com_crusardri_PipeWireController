import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Adw, GLib, Gdk, Pango, PangoCairo
import cairo
import globals as gl

from src.backend.PluginManager.ActionBase import ActionBase
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

import pulsectl
import math
from PIL import Image, ImageDraw, ImageFont
import threading
import time
import os
import traceback

try:
    gi.require_version('Rsvg', '2.0')
    from gi.repository import Rsvg
    HAS_RSVG = True
except Exception:
    HAS_RSVG = False

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
            self.entry = Gtk.Entry(hexpand=True, placeholder_text=self.parent.plugin_base.lm.get("config.label.placeholder", "Default Text"))
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
        x_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.x"), margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.x_spin.set_value(self.settings.get(f"pos_x_{key_prefix}", -1))
        self.x_spin.connect("value-changed", self.on_change)
        
        y_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.y"), margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.y_spin.set_value(self.settings.get(f"pos_y_{key_prefix}", -1))
        self.y_spin.connect("value-changed", self.on_change)
        
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        
        # Width
        self.w_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.w_box)
        w_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.width"), margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.w_spin.set_value(self.settings.get(f"width_{key_prefix}", -1))
        self.w_spin.connect("value-changed", self.on_change)
        self.w_box.append(w_lbl)
        self.w_box.append(self.w_spin)


    def on_change(self, *args):
        if getattr(self, "entry", None):
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
        
        self.settings[f"pos_x_{self.key_prefix}"] = int(self.x_spin.get_value())
        self.settings[f"pos_y_{self.key_prefix}"] = int(self.y_spin.get_value())
        self.settings[f"width_{self.key_prefix}"] = int(self.w_spin.get_value())

        self.parent.set_settings(self.settings)
        self.parent.last_state["vol"] = -1
        self.parent.draw_image()


class CustomIconRow(Adw.PreferencesRow):
    def __init__(self, settings_dict, parent_action, key_prefix=""):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        self.kp = f"_{key_prefix}" if key_prefix else ""
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        
        title = self.parent.plugin_base.lm.get("config.icon.format")
        if key_prefix:
            title += f" ({key_prefix.upper()})"
        label = Gtk.Label(label=title, xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
        self.file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.file_box)
        
        self.btn_file = Gtk.Button(label=self.parent.plugin_base.lm.get("config.icon.select"))
        self.btn_file.connect("clicked", self.on_btn_file_clicked)
        self.file_box.append(self.btn_file)
        
        self.btn_clear = Gtk.Button(icon_name="user-trash-symbolic", margin_start=5)
        self.btn_clear.connect("clicked", self.on_btn_clear_clicked)
        self.file_box.append(self.btn_clear)
        
        self.lbl_file = Gtk.Label(label=self.settings.get(f"icon_path{self.kp}", self.parent.plugin_base.lm.get("config.icon.default")), margin_start=10)
        self.lbl_file.set_ellipsize(Pango.EllipsizeMode.END)
        self.file_box.append(self.lbl_file)
        
        self.wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.wh_box)
        
        w_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.width"), margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.w_spin.set_value(self.settings.get(f"icon_width{self.kp}", -1))
        self.w_spin.connect("value-changed", self.on_change)
        
        h_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.height"), margin_start=10, margin_end=5)
        self.h_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.h_spin.set_value(self.settings.get(f"icon_height{self.kp}", -1))
        self.h_spin.connect("value-changed", self.on_change)
        
        self.wh_box.append(w_lbl)
        self.wh_box.append(self.w_spin)
        self.wh_box.append(h_lbl)
        self.wh_box.append(self.h_spin)
        
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        
        x_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.x"), margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.x_spin.set_value(self.settings.get(f"icon_x{self.kp}", -1))
        self.x_spin.connect("value-changed", self.on_change)
        
        y_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.y"), margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.y_spin.set_value(self.settings.get(f"icon_y{self.kp}", -1))
        self.y_spin.connect("value-changed", self.on_change)
        
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        
    def on_btn_file_clicked(self, btn):
        media_path = self.settings.get(f"icon_path{self.kp}", "")
        GLib.idle_add(gl.app.let_user_select_asset, media_path, self.on_media_selected)
        
    def on_btn_clear_clicked(self, btn):
        self.settings[f"icon_path{self.kp}"] = ""
        self.lbl_file.set_label(self.parent.plugin_base.lm.get("config.icon.default"))
        self.parent.set_settings(self.settings)
        self.parent.draw_image()

    def on_media_selected(self, path):
        if path is not None:
            self.settings[f"icon_path{self.kp}"] = path
            self.lbl_file.set_label(path)
            self.parent.set_settings(self.settings)
            self.parent.draw_image()
            
    def on_change(self, *args):
        self.settings[f"icon_width{self.kp}"] = int(self.w_spin.get_value())
        self.settings[f"icon_height{self.kp}"] = int(self.h_spin.get_value())
        self.settings[f"icon_x{self.kp}"] = int(self.x_spin.get_value())
        self.settings[f"icon_y{self.kp}"] = int(self.y_spin.get_value())
        self.parent.set_settings(self.settings)
        self.parent.draw_image()
class CustomBarRow(Adw.PreferencesRow):
    def __init__(self, settings_dict, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        
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

        # Row W, H
        self.wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.wh_box)
        w_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.width"), margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.w_spin.set_value(self.settings.get("bar_width", -1))
        self.w_spin.connect("value-changed", self.on_change)
        h_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.height"), margin_start=10, margin_end=5)
        self.h_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.h_spin.set_value(self.settings.get("bar_height", -1))
        self.h_spin.connect("value-changed", self.on_change)
        self.wh_box.append(w_lbl)
        self.wh_box.append(self.w_spin)
        self.wh_box.append(h_lbl)
        self.wh_box.append(self.h_spin)
        
        # Row X, Y, Radius
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        x_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.x"), margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.x_spin.set_value(self.settings.get("bar_x", -1))
        self.x_spin.connect("value-changed", self.on_change)
        y_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.pos.y"), margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.y_spin.set_value(self.settings.get("bar_y", -1))
        self.y_spin.connect("value-changed", self.on_change)
        rad_lbl = Gtk.Label(label=self.parent.plugin_base.lm.get("config.size.radius"), margin_start=10, margin_end=5)
        self.rad_spin = Gtk.SpinButton.new_with_range(-1, 100, 1)
        self.rad_spin.set_value(self.settings.get("bar_radius", -1))
        self.rad_spin.connect("value-changed", self.on_change)
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        self.xy_box.append(rad_lbl)
        self.xy_box.append(self.rad_spin)
        
    def on_change(self, *args):
        rgba = self.color_btn.get_rgba()
        self.settings["bar_color"] = f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"
        
        rgba_bg = self.bg_color_btn.get_rgba()
        self.settings["bar_bg_color"] = f"#{int(rgba_bg.red*255):02x}{int(rgba_bg.green*255):02x}{int(rgba_bg.blue*255):02x}"

        rgba_over = self.over_color_btn.get_rgba()
        self.settings["bar_over_color"] = f"#{int(rgba_over.red*255):02x}{int(rgba_over.green*255):02x}{int(rgba_over.blue*255):02x}"

        self.settings["bar_width"] = int(self.w_spin.get_value())
        self.settings["bar_height"] = int(self.h_spin.get_value())
        self.settings["bar_x"] = int(self.x_spin.get_value())
        self.settings["bar_y"] = int(self.y_spin.get_value())
        self.settings["bar_radius"] = int(self.rad_spin.get_value())
        self.parent.set_settings(self.settings)
        self.parent.last_state["vol"] = -1
        self.parent.draw_image()




class PipeWireAudioMixer(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        
        self.add_event_assigner(EventAssigner(
            id="ToggleMute",
            ui_label=self.plugin_base.lm.get("actions.pipewire-mixer.event.toggle-mute", "Toggle Mute"),
            default_events=[Input.Dial.Events.SHORT_UP, Input.Dial.Events.SHORT_TOUCH_PRESS],
            callback=self.on_toggle_mute
        ))

        self.add_event_assigner(EventAssigner(
            id="MixerRight",
            ui_label=self.plugin_base.lm.get("actions.pipewire-mixer.event.right", "Mix Right (App 2)"),
            default_events=[Input.Dial.Events.TURN_CW],
            callback=self.on_volume_up
        ))

        self.add_event_assigner(EventAssigner(
            id="MixerLeft",
            ui_label=self.plugin_base.lm.get("actions.pipewire-mixer.event.left", "Mix Left (App 1)"),
            default_events=[Input.Dial.Events.TURN_CCW],
            callback=self.on_volume_down
        ))

        self.last_state = {"vol_a": -1, "vol_b": -1, "muted_a": False, "muted_b": False, "dev_a": None, "dev_b": None, "balance": 50.0}
        self.internal_balance = 50.0
        self.device_mapping = {"a": [], "b": []}

    def on_tick(self):
        try:
            dev_a = self.get_target_device("a")
            dev_b = self.get_target_device("b")
            if dev_a and dev_b and getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)):
                dev_b = None
            is_single_mode = dev_b is None
                
            vol_a = int(round(self.get_pulse().volume_get_all_chans(dev_a) * 100)) if dev_a else 0
            vol_b = int(round(self.get_pulse().volume_get_all_chans(dev_b) * 100)) if dev_b else 0
            
            mut_a = bool(dev_a.mute) if dev_a else False
            mut_b = bool(dev_b.mute) if dev_b else False
            
            nm_a = dev_a.name if dev_a else "OFFLINE"
            nm_b = dev_b.name if dev_b else "OFFLINE"
            
            changed = (self.last_state["vol_a"] != vol_a or 
                       self.last_state["vol_b"] != vol_b or 
                       self.last_state["muted_a"] != mut_a or 
                       self.last_state["muted_b"] != mut_b or
                       self.last_state["dev_a"] != nm_a or
                       self.last_state["dev_b"] != nm_b)
                       
            if changed:
                self.last_state.update({
                    "vol_a": vol_a, "vol_b": vol_b,
                    "muted_a": mut_a, "muted_b": mut_b,
                    "dev_a": nm_a, "dev_b": nm_b
                })
                # Infer balance
                settings = self.get_settings()
                lim_a = float(settings.get("volume_limit_a", 100))
                lim_b = float(settings.get("volume_limit_b", 100))
                pct_a = vol_a / lim_a if lim_a > 0 else 0
                pct_b = vol_b / lim_b if lim_b > 0 else 0
                
                if is_single_mode:
                    self.internal_balance = pct_a * 100.0
                else:
                    if pct_a >= pct_b:
                        self.internal_balance = 50.0 * pct_b
                    else:
                        self.internal_balance = 100.0 - (50.0 * pct_a)
                    
                self.draw_image()
        except Exception:
            pass

    def on_ready(self):
        self.draw_image()

    def get_pulse(self):
        return self.plugin_base.pulse

    def get_target_device(self, suffix):
        settings = self.get_settings()
        device_type = settings.get(f"device_type_{suffix}", "sink")
        device_name = settings.get(f"device_name_{suffix}", "default")
        
        pulse = self.get_pulse()
        if not pulse:
            return None

        server_info = pulse.server_info()
        
        if device_type == "application":
            target_app = None
            auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
            
            inputs = pulse.sink_input_list()
            
            if device_name.startswith(auto_prefix) or device_name == "Auto":
                try:
                    if device_name == "Auto":
                        idx = settings.get(f"auto_index_{suffix}", 0)
                    else:
                        idx = int(device_name.split(" ")[1]) - 1
                    
                    seen = set()
                    apps = []
                    for src in inputs:
                        binary = src.proplist.get('application.process.binary') or src.proplist.get('application.name')
                        if binary and binary not in seen:
                            seen.add(binary)
                            apps.append(binary)
                            
                    if idx < len(apps):
                        target_app = apps[idx]
                except (ValueError, IndexError):
                    pass
            else:
                target_app = device_name
                
            if not target_app:
                return None
                
            for src in inputs:
                binary = src.proplist.get('application.process.binary') or src.proplist.get('application.name')
                if binary == target_app:
                    return src
            return None
            
        elif device_type == "sink":
            devices = pulse.sink_list()
            target_name = server_info.default_sink_name if device_name == "default" else device_name
        else:
            devices = pulse.source_list()
            target_name = server_info.default_source_name if device_name == "default" else device_name

        for dev in devices:
            if dev.name == target_name:
                return dev
        
        if len(devices) > 0:
            return devices[0]
        return None

    def on_toggle_mute(self, data=None):
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")
        if dev_a and dev_b and getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)):
            dev_b = None
        if dev_a: self.get_pulse().mute(dev_a, not dev_a.mute)
        if dev_b: self.get_pulse().mute(dev_b, not dev_b.mute)
        self.draw_image()

    def change_balance(self, amount):
        settings = self.get_settings()
        self.internal_balance += amount
        if self.internal_balance < 0: self.internal_balance = 0.0
        if self.internal_balance > 100: self.internal_balance = 100.0
        
        limit_a = float(settings.get("volume_limit_a", 100)) / 100.0
        limit_b = float(settings.get("volume_limit_b", 100)) / 100.0
        
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")
        if dev_a and dev_b and getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)):
            dev_b = None
            
        is_single_mode = dev_b is None
        
        if is_single_mode:
            vol_a = limit_a * (self.internal_balance / 100.0)
            vol_b = 0
        else:
            if self.internal_balance <= 50:
                vol_a = limit_a
                vol_b = limit_b * (self.internal_balance / 50.0)
            else:
                vol_a = limit_a * ((100.0 - self.internal_balance) / 50.0)
                vol_b = limit_b
            
        if dev_a: self.get_pulse().volume_set_all_chans(dev_a, vol_a)
        if dev_b: self.get_pulse().volume_set_all_chans(dev_b, vol_b)
        
        self.draw_image()

    def on_volume_up(self, data=None):
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(step)

    def on_volume_down(self, data=None):
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(-step)

    def _parse_color(self, hex_str):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 6:
            return tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4)) + (1.0,)
        elif len(hex_str) == 8:
            return tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4, 6))
        return (0,0,0,1.0)

    def draw_image(self):
        settings = self.get_settings()
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")
        
        width, height = 100, 100
        try:
            ctrl_input = self.get_input()
            if ctrl_input:
                width, height = ctrl_input.get_image_size()
        except Exception:
            pass
            
        width = max(32, width)
        height = max(32, height)
        
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        
        defaults = gl.settings_manager.font_defaults

        def get_def_color(key, fallback="#FFFFFF"):
            val = defaults.get(key)
            if val: return f"#{val[0]:02x}{val[1]:02x}{val[2]:02x}"
            return fallback

        def_color = get_def_color("font-color", "#FFFFFF")
        def_out_color = get_def_color("outline-color", "#000000")
        def_out_width = int(defaults.get("outline-width", 2))
        def_align = defaults.get("alignment", "center")
        def_font_family = defaults.get("font-family", "Sans")
        def_font_size = int(defaults.get("font-size", 15))
        def_font_desc = f"{def_font_family} {def_font_size}"
        
        is_single_mode = dev_b is None or (dev_a and getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)))

        custom_bar_h = settings.get("bar_height", -1)
        if custom_bar_h >= 0:
            bar_h_each = max(1, (custom_bar_h - 2) // 2)
        else:
            bar_h_each = max(2, int(height * 0.03))
            custom_bar_h = bar_h_each * 2 + 2
            
        if is_single_mode:
            bar_h_each = custom_bar_h
        
        custom_bar_x = settings.get("bar_x", -1)
        bar_x = custom_bar_x if custom_bar_x >= 0 else int(width * 0.1)
        
        custom_bar_y = settings.get("bar_y", -1)
        base_bar_y = custom_bar_y if custom_bar_y >= 0 else height - custom_bar_h - int(height * 0.1)
        
        custom_bar_w = settings.get("bar_width", -1)
        bar_w = custom_bar_w if custom_bar_w >= 0 else width - (bar_x * 2)

        def draw_text_section(key_suffix, text, default_y):
            align = settings.get(f"align_{key_suffix}", "center" if key_suffix == "pct" else def_align)
            out_width = int(settings.get(f"outline_width_{key_suffix}", def_out_width))
            c_out = self._parse_color(settings.get(f"outline_color_{key_suffix}", def_out_color))
            c_text = self._parse_color(settings.get(f"color_{key_suffix}", def_color))

            def_font = f"{def_font_family} 22" if key_suffix == "pct" else def_font_desc
            curr_font = settings.get(f"font_desc_{key_suffix}", def_font)
            desc = Pango.FontDescription.from_string(curr_font) if curr_font else Pango.FontDescription()

            layout = PangoCairo.create_layout(ctx)
            layout.set_font_description(desc)
            layout.set_text(text, -1)

            custom_x = settings.get(f"pos_x_{key_suffix}", -1)
            custom_y = settings.get(f"pos_y_{key_suffix}", -1)
            custom_w = settings.get(f"width_{key_suffix}", -1)

            padding = max(6, int(width * 0.065))
            
            if custom_w < 0: max_w = width - (padding * 2)
            else: max_w = custom_w

            # Apply Pango native truncation with "..."
            layout.set_width(max_w * Pango.SCALE)
            layout.set_ellipsize(Pango.EllipsizeMode.END)

            w_pango, h_pango = layout.get_pixel_size()
            
            if custom_x < 0: base_x = padding
            else: base_x = custom_x
            
            if custom_y < 0:
                if key_suffix == "pct":
                    y_pos = base_bar_y - h_pango - 4
                else:
                    y_pos = default_y
            else: y_pos = custom_y

            if align == "left": x = base_x
            elif align == "right": x = base_x + max_w - w_pango
            else: x = base_x + int((max_w - w_pango) / 2)
            
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
        
        bar_rad = settings.get("bar_radius", -1)
        if bar_rad < 0: bar_rad = min(bar_w, bar_h_each) // 2

        c_bar = self._parse_color(settings.get("bar_color", "#FFFFFF"))
        c_bg = self._parse_color(settings.get("bar_bg_color", "#424242"))
        c_over = self._parse_color(settings.get("bar_over_color", "#ff4b4b"))

        def draw_rounded_rect(ctx, x, y, w, h, r):
            ctx.new_sub_path()
            ctx.arc(x + w - r, y + r, r, -math.pi/2, 0)
            ctx.arc(x + w - r, y + h - r, r, 0, math.pi/2)
            ctx.arc(x + r, y + h - r, r, math.pi/2, math.pi)
            ctx.arc(x + r, y + r, r, math.pi, 3*math.pi/2)
            ctx.close_path()

        def draw_single_bar(y_offset, dev, limit, invert=False):
            draw_rounded_rect(ctx, bar_x, y_offset, bar_w, bar_h_each, bar_rad)
            ctx.set_source_rgba(*c_bg)
            ctx.fill()
            
            if not dev: return
            
            vol_pct = (self.get_pulse().volume_get_all_chans(dev) * 100)
            if dev.mute: vol_pct = 0
            
            is_over = False
            if vol_pct > limit:
                vol_pct = limit
                is_over = True
                
            fill_w = int(bar_w * (vol_pct / max(100.0, float(limit))))
            if fill_w > bar_w: fill_w = bar_w
            
            if fill_w > 0:
                rad = bar_rad if fill_w > bar_rad * 2 else fill_w / 2
                start_x = bar_x if not invert else bar_x + bar_w - fill_w
                draw_rounded_rect(ctx, start_x, y_offset, fill_w, bar_h_each, rad)
                ctx.set_source_rgba(*(c_over if is_over else c_bar))
                ctx.fill()

        limit_a = settings.get("volume_limit_a", 100)
        limit_b = settings.get("volume_limit_b", 100)
        
        draw_single_bar(base_bar_y, dev_a, limit_a, invert=not is_single_mode)
        
        if not is_single_mode:
            draw_single_bar(base_bar_y + bar_h_each + 2, dev_b, limit_b, invert=False)

        # Draw icons
        def draw_icon(suffix, default_x):
            icon_path = settings.get(f"icon_path_{suffix}", "")
            icon_w = settings.get(f"icon_width_{suffix}", -1)
            icon_h = settings.get(f"icon_height_{suffix}", -1)
            icon_x = settings.get(f"icon_x_{suffix}", -1)
            icon_y = settings.get(f"icon_y_{suffix}", -1)
            
            if icon_w < 0: icon_w = 48
            if icon_h < 0: icon_h = 48
            if icon_x < 0:
                if suffix == "a": icon_x = bar_x
                else: icon_x = width - bar_x - icon_w
            if icon_y < 0: icon_y = base_bar_y - icon_h - 4
            
            if not icon_path or not os.path.isfile(icon_path):
                dtype = settings.get(f"device_type_{suffix}", "sink")
                if dtype == "application":
                    auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
                    dev_name = settings.get(f"device_name_{suffix}", auto_prefix + "1")
                    is_auto = dev_name.startswith(auto_prefix)
                    
                    target_app_icon = None
                    dev = dev_a if suffix == "a" else dev_b
                    if dev:
                        target_app_icon = dev.proplist.get('application.icon_name') or dev.proplist.get('application.process.binary')
                    elif not is_auto:
                        target_app_icon = dev_name
                        
                    found_icon = False
                    if target_app_icon:
                        try:
                            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
                            icon_info = theme.lookup_icon(target_app_icon, None, 48, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.NONE)
                            if icon_info:
                                found_path = icon_info.get_file().get_path()
                                if found_path:
                                    icon_path = found_path
                                    found_icon = True
                        except Exception as e:
                            pass
                            
                    if not found_icon:
                        icon_path = os.path.join(self.plugin_base.PATH, "assets", "speaker.svg")
                elif dtype == "source":
                    icon_path = os.path.join(self.plugin_base.PATH, "assets", "mic.svg")
                else:
                    icon_path = os.path.join(self.plugin_base.PATH, "assets", "speaker.svg")
                    
            try:
                i_w = settings.get(f"icon_width_{suffix}", -1)
                i_h = settings.get(f"icon_height_{suffix}", -1)
                if i_w < 0 and i_h < 0:
                    i_w = icon_w
                    i_h = icon_h

                if HAS_RSVG and icon_path.lower().endswith('.svg'):
                    handle = Rsvg.Handle.new_from_file(icon_path)
                    dim = handle.get_dimensions()
                    svg_w, svg_h = dim.width, dim.height
                    
                    if i_w < 0: i_w = int(svg_w * (i_h / float(svg_h))) if svg_h > 0 else icon_w
                    if i_h < 0: i_h = int(svg_h * (i_w / float(svg_w))) if svg_w > 0 else icon_h
                    
                    ctx.save()
                    ctx.translate(icon_x, icon_y)
                    if svg_w > 0 and svg_h > 0:
                        ctx.scale(i_w / svg_w, i_h / svg_h)
                    handle.render_cairo(ctx)
                    ctx.restore()
                else:
                    pil_img = Image.open(icon_path).convert("RGBA")
                    if i_w < 0: i_w = int(pil_img.width * (i_h / float(pil_img.height)))
                    if i_h < 0: i_h = int(pil_img.height * (i_w / float(pil_img.width)))
                    
                    pil_img = pil_img.resize((i_w, i_h), Image.Resampling.LANCZOS)
                    
                    surface_data = surface.get_data()
                    base_img = Image.frombuffer("RGBA", (width, height), surface_data, "raw", "BGRA", 0, 1)
                    base_img.alpha_composite(pil_img, (icon_x, icon_y))
                    
                    arr = bytearray(base_img.tobytes("raw", "BGRA"))
                    surface_data[:] = arr
                
            except Exception as e:
                pass
                
            dev = dev_a if suffix == "a" else dev_b
            if dev and getattr(dev, 'mute', False):
                ctx.save()
                ctx.set_source_rgba(1.0, 0.0, 0.0, 1.0)
                ctx.set_line_width(max(3, icon_h // 10))
                ctx.move_to(icon_x, icon_y)
                ctx.line_to(icon_x + icon_w, icon_y + icon_h)
                ctx.stroke()
                ctx.restore()
                
        draw_icon("a", int(width * 0.3))
        if not is_single_mode:
            draw_icon("b", int(width * 0.7))
        
        # Draw Name
        text_name = settings.get("text_name", "")
        if not text_name: text_name = self.plugin_base.lm.get("config.mixer.default_name", "Mixer")
        draw_text_section("name", text_name, 3)

        # Pct Text
        draw_text_section("pct", f"{int(self.internal_balance)}%", int(height * 0.28))

        self.set_media(image=Image.frombuffer("RGBA", (width, height), surface.get_data(), "raw", "BGRA", 0, 1))

    def get_config_rows(self):
        try:
            settings = self.get_settings()
            
            def create_device_ui(suffix, title):
                group = Adw.PreferencesGroup(title=title)
                
                type_row = Adw.ComboRow(title=self.plugin_base.lm.get("config.type.title", "Type"))
                model = Gtk.StringList()
                model.append(self.plugin_base.lm.get("config.type.sink", "Output"))
                model.append(self.plugin_base.lm.get("config.type.source", "Input"))
                model.append(self.plugin_base.lm.get("config.type.application", "Application"))
                type_row.set_model(model)
                dtype = settings.get(f"device_type_{suffix}", "sink")
                type_row.set_selected(0 if dtype == "sink" else 1 if dtype == "source" else 2)
                
                device_row = Adw.ComboRow(title=self.plugin_base.lm.get("config.device.title", "Device"))
                
                auto_index_row = Adw.SpinRow(title=self.plugin_base.lm.get("config.device.auto", "Auto") + " #")
                auto_index_row.set_adjustment(Gtk.Adjustment(value=settings.get(f"auto_index_{suffix}", 0), lower=0, upper=100, step_increment=1))
                
                limit_row = Adw.SpinRow(title=self.plugin_base.lm.get("config.limit.title", "Limit (%)"))
                limit_row.set_adjustment(Gtk.Adjustment(value=settings.get(f"volume_limit_{suffix}", 100), lower=1, upper=150, step_increment=1))
                
                group.add(type_row)
                group.add(device_row)
                group.add(auto_index_row)
                group.add(limit_row)
                
                return group, type_row, device_row, auto_index_row, limit_row

            self.grp_a, self.type_a, self.dev_a, self.auto_a, self.lim_a = create_device_ui("a", self.plugin_base.lm.get("config.mixer.device_a", "Device A"))
            self.grp_b, self.type_b, self.dev_b, self.auto_b, self.lim_b = create_device_ui("b", self.plugin_base.lm.get("config.mixer.device_b", "Device B"))
            
            self.type_a.connect("notify::selected-item", lambda *a: self.on_type_changed("a", self.type_a))
            self.type_b.connect("notify::selected-item", lambda *a: self.on_type_changed("b", self.type_b))
            
            self.dev_a.connect("notify::selected-item", lambda *a: self.on_device_changed("a", self.dev_a))
            self.dev_b.connect("notify::selected-item", lambda *a: self.on_device_changed("b", self.dev_b))
            
            self.auto_a.connect("notify::value", lambda spin, pspec: self.save_setting("auto_index_a", int(spin.get_value())))
            self.auto_b.connect("notify::value", lambda spin, pspec: self.save_setting("auto_index_b", int(spin.get_value())))
            
            self.lim_a.connect("notify::value", lambda spin, pspec: self.save_setting("volume_limit_a", int(spin.get_value())))
            self.lim_b.connect("notify::value", lambda spin, pspec: self.save_setting("volume_limit_b", int(spin.get_value())))

            self.update_device_model("a", self.dev_a, self.auto_a)
            self.update_device_model("b", self.dev_b, self.auto_b)

            grp_misc = Adw.PreferencesGroup(title="Mixer Settings")
            self.step_row = Adw.SpinRow(title=self.plugin_base.lm.get("config.step.title", "Step (%)"))
            self.step_row.set_adjustment(Gtk.Adjustment(value=settings.get("volume_step", 5), lower=1, upper=100, step_increment=1))
            self.step_row.connect("notify::value", lambda spin, pspec: self.save_setting("volume_step", int(spin.get_value())))
            grp_misc.add(self.step_row)
            
            self.exp_bar = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.bar.format", "Bar Format"))
            self.exp_bar.add_row(CustomBarRow(settings, self))
            grp_misc.add(self.exp_bar)
            
            self.exp_name = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.format.name.title", "Name Format"))
            self.exp_name.add_row(CustomLabelRow(self.plugin_base.lm.get("config.format.name.top", "Top Name"), settings, "name", self))
            grp_misc.add(self.exp_name)

            self.exp_pct = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.format.pct.title", "Percentage Format"))
            self.exp_pct.add_row(CustomLabelRow(self.plugin_base.lm.get("config.format.pct.text", "Pct"), settings, "pct", self))
            grp_misc.add(self.exp_pct)
            
            self.exp_icon_a = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.icon.format", "Icon Format") + " A")
            self.exp_icon_a.add_row(CustomIconRow(settings, self, "a"))
            grp_misc.add(self.exp_icon_a)
            
            self.exp_icon_b = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.icon.format", "Icon Format") + " B")
            self.exp_icon_b.add_row(CustomIconRow(settings, self, "b"))
            grp_misc.add(self.exp_icon_b)

            return [self.grp_a, self.grp_b, grp_misc]
        except Exception as e:
            err = Adw.ActionRow(title=f"GLOBAL ERROR: {e}", subtitle=traceback.format_exc()[-100:])
            return [err]

    def save_setting(self, key, value):
        settings = self.get_settings()
        settings[key] = value
        self.set_settings(settings)
        self.draw_image()

    def update_device_model(self, suffix, dev_row, auto_row):
        settings = self.get_settings()
        device_type = settings.get(f"device_type_{suffix}", "sink")
        
        pulse = self.get_pulse()
        if not pulse: return
            
        model = Gtk.StringList()
        mapping = []
        
        if device_type == "application":
            auto_str = self.plugin_base.lm.get("config.device.auto", "Auto")
            model.append(auto_str)
            mapping.append("Auto")
                
            seen = set()
            for src in pulse.sink_input_list():
                binary = src.proplist.get('application.process.binary') or src.proplist.get('application.name')
                if binary and binary not in seen:
                    seen.add(binary)
                    model.append(binary)
                    mapping.append(binary)
                
            selected_name = settings.get(f"device_name_{suffix}", "Auto")
        else:
            if device_type == "sink":
                devices = pulse.sink_list()
            else:
                devices = [d for d in pulse.source_list() if not d.name.endswith(".monitor")]
            model.append(self.plugin_base.lm.get("config.device.default", "Default"))
            mapping.append("default")
            
            for dev in devices:
                model.append(dev.description)
                mapping.append(dev.name)
                
            selected_name = settings.get(f"device_name_{suffix}", "Auto" if device_type == "application" else "default")
            
        selected_idx = 0
        if selected_name in mapping:
            selected_idx = mapping.index(selected_name)
            
        self.device_mapping[suffix] = mapping
        dev_row.set_model(model)
        dev_row.set_selected(selected_idx)

        auto_row.set_visible(selected_name == "Auto" and device_type == "application")

    def on_type_changed(self, suffix, combo):
        settings = self.get_settings()
        idx = combo.get_selected()
        new_type = "sink" if idx == 0 else "source" if idx == 1 else "application"
        settings[f"device_type_{suffix}"] = new_type
        settings[f"device_name_{suffix}"] = "default" if new_type != "application" else "Auto"
        self.set_settings(settings)
        dev_row = self.dev_a if suffix == "a" else self.dev_b
        auto_row = self.auto_a if suffix == "a" else self.auto_b
        self.update_device_model(suffix, dev_row, auto_row)

    def on_device_changed(self, suffix, combo):
        settings = self.get_settings()
        idx = combo.get_selected()
        mapping = self.device_mapping[suffix]
        if idx < len(mapping):
            settings[f"device_name_{suffix}"] = mapping[idx]
            self.set_settings(settings)
            auto_row = self.auto_a if suffix == "a" else self.auto_b
            auto_row.set_visible(mapping[idx] == "Auto")
