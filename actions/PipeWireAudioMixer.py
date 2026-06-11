"""Acción Audio Mixer: controla volumen/mute/balance de dispositivos PipeWire.

Rol de CONTROLADOR: gestiona eventos del dial, lee el estado vía PulseService
(modelo) y delega el dibujado en el paquete `rendering` (vista).
"""
import os
import time
import logging
import traceback

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib
import cairo
from PIL import Image, ImageDraw

from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

from .PipeWireActionBase import PipeWireActionBase
from .PeakMonitor import PeakMonitor
from .core.pulse_service import PulseService
from .rendering import icons
from .rendering.bars import BarRenderer, STYLE_TWO_BARS
from .rendering.colors import parse_color
from .rendering.text import FontDefaults, draw_text_section, draw_centered_text, draw_anchored_text
from .UIComponents import (CustomLabelRow, CustomIconRow, CustomBarRow,
                           DeviceConfigGroup, VolumeMonitorConfigRow, CarouselConfigRow)

log = logging.getLogger(__name__)

VOLUME_LIMIT_MAX = 150.0      # tope duro de volumen (%)
CAROUSEL_TIMEOUT_S = 10.0     # segundos sin interacción antes de cerrar el carrusel
IDLE_TICK_INTERVAL_S = 0.2    # refresco normal (sin monitor de picos)
FAST_TICK_MS = 40             # resolución del temporizador GLib
PCT_FORMAT_DISABLED = 5
CAROUSEL_ICON_CENTER = 48
CAROUSEL_ICON_SIDE = 28


