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

class CustomLabelRow(Adw.PreferencesRow):
    def __init__(self, title_text, settings_dict, key_prefix, parent_action):
        super().__init__()
        self.settings = settings_dict
        self.key_prefix = key_prefix
        self.parent = parent_action

        # Defaults
        defaults = gl.settings_manager.font_defaults
        def_color_rgba = Gdk.RGBA()
        def_color = defaults.get("font-color", [255, 255, 255, 255])
        def_color_rgba.red = def_color[0]/255.0
        def_color_rgba.green = def_color[1]/255.0
        def_color_rgba.blue = def_color[2]/255.0
        def_color_rgba.alpha = def_color[3]/255.0

        def_font_family = defaults.get("font-family", "Sans")
        def_font_size = defaults.get("font-size", 15)
        def_font_desc_str = f"{def_font_family} {def_font_size}"

        def_out_color = defaults.get("outline-color", [0, 0, 0, 255])
        def_out_rgba = Gdk.RGBA()
        def_out_rgba.red = def_out_color[0]/255.0
        def_out_rgba.green = def_out_color[1]/255.0
        def_out_rgba.blue = def_out_color[2]/255.0
        def_out_rgba.alpha = def_out_color[3]/255.0

        def_out_width = int(defaults.get("outline-width", 2))
        def_align = defaults.get("alignment", "center")
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                                margin_start=15, margin_end=15, margin_top=15, margin_bottom=15)
        self.set_child(self.main_box)

        # Title
        label = Gtk.Label(label=title_text, xalign=0, margin_bottom=3, css_classes=["bold"])
        self.main_box.append(label)

        # Row 1: Text Entry + Color
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

        # Row 2: Font
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

        # Row 3: Alignment
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

        # Row 4: Outline
        self.out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, margin_top=6)
        self.main_box.append(self.out_box)

        out_width_label = Gtk.Label(label="Ancho del contorno:", xalign=0, hexpand=False, margin_start=2, margin_end=5)
        self.out_box.append(out_width_label)

        val_out_width = self.settings.get(f"outline_width_{key_prefix}", def_out_width)
        self.out_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.out_spin.set_value(val_out_width)
        self.out_spin.connect("value-changed", self.on_change)
        self.out_box.append(self.out_spin)

        out_color_label = Gtk.Label(label="Color del contorno:", xalign=1, hexpand=True, margin_start=2, margin_end=5)
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

        out_rgba = self.out_color_btn.get_rgba()
        self.settings[f"outline_color_{self.key_prefix}"] = f"#{int(out_rgba.red*255):02x}{int(out_rgba.green*255):02x}{int(out_rgba.blue*255):02x}"

        self.parent.set_settings(self.settings)
        self.parent.last_state["vol"] = -1


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

    def on_tick(self):
        device = self.get_target_device()
        if not device:
            return

        vol_pct = int(round(self.get_pulse().volume_get_all_chans(device) * 100))
        is_muted = bool(device.mute)

        # Si el volumen o el estado de silencio cambian, actualizamos la imagen
        if (self.last_state["vol"] != vol_pct or 
            self.last_state["muted"] != is_muted or 
            self.last_state["device"] != device.name):
            
            self.last_state["vol"] = vol_pct
            self.last_state["muted"] = is_muted
            self.last_state["device"] = device.name
            
            self.draw_image()

    def on_ready(self):
        self.draw_image()

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
                return (int(hex_str[0:2], 16)/255.0, int(hex_str[2:4], 16)/255.0, int(hex_str[4:6], 16)/255.0, 1.0)
            elif len(hex_str) == 8:
                return (int(hex_str[0:2], 16)/255.0, int(hex_str[2:4], 16)/255.0, int(hex_str[4:6], 16)/255.0, int(hex_str[6:8], 16)/255.0)
            return (1.0, 1.0, 1.0, 1.0)

        c_name = parse_color(settings.get("color_name", "#FFFFFF"))
        c_pct = parse_color(settings.get("color_pct", "#FFFFFF"))
        c_bar = parse_color(settings.get("color_bar", "#FFFFFF"))

        # Obtener el tamaño exacto (1x) del dispositivo para esta tecla/dial
        width, height = 100, 100 # Valor por defecto seguro
        
        try:
            if type(self.input_ident) == Input.Key:
                width, height = self.deck_controller.get_key_image_size()
            elif type(self.input_ident) == Input.Dial:
                ts_size = self.deck_controller.get_touchscreen_image_size()
                dial_count = self.deck_controller.deck.dial_count()
                if dial_count > 0:
                    width = ts_size[0] // dial_count
                    height = ts_size[1]
        except Exception as e:
            pass # Si falla por alguna razón, usar el tamaño por defecto
            
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

        # Configuraciones de texto (Nombre)
        text_name = settings.get("text_name", "")
        if not text_name:
            text_name = dev_desc
            if len(text_name) > 15:
                text_name = text_name[:13] + "..."
        else:
            text_name = text_name.replace("{vol}", str(vol_pct))
            
        align_name = settings.get("align_name", def_align)
        out_width_name = settings.get("outline_width_name", def_out_width)
        c_out_name = parse_color(settings.get("outline_color_name", def_out_color))
        c_name = parse_color(settings.get("color_name", def_color))
        
        curr_font_name = settings.get("font_desc_name", def_font_desc)
        desc_name = Pango.FontDescription.from_string(curr_font_name) if curr_font_name else Pango.FontDescription()
            
        # Configuraciones de texto (Porcentaje)
        text_pct = settings.get("text_pct", "")
        if not text_pct:
            text_pct = f"{vol_pct} %"
        else:
            text_pct = text_pct.replace("{vol}", str(vol_pct))
            
        align_pct = settings.get("align_pct", def_align)
        out_width_pct = settings.get("outline_width_pct", def_out_width)
        c_out_pct = parse_color(settings.get("outline_color_pct", def_out_color))
        c_pct = parse_color(settings.get("color_pct", def_color))

        curr_font_pct = settings.get("font_desc_pct", def_font_desc)
        desc_pct = Pango.FontDescription.from_string(curr_font_pct) if curr_font_pct else Pango.FontDescription()

        # Barra
        c_bar = parse_color(settings.get("color_bar", "#FFFFFF"))

        # --- 1. Dibujar el Nombre del Dispositivo (Arriba) ---
        layout_name = PangoCairo.create_layout(ctx)
        layout_name.set_font_description(desc_name)
        layout_name.set_text(text_name, -1)
        w_name, h_name = layout_name.get_pixel_size()
        
        y_name = int(height * 0.05)
        if align_name == "left": x_name = int(width * 0.1)
        elif align_name == "right": x_name = int(width * 0.9 - w_name)
        else: x_name = int((width - w_name) / 2)

        if out_width_name > 0:
            ctx.move_to(x_name, y_name)
            PangoCairo.layout_path(ctx, layout_name)
            ctx.set_source_rgba(*c_out_name)
            ctx.set_line_width(out_width_name * 2) # Doble de grosor porque el trazo se centra en el path
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            ctx.stroke()
        
        ctx.set_source_rgba(*c_name)
        ctx.move_to(x_name, y_name)
        PangoCairo.show_layout(ctx, layout_name)

        # --- 2. Dibujar Volumen % (Centro superior) ---
        layout_pct = PangoCairo.create_layout(ctx)
        layout_pct.set_font_description(desc_pct)
        layout_pct.set_text(text_pct, -1)
        
        w_pango, h_pango = layout_pct.get_pixel_size()
        y_pct = int(height * 0.25)
        if align_pct == "left": x_pct = int(width * 0.1)
        elif align_pct == "right": x_pct = int(width * 0.9 - w_pango)
        else: x_pct = int((width - w_pango) / 2)

        if out_width_pct > 0:
            ctx.move_to(x_pct, y_pct)
            PangoCairo.layout_path(ctx, layout_pct)
            ctx.set_source_rgba(*c_out_pct)
            ctx.set_line_width(out_width_pct * 2)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            ctx.stroke()

        ctx.set_source_rgba(*c_pct)
        ctx.move_to(x_pct, y_pct)
        PangoCairo.show_layout(ctx, layout_pct)

        # --- 3. Dibujar Icono (Centro Inferior) ---
        icon_str = "🔇" if is_muted else "🔊"
        if settings.get("device_type", "sink") == "source":
            icon_str = "🛑" if is_muted else "🎙️"
            
        layout_icon = PangoCairo.create_layout(ctx)
        layout_icon.set_font_description(desc_pct)
        layout_icon.set_text(icon_str, -1)
        
        w_icon, h_icon = layout_icon.get_pixel_size()
        ctx.move_to(int((width - w_icon) / 2), int(height * 0.5))
        PangoCairo.show_layout(ctx, layout_icon)

        # --- 4. Dibujar barra de progreso (Abajo) ---
        bar_x = int(width * 0.1)
        bar_w = width - (bar_x * 2)
        bar_h = max(5, int(height * 0.06))
        bar_y = height - bar_h - int(height * 0.1)
        
        radius = max(2.0, bar_h / 2.0)
        
        # Función auxiliar para dibujar un rectángulo redondeado en Cairo
        def draw_rounded_rect(cr, x, y, w, h, r):
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -math.pi/2, 0)
            cr.arc(x + w - r, y + h - r, r, 0, math.pi/2)
            cr.arc(x + r, y + h - r, r, math.pi/2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3*math.pi/2)
            cr.close_path()

        # Fondo de la barra (#444444 aproximado = 0.26)
        ctx.set_source_rgba(0.26, 0.26, 0.26, 1.0)
        draw_rounded_rect(ctx, bar_x, bar_y, bar_w, bar_h, radius)
        ctx.fill()
        
        # Relleno de la barra
        fill_w = int(bar_w * (vol_pct / 100.0))
        if fill_w > bar_w: fill_w = bar_w
        if fill_w > 0:
            ctx.set_source_rgba(*c_bar)
            draw_rounded_rect(ctx, bar_x, bar_y, fill_w, bar_h, radius)
            ctx.fill()

        # --- 5. Extraer la imagen a PIL ---
        # El formato ARGB32 de Cairo guarda los bytes en memoria como BGRA en sistemas Little Endian
        buf = surface.get_data()
        img = Image.frombuffer("RGBA", (width, height), buf.tobytes(), "raw", "BGRA", 0, 1)

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
            self.limit_row.set_adjustment(Gtk.Adjustment(value=settings.get("volume_limit", 100), lower=1, upper=200, step_increment=1))
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
                self.exp_pct.add_row(CustomLabelRow("Centro", settings, "pct", self))
            except Exception as e:
                import traceback
                err_row = Adw.ActionRow(title=f"Error: {e}", subtitle=traceback.format_exc())
                self.exp_pct.add_row(err_row)

            self.color_bar_row = Adw.ActionRow(title=self.plugin_base.lm.get("config.color.bar.title", "Color de la barra"))
            self.color_bar_btn = Gtk.ColorButton()
            rgba_bar = Gdk.RGBA()
            rgba_bar.parse(settings.get("color_bar", "#FFFFFF"))
            self.color_bar_btn.set_rgba(rgba_bar)
            self.color_bar_btn.connect("color-set", self.on_color_bar_changed)
            self.color_bar_row.add_suffix(self.color_bar_btn)

            return [
                self.type_row, 
                self.device_row, 
                self.step_row, 
                self.limit_row, 
                self.exp_name, 
                self.exp_pct,
                self.color_bar_row
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
