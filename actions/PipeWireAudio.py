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
            def_font_size = 20
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

        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Texto por defecto")
        if f"text_{key_prefix}" in self.settings:
            self.entry.set_text(self.settings[f"text_{key_prefix}"])
        self.entry.connect("changed", self.on_change)
        self.text_box.append(self.entry)

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

        font_label = Gtk.Label(label="Fuente:", xalign=0, hexpand=True, margin_start=2)
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

        align_label = Gtk.Label(label="Alineación:", xalign=0, hexpand=True, margin_start=2)
        self.align_box.append(align_label)

        self.btn_left = Gtk.ToggleButton(icon_name="format-justify-left-symbolic", tooltip_text="Izquierda")
        self.btn_center = Gtk.ToggleButton(icon_name="format-justify-center-symbolic", tooltip_text="Centro")
        self.btn_right = Gtk.ToggleButton(icon_name="format-justify-right-symbolic", tooltip_text="Derecha")
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

        out_width_label = Gtk.Label(label="Contorno:", xalign=0, hexpand=False, margin_start=2, margin_end=5)
        self.out_box.append(out_width_label)

        val_out_width = self.settings.get(f"outline_width_{key_prefix}", def_out_width)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(val_out_width)
        self.out_spin.connect("value-changed", self.on_change)
        self.out_box.append(self.out_spin)

        out_color_label = Gtk.Label(label="Color cont.:", xalign=1, hexpand=True, margin_start=2, margin_end=5)
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
        x_lbl = Gtk.Label(label="Pos X:", margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.x_spin.set_value(self.settings.get(f"pos_x_{key_prefix}", -1))
        self.x_spin.connect("value-changed", self.on_change)
        
        y_lbl = Gtk.Label(label="Pos Y:", margin_start=10, margin_end=5)
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
        w_lbl = Gtk.Label(label="Ancho:", margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.w_spin.set_value(self.settings.get(f"width_{key_prefix}", -1))
        self.w_spin.connect("value-changed", self.on_change)
        self.w_box.append(w_lbl)
        self.w_box.append(self.w_spin)


    def on_change(self, *args):
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
    def __init__(self, settings_dict, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.parent = parent_action
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)
        
        label = Gtk.Label(label="Formato del Icono", xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
        # Row 1: File Button
        self.file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.file_box)
        
        self.btn_file = Gtk.Button(label="Seleccionar Imagen")
        self.btn_file.connect("clicked", self.on_btn_file_clicked)
        self.file_box.append(self.btn_file)
        
        self.btn_clear = Gtk.Button(icon_name="user-trash-symbolic", margin_start=5)
        self.btn_clear.connect("clicked", self.on_btn_clear_clicked)
        self.file_box.append(self.btn_clear)
        
        self.lbl_file = Gtk.Label(label=self.settings.get("icon_path", "Por defecto (emoji)"), margin_start=10)
        self.lbl_file.set_ellipsize(Pango.EllipsizeMode.END)
        self.file_box.append(self.lbl_file)
        
        # Row 2: W, H
        self.wh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.wh_box)
        
        w_lbl = Gtk.Label(label="Ancho:", margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.w_spin.set_value(self.settings.get("icon_width", -1))
        self.w_spin.connect("value-changed", self.on_change)
        
        h_lbl = Gtk.Label(label="Alto:", margin_start=10, margin_end=5)
        self.h_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.h_spin.set_value(self.settings.get("icon_height", -1))
        self.h_spin.connect("value-changed", self.on_change)
        
        self.wh_box.append(w_lbl)
        self.wh_box.append(self.w_spin)
        self.wh_box.append(h_lbl)
        self.wh_box.append(self.h_spin)
        
        # Row 3: X, Y
        self.xy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.xy_box)
        
        x_lbl = Gtk.Label(label="Pos X:", margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.x_spin.set_value(self.settings.get("icon_x", -1))
        self.x_spin.connect("value-changed", self.on_change)
        
        y_lbl = Gtk.Label(label="Pos Y:", margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.y_spin.set_value(self.settings.get("icon_y", -1))
        self.y_spin.connect("value-changed", self.on_change)
        
        self.xy_box.append(x_lbl)
        self.xy_box.append(self.x_spin)
        self.xy_box.append(y_lbl)
        self.xy_box.append(self.y_spin)
        
    def on_btn_file_clicked(self, btn):
        import globals as gl
        media_path = self.settings.get("icon_path", "")
        GLib.idle_add(gl.app.let_user_select_asset, media_path, self.on_media_selected)
        
    def on_btn_clear_clicked(self, btn):
        self.settings["icon_path"] = ""
        self.lbl_file.set_label("Por defecto (emoji)")
        self.parent.set_settings(self.settings)
        self.parent.draw_image()

    def on_media_selected(self, path):
        if path is not None:
            self.settings["icon_path"] = path
            self.lbl_file.set_label(path)
            self.parent.set_settings(self.settings)
            self.parent.draw_image()
            
    def on_change(self, *args):
        self.settings["icon_width"] = int(self.w_spin.get_value())
        self.settings["icon_height"] = int(self.h_spin.get_value())
        self.settings["icon_x"] = int(self.x_spin.get_value())
        self.settings["icon_y"] = int(self.y_spin.get_value())
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
        
        label = Gtk.Label(label="Formato de la Barra", xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)
        
        # Color Box (Base)
        self.color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.main_box.append(self.color_box)
        
        color_lbl = Gtk.Label(label="Color de la barra:", margin_end=5)
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
        bg_lbl = Gtk.Label(label="Fondo:", margin_start=15, margin_end=5)
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
        over_lbl = Gtk.Label(label=">100%:", margin_start=15, margin_end=5)
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
        w_lbl = Gtk.Label(label="Ancho:", margin_end=5)
        self.w_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.w_spin.set_value(self.settings.get("bar_width", -1))
        self.w_spin.connect("value-changed", self.on_change)
        h_lbl = Gtk.Label(label="Alto:", margin_start=10, margin_end=5)
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
        x_lbl = Gtk.Label(label="Pos X:", margin_end=5)
        self.x_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.x_spin.set_value(self.settings.get("bar_x", -1))
        self.x_spin.connect("value-changed", self.on_change)
        y_lbl = Gtk.Label(label="Pos Y:", margin_start=10, margin_end=5)
        self.y_spin = Gtk.SpinButton.new_with_range(-1, 2000, 1)
        self.y_spin.set_value(self.settings.get("bar_y", -1))
        self.y_spin.connect("value-changed", self.on_change)
        rad_lbl = Gtk.Label(label="Radio:", margin_start=10, margin_end=5)
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



class PipeWireAudio(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        
        # Add event assigners for each functionality
        self.add_event_assigner(EventAssigner(
            id="ToggleMute",
            ui_label=self.plugin_base.lm.get("actions.pipewire-audio.event.toggle-mute"),
            default_events=[Input.Key.Events.DOWN, Input.Dial.Events.SHORT_UP, Input.Dial.Events.SHORT_TOUCH_PRESS],
            callback=self.on_toggle_mute
        ))
        
        self.add_event_assigner(EventAssigner(
            id="MuteOn",
            ui_label=self.plugin_base.lm.get("actions.pipewire-audio.event.mute-on"),
            default_events=[],
            callback=self.on_mute_on
        ))

        self.add_event_assigner(EventAssigner(
            id="MuteOff",
            ui_label=self.plugin_base.lm.get("actions.pipewire-audio.event.mute-off"),
            default_events=[],
            callback=self.on_mute_off
        ))

        self.add_event_assigner(EventAssigner(
            id="VolumeUp",
            ui_label=self.plugin_base.lm.get("actions.pipewire-audio.event.volume-up"),
            default_events=[Input.Dial.Events.TURN_CW],
            callback=self.on_volume_up
        ))

        self.add_event_assigner(EventAssigner(
            id="VolumeDown",
            ui_label=self.plugin_base.lm.get("actions.pipewire-audio.event.volume-down"),
            default_events=[Input.Dial.Events.TURN_CCW],
            callback=self.on_volume_down
        ))

        # Añadir al final de __init__
        self.last_state = {"vol": -1, "muted": None, "device": None}
        self.scroll_state = {}
        self.is_scrolling = False

    def on_fast_tick(self):
        device = self.get_target_device()
        if not device:
            return

        vol_pct = int(round(self.get_pulse().volume_get_all_chans(device) * 100))
        is_muted = bool(device.mute)

        # Si el volumen o el estado de silencio cambian, o si hay texto scrolleando, actualizamos la imagen
        if (self.last_state["vol"] != vol_pct or 
            self.last_state["muted"] != is_muted or 
            self.last_state["device"] != device.name or
            self.is_scrolling):
            
            self.last_state["vol"] = vol_pct
            self.last_state["muted"] = is_muted
            self.last_state["device"] = device.name
            
            self.draw_image()

    def on_ready(self):
        self.stop_threads = False
        self.tick_thread = threading.Thread(target=self._fast_tick_loop, daemon=True)
        self.tick_thread.start()
        self.draw_image()

    def _fast_tick_loop(self):
        while not self.stop_threads:
            time.sleep(0.2)
            try:
                self.on_fast_tick()
            except Exception:
                pass

    def on_remove(self) -> None:
        self.stop_threads = True
        if hasattr(super(), "on_remove"):
            super().on_remove()

    def on_removed_from_cache(self) -> None:
        self.stop_threads = True
        if hasattr(super(), "on_removed_from_cache"):
            super().on_removed_from_cache()

    def get_pulse(self):
        return self.plugin_base.pulse

    def get_target_device(self):
        settings = self.get_settings()
        device_type = settings.get("device_type", "sink")
        device_name = settings.get("device_name", "default")
        
        pulse = self.get_pulse()
        if not pulse:
            return None

        server_info = pulse.server_info()
        
        if device_type == "sink":
            devices = pulse.sink_list()
            target_name = server_info.default_sink_name if device_name == "default" else device_name
        else:
            devices = pulse.source_list()
            target_name = server_info.default_source_name if device_name == "default" else device_name

        for dev in devices:
            if dev.name == target_name:
                return dev
        
        # Fallback to default if custom not found
        if len(devices) > 0:
            return devices[0]
        return None

    def on_toggle_mute(self, data=None):
        dev = self.get_target_device()
        if dev:
            self.get_pulse().mute(dev, not dev.mute)
            self.draw_image()

    def on_mute_on(self, data=None):
        dev = self.get_target_device()
        if dev and not dev.mute:
            self.get_pulse().mute(dev, True)
            self.draw_image()

    def on_mute_off(self, data=None):
        dev = self.get_target_device()
        if dev and dev.mute:
            self.get_pulse().mute(dev, False)
            self.draw_image()

    def change_volume(self, amount):
        settings = self.get_settings()
        limit = float(settings.get("volume_limit", 100)) / 100.0
        
        dev = self.get_target_device()
        if not dev:
            return

        current_vol = round(self.get_pulse().volume_get_all_chans(dev), 2)
        new_vol = current_vol + amount
        
        if new_vol < 0:
            new_vol = 0.0
        if new_vol > limit:
            new_vol = limit
            
        self.get_pulse().volume_set_all_chans(dev, new_vol)
        self.draw_image()

    def on_volume_up(self, data=None):
        step = float(self.get_settings().get("volume_step", 5)) / 100.0
        self.change_volume(step)

    def on_volume_down(self, data=None):
        step = float(self.get_settings().get("volume_step", 5)) / 100.0
        self.change_volume(-step)

    def draw_image(self):
        settings = self.get_settings()
        dev = self.get_target_device()
        
        if not dev:
            return

        vol_pct = int(round(self.get_pulse().volume_get_all_chans(dev) * 100))
        is_muted = dev.mute == 1
        dev_desc = dev.description

        # Función auxiliar de parseo de color HEX a tupla RGBA de 0.0 a 1.0 para Cairo
        def parse_color(hex_str):
            hex_str = hex_str.lstrip('#')
            if len(hex_str) == 6:
                return tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4)) + (1.0,)
            elif len(hex_str) == 8:
                return tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4, 6))
            return (0,0,0,1.0)

        # Obtener el tamaño exacto (1x) del dispositivo para esta entrada (Tecla, Dial, etc.)
        width, height = 100, 100
        try:
            ctrl_input = self.get_input()
            if ctrl_input:
                width, height = ctrl_input.get_image_size()
        except Exception:
            pass
            
        # Asegurarnos de que no sean 0 o demasiado pequeños
        width = max(32, width)
        height = max(32, height)
        
        # Crear la superficie de Cairo (el lienzo)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        
        # El fondo es transparente por defecto en cairo.ImageSurface(FORMAT_ARGB32)

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

        custom_bar_h = settings.get("bar_height", -1)
        bar_h = custom_bar_h if custom_bar_h >= 0 else max(5, int(height * 0.06))
        
        custom_bar_x = settings.get("bar_x", -1)
        bar_x = custom_bar_x if custom_bar_x >= 0 else int(width * 0.1)
        
        custom_bar_y = settings.get("bar_y", -1)
        bar_y = custom_bar_y if custom_bar_y >= 0 else height - bar_h - int(height * 0.1)

        c_bar = parse_color(settings.get("color_bar", "#FFFFFF"))

        any_scrolling = False

        def draw_text_section(key_suffix, text, default_y):
            nonlocal any_scrolling
            
            align = settings.get(f"align_{key_suffix}", "right" if key_suffix == "pct" else def_align)
            out_width = int(settings.get(f"outline_width_{key_suffix}", def_out_width))
            c_out = parse_color(settings.get(f"outline_color_{key_suffix}", def_out_color))
            c_text = parse_color(settings.get(f"color_{key_suffix}", def_color))
            
            def_font = f"{def_font_family} 20" if key_suffix == "pct" else def_font_desc
            curr_font = settings.get(f"font_desc_{key_suffix}", def_font)
            desc = Pango.FontDescription.from_string(curr_font) if curr_font else Pango.FontDescription()

            layout = PangoCairo.create_layout(ctx)
            layout.set_font_description(desc)
            layout.set_text(text, -1)
            w_pango, h_pango = layout.get_pixel_size()

            custom_x = settings.get(f"pos_x_{key_suffix}", -1)
            custom_y = settings.get(f"pos_y_{key_suffix}", -1)
            custom_w = settings.get(f"width_{key_suffix}", -1)

            padding = max(6, int(width * 0.065))
            
            if custom_w < 0: max_w = width - (padding * 2)
            else: max_w = custom_w
            
            if custom_x < 0: base_x = padding
            else: base_x = custom_x
            
            if custom_y < 0:
                if key_suffix == "pct":
                    y_pos = bar_y - h_pango - 4
                else:
                    y_pos = default_y
            else: y_pos = custom_y

            if w_pango > max_w and settings.get("rolling-labels", True):
                any_scrolling = True
                start = base_x
                stop = base_x + max_w - w_pango
                scroll_wait = 15
                state = self.scroll_state.get(key_suffix, {"pos": start, "wait": scroll_wait})
                
                if state["pos"] > stop:
                    if state["wait"] <= 0:
                        state["pos"] -= 3
                        if state["pos"] <= stop:
                            state["pos"] = stop
                            state["wait"] = scroll_wait
                    else:
                        state["wait"] -= 1
                else:
                    if state["wait"] <= 0:
                        state["pos"] = start
                        state["wait"] = scroll_wait
                    else:
                        state["wait"] -= 1
                        
                x = state["pos"]
                self.scroll_state[key_suffix] = state
                
                ctx.save()
                ctx.rectangle(base_x, 0, max_w, height)
                ctx.clip()
            else:
                if key_suffix in self.scroll_state: del self.scroll_state[key_suffix]
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
            
            if w_pango > max_w and settings.get("rolling-labels", True):
                ctx.restore()

        text_name = settings.get("text_name", "")
        if not text_name: text_name = dev_desc
        text_name = text_name.replace("{vol}", str(vol_pct))
        draw_text_section("name", text_name, 3)

        text_pct = settings.get("text_pct", "")
        if not text_pct: text_pct = f"{vol_pct} %"
        
        if is_muted:
            text_pct = "- - %"
        else:
            text_pct = text_pct.replace("{vol}", str(vol_pct))
            
        draw_text_section("pct", text_pct, int(height * 0.28))
        
        # --- 3. Dibujar Icono ---
        icon_path = settings.get("icon_path", "")
        icon_w = settings.get("icon_width", -1)
        icon_h = settings.get("icon_height", -1)
        icon_x = settings.get("icon_x", -1)
        icon_y = settings.get("icon_y", -1)
        
        padding = max(6, int(width * 0.065))
        
        if icon_w < 0: icon_w = 48
        if icon_h < 0: icon_h = 48
        if icon_x < 0: icon_x = bar_x
        if icon_y < 0: icon_y = bar_y - icon_h - 4

        if not icon_path or icon_path.strip() == "":
            import os
            if settings.get("device_type", "sink") == "source":
                icon_path = os.path.join(self.plugin_base.PATH, "assets", "mic.svg")
            else:
                icon_path = os.path.join(self.plugin_base.PATH, "assets", "speaker.svg")

        if icon_path.lower().endswith(".svg"):
            try:
                import gi
                gi.require_version('Rsvg', '2.0')
                from gi.repository import Rsvg
                handle = Rsvg.Handle.new_from_file(icon_path)
                dimensions = handle.get_dimensions()
                svg_w, svg_h = dimensions.width, dimensions.height
                
                ctx.save()
                ctx.translate(icon_x, icon_y)
                if svg_w > 0 and svg_h > 0:
                    ctx.scale(icon_w / svg_w, icon_h / svg_h)
                handle.render_cairo(ctx)
                ctx.restore()
            except Exception as e:
                print("Failed to load SVG icon:", e)

        if is_muted:
            ctx.save()
            ctx.set_source_rgba(1.0, 0.0, 0.0, 1.0)
            ctx.set_line_width(max(3, icon_h // 10))
            ctx.move_to(icon_x, icon_y)
            ctx.line_to(icon_x + icon_w, icon_y + icon_h)
            ctx.stroke()
            ctx.restore()

        c_bar_bg = parse_color(settings.get("bar_bg_color", "#424242"))
        c_bar_over = parse_color(settings.get("bar_over_color", "#ff4b4b"))
        limit_val = settings.get("volume_limit", 100)

        # --- 4. Dibujar barra de progreso ---
        custom_bar_w = settings.get("bar_width", -1)
        custom_bar_rad = settings.get("bar_radius", -1)
        
        bar_w = custom_bar_w if custom_bar_w >= 0 else width - (bar_x * 2)
        radius = custom_bar_rad if custom_bar_rad >= 0 else max(2.0, bar_h / 2.0)
        
        def draw_rounded_rect(cr, x, y, w, h, r):
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -math.pi/2, 0)
            cr.arc(x + w - r, y + h - r, r, 0, math.pi/2)
            cr.arc(x + r, y + h - r, r, math.pi/2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3*math.pi/2)
            cr.close_path()

        ctx.set_source_rgba(*c_bar_bg)
        draw_rounded_rect(ctx, bar_x, bar_y, bar_w, bar_h, radius)
        ctx.fill()
        
        fill_pct = min(vol_pct, limit_val) / max(limit_val, 100)
        fill_w = int(bar_w * fill_pct)
        if fill_w > bar_w: fill_w = bar_w
        
        if fill_w > 0:
            ctx.save()
            draw_rounded_rect(ctx, bar_x, bar_y, fill_w, bar_h, radius)
            ctx.clip()
            
            w_100 = int(bar_w * (100.0 / max(limit_val, 100)))
            
            ctx.set_source_rgba(*c_bar)
            ctx.rectangle(bar_x, bar_y, w_100, bar_h)
            ctx.fill()
            
            if vol_pct > 100:
                ctx.set_source_rgba(*c_bar_over)
                ctx.rectangle(bar_x + w_100, bar_y, bar_w - w_100, bar_h)
                ctx.fill()
                
            ctx.restore()

        buf = surface.get_data()
        cairo_img = Image.frombuffer("RGBA", (width, height), buf.tobytes(), "raw", "BGRA", 0, 1)

        base_img = Image.new("RGBA", (width, height), (0,0,0,0))
        if icon_path and icon_path.strip() != "" and not icon_path.lower().endswith(".svg"):
            try:
                user_icon = Image.open(icon_path).convert("RGBA")
                user_icon = user_icon.resize((icon_w, icon_h), Image.Resampling.LANCZOS)
                base_img.paste(user_icon, (icon_x, icon_y), user_icon)
            except Exception as e:
                print("Failed to load user icon", e)
                
        img = Image.alpha_composite(base_img, cairo_img)

        self.is_scrolling = any_scrolling
        self.set_media(image=img)

    def hex_to_rgba(self, hex_str):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 6:
            return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4)) + (255,)
        elif len(hex_str) == 8:
            return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4, 6))
        return (0,0,0,0)

    # ----------------------
    # Configuration UI
    # ----------------------


    def get_config_rows(self) -> list:
        try:
            settings = self.get_settings()
            
            self.type_row = Adw.ComboRow(title=self.plugin_base.lm.get("config.type.title", "Tipo de dispositivo"))
            model = Gtk.StringList()
            model.append(self.plugin_base.lm.get("config.type.sink", "Sink (Salida/Altavoces)"))
            model.append(self.plugin_base.lm.get("config.type.source", "Source (Entrada/Micrófono)"))
            self.type_row.set_model(model)
            
            if settings.get("device_type", "sink") == "sink":
                self.type_row.set_selected(0)
            else:
                self.type_row.set_selected(1)
                
            self.type_row.connect("notify::selected-item", self.on_type_changed)

            self.device_row = Adw.ComboRow(title=self.plugin_base.lm.get("config.device.title", "Dispositivo"))
            self.device_model = Gtk.StringList()
            self.device_row.set_model(self.device_model)
            self.update_device_model()
            self.device_row.connect("notify::selected-item", self.on_device_changed)

            self.step_row = Adw.SpinRow(title=self.plugin_base.lm.get("config.step.title", "Incremento de Volumen (%)"))
            self.step_row.set_adjustment(Gtk.Adjustment(value=settings.get("volume_step", 5), lower=1, upper=100, step_increment=1))
            self.step_row.connect("notify::value", self.on_step_changed)

            self.limit_row = Adw.SpinRow(title=self.plugin_base.lm.get("config.limit.title", "Límite Máximo de Volumen (%)"))
            self.limit_row.set_adjustment(Gtk.Adjustment(value=settings.get("volume_limit", 100), lower=1, upper=150, step_increment=1))
            self.limit_row.connect("notify::value", self.on_limit_changed)
            
            self.exp_name = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.format.name.title", "Formato Nombre"))
            try:
                self.exp_name.add_row(CustomLabelRow("Arriba", settings, "name", self))
            except Exception as e:
                import traceback
                err_row = Adw.ActionRow(title=f"Error: {e}", subtitle=traceback.format_exc())
                self.exp_name.add_row(err_row)

            self.exp_pct = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.format.pct.title", "Formato Porcentaje"))
            try:
                self.exp_pct.add_row(CustomLabelRow("Texto Porcentaje", settings, "pct", self))
            except Exception as e:
                import traceback
                err_row = Adw.ActionRow(title=f"Error: {e}", subtitle=traceback.format_exc())
                self.exp_pct.add_row(err_row)

            self.exp_icon = Adw.ExpanderRow(title="Formato del Icono")
            try:
                self.exp_icon.add_row(CustomIconRow(settings, self))
            except Exception as e:
                import traceback
                err_row = Adw.ActionRow(title=f"Error: {e}", subtitle=traceback.format_exc())
                self.exp_icon.add_row(err_row)
                
            self.exp_bar = Adw.ExpanderRow(title="Formato de la Barra")
            try:
                self.exp_bar.add_row(CustomBarRow(settings, self))
            except Exception as e:
                import traceback
                err_row = Adw.ActionRow(title=f"Error: {e}", subtitle=traceback.format_exc())
                self.exp_bar.add_row(err_row)

            return [
                self.type_row, 
                self.device_row, 
                self.step_row, 
                self.limit_row, 
                self.exp_name, 
                self.exp_pct,
                self.exp_icon,
                self.exp_bar
            ]
        except Exception as e:
            import traceback
            err = Adw.ActionRow(title=f"GLOBAL ERROR: {e}", subtitle=traceback.format_exc()[-100:])
            return [err]

    def update_device_model(self):
        settings = self.get_settings()
        is_sink = settings.get("device_type", "sink") == "sink"
        
        pulse = self.get_pulse()
        if not pulse:
            return
            
        devices = pulse.sink_list() if is_sink else pulse.source_list()
        
        model = Gtk.StringList()
        model.append(self.plugin_base.lm.get("config.device.default", "Predeterminado (Defecto del Sistema)"))
        
        selected_name = settings.get("device_name", "default")
        selected_idx = 0
        
        for i, dev in enumerate(devices):
            model.append(dev.description)
            if dev.name == selected_name:
                selected_idx = i + 1

        self.device_mapping = ["default"] + [dev.name for dev in devices]
        self.device_row.set_model(model)
        self.device_row.set_selected(selected_idx)

    def on_type_changed(self, combo, pspec):
        settings = self.get_settings()
        idx = combo.get_selected()
        new_type = "sink" if idx == 0 else "source"
        settings["device_type"] = new_type
        settings["device_name"] = "default"
        self.set_settings(settings)
        self.update_device_model()
        self.last_state["vol"] = -1

    def on_device_changed(self, combo, pspec):
        settings = self.get_settings()
        idx = combo.get_selected()
        if hasattr(self, 'device_mapping') and idx < len(self.device_mapping):
            settings["device_name"] = self.device_mapping[idx]
            self.set_settings(settings)
            self.last_state["vol"] = -1

    def on_step_changed(self, spin, pspec):
        settings = self.get_settings()
        settings["volume_step"] = int(spin.get_value())
        self.set_settings(settings)

    def on_limit_changed(self, spin, pspec):
        settings = self.get_settings()
        settings["volume_limit"] = int(spin.get_value())
        self.set_settings(settings)

    def on_color_bar_changed(self, btn):
        settings = self.get_settings()
        rgba = btn.get_rgba()
        settings["color_bar"] = f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"
        self.set_settings(settings)
        self.last_state["vol"] = -1