class PipeWireAudioMixer(PipeWireActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        self.icon_cache = icons.IconCache(max_size=50)

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

        self.carousel_active = False
        self.carousel_target = "a"
        self.carousel_index = 0
        self.carousel_devices = []
        self.carousel_last_interaction = 0

        self.last_state = {"vol_a": -1, "vol_b": -1, "muted_a": False, "muted_b": False,
                           "dev_a": None, "dev_b": None}
        self.internal_balance = 50.0
        self.last_tick_time = 0
        self._force_redraw = False
        self._last_single_mode = True
        self._tick_source_id = None

        self.peak_monitor = PeakMonitor()
        self._monitor_display_active = False
        self._last_interaction_time = time.time()

    # ---------- ciclo de vida ----------

    def on_ready(self):
        self.draw_image()
        if self._tick_source_id is None:
            self._tick_source_id = GLib.timeout_add(FAST_TICK_MS, self.on_fast_tick)

    def on_remove(self):
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
        self.peak_monitor.stop()

    def on_fast_tick(self):
        self.on_tick()
        return True

    def on_tick(self):
        current_time = time.time()
        settings = self.get_settings()
        monitor_enabled = settings.get("monitor_enabled", False)
        monitor_delay = settings.get("monitor_delay", 5)
        monitor_fps = settings.get("monitor_fps", 10)

        time_since_interaction = current_time - self._last_interaction_time
        should_monitor_pre = (monitor_enabled and time_since_interaction >= monitor_delay
                              and self._last_single_mode)
        tick_interval = 1.0 / monitor_fps if should_monitor_pre else IDLE_TICK_INTERVAL_S
        if current_time - self.last_tick_time < tick_interval:
            return
        self.last_tick_time = current_time

        if self.carousel_active:
            if current_time - self.carousel_last_interaction > CAROUSEL_TIMEOUT_S:
                self.carousel_active = False
                self._force_redraw = True
            else:
                self.draw_image()
                return

        try:
            dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
            self._last_single_mode = is_single_mode
            should_monitor = (monitor_enabled and time_since_interaction >= monitor_delay
                              and is_single_mode)

            with self.pulse_service.locked() as pulse:
                vol_a = int(round(pulse.volume_get_all_chans(dev_a) * 100)) if dev_a else 0
                vol_b = int(round(pulse.volume_get_all_chans(dev_b) * 100)) if dev_b else 0
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
            elif self.peak_monitor.is_running:
                self.peak_monitor.stop()

            force_draw = (should_monitor and self.peak_monitor.is_running) or self._force_redraw

            if changed or force_draw or self._monitor_display_active != should_monitor:
                self._force_redraw = False
                self._monitor_display_active = should_monitor and self.peak_monitor.is_running

                self.last_state.update({
                    "vol_a": vol_a, "vol_b": vol_b,
                    "muted_a": mut_a, "muted_b": mut_b,
                    "dev_a": nm_a, "dev_b": nm_b
                })

                lim_a = self._volume_limit(settings, "a")
                lim_b = self._volume_limit(settings, "b")
                pct_a = vol_a / lim_a if lim_a > 0 else 0
                pct_b = vol_b / lim_b if lim_b > 0 else 0
                if is_single_mode:
                    self.internal_balance = pct_a * 100.0
                else:
                    self.internal_balance = self._balance_from_pcts(pct_a, pct_b)

                self.draw_image()
        except Exception as e:
            log.debug("on_tick error: %s", e)

    # ---------- helpers de estado ----------

    @staticmethod
    def _balance_from_pcts(pct_a, pct_b):
        if pct_a >= pct_b:
            return 50.0 * pct_b
        return 100.0 - (50.0 * pct_a)

    @staticmethod
    def _volume_limit(settings, suffix):
        return min(VOLUME_LIMIT_MAX, float(settings.get(f"volume_limit_{suffix}", 100)))

    def _mark_interaction(self):
        self._last_interaction_time = time.time()

    def _refresh(self):
        """Fuerza un tick (y redibujado) inmediato."""
        self.last_tick_time = 0
        self.on_tick()

    def invalidate_render(self):
        """Invalida el estado cacheado y redibuja; lo usa la UI de configuración."""
        self.last_state["vol_a"] = -1
        self.last_state["vol_b"] = -1
        self.draw_image()

    # ---------- resolución de dispositivos ----------

    def get_active_devices_and_mode(self):
        """Devuelve (dev_a, dev_b, is_single_mode); B se ignora si coincide con A."""
        dev_a = self.get_target_device("a")
        dev_b = self.get_target_device("b")

        if dev_a is None or dev_b is None:
            dev_b = None
        elif getattr(dev_a, "index", id(dev_a)) == getattr(dev_b, "index", id(dev_b)):
            dev_b = None

        return dev_a, dev_b, dev_b is None

    def get_target_device(self, suffix):
        settings = self.get_settings()
        if suffix == "b" and not settings.get("dual_mode", False):
            return None

        device_type = settings.get(f"device_type_{suffix}", "sink")
        device_name = settings.get(f"device_name_{suffix}") or \
            ("Auto" if device_type == "application" else "default")

        svc = self.pulse_service
        if not svc:
            return None
        auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
        return svc.resolve_device(device_type, device_name,
                                  auto_index=settings.get(f"auto_index_{suffix}", 0),
                                  auto_prefix=auto_prefix)

    def get_device_friendly_name(self, suffix):
        dev = self.get_target_device(suffix)
        if dev:
            app_name = PulseService.app_binary(dev)
            if app_name:
                return app_name
            if getattr(dev, "description", None):
                return dev.description
            if getattr(dev, "name", None):
                return dev.name

        settings = self.get_settings()
        dtype = settings.get(f"device_type_{suffix}", "sink")
        name = settings.get(f"device_name_{suffix}") or \
            ("Auto" if dtype == "application" else "default")
        if name == "default":
            if dtype in ("sink", "source") and self.pulse_service:
                desc = self.pulse_service.get_default_device_description(dtype)
                if desc:
                    return desc
            return "Default"
        return name

    # ---------- eventos ----------

    def on_toggle_mute(self, data=None):
        self._mark_interaction()
        if self.carousel_active:
            self._apply_carousel_selection()
            return

        dev_a, dev_b, _ = self.get_active_devices_and_mode()
        with self.pulse_service.locked() as pulse:
            if dev_a:
                pulse.mute(dev_a, not dev_a.mute)
            if dev_b:
                pulse.mute(dev_b, not dev_b.mute)
        self._refresh()

    def on_volume_up(self, data=None):
        self._on_dial_turn(+1)

    def on_volume_down(self, data=None):
        self._on_dial_turn(-1)

    def _on_dial_turn(self, direction):
        if self.carousel_active:
            self._mark_interaction()
            self.carousel_last_interaction = time.time()
            if self.carousel_devices:
                self.carousel_index = (self.carousel_index + direction) % len(self.carousel_devices)
                self._refresh()
            return
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(step * direction)

    def change_balance(self, amount):
        """Single: sube/baja volumen. Dual: mezcla hacia A (neg.) o B (pos.)."""
        self._mark_interaction()
        settings = self.get_settings()
        limit_a = self._volume_limit(settings, "a")
        limit_b = self._volume_limit(settings, "b")
        dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
        svc = self.pulse_service

        if is_single_mode:
            if dev_a:
                vol_a = svc.get_volume_pct(dev_a)
                svc.set_volume_pct(dev_a, max(0.0, min(limit_a, vol_a + amount)))
        else:
            with svc.locked() as pulse:
                vol_a = int(round(pulse.volume_get_all_chans(dev_a) * 100)) if dev_a else 0
                vol_b = int(round(pulse.volume_get_all_chans(dev_b) * 100)) if dev_b else 0
                if dev_a and dev_a.mute:
                    vol_a = 0
                if dev_b and dev_b.mute:
                    vol_b = 0

            vol_a, vol_b = self._mix(vol_a, vol_b, amount, limit_a, limit_b)

            with svc.locked() as pulse:
                if dev_a:
                    pulse.volume_set_all_chans(dev_a, vol_a / 100.0)
                if dev_b:
                    pulse.volume_set_all_chans(dev_b, vol_b / 100.0)

            pct_a = vol_a / limit_a if limit_a > 0 else 0
            pct_b = vol_b / limit_b if limit_b > 0 else 0
            self.internal_balance = self._balance_from_pcts(pct_a, pct_b)

        self._refresh()

    @staticmethod
    def _mix(vol_a, vol_b, amount, limit_a, limit_b):
        """Mezcla hacia B (amount>0) o A (amount<0): primero sube uno, luego baja el otro."""
        if amount > 0:
            space_b = max(0.0, limit_b - vol_b)
            add_b = min(amount, space_b)
            vol_b += add_b
            amount -= add_b
            if amount > 0:
                vol_a = max(0.0, vol_a - amount)
        else:
            amt = abs(amount)
            space_a = max(0.0, limit_a - vol_a)
            add_a = min(amt, space_a)
            vol_a += add_a
            amt -= add_a
            if amt > 0:
                vol_b = max(0.0, vol_b - amt)
        return vol_a, vol_b

    # ---------- carrusel ----------

    def on_touch_action(self, data=None):
        self._mark_interaction()
        settings = self.get_settings()
        if not settings.get("carousel_enabled", False):
            return

        is_single_mode = not settings.get("dual_mode", False)

        if self.carousel_active:
            self.carousel_last_interaction = time.time()
            if not is_single_mode and self.carousel_target == "b":
                # En B un segundo toque cancela
                self.carousel_active = False
                self._force_redraw = True
            elif not is_single_mode and self.carousel_target == "a":
                self.switch_carousel_target("b")
        else:
            self.carousel_devices = self._build_carousel_device_list()
            if not self.carousel_devices:
                return
            self.carousel_active = True
            self.carousel_last_interaction = time.time()
            self.switch_carousel_target("a")

        self._refresh()

    def switch_carousel_target(self, new_target):
        self.carousel_target = new_target
        cur_target_str = self.get_settings().get(f"device_target_{new_target}", "auto")
        self.carousel_index = 0
        for i, d in enumerate(self.carousel_devices):
            if cur_target_str == f"{d['type']}_{d['id']}":
                self.carousel_index = i
                break

    def _build_carousel_device_list(self):
        settings = self.get_settings()
        devices = []
        with self.pulse_service.locked() as pulse:
            if settings.get("carousel_show_default_sink", True):
                def_sink = pulse.server_info().default_sink_name
                dev = next((d for d in pulse.sink_list() if d.name == def_sink), None)
                if dev:
                    devices.append({"type": "sink", "id": dev.index, "name": "Default Sink",
                                    "target_name": "default", "dev": dev})

            if settings.get("carousel_show_default_source", False):
                def_source = pulse.server_info().default_source_name
                dev = next((d for d in pulse.source_list() if d.name == def_source), None)
                if dev:
                    devices.append({"type": "source", "id": dev.index, "name": "Default Source",
                                    "target_name": "default", "dev": dev})

            if settings.get("carousel_show_sinks", False):
                for dev in pulse.sink_list():
                    devices.append({"type": "sink", "id": dev.index, "name": dev.description,
                                    "target_name": dev.name, "dev": dev})

            if settings.get("carousel_show_sources", False):
                for dev in pulse.source_list():
                    devices.append({"type": "source", "id": dev.index, "name": dev.description,
                                    "target_name": dev.name, "dev": dev})

            if settings.get("carousel_show_apps", True):
                for dev in pulse.sink_input_list():
                    t_name = PulseService.app_binary(dev)
                    if t_name:
                        devices.append({"type": "app", "id": dev.index, "name": t_name,
                                        "target_name": t_name, "dev": dev})

        seen = set()
        unique_devices = []
        for d in devices:
            key = f"app_{d['target_name']}" if d["type"] == "app" else f"{d['type']}_{d['id']}"
            if key not in seen:
                seen.add(key)
                unique_devices.append(d)
        return unique_devices

    def _apply_carousel_selection(self):
        """Asigna el dispositivo seleccionado en el carrusel al objetivo A/B."""
        self.carousel_active = False
        if self.carousel_devices and self.carousel_index < len(self.carousel_devices):
            selected = self.carousel_devices[self.carousel_index]
            dev_type = "application" if selected["type"] == "app" else selected["type"]
            target = self.carousel_target if self.carousel_target in ("a", "b") else "a"

            settings = self.get_settings()
            settings[f"device_type_{target}"] = dev_type
            settings[f"device_name_{target}"] = selected.get("target_name", "")
            self.set_settings(settings)

        self._force_redraw = True
        self._refresh()

    # ---------- iconos ----------

    def _default_icon_path(self, dtype):
        fname = "mic.svg" if dtype == "source" else "speaker.svg"
        return os.path.join(self.plugin_base.PATH, "assets", fname)

    def _resolve_auto_icon_path(self, suffix, settings, dev):
        """Icono automático: el de la app si aplica; si no, el del tipo de dispositivo."""
        dtype = settings.get(f"device_type_{suffix}", "sink")
        if dtype == "application":
            auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
            dev_name = settings.get(f"device_name_{suffix}") or (auto_prefix + "1")

            icon_name = None
            if dev:
                icon_name = dev.proplist.get("application.icon_name") or \
                    dev.proplist.get("application.process.binary")
            elif not dev_name.startswith(auto_prefix):
                icon_name = dev_name

            found = icons.lookup_theme_icon(icon_name)
            if found:
                return found
        return self._default_icon_path(dtype)

    def _get_icon_data(self, suffix, settings, defs, dev):
        """Devuelve (imagen PIL, x, y, is_muted, paleta) del icono configurado."""
        icon_path = settings.get(f"icon_path_{suffix}", "")
        icon_h = settings.get(f"icon_height_{suffix}", defs[f"icon_height_{suffix}"])
        icon_x = settings.get(f"icon_x_{suffix}", defs[f"icon_x_{suffix}"])
        icon_y = settings.get(f"icon_y_{suffix}", defs[f"icon_y_{suffix}"])
        icon_out_w = settings.get(f"icon_out_width_{suffix}", defs[f"icon_out_width_{suffix}"])
        icon_out_c = parse_color(settings.get(f"icon_out_color_{suffix}", defs[f"icon_out_color_{suffix}"]))

        if not icon_path or not os.path.isfile(icon_path):
            icon_path = self._resolve_auto_icon_path(suffix, settings, dev)

        cache_key = f"{icon_path}_{icon_h}_{icon_out_w}_{icon_out_c}"
        cached = self.icon_cache.get(cache_key)
        if cached:
            pil_img, pal = cached
        else:
            try:
                pil_img = icons.load_icon_as_pil(icon_path, icon_h)
                pal = icons.extract_palette(pil_img, 3)
                if icon_out_w > 0:
                    pil_img = icons.apply_outline(pil_img, icon_out_w, icon_out_c)
                self.icon_cache.put(cache_key, (pil_img, pal))
            except Exception as e:
                log.warning("Error generating icon: %s", e)
                pil_img = Image.new("RGBA", (icon_h, icon_h), (0, 0, 0, 0))
                pal = ["#ffffff"] * 3

        is_muted = bool(dev and getattr(dev, "mute", False))
        return pil_img, icon_x, icon_y, is_muted, pal

    # ---------- renderizado ----------

    @staticmethod
    def _surface_to_pil(surface, width, height):
        return Image.frombuffer("RGBA", (width, height),
                                surface.get_data().tobytes(), "raw", "BGRA", 0, 1)

    def draw_image(self):
        if self.carousel_active:
            self._draw_carousel()
            return

        settings = self.get_settings()
        dev_a, dev_b, is_single_mode = self.get_active_devices_and_mode()
        defs, width, height = self.get_calculated_defaults()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        fd = FontDefaults.from_global()

        svc = self.pulse_service
        vol_a = svc.get_volume_pct(dev_a) if dev_a else None
        vol_b = svc.get_volume_pct(dev_b) if dev_b else None

        icon_data_a = self._get_icon_data("a", settings, defs, dev_a) \
            if settings.get("show_icon_a", True) else None
        icon_data_b = self._get_icon_data("b", settings, defs, dev_b) \
            if not is_single_mode and settings.get("show_icon_b", True) else None
        palettes = {"a": icon_data_a[4] if icon_data_a else None,
                    "b": icon_data_b[4] if icon_data_b else None}

        is_monitor_active = self._monitor_display_active and self.peak_monitor is not None
        bars = BarRenderer(ctx, settings, defs,
                           is_single_mode=is_single_mode,
                           is_monitor_active=is_monitor_active,
                           palettes=palettes)

        if is_monitor_active:
            self._draw_monitor_bars(bars)
        elif bars.style == STYLE_TWO_BARS and not is_single_mode:
            bars.legacy_bar(bars.base_y, "a", vol_a, invert=not bars.invert)
            bars.legacy_bar(bars.base_y + bars.h + 2, "b", vol_b, invert=bars.invert)
        elif is_single_mode:
            bars.marker(bars.single_bar(vol_a))
        else:
            bars.marker(bars.balance_bar(self.internal_balance))

        margin = defs["bar_x"]
        text_name = settings.get("text_name", "") or self._default_name_text(is_single_mode)
        draw_text_section(ctx, settings, "name", text_name, fd, defs=defs,
                          default_y=3, margin=margin, default_max_w=width - margin * 2)

        pct_str = self._build_pct_text(settings, is_single_mode, vol_a or 0)
        if pct_str is not None:
            draw_text_section(ctx, settings, "pct", pct_str, fd, defs=defs,
                              default_y=int(height * 0.28), margin=margin,
                              default_max_w=width - margin * 2,
                              default_font_desc=f"{fd.family} 22", anchor_bottom=True)

        cairo_img = self._surface_to_pil(surface, width, height)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for icon_data in (icon_data_a, icon_data_b):
            if icon_data:
                icon, x, y, muted, _ = icon_data
                overlay.alpha_composite(icon, (x, y))
                if muted:
                    icons.draw_mute_cross(overlay, x, y, icon.width, icon.height)
        cairo_img.alpha_composite(overlay)
        self.set_media(image=cairo_img)

    def _draw_monitor_bars(self, bars):
        (peak_l, peak_r), (rms_l, rms_r) = self.peak_monitor.get_peak()
        if self.last_state.get("muted_a", False):
            peak_l = peak_r = rms_l = rms_r = 0.0

        if bars.mon_bar_mode == 0:
            peak_db = PeakMonitor.linear_to_db((peak_l + peak_r) / 2.0)
            rms_db = PeakMonitor.linear_to_db((rms_l + rms_r) / 2.0)
            bars.monitor_meter(bars.base_y, "a", peak_db, rms_db)
        else:
            bars.monitor_meter(bars.base_y, "a",
                               PeakMonitor.linear_to_db(peak_l), PeakMonitor.linear_to_db(rms_l))
            bars.monitor_meter(bars.base_y + bars.h + 2, "a",
                               PeakMonitor.linear_to_db(peak_r), PeakMonitor.linear_to_db(rms_r))

    def _default_name_text(self, is_single_mode):
        if is_single_mode:
            return self.get_device_friendly_name("a")
        return f"{self.get_device_friendly_name('a')} / {self.get_device_friendly_name('b')}"

    def _build_pct_text(self, settings, is_single_mode, vol_a):
        """Texto del indicador numérico (% / pan / crossfade / dB), o None si está oculto."""
        if self._monitor_display_active and settings.get("monitor_show_db", False) \
                and self.peak_monitor:
            return self._build_db_text(is_single_mode)

        pct_format = int(settings.get("pct_format", 0))
        if pct_format == PCT_FORMAT_DISABLED:
            return None
        val = vol_a if is_single_mode else self.internal_balance
        return self._format_value(val, pct_format, is_single_mode)

    @staticmethod
    def _format_value(val, pct_format, is_single_mode):
        if pct_format == 0:
            return f"{int(round(val))}%"
        if pct_format in (1, 2):
            pan = int(round(val)) if is_single_mode else int(round((val - 50) * 2))
            if pct_format == 1:
                return f"{pan}%" if is_single_mode else f"{pan:+}%"
            return f"{pan}" if is_single_mode else f"{pan:+}"
        if pct_format == 3:
            if is_single_mode:
                return f"{int(round(val))}"
            if val < 49.5:
                return f"B{int(round(val * 2))}"
            if val > 50.5:
                return f"A{int(round((100 - val) * 2))}"
            return "A=B"
        if pct_format == 4:
            if is_single_mode:
                return f"{int(round(val))}"
            if val < 49.5:
                return f"R{int(round(val * 2))}"
            if val > 50.5:
                return f"L{int(round((100 - val) * 2))}"
            return "100"
        return ""

    def _build_db_text(self, is_single_mode):
        muted_a = self.last_state.get("muted_a", False)
        muted_b = self.last_state.get("muted_b", False)
        if (is_single_mode and muted_a) or (not is_single_mode and muted_a and muted_b):
            return "--"

        (peak_l, peak_r), _ = self.peak_monitor.get_peak()
        if muted_a:
            peak_l = 0.0
        if not is_single_mode and muted_b:
            peak_r = 0.0
        peak_db = PeakMonitor.linear_to_db((peak_l + peak_r) / 2.0)
        if peak_db <= -60:
            return "-inf dB"
        return f"{peak_db:.1f} dB"

    def _carousel_icon_path(self, dev_dict):
        if dev_dict["type"] == "app":
            proplist = getattr(dev_dict["dev"], "proplist", None) or {}
            icon_name = proplist.get("application.icon_name") or \
                proplist.get("application.process.binary")
            found = icons.lookup_theme_icon(icon_name)
            if found:
                return found
        return self._default_icon_path(dev_dict["type"])

    def _draw_carousel(self):
        try:
            settings = self.get_settings()
            defs, width, height = self.get_calculated_defaults()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            ctx = cairo.Context(surface)

            if not self.carousel_devices:
                draw_centered_text(ctx, self.plugin_base.lm.get("config.carousel.no_devices", "No devices"),
                                   width, height)
                self.set_media(image=self._surface_to_pil(surface, width, height))
                return

            num_devs = len(self.carousel_devices)
            center_idx = self.carousel_index
            center_y = (height - CAROUSEL_ICON_CENTER) // 2
            side_y = (height - CAROUSEL_ICON_SIDE) // 2
            center_x = (width - CAROUSEL_ICON_CENTER) // 2
            left_x = int(width * 0.1)
            right_x = int(width * 0.9) - CAROUSEL_ICON_SIDE

            base_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

            def paste_icon(idx, x, y, size, opacity):
                path = self._carousel_icon_path(self.carousel_devices[idx])
                try:
                    pil_img = icons.load_icon_as_pil(path, size)
                    if opacity < 255:
                        pil_img.putalpha(pil_img.getchannel("A").point(
                            lambda p: int(p * (opacity / 255.0))))
                    base_img.alpha_composite(pil_img, (x, y))
                except Exception as e:
                    log.debug("Error drawing carousel icon: %s", e)

            if num_devs >= 3:
                paste_icon((center_idx - 1) % num_devs, left_x, side_y, CAROUSEL_ICON_SIDE, 230)
                paste_icon((center_idx + 1) % num_devs, right_x, side_y, CAROUSEL_ICON_SIDE, 230)
            elif num_devs == 2:
                paste_icon((center_idx + 1) % num_devs, right_x, side_y, CAROUSEL_ICON_SIDE, 230)
            paste_icon(center_idx, center_x, center_y, CAROUSEL_ICON_CENTER, 255)

            fd = FontDefaults.from_global()
            current = self.carousel_devices[center_idx]
            vol_pct = self.pulse_service.get_volume_pct(current["dev"]) if current["dev"] else 0
            margin = defs.get("bar_x", int(width * 0.05))

            draw_text_section(ctx, settings, "carousel_name", current["name"], fd, defs=defs,
                              default_y=defs["pos_y_carousel_name"],
                              margin=margin, default_max_w=width - margin * 2)
            draw_text_section(ctx, settings, "carousel_pct", f"{vol_pct}%", fd, defs=defs,
                              default_y=defs["pos_y_carousel_pct"],
                              margin=margin, default_max_w=width - margin * 2,
                              anchor_bottom=True)

            if settings.get("dual_mode", False):
                is_a = self.carousel_target == "a"
                draw_anchored_text(ctx, "A" if is_a else "B", "Sans Bold 20",
                                   (0, 1, 1, 1) if is_a else (1, 0.5, 0, 1),
                                   5, height - 5)

            cairo_img = self._surface_to_pil(surface, width, height)
            cairo_img.alpha_composite(base_img)
            self.set_media(image=cairo_img)
        except Exception as e:
            log.error("Error drawing carousel: %s\n%s", e, traceback.format_exc())
            err_img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
            ImageDraw.Draw(err_img).text((5, 5), "ERROR", fill=(255, 255, 255))
            self.set_media(image=err_img)

    # ---------- defaults de geometría ----------

    def get_calculated_defaults(self):
        defs, width, height = super().get_calculated_defaults(self.get_input())

        margin = defs.get("bar_x", int(width * 0.05))
        defs["pos_y_carousel_name"] = defs.get("pos_y_name", int(round(height * 0.05)))
        defs["pos_y_carousel_pct"] = height - int(round(height * 0.05))
        defs["pos_x_carousel_name"] = margin
        defs["pos_x_carousel_pct"] = margin
        defs["width_carousel_name"] = width - (margin * 2)
        defs["width_carousel_pct"] = width - (margin * 2)

        return defs, width, height

    # ---------- configuración ----------

    def get_config_rows(self):
        try:
            settings = self.get_settings()
            lm = self.plugin_base.lm

            self.exp_dev_a = Adw.ExpanderRow(title=lm.get("config.mixer.device_a", "Device A (Left)"))
            self.exp_dev_a.add_row(DeviceConfigGroup(self, suffix="a"))

            self.exp_dev_b = Adw.ExpanderRow(title=lm.get("config.mixer.device_b", "Device B (Right)"))
            self.exp_dev_b.add_row(DeviceConfigGroup(self, suffix="b"))

            grp_mode = Adw.PreferencesGroup(title=lm.get("config.mode.title", "Mode"))
            self.switch_dual = Adw.SwitchRow(title=lm.get("config.mixer.dual_mode", "Enable Mixer"))
            self.switch_dual.set_active(settings.get("dual_mode", False))
            self.switch_dual.connect("notify::active", self.on_dual_mode_change)
            grp_mode.add(self.switch_dual)

            grp_misc = Adw.PreferencesGroup(title=lm.get("config.mixer.settings.title", "Mixer Settings"))
            self.step_row = Adw.SpinRow(title=lm.get("config.step.title", "Step (%)"))
            self.step_row.set_adjustment(Gtk.Adjustment(value=settings.get("volume_step", 5),
                                                        lower=1, upper=100, step_increment=1))
            self.step_row.connect("notify::value",
                                  lambda spin, pspec: self.save_setting("volume_step", int(spin.get_value())))
            grp_misc.add(self.step_row)

            self.exp_bar = Adw.ExpanderRow(title=lm.get("config.bar.format", "Bar Format"))
            self.exp_bar.add_row(CustomBarRow(settings, self))
            grp_misc.add(self.exp_bar)

            self.exp_name = Adw.ExpanderRow(title=lm.get("config.format.name.title", "Name Format"))
            self.exp_name.add_row(CustomLabelRow(lm.get("config.format.name.top", "Top Name"),
                                                 settings, "name", self))
            grp_misc.add(self.exp_name)

            self.exp_pct = Adw.ExpanderRow(title=lm.get("config.format.pct.title", "Percentage Format"))
            self.exp_pct.add_row(CustomLabelRow(lm.get("config.format.pct.text", "Pct"),
                                                settings, "pct", self))
            grp_misc.add(self.exp_pct)

            self.exp_icon_a = Adw.ExpanderRow(title=lm.get("config.icon.format", "Icon Format") + " A")
            self.exp_icon_a.add_row(CustomIconRow(settings, self, "a"))
            grp_misc.add(self.exp_icon_a)

            self.exp_icon_b = Adw.ExpanderRow(title=lm.get("config.icon.format", "Icon Format") + " B")
            self.exp_icon_b.add_row(CustomIconRow(settings, self, "b"))
            grp_misc.add(self.exp_icon_b)

            self.exp_monitor = Adw.ExpanderRow(title=lm.get("config.monitor.title", "Volume Monitor"))
            self.exp_monitor.add_row(VolumeMonitorConfigRow(settings, self))
            grp_misc.add(self.exp_monitor)

            self.exp_carousel = Adw.ExpanderRow(title=lm.get("config.carousel.title", "Carousel"))
            self.exp_carousel.add_row(CarouselConfigRow(settings, self))
            grp_misc.add(self.exp_carousel)

            self.exp_dev_b.set_visible(settings.get("dual_mode", False))
            self.exp_icon_b.set_visible(settings.get("dual_mode", False))

            return [grp_mode, self.exp_dev_a, self.exp_dev_b, grp_misc]
        except Exception as e:
            log.error("Error building config rows: %s\n%s", e, traceback.format_exc())
            return [Adw.ActionRow(title=f"ERROR: {e}")]

    def save_setting(self, key, value):
        settings = self.get_settings()
        settings[key] = value
        self.set_settings(settings)
        self.draw_image()

    def on_dual_mode_change(self, switch, pspec):
        self.save_setting("dual_mode", switch.get_active())
        self.exp_dev_b.set_visible(switch.get_active())
        self.exp_icon_b.set_visible(switch.get_active())
