import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, GLib, Gdk, Pango, PangoCairo, GdkPixbuf
import cairo
import globals as gl
import logging

from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

import pulsectl
from PIL import Image
import traceback

from .PipeWireActionBase import PipeWireActionBase, HAS_RSVG
from .UIComponents import CustomLabelRow, CustomIconRow, CustomBarRow, DeviceConfigGroup, VolumeMonitorConfigRow
from .PeakMonitor import PeakMonitor

class PipeWireAudioMixer(PipeWireActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        self.icon_cache = {}
        
        self.add_event_assigner(EventAssigner(
            id="ToggleMute",
            ui_label=self.plugin_base.lm.get("actions.pipewire-mixer.event.toggle-mute", "Toggle Mute"),
            default_events=[Input.Dial.Events.SHORT_UP],
            callback=self.on_toggle_mute
        ))

        self.add_event_assigner(EventAssigner(
            id="TouchAction",
            ui_label=self.plugin_base.lm.get("actions.pipewire-mixer.event.touch", "Touch Action"),
            default_events=[Input.Dial.Events.SHORT_TOUCH_PRESS],
            callback=self.on_touch_action
        ))

        self.carousel_active = False
        self.carousel_target = "a"
        self.carousel_index = 0
        self.carousel_devices = []
        self.carousel_last_interaction = 0

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
        
        self.peak_monitor = PeakMonitor()
        self._monitor_display_active = False
        self._last_interaction_time = time.time()

    def _extract_icon_palette(self, pil_img, num_colors=3):
        try:
            if pil_img.mode in ('RGBA', 'LA') or (pil_img.mode == 'P' and 'transparency' in pil_img.info):
                img = pil_img.convert("RGBA")
                img.thumbnail((32, 32))
                colors = img.getcolors(4096)
                if colors:
                    valid_colors = [(count, rgba) for count, rgba in colors if rgba[3] > 128]
                    if valid_colors:
                        valid_colors.sort(key=lambda x: x[0], reverse=True)
                        palette = []
                        for count, rgba in valid_colors:
                            hex_c = f"#{rgba[0]:02x}{rgba[1]:02x}{rgba[2]:02x}"
                            if hex_c not in palette:
                                palette.append(hex_c)
                            if len(palette) >= num_colors:
                                break
                        while len(palette) < num_colors:
                            palette.append(palette[-1] if palette else "#ffffff")
                        return palette
            
            img = pil_img.convert("RGB")
            img.thumbnail((32, 32))
            q = img.quantize(colors=num_colors)
            pal = q.getpalette()
            res = []
            for i in range(num_colors):
                idx = i * 3
                if idx + 2 < len(pal):
                    res.append(f"#{pal[idx]:02x}{pal[idx+1]:02x}{pal[idx+2]:02x}")
                else:
                    res.append("#ffffff")
            return res
        except Exception as e:
            logging.getLogger(__name__).debug("Color extraction error: %s", e)
            return ["#ffffff"] * num_colors

    def get_active_devices_and_mode(self):
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")
        
        if dev_a is None or dev_b is None:
            dev_b = None
        elif getattr(dev_a, 'index', id(dev_a)) == getattr(dev_b, 'index', id(dev_b)):
            dev_b = None
            
        return dev_a, dev_b, dev_b is None

    def on_tick(self):
        import time
        current_time = time.time()
        
        settings = self.get_settings()
        monitor_enabled = settings.get("monitor_enabled", False)
        monitor_delay = settings.get("monitor_delay", 5)
        monitor_fps = settings.get("monitor_fps", 10)
        
        time_since_interaction = current_time - self._last_interaction_time
        should_monitor_pre = monitor_enabled and time_since_interaction >= monitor_delay and getattr(self, "_last_single_mode", True)
        
        tick_interval = 1.0 / monitor_fps if should_monitor_pre else 0.2
        
        if current_time - getattr(self, 'last_tick_time', 0) < tick_interval:
            return
        self.last_tick_time = current_time

        if getattr(self, "carousel_active", False):
            if current_time - getattr(self, "carousel_last_interaction", 0) > 10.0:
                self.carousel_active = False
                self._force_redraw = True
            else:
                self.draw_image()
                return

        try:
            dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
            self._last_single_mode = is_single_mode
            
            should_monitor = monitor_enabled and time_since_interaction >= monitor_delay and is_single_mode
                
            with self.plugin_base.pulse_lock:
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
            
            if should_monitor:
                self.peak_monitor.start(dev_a)
            else:
                if self.peak_monitor._running:
                    self.peak_monitor.stop()

            force_draw = (should_monitor and self.peak_monitor._running) or getattr(self, "_force_redraw", False)

            if changed or force_draw or self._monitor_display_active != should_monitor:
                self._force_redraw = False
                self._monitor_display_active = should_monitor and self.peak_monitor._running
                
                self.last_state.update({
                    "vol_a": vol_a, "vol_b": vol_b,
                    "muted_a": mut_a, "muted_b": mut_b,
                    "dev_a": nm_a, "dev_b": nm_b
                })
                
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
        except Exception as e:
            logging.getLogger(__name__).debug("on_tick error: %s", e)

    def on_ready(self):
        self.draw_image()
        import gi
        gi.require_version('GLib', '2.0')
        from gi.repository import GLib
        GLib.timeout_add(40, self.on_fast_tick)

    def on_fast_tick(self):
        self.on_tick()
        return True

    def get_target_device(self, suffix):
        settings = self.get_settings()
        if suffix == "b" and not settings.get("dual_mode", False):
            return None
            
        device_type = settings.get(f"device_type_{suffix}", "sink")
        device_name = settings.get(f"device_name_{suffix}")
        if not device_name:
            device_name = "Auto" if device_type == "application" else "default"
        
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
                    except (ValueError, IndexError) as e:
                        logging.getLogger(__name__).debug("Error getting auto index: %s", e)
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
            
            if device_name == "default" and len(devices) > 0:
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
        dtype = settings.get(f"device_type_{suffix}", "sink")
        name = settings.get(f"device_name_{suffix}")
        if not name:
            name = "Auto" if dtype == "application" else "default"
            
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
                except Exception as e:
                    logging.getLogger(__name__).debug("Error getting default name: %s", e)
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
        except Exception as e:
            logging.getLogger(__name__).debug("Error loading icon with GdkPixbuf: %s", e)
        
        try:
            pil_img = Image.open(icon_path).convert("RGBA")
            i_w = int(pil_img.width * (target_h / float(pil_img.height))) if pil_img.height > 0 else target_h
            return pil_img.resize((i_w, target_h), Image.Resampling.LANCZOS)
        except Exception as e:
            logging.getLogger(__name__).debug("Error resizing PIL image: %s", e)
            return Image.new("RGBA", (target_h, target_h), (0, 0, 0, 0))

    def _build_carousel_device_list(self):
        settings = self.get_settings()
        devices = []
        with self.plugin_base.pulse_lock:
            pulse = self.get_pulse()
            if settings.get("carousel_show_default_sink", True):
                def_sink = pulse.server_info().default_sink_name
                dev = next((d for d in pulse.sink_list() if d.name == def_sink), None)
                if dev: devices.append({"type": "sink", "id": dev.index, "name": "Default Sink", "target_name": "default", "dev": dev})
            
            if settings.get("carousel_show_default_source", False):
                def_source = pulse.server_info().default_source_name
                dev = next((d for d in pulse.source_list() if d.name == def_source), None)
                if dev: devices.append({"type": "source", "id": dev.index, "name": "Default Source", "target_name": "default", "dev": dev})
                
            if settings.get("carousel_show_sinks", False):
                for dev in pulse.sink_list():
                    devices.append({"type": "sink", "id": dev.index, "name": dev.description, "target_name": dev.name, "dev": dev})
                    
            if settings.get("carousel_show_sources", False):
                for dev in pulse.source_list():
                    devices.append({"type": "source", "id": dev.index, "name": dev.description, "target_name": dev.name, "dev": dev})
                    
            if settings.get("carousel_show_apps", True):
                for dev in pulse.sink_input_list():
                    t_name = dev.proplist.get('application.process.binary') or dev.proplist.get('application.name')
                    if t_name:
                        devices.append({"type": "app", "id": dev.index, "name": t_name, "target_name": t_name, "dev": dev})
        
        seen = set()
        unique_devices = []
        for d in devices:
            if d['type'] == 'app':
                key = f"app_{d['target_name']}"
            else:
                key = f"{d['type']}_{d['id']}"
                
            if key not in seen:
                seen.add(key)
                unique_devices.append(d)
                
        return unique_devices

    def switch_carousel_target(self, new_target):
        self.carousel_target = new_target
        settings = self.get_settings()
        cur_target_str = settings.get(f"device_target_{new_target}", "auto")
        found = False
        for i, d in enumerate(self.carousel_devices):
            target_id = f"{d['type']}_{d['id']}"
            if cur_target_str == target_id:
                self.carousel_index = i
                found = True
                break
        if not found:
            self.carousel_index = 0

    def on_touch_action(self, data=None):
        import time
        self._last_interaction_time = time.time()
        
        settings = self.get_settings()
        if not settings.get("carousel_enabled", False):
            return

        is_single_mode = not settings.get("dual_mode", False)
        
        if getattr(self, "carousel_active", False):
            self.carousel_last_interaction = time.time()
            if not is_single_mode and getattr(self, "carousel_target", "a") == "b":
                # Target B -> Cancel
                self.carousel_active = False
                self._force_redraw = True
            elif not is_single_mode and getattr(self, "carousel_target", "a") == "a":
                # Target A -> Switch to Target B
                self.switch_carousel_target("b")
            else:
                # Single mode -> Do nothing
                pass
        else:
            self.carousel_devices = self._build_carousel_device_list()
            if not self.carousel_devices:
                return
            
            self.carousel_active = True
            self.carousel_last_interaction = time.time()
            self.switch_carousel_target("a")
                
        self.last_tick_time = 0
        self.on_tick()

    def on_toggle_mute(self, data=None):
        import time
        self._last_interaction_time = time.time()
        
        if getattr(self, "carousel_active", False):
            self.carousel_active = False
            if getattr(self, "carousel_devices", []) and self.carousel_index < len(self.carousel_devices):
                selected = self.carousel_devices[self.carousel_index]
                dev_type_carousel = selected['type']
                dev_type = "application" if dev_type_carousel == "app" else dev_type_carousel
                target_name = selected.get('target_name', '')
                
                if getattr(self, "carousel_target", "a") == "a":
                    self.save_setting("device_type_a", dev_type)
                    self.save_setting("device_name_a", target_name)
                else:
                    self.save_setting("device_type_b", dev_type)
                    self.save_setting("device_name_b", target_name)
            self._force_redraw = True
            self.last_tick_time = 0
            self.on_tick()
            return
            
        dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
            
        with self.plugin_base.pulse_lock:
            if dev_a: self.get_pulse().mute(dev_a, not dev_a.mute)
            if dev_b: self.get_pulse().mute(dev_b, not dev_b.mute)
        self.last_tick_time = 0
        self.on_tick()

    def change_balance(self, amount):
        import time
        self._last_interaction_time = time.time()
        settings = self.get_settings()
        limit_a = min(150.0, float(settings.get("volume_limit_a", 100)))
        limit_b = min(150.0, float(settings.get("volume_limit_b", 100)))
        
        dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
        
        if is_single_mode:
            if dev_a:
                vol_a = int(round(self.get_pulse().volume_get_all_chans(dev_a) * 100))
                if dev_a.mute: vol_a = 0
                new_vol_a = max(0.0, min(limit_a, vol_a + amount))
                with self.plugin_base.pulse_lock:
                    self.get_pulse().volume_set_all_chans(dev_a, new_vol_a / 100.0)
        else:
            with self.plugin_base.pulse_lock:
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
            
        self.last_tick_time = 0
        self.on_tick()

    def on_volume_up(self, data=None):
        if getattr(self, "carousel_active", False):
            import time
            self._last_interaction_time = time.time()
            self.carousel_last_interaction = time.time()
            devs = getattr(self, "carousel_devices", [])
            if devs:
                self.carousel_index = (getattr(self, "carousel_index", 0) + 1) % len(devs)
                self.last_tick_time = 0
                self.on_tick()
            return
            
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(step)

    def on_volume_down(self, data=None):
        if getattr(self, "carousel_active", False):
            import time
            self._last_interaction_time = time.time()
            self.carousel_last_interaction = time.time()
            devs = getattr(self, "carousel_devices", [])
            if devs:
                self.carousel_index = (getattr(self, "carousel_index", 0) - 1) % len(devs)
                self.last_tick_time = 0
                self.on_tick()
            return
            
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(-step)

    def draw_image(self):
        if getattr(self, "carousel_active", False):
            self._draw_carousel()
            return
            
        settings = self.get_settings()
        dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
        
        defs, width, height = self.get_calculated_defaults()
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

        bar_style = int(settings.get("bar_style", 0))
        if is_single_mode and bar_style == 0:
            bar_style = 1
            
        bar_h_each = settings.get("bar_height", defs["bar_height"])
        
        is_monitor_active = getattr(self, "_monitor_display_active", False) and getattr(self, "peak_monitor", None)
        mon_bar_mode = int(settings.get("monitor_bar_mode", 0))
        
        if is_monitor_active:
            if mon_bar_mode != 0:
                bar_h_each = max(1, (bar_h_each - 2) // 2)
        else:
            if not is_single_mode and bar_style == 0:
                bar_h_each = max(1, (bar_h_each - 2) // 2)

        bar_x = settings.get("bar_x", defs["bar_x"])
        base_bar_y = settings.get("bar_y", defs["bar_y"])
        bar_w = settings.get("bar_width", defs["bar_width"])
        bar_rad = settings.get("bar_radius", defs["bar_radius"])
        
        if (is_monitor_active and mon_bar_mode != 0) or (not is_monitor_active and not is_single_mode and bar_style == 0):
            bar_rad /= 2.0
        bar_rad = min(bar_rad, bar_h_each / 2.0)

        def get_icon_data(suffix):
            icon_path = settings.get(f"icon_path_{suffix}", "")
            import os
            icon_h = settings.get(f"icon_height_{suffix}", defs[f"icon_height_{suffix}"])
            icon_x = settings.get(f"icon_x_{suffix}", defs[f"icon_x_{suffix}"])
            icon_y = settings.get(f"icon_y_{suffix}", defs[f"icon_y_{suffix}"])
            
            icon_out_w = settings.get(f"icon_out_width_{suffix}", defs[f"icon_out_width_{suffix}"])
            icon_out_c = self._parse_color(settings.get(f"icon_out_color_{suffix}", defs[f"icon_out_color_{suffix}"]))
            
            if not icon_path or not os.path.isfile(icon_path):
                dtype = settings.get(f"device_type_{suffix}", "sink")
                if dtype == "application":
                    auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
                    dev_name = settings.get(f"device_name_{suffix}") or (auto_prefix + "1")
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
                            logging.getLogger(__name__).debug("Error finding icon: %s", e)
                            
                    if not found_icon:
                        icon_path = os.path.join(self.plugin_base.PATH, "assets", "speaker.svg")
                elif dtype == "source":
                    icon_path = os.path.join(self.plugin_base.PATH, "assets", "mic.svg")
                else:
                    icon_path = os.path.join(self.plugin_base.PATH, "assets", "speaker.svg")

            cache_key = f"{icon_path}_{icon_h}_{icon_out_w}_{icon_out_c}"
            if cache_key in self.icon_cache:
                pil_img, pal = self.icon_cache[cache_key]
            else:
                try:
                    pil_img = self.load_icon_as_pil(icon_path, icon_h)
                    pal = self._extract_icon_palette(pil_img, 3)
                    if icon_out_w > 0:
                        from PIL import ImageFilter
                        r, g, b, a = [int(c*255) for c in icon_out_c]
                        alpha = pil_img.split()[3]
                        expanded_alpha = alpha.filter(ImageFilter.MaxFilter(icon_out_w * 2 + 1))
                        outline_img = Image.new("RGBA", pil_img.size, (r, g, b, 255))
                        outline_img.putalpha(expanded_alpha)
                        outline_img.paste(pil_img, (0, 0), pil_img)
                        pil_img = outline_img
                    self.icon_cache[cache_key] = (pil_img, pal)
                except Exception as e:
                    logging.getLogger(__name__).warning("Error generating icon: %s", e)
                    pil_img = Image.new("RGBA", (icon_h, icon_h), (0,0,0,0))
                    pal = ["#ffffff", "#ffffff", "#ffffff"]
            
            dev = dev_a if suffix == "a" else dev_b
            is_muted = dev and getattr(dev, 'mute', False)
            return pil_img, icon_x, icon_y, is_muted, pal

        icon_data_a = get_icon_data("a") if settings.get("show_icon_a", True) else None
        icon_data_b = get_icon_data("b") if not is_single_mode and settings.get("show_icon_b", True) else None

        def get_auto_color(prefix, suffix, index=0):
            data = icon_data_a if suffix == "a" else icon_data_b
            base_color = data[4][index] if data and len(data[4]) > index else "#ffffff"
            def_darken = 50 if prefix == "bar_bg" else 0
            darken_pct = int(settings.get(f"{prefix}_auto_darken", def_darken))
            if darken_pct > 0:
                r, g, b = int(base_color[1:3], 16), int(base_color[3:5], 16), int(base_color[5:7], 16)
                f = max(0.0, 1.0 - (darken_pct / 100.0))
                return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"
            return base_color

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
        
        def get_gradient_source(prefix, suffix, x, w, invert):
            if invert:
                lg = cairo.LinearGradient(x + w, 0, x, 0)
            else:
                lg = cairo.LinearGradient(x, 0, x + w, 0)
                
            cmode = int(settings.get(f"{prefix}_color_mode", 0))
            if cmode == 3 or (prefix == "monitor" and cmode == 4):
                stops = 3
                for i in range(stops):
                    c = self._parse_color(get_auto_color(prefix, suffix, i))
                    lg.add_color_stop_rgba(i / (stops - 1), *c)
            else:
                stops = int(settings.get(f"{prefix}_gradient_stops", 3))
                default_colors = ["#00ff00", "#ffff00", "#ff0000", "#00ffff", "#ffff00", "#ff00ff"]
                for i in range(stops):
                    c = self._parse_color(settings.get(f"{prefix}_gradient_{i+1}", default_colors[i] if i < len(default_colors) else "#ffffff"))
                    lg.add_color_stop_rgba(i / (stops - 1), *c)
            return lg

        def get_bar_color_source(prefix, suffix, def_color, invert=None):
            cmode = int(settings.get(f"{prefix}_color_mode", 0))
            if cmode == 0:
                return self._parse_color(settings.get(f"{prefix}_color", def_color))
            elif cmode == 2:
                return self._parse_color(get_auto_color(prefix, suffix, 0))
            if invert is None: invert = settings.get("bar_invert", False)
            return get_gradient_source(prefix, suffix, bar_x, bar_w, invert)
            
        c_over = self._parse_color(settings.get("bar_over_color", "#ff4b4b"))
        c_ind = self._parse_color(settings.get("bar_ind_color", "#FFFFFF"))
        c_neu = self._parse_color(settings.get("bar_neu_color", "#808080"))

        def draw_bar_background(y_offset, suffix="a", invert=None):
            c_bg = get_bar_color_source("bar_bg", suffix, "#424242", invert)
            self.draw_rounded_rect(ctx, bar_x, y_offset, bar_w, bar_h_each, bar_rad)
            if isinstance(c_bg, cairo.LinearGradient):
                ctx.set_source(c_bg)
            else:
                ctx.set_source_rgba(*c_bg)
            ctx.fill()
            
        def draw_fill(start_x, w, rad, color, y_offset, corner_flags=None):
            if corner_flags is None:
                tl = tr = br = bl = rad
            else:
                tl = rad if corner_flags[0] else 0
                tr = rad if corner_flags[1] else 0
                br = rad if corner_flags[2] else 0
                bl = rad if corner_flags[3] else 0
                
            self.draw_rounded_rect_custom(ctx, start_x, y_offset, w, bar_h_each, tl, tr, br, bl)
            if isinstance(color, cairo.LinearGradient):
                ctx.set_source(color)
            else:
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

        def draw_monitor_meter(y_offset, suffix, peak_db, rms_db, width, rad):
            invert = settings.get("monitor_invert", False)
            draw_bar_background(y_offset, suffix, invert)
            
            cmode = int(settings.get("monitor_color_mode", 0))
            min_db = -60.0
            max_db = 0.0
            pct = (peak_db - min_db) / (max_db - min_db)
            pct = max(0.0, min(1.0, pct))
            
            fill_w = int(width * pct)
            if fill_w > 0:
                rad_fill = rad if fill_w > rad * 2 else fill_w / 2
                fill_x = (bar_x + width - fill_w) if invert else bar_x
                
                if cmode == 0:
                    c_fill = self._parse_color(settings.get("monitor_color_solid", "#ffffff"))
                    draw_fill(fill_x, fill_w, rad_fill, c_fill, y_offset)
                elif cmode == 3:
                    c_fill = self._parse_color(get_auto_color("monitor", suffix, 0))
                    draw_fill(fill_x, fill_w, rad_fill, c_fill, y_offset)
                else:
                    self.draw_rounded_rect_custom(ctx, fill_x, y_offset, fill_w, bar_h_each, rad_fill, rad_fill, rad_fill, rad_fill)
                    if cmode == 1:
                        if invert:
                            lg = cairo.LinearGradient(bar_x + width, 0, bar_x, 0)
                        else:
                            lg = cairo.LinearGradient(bar_x, 0, bar_x + width, 0)
                        c_low = self._parse_color(settings.get("monitor_color_low", "#00ff00"))
                        c_mid = self._parse_color(settings.get("monitor_color_mid", "#ffff00"))
                        c_high = self._parse_color(settings.get("monitor_color_high", "#ff0000"))
                        t_mid = float(settings.get("monitor_threshold_mid", -20))
                        t_high = float(settings.get("monitor_threshold_high", -9))
                        
                        pct_mid = max(0.0, min(1.0, (t_mid - min_db) / (max_db - min_db)))
                        pct_high = max(0.0, min(1.0, (t_high - min_db) / (max_db - min_db)))
                        
                        lg.add_color_stop_rgba(0.0, *c_low)
                        lg.add_color_stop_rgba(max(0.0, pct_mid-0.001), *c_low)
                        lg.add_color_stop_rgba(pct_mid, *c_mid)
                        lg.add_color_stop_rgba(max(0.0, pct_high-0.001), *c_mid)
                        lg.add_color_stop_rgba(pct_high, *c_high)
                    elif cmode == 2 or cmode == 4:
                        lg = get_gradient_source("monitor", suffix, bar_x, width, invert)
                            
                    ctx.set_source(lg)
                    ctx.fill()
            draw_outline(y_offset)
            
            if settings.get("monitor_show_rms", False):
                pct_rms = (rms_db - min_db) / (max_db - min_db)
                pct_rms = max(0.0, min(1.0, pct_rms))
                
                if invert:
                    rms_x = bar_x + width - int(width * pct_rms)
                    rms_x = min(bar_x + width - 1, max(bar_x + 1, rms_x))
                else:
                    rms_x = bar_x + int(width * pct_rms)
                    rms_x = min(bar_x + width - 2, max(bar_x, rms_x - 1))
                
                c_rms = self._parse_color(settings.get("monitor_rms_color", "#FFFFFF"))
                
                rms_out_w = float(settings.get("monitor_rms_out_width", 1.0))
                if rms_out_w > 0:
                    c_rms_out = self._parse_color(settings.get("monitor_rms_out_color", "#000000"))
                    ctx.set_source_rgba(*c_rms_out)
                    ctx.set_line_width(2.0 + (rms_out_w * 2))
                    ctx.move_to(rms_x, y_offset)
                    ctx.line_to(rms_x, y_offset + bar_h_each)
                    ctx.stroke()
                    
                ctx.set_source_rgba(*c_rms)
                ctx.set_line_width(2.0)
                ctx.move_to(rms_x, y_offset)
                ctx.line_to(rms_x, y_offset + bar_h_each)
                ctx.stroke()

        if getattr(self, "_monitor_display_active", False) and getattr(self, "peak_monitor", None):
            (peak_l, peak_r), (rms_l, rms_r) = self.peak_monitor.get_peak()
            
            is_muted_a = self.last_state.get("muted_a", False)
            is_muted_b = self.last_state.get("muted_b", False)
            
            if mon_bar_mode == 0:
                if is_muted_a:
                    peak_l = peak_r = rms_l = rms_r = 0.0
                peak_db = PeakMonitor.linear_to_db((peak_l + peak_r) / 2.0)
                rms_db = PeakMonitor.linear_to_db((rms_l + rms_r) / 2.0)
                draw_monitor_meter(base_bar_y, "a", peak_db, rms_db, bar_w, bar_rad)
            else:
                if is_muted_a: peak_l = peak_r = rms_l = rms_r = 0.0
                peak_l_db = PeakMonitor.linear_to_db(peak_l)
                peak_r_db = PeakMonitor.linear_to_db(peak_r)
                rms_l_db = PeakMonitor.linear_to_db(rms_l)
                rms_r_db = PeakMonitor.linear_to_db(rms_r)
                draw_monitor_meter(base_bar_y, "a", peak_l_db, rms_l_db, bar_w, bar_rad)
                draw_monitor_meter(base_bar_y + bar_h_each + 2, "a", peak_r_db, rms_r_db, bar_w, bar_rad)
                
        elif bar_style == 0 and not is_single_mode:
            def draw_legacy_bar(y_offset, suffix, dev, limit, invert=False):
                draw_bar_background(y_offset, suffix, invert)
                c_bar = get_bar_color_source("bar", suffix, "#FFFFFF", invert)
                if dev:
                    with self.plugin_base.pulse_lock:
                        vol_pct = round(self.get_pulse().volume_get_all_chans(dev) * 100)
                        is_mute = dev.mute
                    if is_mute: vol_pct = 0
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

            bar_invert = settings.get("bar_invert", False)
            limit_a = min(150.0, float(settings.get("volume_limit_a", 100)))
            limit_b = min(150.0, float(settings.get("volume_limit_b", 100)))
            draw_legacy_bar(base_bar_y, "a", dev_a, limit_a, invert=not bar_invert)
            draw_legacy_bar(base_bar_y + bar_h_each + 2, "b", dev_b, limit_b, invert=bar_invert)
        else:
            bar_invert = settings.get("bar_invert", False)
            y_offset = base_bar_y
            draw_bar_background(y_offset, "a", bar_invert)
            c_bar = get_bar_color_source("bar", "a", "#FFFFFF", bar_invert)
            
            fill_start_x = bar_x
            fill_w = 0
            over_fill_w = 0
            over_start_x = 0
            marker_x = bar_x
            
            if is_single_mode:
                if dev_a:
                    with self.plugin_base.pulse_lock:
                        vol_pct = round(self.get_pulse().volume_get_all_chans(dev_a) * 100)
                        is_mute = dev_a.mute
                    if is_mute: vol_pct = 0
                    
                    active_vol = min(vol_pct, 100.0)
                    over_vol = min(50.0, max(0.0, vol_pct - 100.0))
                    
                    fill_w = int(bar_w * (active_vol / 100.0))
                    fill_w = min(bar_w, fill_w)
                    
                    over_fill_w = int(bar_w * (over_vol / 100.0))
                    
                    if bar_invert:
                        fill_start_x = bar_x + bar_w - fill_w
                        over_start_x = bar_x + bar_w - over_fill_w
                        marker_x = bar_x + bar_w - fill_w
                    else:
                        fill_start_x = bar_x
                        over_start_x = bar_x
                        marker_x = bar_x + fill_w
            else:
                balance = self.internal_balance
                center_x = bar_x + bar_w / 2.0
                corner_flags = [True, True, True, True]
                if balance < 50:
                    pct = (50.0 - balance) / 50.0
                    fill_w = int((bar_w / 2.0) * pct)
                    fill_start_x = int(center_x - fill_w)
                    marker_x = fill_start_x
                    corner_flags = [True, False, False, True] # Sharp on the right side
                else:
                    pct = (balance - 50.0) / 50.0
                    fill_w = int((bar_w / 2.0) * pct)
                    fill_start_x = int(center_x)
                    marker_x = int(center_x + fill_w)
                    corner_flags = [False, True, True, False] # Sharp on the left side
                    
            if fill_w > 0:
                rad = bar_rad if fill_w > bar_rad * 2 else fill_w / 2
                if is_single_mode:
                    draw_fill(fill_start_x, fill_w, rad, c_bar, y_offset)
                else:
                    draw_fill(fill_start_x, fill_w, rad, c_bar, y_offset, corner_flags)
                
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
            with self.plugin_base.pulse_lock:
                v = (self.get_pulse().volume_get_all_chans(dev_a) * 100) if dev_a else 0
                is_mute = dev_a.mute if dev_a else False
            if is_mute: v = 0
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
                    pct_str = f"B{int(round(val * 2))}"
                elif val > 50.5:
                    pct_str = f"A{int(round((100 - val) * 2))}"
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
            
        if getattr(self, "_monitor_display_active", False) and settings.get("monitor_show_db", False) and getattr(self, "peak_monitor", None):
            is_muted_a = self.last_state.get("muted_a", False)
            is_muted_b = self.last_state.get("muted_b", False)
            
            if is_single_mode and is_muted_a:
                pct_str = "--"
            elif not is_single_mode and is_muted_a and is_muted_b:
                pct_str = "--"
            else:
                (peak_l, peak_r), (rms_l, rms_r) = self.peak_monitor.get_peak()
                if is_muted_a: peak_l = 0.0
                if not is_single_mode and is_muted_b: peak_r = 0.0
                
                avg_peak = (peak_l + peak_r) / 2.0
                peak_db = PeakMonitor.linear_to_db(avg_peak)
                if peak_db <= -60:
                    pct_str = "-inf dB"
                else:
                    pct_str = f"{peak_db:.1f} dB"
            draw_text_section("pct", pct_str, int(height * 0.28))
        elif pct_format != 5:
            draw_text_section("pct", pct_str, int(height * 0.28))

        # Composite everything
        surface_data = surface.get_data()
        cairo_img = Image.frombuffer("RGBA", (width, height), surface_data.tobytes(), "raw", "BGRA", 0, 1)
        base_img = Image.new("RGBA", (width, height), (0,0,0,0))

        if icon_data_a:
            icon_a, xa, ya, mute_a, _ = icon_data_a
            base_img.alpha_composite(icon_a, (xa, ya))
            if mute_a:
                from PIL import ImageDraw
                d = ImageDraw.Draw(base_img)
                d.line([(xa, ya), (xa + icon_a.width, ya + icon_a.height)], fill=(255, 0, 0, 255), width=max(3, icon_a.height // 10))
            
        if icon_data_b:
            icon_b, xb, yb, mute_b, _ = icon_data_b
            base_img.alpha_composite(icon_b, (xb, yb))
            if mute_b:
                from PIL import ImageDraw
                d = ImageDraw.Draw(base_img)
                d.line([(xb, yb), (xb + icon_b.width, yb + icon_b.height)], fill=(255, 0, 0, 255), width=max(3, icon_b.height // 10))
                
        cairo_img.alpha_composite(base_img)
        self.set_media(image=cairo_img)

    def get_calculated_defaults(self):
        ctrl_input = self.get_input()
        is_single_mode = self.get_settings().get("dual_mode", False) == False
        defs, width, height = super().get_calculated_defaults(ctrl_input, is_single_mode)
        
        # Override specific needs
        bar_h = 8
        defs["bar_height"] = bar_h
        defs["pos_y_carousel_name"] = defs.get("pos_y_name", int(round(height * 0.05)))
        defs["pos_y_carousel_pct"] = height - int(round(height * 0.05))
        
        margin = defs.get("bar_x", int(width * 0.05))
        defs["pos_x_carousel_name"] = margin
        defs["pos_x_carousel_pct"] = margin
        defs["width_carousel_name"] = width - (margin * 2)
        defs["width_carousel_pct"] = width - (margin * 2)
        defs["bar_y"] = height - bar_h - int(round(height * 0.03))
        defs["bar_radius"] = 5
        defs["bar_out_width"] = 1
        defs["bar_out_color"] = "#000000"
        
        margin = defs.get("bar_x", 5)
        defs["width_name"] = width - (margin * 2)
        defs["pos_x_name"] = margin
        defs["pos_y_name"] = 3
        defs["align_name"] = "center"

        icon_size = 48
        defs["icon_height_a"] = icon_size
        defs["icon_x_a"] = margin
        defs["icon_y_a"] = defs["bar_y"] - icon_size - 4

        defs["width_pct"] = defs["bar_width"]
        defs["pos_x_pct"] = defs["bar_x"]
        defs["pos_y_pct"] = defs["bar_y"] - 5
        defs["align_pct"] = "right"
        defs["icon_out_width_a"] = 1
        defs["icon_out_color_a"] = "#000000"

        defs["icon_height_b"] = icon_size
        defs["icon_x_b"] = defs["icon_x_a"] + icon_size + 5
        defs["icon_y_b"] = defs["icon_y_a"]
        defs["icon_out_width_b"] = 1
        defs["icon_out_color_b"] = "#000000"
        
        return defs, width, height

    def get_config_rows(self):
        try:
            settings = self.get_settings()
            
            self.exp_dev_a = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.mixer.device_a", "Device A (Left)"))
            self.grp_a = DeviceConfigGroup(self, suffix="a")
            self.exp_dev_a.add_row(self.grp_a)
            
            self.exp_dev_b = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.mixer.device_b", "Device B (Right)"))
            self.grp_b = DeviceConfigGroup(self, suffix="b")
            self.exp_dev_b.add_row(self.grp_b)
            
            grp_mode = Adw.PreferencesGroup(title=self.plugin_base.lm.get("config.mode.title", "Mode"))
            self.switch_dual = Adw.SwitchRow(title=self.plugin_base.lm.get("config.mixer.dual_mode", "Enable Mixer"))
            self.switch_dual.set_active(settings.get("dual_mode", False))
            self.switch_dual.connect("notify::active", self.on_dual_mode_change)
            grp_mode.add(self.switch_dual)
            
            grp_misc = Adw.PreferencesGroup(title=self.plugin_base.lm.get("config.mixer.settings.title", "Mixer Settings"))
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
            
            self.exp_monitor = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.monitor.title", "Volume Monitor"))
            self.exp_monitor.add_row(VolumeMonitorConfigRow(settings, self))
            grp_misc.add(self.exp_monitor)
            
            from .UIComponents import CarouselConfigRow
            self.exp_carousel = Adw.ExpanderRow(title=self.plugin_base.lm.get("config.carousel.title", "Carousel"))
            self.exp_carousel.add_row(CarouselConfigRow(settings, self))
            grp_misc.add(self.exp_carousel)

            self.exp_dev_b.set_visible(settings.get("dual_mode", False))
            self.exp_icon_b.set_visible(settings.get("dual_mode", False))

            return [grp_mode, self.exp_dev_a, self.exp_dev_b, grp_misc]
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
        
        self.exp_dev_b.set_visible(switch.get_active())
        self.exp_icon_b.set_visible(switch.get_active())
        
        self.draw_image()

    def _draw_carousel(self):
        try:
            import cairo, os
            from PIL import Image
            from gi.repository import Pango, PangoCairo, Gtk, Gdk
            
            settings = self.get_settings()
            defs, width, height = self.get_calculated_defaults()
            
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            ctx = cairo.Context(surface)
            
            if not getattr(self, "carousel_devices", []):
                layout = PangoCairo.create_layout(ctx)
                layout.set_text(self.plugin_base.lm.get("config.carousel.no_devices", "No devices"), -1)
                ctx.set_source_rgba(1, 1, 1, 1)
                w_pango, h_pango = layout.get_pixel_size()
                ctx.move_to((width - w_pango) // 2, (height - h_pango) // 2)
                PangoCairo.show_layout(ctx, layout)
                cairo_img = Image.frombuffer("RGBA", (width, height), surface.get_data().tobytes(), "raw", "BGRA", 0, 1)
                self.set_media(image=cairo_img)
                return

            num_devs = len(self.carousel_devices)
            center_idx = getattr(self, "carousel_index", 0)
            left_idx = (center_idx - 1) % num_devs
            right_idx = (center_idx + 1) % num_devs
            
            icon_size_center = 48
            icon_size_side = 28
            
            center_y = (height - icon_size_center) // 2
            side_y = (height - icon_size_side) // 2
            
            center_x = (width - icon_size_center) // 2
            left_x = int(width * 0.1)
            right_x = int(width * 0.9) - icon_size_side
            
            def get_icon_path(dev_dict):
                icon_path = ""
                if dev_dict['type'] == 'app':
                    target_app_icon = dev_dict['dev'].proplist.get('application.icon_name') or dev_dict['dev'].proplist.get('application.process.binary')
                    if target_app_icon:
                        try:
                            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
                            icon_info = theme.lookup_icon(target_app_icon, None, 48, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.NONE)
                            if icon_info: 
                                found_path = icon_info.get_file().get_path()
                                if found_path: icon_path = found_path
                        except: pass
                
                if not icon_path:
                    if dev_dict['type'] == 'source':
                        icon_path = os.path.join(self.plugin_base.PATH, "assets", "mic.svg")
                    else:
                        icon_path = os.path.join(self.plugin_base.PATH, "assets", "speaker.svg")
                return icon_path

            def draw_device_icon(idx, x, y, size, opacity):
                d = self.carousel_devices[idx]
                path = get_icon_path(d)
                try:
                    pil_img = self.load_icon_as_pil(path, size)
                    if opacity < 255:
                        pil_img.putalpha(pil_img.getchannel("A").point(lambda p: int(p * (opacity / 255.0))))
                    base_img.alpha_composite(pil_img, (x, y))
                except Exception:
                    pass

            base_img = Image.new("RGBA", (width, height), (0,0,0,0))
            if num_devs >= 3:
                draw_device_icon(left_idx, left_x, side_y, icon_size_side, 230)
                draw_device_icon(right_idx, right_x, side_y, icon_size_side, 230)
            elif num_devs == 2:
                draw_device_icon(right_idx, right_x, side_y, icon_size_side, 230)
                
            draw_device_icon(center_idx, center_x, center_y, icon_size_center, 255)

            # TEXT RENDERING WITH PANGOCAIRO
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

            def draw_text_section(key_suffix, text, default_y, is_pct=False):
                align = settings.get(f"align_{key_suffix}", def_align)
                out_width = int(settings.get(f"outline_width_{key_suffix}", def_out_width))
                c_out = self._parse_color(settings.get(f"outline_color_{key_suffix}", def_out_color))
                c_text = self._parse_color(settings.get(f"color_{key_suffix}", def_color))

                curr_font = settings.get(f"font_desc_{key_suffix}", def_font_desc)
                desc = Pango.FontDescription.from_string(curr_font) if curr_font else Pango.FontDescription()

                layout = PangoCairo.create_layout(ctx)
                layout.set_font_description(desc)
                layout.set_text(text, -1)

                margin = defs.get("bar_x", int(width * 0.05))
                if f"width_{key_suffix}" in settings: max_w = settings[f"width_{key_suffix}"]
                else: max_w = width - (margin * 2)

                layout.set_width(max_w * Pango.SCALE)
                layout.set_ellipsize(Pango.EllipsizeMode.END)

                w_pango, h_pango = layout.get_pixel_size()

                if f"pos_x_{key_suffix}" in settings: base_x = settings[f"pos_x_{key_suffix}"]
                else: base_x = margin

                if f"pos_y_{key_suffix}" in settings: y_val = settings[f"pos_y_{key_suffix}"]
                else: y_val = default_y

                if is_pct:
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

            current_dev = self.carousel_devices[center_idx]
            dev_name = current_dev["name"]
            
            vol_pct = 0
            is_muted = False
            if current_dev["dev"]:
                with self.plugin_base.pulse_lock:
                    vol_pct = int(round(self.get_pulse().volume_get_all_chans(current_dev["dev"]) * 100))
                    is_muted = current_dev["dev"].mute
            
            if is_muted:
                vol_pct = 0
                
            name_y = defs.get("pos_y_name", int(round(height * 0.05)))
            pct_y = height - int(round(height * 0.05))

            draw_text_section("carousel_name", dev_name, name_y, is_pct=False)
            draw_text_section("carousel_pct", f"{vol_pct}%", pct_y, is_pct=True)

            is_single_mode = not settings.get("dual_mode", False)
            if not is_single_mode:
                ind_text = "A" if getattr(self, "carousel_target", "a") == "a" else "B"
                ind_color = (0, 1, 1, 1) if ind_text == "A" else (1, 0.5, 0, 1)
                
                layout = PangoCairo.create_layout(ctx)
                desc = Pango.FontDescription.from_string("Sans Bold 20")
                layout.set_font_description(desc)
                layout.set_text(ind_text, -1)
                w_pango, h_pango = layout.get_pixel_size()
                
                ctx.move_to(5, height - h_pango - 5)
                ctx.set_source_rgba(*ind_color)
                PangoCairo.show_layout(ctx, layout)
                
            cairo_img = Image.frombuffer("RGBA", (width, height), surface.get_data().tobytes(), "raw", "BGRA", 0, 1)
            cairo_img.alpha_composite(base_img)
            self.set_media(image=cairo_img)

        except Exception as e:
            import traceback
            import logging
            logging.error(f"Error drawing carousel: {e}\n{traceback.format_exc()}")
            from PIL import Image, ImageDraw
            err_img = Image.new("RGBA", (100, 100), (255,0,0,255))
            d = ImageDraw.Draw(err_img)
            d.text((5, 5), "ERROR", fill=(255,255,255))
            self.set_media(image=err_img)
