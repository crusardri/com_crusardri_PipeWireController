import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, GLib, Gdk, Pango, PangoCairo, GdkPixbuf
import cairo
import globals as gl

from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

import pulsectl
import math
from PIL import Image
import traceback
import os

from .PipeWireActionBase import PipeWireActionBase, HAS_RSVG
from .UIComponents import CustomLabelRow, CustomIconRow, CustomBarRow, DeviceConfigGroup

class PipeWireAudioMixer(PipeWireActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        self.icon_cache = {}
        
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
        import time
        self.last_tick_time = 0

    def on_tick(self):
        import time
        current_time = time.time()
        if current_time - getattr(self, 'last_tick_time', 0) < 0.2:
            return
        self.last_tick_time = current_time

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
                settings = self.get_settings()
                lim_a = min(150.0, float(settings.get("volume_limit_a", 100)))
                lim_b = min(150.0, float(settings.get("volume_limit_b", 100)))
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

    def get_target_device(self, suffix):
        settings = self.get_settings()
        if suffix == "b" and not settings.get("dual_mode", False):
            return None
            
        device_type = settings.get(f"device_type_{suffix}", "sink")
        device_name = settings.get(f"device_name_{suffix}", "default")
        
        pulse = self.get_pulse()
        if not pulse:
            return None

        with self.plugin_base.pulse_lock:
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

    def get_device_friendly_name(self, suffix):
        dev = self.get_target_device(suffix)
        if dev:
            if hasattr(dev, 'proplist') and dev.proplist:
                app_name = dev.proplist.get('application.process.binary') or dev.proplist.get('application.name')
                if app_name:
                    return app_name
            if hasattr(dev, 'description') and dev.description:
                return dev.description
            if hasattr(dev, 'name') and dev.name:
                return dev.name
        
        settings = self.get_settings()
        name = settings.get(f"device_name_{suffix}", "default")
        if name == "default":
            pulse = self.get_pulse()
            if pulse:
                try:
                    with self.plugin_base.pulse_lock:
                        server_info = pulse.server_info()
                        dtype = settings.get(f"device_type_{suffix}", "sink")
                        if dtype == "sink":
                            def_name = server_info.default_sink_name
                            for d in pulse.sink_list():
                                if d.name == def_name:
                                    return d.description
                        elif dtype == "source":
                            def_name = server_info.default_source_name
                            for d in pulse.source_list():
                                if d.name == def_name:
                                    return d.description
                except Exception:
                    pass
            return "Default"
        return name

    def load_icon_as_pil(self, icon_path, target_h):
        try:
            pixbuf_info = GdkPixbuf.Pixbuf.get_file_info(icon_path)
            if pixbuf_info:
                w, h = pixbuf_info[1], pixbuf_info[2]
                target_w = int(w * (target_h / float(h))) if h > 0 else target_h
            else:
                target_w = target_h
                
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, target_w, target_h, True)
            
            import io
            success, buffer = pixbuf.save_to_bufferv("png", [], [])
            if success:
                return Image.open(io.BytesIO(buffer)).convert("RGBA")
        except Exception:
            pass
        
        try:
            pil_img = Image.open(icon_path).convert("RGBA")
            i_w = int(pil_img.width * (target_h / float(pil_img.height))) if pil_img.height > 0 else target_h
            return pil_img.resize((i_w, target_h), Image.Resampling.LANCZOS)
        except Exception:
            return Image.new("RGBA", (target_h, target_h), (0, 0, 0, 0))

    def on_toggle_mute(self, data=None):
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")
        if dev_a and dev_b and getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)):
            dev_b = None
            
        with self.plugin_base.pulse_lock:
            if dev_a: self.get_pulse().mute(dev_a, not dev_a.mute)
            if dev_b: self.get_pulse().mute(dev_b, not dev_b.mute)
        self.draw_image()

    def change_balance(self, amount):
        settings = self.get_settings()
        limit_a = min(150.0, float(settings.get("volume_limit_a", 100)))
        limit_b = min(150.0, float(settings.get("volume_limit_b", 100)))
        
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")
        if dev_a and dev_b and getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)):
            dev_b = None
            
        is_single_mode = dev_b is None
        
        if is_single_mode:
            if dev_a:
                vol_a = int(round(self.get_pulse().volume_get_all_chans(dev_a) * 100))
                if dev_a.mute: vol_a = 0
                new_vol_a = max(0.0, min(limit_a, vol_a + amount))
                with self.plugin_base.pulse_lock:
                    self.get_pulse().volume_set_all_chans(dev_a, new_vol_a / 100.0)
        else:
            vol_a = int(round(self.get_pulse().volume_get_all_chans(dev_a) * 100)) if dev_a else 0
            vol_b = int(round(self.get_pulse().volume_get_all_chans(dev_b) * 100)) if dev_b else 0
            if dev_a and dev_a.mute: vol_a = 0
            if dev_b and dev_b.mute: vol_b = 0
            
            if amount > 0:
                # Mix towards B: increase B up to limit_b, then decrease A
                space_b = max(0.0, limit_b - vol_b)
                if space_b > 0:
                    add_b = min(amount, space_b)
                    vol_b += add_b
                    amount -= add_b
                if amount > 0:
                    vol_a = max(0.0, vol_a - amount)
            else:
                # Mix towards A: increase A up to limit_a, then decrease B
                amt_abs = abs(amount)
                space_a = max(0.0, limit_a - vol_a)
                if space_a > 0:
                    add_a = min(amt_abs, space_a)
                    vol_a += add_a
                    amt_abs -= add_a
                if amt_abs > 0:
                    vol_b = max(0.0, vol_b - amt_abs)
                    
            with self.plugin_base.pulse_lock:
                if dev_a: self.get_pulse().volume_set_all_chans(dev_a, vol_a / 100.0)
                if dev_b: self.get_pulse().volume_set_all_chans(dev_b, vol_b / 100.0)
                
            pct_a = vol_a / limit_a if limit_a > 0 else 0
            pct_b = vol_b / limit_b if limit_b > 0 else 0
            if pct_a >= pct_b:
                self.internal_balance = 50.0 * pct_b
            else:
                self.internal_balance = 100.0 - (50.0 * pct_a)
            
        self.draw_image()

    def on_volume_up(self, data=None):
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(step)

    def on_volume_down(self, data=None):
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(-step)

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

        defs = self.get_calculated_defaults(is_single_mode)

        bar_style = int(settings.get("bar_style", 0))
        if is_single_mode and bar_style == 0:
            bar_style = 1
            
        bar_h_each = settings.get("bar_height", defs["bar_height"])
        if not is_single_mode and bar_style == 0:
            bar_h_each = max(1, (bar_h_each - 2) // 2)

        bar_x = settings.get("bar_x", defs["bar_x"])
        base_bar_y = settings.get("bar_y", defs["bar_y"])
        bar_w = settings.get("bar_width", defs["bar_width"])
        bar_rad = settings.get("bar_radius", defs["bar_radius"])
        
        if not is_single_mode and bar_style == 0:
            bar_rad /= 2.0
        bar_rad = min(bar_rad, bar_h_each / 2.0)

        def draw_text_section(key_suffix, text, default_y):
            align = settings.get(f"align_{key_suffix}", defs.get(f"align_{key_suffix}", def_align))
            out_width = int(settings.get(f"outline_width_{key_suffix}", def_out_width))
            c_out = self._parse_color(settings.get(f"outline_color_{key_suffix}", def_out_color))
            c_text = self._parse_color(settings.get(f"color_{key_suffix}", def_color))

            def_font = f"{def_font_family} 22" if key_suffix == "pct" else def_font_desc
            curr_font = settings.get(f"font_desc_{key_suffix}", def_font)
            desc = Pango.FontDescription.from_string(curr_font) if curr_font else Pango.FontDescription()

            layout = PangoCairo.create_layout(ctx)
            layout.set_font_description(desc)
            layout.set_text(text, -1)

            margin = defs["bar_x"]
            if f"width_{key_suffix}" in settings: max_w = settings[f"width_{key_suffix}"]
            else: max_w = defs.get(f"width_{key_suffix}", width - (margin * 2))

            layout.set_width(max_w * Pango.SCALE)
            layout.set_ellipsize(Pango.EllipsizeMode.END)

            w_pango, h_pango = layout.get_pixel_size()
            
            if f"pos_x_{key_suffix}" in settings: base_x = settings[f"pos_x_{key_suffix}"]
            else: base_x = defs.get(f"pos_x_{key_suffix}", margin)
            
            if f"pos_y_{key_suffix}" in settings: y_val = settings[f"pos_y_{key_suffix}"]
            else: y_val = defs.get(f"pos_y_{key_suffix}", default_y)
            
            if key_suffix == "pct":
                y_pos = y_val - h_pango
            else:
                y_pos = y_val

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
        
        c_bar = self._parse_color(settings.get("bar_color", "#FFFFFF"))
        c_bg = self._parse_color(settings.get("bar_bg_color", "#424242"))
        c_over = self._parse_color(settings.get("bar_over_color", "#ff4b4b"))
        c_ind = self._parse_color(settings.get("bar_ind_color", "#FFFFFF"))
        c_neu = self._parse_color(settings.get("bar_neu_color", "#808080"))

        def draw_bar_background(y_offset):
            self.draw_rounded_rect(ctx, bar_x, y_offset, bar_w, bar_h_each, bar_rad)
            ctx.set_source_rgba(*c_bg)
            ctx.fill()
            
        def draw_fill(start_x, w, rad, color, y_offset):
            self.draw_rounded_rect(ctx, start_x, y_offset, w, bar_h_each, rad)
            ctx.set_source_rgba(*color)
            ctx.fill()
            
        def draw_outline(y_offset):
            bar_out_w = settings.get("bar_out_width", defs.get("bar_out_width", 1))
            if bar_out_w > 0:
                c_bar_out = self._parse_color(settings.get("bar_out_color", defs.get("bar_out_color", "#000000")))
                ctx.set_source_rgba(*c_bar_out)
                ctx.set_line_width(bar_out_w)
                self.draw_rounded_rect(ctx, bar_x, y_offset, bar_w, bar_h_each, bar_rad)
                ctx.stroke()

        if bar_style == 0 and not is_single_mode:
            def draw_legacy_bar(y_offset, dev, limit, invert=False):
                draw_bar_background(y_offset)
                if dev:
                    vol_pct = round(self.get_pulse().volume_get_all_chans(dev) * 100)
                    if dev.mute: vol_pct = 0
                    active_vol = min(vol_pct, 100.0)
                    over_vol = min(50.0, max(0.0, vol_pct - 100.0))
                    
                    active_fill_w = int(bar_w * (active_vol / 100.0))
                    active_fill_w = min(bar_w, active_fill_w)
                    over_fill_w = int(bar_w * (over_vol / 100.0))
                    
                    if active_fill_w > 0:
                        rad = bar_rad if active_fill_w > bar_rad * 2 else active_fill_w / 2
                        start_x = bar_x if not invert else bar_x + bar_w - active_fill_w
                        draw_fill(start_x, active_fill_w, rad, c_bar, y_offset)
                        
                    if over_fill_w > 0:
                        rad = bar_rad if over_fill_w > bar_rad * 2 else over_fill_w / 2
                        start_x = bar_x if not invert else bar_x + bar_w - over_fill_w
                        draw_fill(start_x, over_fill_w, rad, c_over, y_offset)
                draw_outline(y_offset)

            limit_a = min(150.0, float(settings.get("volume_limit_a", 100)))
            limit_b = min(150.0, float(settings.get("volume_limit_b", 100)))
            draw_legacy_bar(base_bar_y, dev_a, limit_a, invert=True)
            draw_legacy_bar(base_bar_y + bar_h_each + 2, dev_b, limit_b, invert=False)
        else:
            y_offset = base_bar_y
            draw_bar_background(y_offset)
            
            fill_start_x = bar_x
            fill_w = 0
            over_fill_w = 0
            over_start_x = 0
            marker_x = bar_x
            
            if is_single_mode:
                if dev_a:
                    vol_pct = round(self.get_pulse().volume_get_all_chans(dev_a) * 100)
                    if dev_a.mute: vol_pct = 0
                    
                    active_vol = min(vol_pct, 100.0)
                    over_vol = min(50.0, max(0.0, vol_pct - 100.0))
                    
                    fill_w = int(bar_w * (active_vol / 100.0))
                    fill_w = min(bar_w, fill_w)
                    
                    over_fill_w = int(bar_w * (over_vol / 100.0))
                    over_start_x = bar_x
                    marker_x = bar_x + fill_w
            else:
                balance = self.internal_balance
                center_x = bar_x + bar_w / 2.0
                if balance < 50:
                    pct = (50.0 - balance) / 50.0
                    fill_w = int((bar_w / 2.0) * pct)
                    fill_start_x = int(center_x - fill_w)
                    marker_x = fill_start_x
                else:
                    pct = (balance - 50.0) / 50.0
                    fill_w = int((bar_w / 2.0) * pct)
                    fill_start_x = int(center_x)
                    marker_x = int(center_x + fill_w)
                    
            if fill_w > 0:
                rad = bar_rad if fill_w > bar_rad * 2 else fill_w / 2
                draw_fill(fill_start_x, fill_w, rad, c_bar, y_offset)
                
            if over_fill_w > 0:
                rad = bar_rad if over_fill_w > bar_rad * 2 else over_fill_w / 2
                draw_fill(over_start_x, over_fill_w, rad, c_over, y_offset)
                
            if not is_single_mode:
                ctx.set_source_rgba(*c_neu)
                ctx.move_to(bar_x + bar_w / 2.0, y_offset)
                ctx.line_to(bar_x + bar_w / 2.0, y_offset + bar_h_each)
                ctx.set_line_width(2)
                ctx.stroke()
                
            draw_outline(y_offset)
            
            bar_out_w = settings.get("bar_out_width", defs.get("bar_out_width", 1))
            c_bar_out = self._parse_color(settings.get("bar_out_color", defs.get("bar_out_color", "#000000")))
            
            if bar_style == 2:
                tri_w = 8
                tri_h = 8
                
                ctx.move_to(marker_x, y_offset - 2)
                ctx.line_to(marker_x - tri_w/2, y_offset - 2 - tri_h)
                ctx.line_to(marker_x + tri_w/2, y_offset - 2 - tri_h)
                ctx.close_path()
                
                ctx.move_to(marker_x, y_offset + bar_h_each + 2)
                ctx.line_to(marker_x - tri_w/2, y_offset + bar_h_each + 2 + tri_h)
                ctx.line_to(marker_x + tri_w/2, y_offset + bar_h_each + 2 + tri_h)
                ctx.close_path()
                
                ctx.set_source_rgba(*c_ind)
                ctx.fill_preserve()
                
                if bar_out_w > 0:
                    ctx.set_source_rgba(*c_bar_out)
                    ctx.set_line_width(bar_out_w)
                    ctx.stroke()
                else:
                    ctx.new_path()
                
            elif bar_style == 3:
                if bar_out_w > 0:
                    ctx.set_source_rgba(*c_bar_out)
                    ctx.move_to(marker_x, y_offset - 4)
                    ctx.line_to(marker_x, y_offset + bar_h_each + 4)
                    ctx.set_line_width(3 + bar_out_w * 2)
                    ctx.stroke()
                    
                ctx.set_source_rgba(*c_ind)
                ctx.move_to(marker_x, y_offset - 4)
                ctx.line_to(marker_x, y_offset + bar_h_each + 4)
                ctx.set_line_width(3)
                ctx.stroke()

        def draw_icon(suffix):
            icon_path = settings.get(f"icon_path_{suffix}", "")
            icon_h = settings.get(f"icon_height_{suffix}", defs[f"icon_height_{suffix}"])
            icon_x = settings.get(f"icon_x_{suffix}", defs[f"icon_x_{suffix}"])
            icon_y = settings.get(f"icon_y_{suffix}", defs[f"icon_y_{suffix}"])
            
            icon_out_w = settings.get(f"icon_out_width_{suffix}", defs[f"icon_out_width_{suffix}"])
            icon_out_c = self._parse_color(settings.get(f"icon_out_color_{suffix}", defs[f"icon_out_color_{suffix}"]))
            
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

            cache_key = f"{icon_path}_{icon_h}_{icon_out_w}_{icon_out_c}"
            if cache_key in self.icon_cache:
                pil_img = self.icon_cache[cache_key]
            else:
                try:
                    pil_img = self.load_icon_as_pil(icon_path, icon_h)
                    if icon_out_w > 0:
                        from PIL import ImageFilter
                        r, g, b, a = [int(c*255) for c in icon_out_c]
                        alpha = pil_img.split()[3]
                        expanded_alpha = alpha.filter(ImageFilter.MaxFilter(icon_out_w * 2 + 1))
                        outline_img = Image.new("RGBA", pil_img.size, (r, g, b, 255))
                        outline_img.putalpha(expanded_alpha)
                        outline_img.paste(pil_img, (0, 0), pil_img)
                        pil_img = outline_img
 
                    self.icon_cache[cache_key] = pil_img
                except Exception as e:
                    pil_img = Image.new("RGBA", (icon_h, icon_h), (0,0,0,0))
            
            dev = dev_a if suffix == "a" else dev_b
            is_muted = dev and getattr(dev, 'mute', False)
            return pil_img, icon_x, icon_y, is_muted
                
        text_name = settings.get("text_name", "")
        if not text_name:
            if is_single_mode:
                text_name = self.get_device_friendly_name("a")
            else:
                text_name = f"{self.get_device_friendly_name('a')} / {self.get_device_friendly_name('b')}"
        draw_text_section("name", text_name, 3)

        pct_format = int(settings.get("pct_format", 0))
        val = 0.0
        if is_single_mode:
            v = (self.get_pulse().volume_get_all_chans(dev_a) * 100) if dev_a else 0
            if dev_a and dev_a.mute: v = 0
            val = v
        else:
            val = self.internal_balance
            
        pct_str = ""
        if pct_format == 0:
            pct_str = f"{int(round(val))}%"
        elif pct_format == 1:
            pan = int(round((val - 50) * 2)) if not is_single_mode else int(round(val))
            pct_str = f"{pan:+}%" if not is_single_mode else f"{pan}%"
        elif pct_format == 2:
            pan = int(round((val - 50) * 2)) if not is_single_mode else int(round(val))
            pct_str = f"{pan:+}" if not is_single_mode else f"{pan}"
        elif pct_format == 3:
            if is_single_mode:
                pct_str = f"{int(round(val))}"
            else:
                if val < 49.5:
                    pct_str = f"B {int(round(val * 2))}"
                elif val > 50.5:
                    pct_str = f"A {int(round((100 - val) * 2))}"
                else:
                    pct_str = "A=B"
        elif pct_format == 4:
            if is_single_mode:
                pct_str = f"{int(round(val))}"
            else:
                if val < 49.5:
                    pct_str = f"R{int(round(val * 2))}"
                elif val > 50.5:
                    pct_str = f"L{int(round((100 - val) * 2))}"
                else:
                    pct_str = "100"
        elif pct_format == 5:
            pct_str = ""
            
        if pct_format != 5:
            draw_text_section("pct", pct_str, int(height * 0.28))

        # Composite everything
        surface_data = surface.get_data()
        cairo_img = Image.frombuffer("RGBA", (width, height), surface_data.tobytes(), "raw", "BGRA", 0, 1)
        base_img = Image.new("RGBA", (width, height), (0,0,0,0))

        if settings.get("show_icon_a", True):
            icon_a, xa, ya, mute_a = draw_icon("a")
            base_img.alpha_composite(icon_a, (xa, ya))
            if mute_a:
                from PIL import ImageDraw
                d = ImageDraw.Draw(base_img)
                d.line([(xa, ya), (xa + icon_a.width, ya + icon_a.height)], fill=(255, 0, 0, 255), width=max(3, icon_a.height // 10))
            
        if not is_single_mode and settings.get("show_icon_b", True):
            icon_b, xb, yb, mute_b = draw_icon("b")
            base_img.alpha_composite(icon_b, (xb, yb))
            if mute_b:
                from PIL import ImageDraw
                d = ImageDraw.Draw(base_img)
                d.line([(xb, yb), (xb + icon_b.width, yb + icon_b.height)], fill=(255, 0, 0, 255), width=max(3, icon_b.height // 10))
                
        cairo_img.alpha_composite(base_img)
        self.set_media(image=cairo_img)

    def get_calculated_defaults(self, is_single_mode=False):
        width, height = 100, 100
        visible_width = 100
        crop_margin_x = 0
        try:
            ctrl_input = self.get_input()
            if ctrl_input:
                width, height = ctrl_input.get_image_size()
                visible_width = width
                if hasattr(ctrl_input.deck_controller, "deck"):
                    deck = ctrl_input.deck_controller.deck
                    if hasattr(deck, "TOUCHBAR_KEY_PIXEL_WIDTH"):
                        visible_width = deck.TOUCHBAR_KEY_PIXEL_WIDTH
                        crop_margin_x = (width - visible_width) // 2
                    elif deck.deck_type() == "Mirabox StreamDeck N4":
                        visible_width = 176
                        crop_margin_x = (width - visible_width) // 2
        except Exception:
            pass
            
        width = max(32, width)
        visible_width = max(32, visible_width)
        height = max(32, height)

        defaults = {}
        bar_h = 8
        defaults["bar_height"] = bar_h
        margin = int(round(visible_width * 0.03))
        
        defaults["bar_x"] = crop_margin_x + margin
        defaults["bar_y"] = height - bar_h - int(round(height * 0.03))
        defaults["bar_width"] = visible_width - (margin * 2)
        defaults["bar_radius"] = 5
        defaults["bar_out_width"] = 1
        defaults["bar_out_color"] = "#000000"

        max_w = visible_width - (margin * 2)

        defaults["width_name"] = max_w
        defaults["pos_x_name"] = crop_margin_x + margin
        defaults["pos_y_name"] = 3
        defaults["align_name"] = "center"

        icon_size = 48
        defaults["icon_height_a"] = icon_size
        defaults["icon_x_a"] = crop_margin_x + margin
        defaults["icon_y_a"] = defaults["bar_y"] - icon_size - 4

        defaults["width_pct"] = defaults["bar_width"]
        defaults["pos_x_pct"] = defaults["bar_x"]
        defaults["pos_y_pct"] = defaults["bar_y"] - 5
        defaults["align_pct"] = "right"
        defaults["icon_out_width_a"] = 1
        defaults["icon_out_color_a"] = "#000000"

        defaults["icon_height_b"] = icon_size
        defaults["icon_x_b"] = defaults["icon_x_a"] + icon_size + 5
        defaults["icon_y_b"] = defaults["icon_y_a"]
        defaults["icon_out_width_b"] = 1
        defaults["icon_out_color_b"] = "#000000"

        return defaults

    def get_config_rows(self):
        try:
            settings = self.get_settings()
            
            self.grp_a = DeviceConfigGroup(self, self.plugin_base.lm.get("config.mixer.device_a", "Device A"), suffix="a")
            self.grp_b = DeviceConfigGroup(self, self.plugin_base.lm.get("config.mixer.device_b", "Device B"), suffix="b")
            
            grp_mode = Adw.PreferencesGroup(title="Mode")
            self.switch_dual = Adw.SwitchRow(title=self.plugin_base.lm.get("config.mixer.dual_mode", "Habilitar mezclador"))
            self.switch_dual.set_active(settings.get("dual_mode", False))
            self.switch_dual.connect("notify::active", self.on_dual_mode_change)
            grp_mode.add(self.switch_dual)
            
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

            self.grp_b.set_visible(settings.get("dual_mode", False))
            self.exp_icon_b.set_visible(settings.get("dual_mode", False))

            return [grp_mode, self.grp_a, self.grp_b, grp_misc]
        except Exception as e:
            err = Adw.ActionRow(title=f"GLOBAL ERROR: {e}", subtitle=traceback.format_exc()[-100:])
            return [err]

    def save_setting(self, key, value):
        settings = self.get_settings()
        settings[key] = value
        self.set_settings(settings)
        self.draw_image()

    def on_dual_mode_change(self, switch, pspec):
        settings = self.get_settings()
        settings["dual_mode"] = switch.get_active()
        self.set_settings(settings)
        
        self.grp_b.set_visible(switch.get_active())
        self.exp_icon_b.set_visible(switch.get_active())
        
        self.draw_image()
