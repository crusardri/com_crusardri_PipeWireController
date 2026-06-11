"""Audio Mixer action: controls volume, mute, and balance for PipeWire devices.

CONTROLLER role in MVC: handles dial events, reads state via PulseService
(MODEL), and delegates all drawing to the `rendering` package (VIEW).

The action manages a dynamic GLib timer (_on_tick_timer) that self-adjusts
its interval based on whether the peak monitor is active.  In idle mode the
timer fires every 200 ms; during monitoring it fires at the configured FPS.
"""
import io
import os
import time
import logging
import traceback
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk
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
from .rendering.text import FontDefaults, draw_text_section, draw_centered_text
from .UIComponents import (CustomLabelRow, CustomIconRow, CustomBarRow,
                           DeviceConfigGroup, VolumeMonitorBarRow, VolumeMonitorSettingsRow,
                           CarouselSettingsRow, CarouselIconsRow)

log = logging.getLogger(__name__)

# Hard ceiling for volume in percent (applies to all set/clamp operations).
VOLUME_LIMIT_MAX = 150.0
# Default seconds of inactivity before the carousel closes automatically.
DEFAULT_CAROUSEL_TIMEOUT_S = 10.0
# Timer interval used when the peak monitor is not running.
IDLE_TICK_INTERVAL_S = 0.2
# Minimum timer interval in milliseconds (prevents CPU spinning).
MIN_TICK_MS = 20
# pct_format value that disables the percentage text overlay.
PCT_FORMAT_DISABLED = 5
# Allowed values for the number of icons visible in the carousel.
CAROUSEL_COUNTS = (3, 5, 7)


class PipeWireAudioMixer(PipeWireActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        # Bounded icon cache shared across all draw calls for this action instance.
        self.icon_cache = icons.IconCache(max_size=50)

        # Register the four dial events with their default triggers.
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

        # Carousel state: which device the user is browsing.
        self.carousel_active = False
        self.carousel_target = "a"   # "a" or "b" — which device slot is being reassigned
        self.carousel_index = 0
        self.carousel_devices = []
        self.carousel_last_interaction = 0

        # Last rendered state: used to detect changes and skip redundant redraws.
        self.last_state = {"vol_a": -1, "vol_b": -1, "muted_a": False, "muted_b": False,
                           "dev_a": None, "dev_b": None}
        # Internal balance position (0=full A, 50=centre, 100=full B).
        self.internal_balance = 50.0
        self.last_tick_time = 0
        self._force_redraw = False
        self._last_single_mode = True
        # GLib timer source ID; None when no timer is scheduled.
        self._tick_source_id = None

        self.peak_monitor = PeakMonitor()
        self._monitor_display_active = False
        self._last_interaction_time = time.time()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_ready(self):
        """Called by StreamController when the action is ready to draw.

        Renders an initial frame and starts the dynamic tick timer.
        The guard prevents double-scheduling if on_ready is called twice.
        """
        self.draw_image()
        if self._tick_source_id is None:
            self._schedule_tick(IDLE_TICK_INTERVAL_S)

    def on_remove(self):
        """Called by StreamController when the action is removed from a dial.

        Cancels the GLib timer and stops the peak monitor thread to avoid leaks.
        """
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
        self.peak_monitor.stop()

    def _schedule_tick(self, interval_s):
        """Schedule a one-shot GLib timer for `interval_s` seconds from now."""
        self._tick_source_id = GLib.timeout_add(max(MIN_TICK_MS, int(interval_s * 1000)),
                                                self._on_tick_timer)

    def _on_tick_timer(self):
        """GLib timer callback: calls on_tick() and reschedules with the returned interval.

        Returns False so GLib does not repeat this timer; rescheduling is done
        explicitly so the interval can vary each cycle.
        """
        self._tick_source_id = None
        try:
            interval = self.on_tick()
        except Exception as e:
            log.debug("tick error: %s", e)
            interval = IDLE_TICK_INTERVAL_S
        self._schedule_tick(interval or IDLE_TICK_INTERVAL_S)
        return False  # tell GLib this is a one-shot timer

    def on_tick(self):
        """Main update loop: check for state changes and redraw if needed.

        Returns the desired interval in seconds until the next tick.
        The interval is shorter (1/fps) when the peak monitor is active.
        """
        current_time = time.time()
        settings = self.get_settings()
        monitor_enabled = settings.get("monitor_enabled", False)
        monitor_delay = settings.get("monitor_delay", 5)
        monitor_fps = settings.get("monitor_fps", 10)

        time_since_interaction = current_time - self._last_interaction_time
        # Monitor should activate only in single-device mode and after the delay.
        should_monitor_pre = (monitor_enabled and time_since_interaction >= monitor_delay
                              and self._last_single_mode)
        tick_interval = 1.0 / monitor_fps if should_monitor_pre else IDLE_TICK_INTERVAL_S

        # Skip this cycle if it fires earlier than the desired interval.
        if current_time - self.last_tick_time < tick_interval:
            return tick_interval
        self.last_tick_time = current_time

        # Handle carousel timeout: auto-close after inactivity.
        if self.carousel_active:
            carousel_timeout = self.get_settings().get("carousel_delay", DEFAULT_CAROUSEL_TIMEOUT_S)
            if current_time - self.carousel_last_interaction > carousel_timeout:
                self.carousel_active = False
                self._force_redraw = True
            else:
                self.draw_image()
                return tick_interval

        try:
            # Resolve devices and read state in a single lock acquire.
            state = self._gather_state(settings)
            self._last_single_mode = state.single
            should_monitor = (monitor_enabled and time_since_interaction >= monitor_delay
                              and state.single)

            # Detect any meaningful change relative to the last rendered frame.
            changed = (self.last_state["vol_a"] != state.vol_a or
                       self.last_state["vol_b"] != state.vol_b or
                       self.last_state["muted_a"] != state.mut_a or
                       self.last_state["muted_b"] != state.mut_b or
                       self.last_state["dev_a"] != state.nm_a or
                       self.last_state["dev_b"] != state.nm_b)

            # Start or stop the peak monitor based on current mode and idle time.
            if should_monitor:
                self.peak_monitor.start(state.dev_a)
            elif self.peak_monitor.is_running:
                self.peak_monitor.stop()

            # Force a redraw every cycle when the monitor is active (new meter data).
            force_draw = (should_monitor and self.peak_monitor.is_running) or self._force_redraw

            if changed or force_draw or self._monitor_display_active != should_monitor:
                self._force_redraw = False
                self._monitor_display_active = should_monitor and self.peak_monitor.is_running

                self.last_state.update({
                    "vol_a": state.vol_a, "vol_b": state.vol_b,
                    "muted_a": state.mut_a, "muted_b": state.mut_b,
                    "dev_a": state.nm_a, "dev_b": state.nm_b
                })

                # Recompute the internal balance position from the current volumes.
                lim_a = self._volume_limit(settings, "a")
                lim_b = self._volume_limit(settings, "b")
                pct_a = state.vol_a / lim_a if lim_a > 0 else 0
                pct_b = state.vol_b / lim_b if lim_b > 0 else 0
                if state.single:
                    self.internal_balance = pct_a * 100.0
                else:
                    self.internal_balance = self._balance_from_pcts(pct_a, pct_b)

                self.draw_image(state)
        except Exception as e:
            log.debug("on_tick error: %s", e)
        return tick_interval

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _balance_from_pcts(pct_a, pct_b):
        """Convert two normalised volume values to a balance position (0-100).

        When pct_a >= pct_b, A is dominant so balance is between 0 and 50.
        When pct_b > pct_a, B is dominant so balance is between 50 and 100.
        """
        if pct_a >= pct_b:
            return 50.0 * pct_b
        return 100.0 - (50.0 * pct_a)

    @staticmethod
    def _volume_limit(settings, suffix):
        """Return the effective volume ceiling for device A or B (max 150%)."""
        return min(VOLUME_LIMIT_MAX, float(settings.get(f"volume_limit_{suffix}", 100)))

    def _mark_interaction(self):
        """Record the current time as the last user interaction and clear the cache.

        Invalidating the snapshot here ensures that reads immediately after a
        write (e.g. mute then draw) see fresh data rather than stale cached values.
        """
        self._last_interaction_time = time.time()
        if self.pulse_service:
            self.pulse_service.invalidate_snapshot()

    def _refresh(self):
        """Trigger an immediate tick (and potential redraw) without waiting for the timer."""
        self.last_tick_time = 0
        self.on_tick()

    def _gather_state(self, settings=None):
        """Resolve devices and read all state in a single lock acquire.

        This is the single source of truth for one render frame: it prevents
        multiple IPC round-trips within the same frame by reading volume, mute,
        and device name in one go.

        Returns a SimpleNamespace with fields:
            dev_a, dev_b: pulsectl objects (or None)
            single: True when only one device is active
            vol_a, vol_b: raw volume percentages (not adjusted for mute)
            mut_a, mut_b: mute booleans
            nm_a, nm_b: device name strings (for change detection)
        """
        if settings is None:
            settings = self.get_settings()
        dev_a, dev_b, single = self.get_active_devices_and_mode(settings)
        with self.pulse_service.locked() as pulse:
            vol_a = int(round(pulse.volume_get_all_chans(dev_a) * 100)) if dev_a else 0
            vol_b = int(round(pulse.volume_get_all_chans(dev_b) * 100)) if dev_b else 0
            mut_a = bool(dev_a.mute) if dev_a else False
            mut_b = bool(dev_b.mute) if dev_b else False
            nm_a = dev_a.name if dev_a else "OFFLINE"
            nm_b = dev_b.name if dev_b else "OFFLINE"
        return SimpleNamespace(dev_a=dev_a, dev_b=dev_b, single=single,
                               vol_a=vol_a, vol_b=vol_b,
                               mut_a=mut_a, mut_b=mut_b,
                               nm_a=nm_a, nm_b=nm_b)

    def invalidate_render(self):
        """Reset cached volumes and force a redraw on the next call.

        Called by configuration UI rows after any setting change so the
        dial face updates immediately without waiting for the next tick.
        """
        self.last_state["vol_a"] = -1
        self.last_state["vol_b"] = -1
        self.draw_image()

    # ------------------------------------------------------------------
    # Device resolution
    # ------------------------------------------------------------------

    def get_active_devices_and_mode(self, settings=None):
        """Return (dev_a, dev_b, is_single_mode).

        is_single_mode is True when dev_b is None (disabled or same as dev_a).
        If both resolve to the same PulseAudio index, dev_b is set to None
        so the UI treats the action as single-device.
        """
        dev_a = self.get_target_device("a", settings)
        dev_b = self.get_target_device("b", settings)

        if dev_a is None or dev_b is None:
            dev_b = None
        elif getattr(dev_a, "index", id(dev_a)) == getattr(dev_b, "index", id(dev_b)):
            # Prevent self-mixing when both slots resolve to the same device.
            dev_b = None

        return dev_a, dev_b, dev_b is None

    def get_target_device(self, suffix, settings=None):
        """Resolve the configured device for slot A or B to a pulsectl object.

        Returns None when the device is offline or the service is unavailable.
        Device B always returns None when dual mode is disabled.
        """
        if settings is None:
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

    def _friendly_name(self, dev, suffix, settings):
        """Return a human-readable name for an already-resolved device.

        Uses the already-resolved pulsectl object (no additional IPC).
        Priority: app binary name → device description → device name → setting name.
        """
        if dev:
            app_name = PulseService.app_binary(dev)
            if app_name:
                return app_name
            if getattr(dev, "description", None):
                return dev.description
            if getattr(dev, "name", None):
                return dev.name

        # Device is offline; fall back to the configured name or a description query.
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

    # ------------------------------------------------------------------
    # Dial events
    # ------------------------------------------------------------------

    def on_toggle_mute(self, data=None):
        """Toggle mute on both active devices (or confirm carousel selection)."""
        self._mark_interaction()
        if self.carousel_active:
            # A short press during carousel confirms the current selection.
            self._apply_carousel_selection()
            return

        dev_a, dev_b, _ = self.get_active_devices_and_mode()
        with self.pulse_service.locked() as pulse:
            if dev_a:
                pulse.mute(dev_a, not dev_a.mute)
            if dev_b:
                pulse.mute(dev_b, not dev_b.mute)
        # Invalidate so the next read picks up the freshly written mute state.
        self.pulse_service.invalidate_snapshot()
        self._refresh()

    def on_volume_up(self, data=None):
        """Handle clockwise dial turn (volume up / mix towards B)."""
        self._on_dial_turn(+1)

    def on_volume_down(self, data=None):
        """Handle counter-clockwise dial turn (volume down / mix towards A)."""
        self._on_dial_turn(-1)

    def _on_dial_turn(self, direction):
        """Unified dial turn handler for both CW (+1) and CCW (−1) directions.

        In carousel mode: advances/retreats the device selection.
        In normal mode: delegates to change_balance() with the configured step.
        """
        if self.carousel_active:
            self._mark_interaction()
            self.carousel_last_interaction = time.time()
            if self.carousel_devices:
                # Wrap around the carousel list.
                self.carousel_index = (self.carousel_index + direction) % len(self.carousel_devices)
                self._refresh()
            return
        step = float(self.get_settings().get("volume_step", 5))
        self.change_balance(step * direction)

    def change_balance(self, amount):
        """Adjust volume or balance by `amount` percent.

        Single-device mode: directly adjusts device A's volume.
        Dual-device mode: first raises the target side, then lowers the other
        (crossfader behaviour — one side always gets louder before the other drops).
        """
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
            # Read both volumes atomically to avoid a race between the two reads.
            with svc.locked() as pulse:
                vol_a = int(round(pulse.volume_get_all_chans(dev_a) * 100)) if dev_a else 0
                vol_b = int(round(pulse.volume_get_all_chans(dev_b) * 100)) if dev_b else 0
                # Treat muted devices as zero for mixing purposes.
                if dev_a and dev_a.mute:
                    vol_a = 0
                if dev_b and dev_b.mute:
                    vol_b = 0

            vol_a, vol_b = self._mix(vol_a, vol_b, amount, limit_a, limit_b)

            # Write both volumes atomically.
            with svc.locked() as pulse:
                if dev_a:
                    pulse.volume_set_all_chans(dev_a, vol_a / 100.0)
                if dev_b:
                    pulse.volume_set_all_chans(dev_b, vol_b / 100.0)
            svc.invalidate_snapshot()

            # Recompute balance so the bar reflects the new volumes immediately.
            pct_a = vol_a / limit_a if limit_a > 0 else 0
            pct_b = vol_b / limit_b if limit_b > 0 else 0
            self.internal_balance = self._balance_from_pcts(pct_a, pct_b)

        self._refresh()

    @staticmethod
    def _mix(vol_a, vol_b, amount, limit_a, limit_b):
        """Compute new (vol_a, vol_b) after applying a crossfader step.

        Positive `amount` shifts towards B: first fills B up to its limit,
        then reduces A for any remaining amount.  Negative `amount` does the
        reverse (shifts towards A).
        """
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

    # ------------------------------------------------------------------
    # Carousel
    # ------------------------------------------------------------------

    def on_touch_action(self, data=None):
        """Handle a touch press: open the carousel or advance between A/B slots."""
        self._mark_interaction()
        settings = self.get_settings()
        if not settings.get("carousel_enabled", False):
            return

        is_single_mode = not settings.get("dual_mode", False)

        if self.carousel_active:
            self.carousel_last_interaction = time.time()
            if not is_single_mode and self.carousel_target == "b":
                # Second touch on B cancels the carousel entirely.
                self.carousel_active = False
                self._force_redraw = True
            elif not is_single_mode and self.carousel_target == "a":
                # First touch on A advances to B selection.
                self.switch_carousel_target("b")
        else:
            # Open the carousel with the current device list.
            self.carousel_devices = self._build_carousel_device_list()
            if not self.carousel_devices:
                return
            self.carousel_active = True
            self.carousel_last_interaction = time.time()
            self.switch_carousel_target("a")

        self._refresh()

    def switch_carousel_target(self, new_target):
        """Switch which device slot (A or B) the carousel is assigning.

        Attempts to pre-select the carousel entry that matches the currently
        configured device so the user sees their current setting highlighted.
        """
        self.carousel_target = new_target
        cur_target_str = self.get_settings().get(f"device_target_{new_target}", "auto")
        self.carousel_index = 0
        for i, d in enumerate(self.carousel_devices):
            if cur_target_str == f"{d['type']}_{d['id']}":
                self.carousel_index = i
                break

    def _build_carousel_device_list(self):
        """Build the ordered, deduplicated device list shown in the carousel.

        Reads directly from PulseAudio (bypasses snapshot cache) because this
        is a user-initiated action that should always see current state.
        Filter flags (carousel_show_sinks, etc.) are read from settings.
        """
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

        # Deduplicate by a stable key (app by binary name, hardware by PulseAudio index).
        seen = set()
        unique_devices = []
        for d in devices:
            key = f"app_{d['target_name']}" if d["type"] == "app" else f"{d['type']}_{d['id']}"
            if key not in seen:
                seen.add(key)
                unique_devices.append(d)

        # Pad the list by looping it if there are fewer items than carousel_count
        if unique_devices:
            carousel_count = settings.get("carousel_count", 5)
            if carousel_count not in CAROUSEL_COUNTS:
                carousel_count = 5
            
            if len(unique_devices) < carousel_count:
                original_list = list(unique_devices)
                while len(unique_devices) < carousel_count:
                    unique_devices.extend(original_list)

        return unique_devices

    def _apply_carousel_selection(self):
        """Persist the highlighted carousel device as the active target (A or B).

        Updates settings and triggers an immediate redraw.
        """
        self.carousel_active = False
        if self.carousel_devices and self.carousel_index < len(self.carousel_devices):
            selected = self.carousel_devices[self.carousel_index]
            # Map carousel type ("app") to the settings key type ("application").
            dev_type = "application" if selected["type"] == "app" else selected["type"]
            target = self.carousel_target if self.carousel_target in ("a", "b") else "a"

            settings = self.get_settings()
            settings[f"device_type_{target}"] = dev_type
            settings[f"device_name_{target}"] = selected.get("target_name", "")
            self.set_settings(settings)

        self._force_redraw = True
        self._refresh()

    # ------------------------------------------------------------------
    # Icon helpers
    # ------------------------------------------------------------------

    def _default_icon_path(self, dtype):
        """Return the path to the built-in icon for the given device type."""
        fname = "mic.svg" if dtype == "source" else "speaker.svg"
        return os.path.join(self.plugin_base.PATH, "assets", fname)

    def _resolve_auto_icon_path(self, suffix, settings, dev):
        """Choose the automatic icon for a device slot.

        For application devices: try to look up the app's icon in the GTK theme.
        For hardware devices: use the built-in speaker or mic asset.
        """
        dtype = settings.get(f"device_type_{suffix}", "sink")
        if dtype == "application":
            auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
            dev_name = settings.get(f"device_name_{suffix}") or (auto_prefix + "1")

            if dev:
                # Try every icon-related stream property (covers flatpak apps
                # that only expose their app ID instead of an icon name).
                found = icons.lookup_app_icon(getattr(dev, "proplist", None))
            elif not dev_name.startswith(auto_prefix):
                found = icons.lookup_theme_icon(dev_name)
            else:
                found = None
            if found:
                return found
        return self._default_icon_path(dtype)

    def _get_icon_data(self, suffix, settings, defs, dev):
        """Load, process, and cache the icon for device slot `suffix`.

        Returns a tuple: (pil_image, x, y, is_muted, palette).
        Cache key includes path, height, outline width, and colour so any
        change in those settings triggers a fresh load.
        """
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

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _surface_to_pil(surface, width, height):
        """Convert a cairo ARGB32 surface to a PIL RGBA image.

        cairo stores pixels as BGRA in native byte order; PIL expects RGBA,
        so 'BGRA' is specified as the raw mode in frombuffer.
        """
        return Image.frombuffer("RGBA", (width, height),
                                surface.get_data().tobytes(), "raw", "BGRA", 0, 1)

    def draw_image(self, state=None):
        """Render the complete dial face and push it to the device.

        If `state` is provided (pre-gathered by on_tick) it is reused to avoid
        another IPC round-trip.  If None, a fresh state is gathered here.

        Rendering pipeline:
            1. Resolve devices and read state (via `state` or _gather_state).
            2. Draw bars onto a cairo surface.
            3. Render text (device name, percentage).
            4. Composite icon overlays (with mute cross if muted).
            5. Push the final PIL image with set_media().
        """
        if self.carousel_active:
            self._draw_carousel()
            return

        settings = self.get_settings()
        if state is None:
            state = self._gather_state(settings)
        dev_a, dev_b, is_single_mode = state.dev_a, state.dev_b, state.single
        defs, width, height = self.get_calculated_defaults()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        fd = FontDefaults.from_global()

        # Display volume: 0 when muted, None when the device is offline.
        vol_a = (0 if state.mut_a else state.vol_a) if dev_a else None
        vol_b = (0 if state.mut_b else state.vol_b) if dev_b else None

        # Load icons (only for slots where they are enabled).
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

        # Choose the bar drawing strategy based on the current mode.
        if is_monitor_active:
            self._draw_monitor_bars(bars)
        elif bars.style == STYLE_TWO_BARS and not is_single_mode:
            # Classic 2-bar mixer mode: one bar per device, stacked.
            bars.legacy_bar(bars.base_y, "a", vol_a, invert=not bars.invert)
            bars.legacy_bar(bars.base_y + bars.h + 2, "b", vol_b, invert=bars.invert)
        elif is_single_mode:
            bars.marker(bars.single_bar(vol_a))
        else:
            bars.marker(bars.balance_bar(self.internal_balance))

        # Text overlays.
        margin = defs["bar_x"]
        text_name = settings.get("text_name", "") or self._default_name_text(state, settings)
        draw_text_section(ctx, settings, "name", text_name, fd, defs=defs,
                          default_y=3, margin=margin, default_max_w=width - margin * 2)

        pct_str = self._build_pct_text(settings, is_single_mode, vol_a or 0)
        if pct_str is not None:
            draw_text_section(ctx, settings, "pct", pct_str, fd, defs=defs,
                              default_y=int(height * 0.28), margin=margin,
                              default_max_w=width - margin * 2,
                              default_font_desc=f"{fd.family} 22", anchor_bottom=True)

        # Composite icons onto the cairo output.
        cairo_img = self._surface_to_pil(surface, width, height)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for icon_data in (icon_data_a, icon_data_b):
            if icon_data:
                icon, x, y, muted, _ = icon_data
                overlay.alpha_composite(icon, (x, y))
                if muted:
                    # Draw the red diagonal cross over the muted icon.
                    icons.draw_mute_cross(overlay, x, y, icon.width, icon.height)
        cairo_img.alpha_composite(overlay)
        self.set_media(image=cairo_img)
        self._update_preview(cairo_img)

    def _update_preview(self, pil_img):
        """Mirror the rendered face into the config panel's live preview.

        Cheap no-op when the config panel is closed (picture not in a window).
        """
        pic = getattr(self, "_preview_picture", None)
        if pic is None or pic.get_root() is None:
            return
        try:
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))
            GLib.idle_add(pic.set_paintable, texture)
        except Exception as e:
            log.debug("preview update error: %s", e)

    def _draw_monitor_bars(self, bars):
        """Delegate peak monitor drawing to BarRenderer.

        Reads the latest peak/RMS values and zeroes them out if the device is muted.
        In stereo mode draws separate L/R bars; in mono mode draws one combined bar.
        """
        (peak_l, peak_r), (rms_l, rms_r) = self.peak_monitor.get_peak()
        if self.last_state.get("muted_a", False):
            peak_l = peak_r = rms_l = rms_r = 0.0

        if bars.mon_bar_mode == 0:
            # Mono: average left and right channels into one bar.
            peak_db = PeakMonitor.linear_to_db((peak_l + peak_r) / 2.0)
            rms_db = PeakMonitor.linear_to_db((rms_l + rms_r) / 2.0)
            bars.monitor_meter(bars.base_y, "a", peak_db, rms_db)
        else:
            # Stereo: one bar for left, one for right (stacked).
            bars.monitor_meter(bars.base_y, "a",
                               PeakMonitor.linear_to_db(peak_l), PeakMonitor.linear_to_db(rms_l))
            bars.monitor_meter(bars.base_y + bars.h + 2, "a",
                               PeakMonitor.linear_to_db(peak_r), PeakMonitor.linear_to_db(rms_r))

    def _default_name_text(self, state, settings):
        """Build the auto device name text from pre-resolved state (no IPC)."""
        name_a = self._friendly_name(state.dev_a, "a", settings)
        if state.single:
            return name_a
        return f"{name_a} / {self._friendly_name(state.dev_b, 'b', settings)}"

    def _build_pct_text(self, settings, is_single_mode, vol_a):
        """Build the percentage/dB indicator string, or return None to hide it."""
        # Show dB text when the monitor is active and dB display is enabled.
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
        """Format a volume or balance value according to the selected display format.

        Formats:
            0: Percentage (e.g. "75%")
            1: Panning percentage (e.g. "+25%", "-10%")
            2: Panning number (e.g. "+25", "-10")
            3: Crossfade A/B label (e.g. "A50", "B25", "A=B")
            4: Crossfade L/R label (e.g. "L50", "R25", "100")
        """
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
        """Build the dB readout string from the current peak monitor values."""
        muted_a = self.last_state.get("muted_a", False)
        muted_b = self.last_state.get("muted_b", False)
        # Show '--' when all active devices are muted.
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
        """Resolve the icon path for a carousel entry.

        For app entries: try every icon-related stream property (icon name,
        flatpak app ID, binary name) against the GTK icon theme.
        For hardware entries: use the device-type default asset.
        """
        if dev_dict["type"] == "app":
            found = icons.lookup_app_icon(getattr(dev_dict["dev"], "proplist", None))
            if found:
                return found
        return self._default_icon_path(dev_dict["type"])

    def _draw_carousel(self):
        """Render the carousel overlay: centre icon (selected) plus neighbours.

        The number of visible icons is configurable (3, 5 or 7).  Icons are
        organised in tiers by distance from the centre:
            tier 1 = centre (selected), tier 2 = adjacent, tier 3 = outer.
        Each tier has its own configurable size, opacity and X/Y position
        (settings carousel_size_N / carousel_opacity_N / carousel_x_N /
        carousel_y_N); defaults scale with the canvas dimensions.

        Device name and volume percentage are drawn as text below/above the
        icons.  In mix mode the percentage is prefixed with "A " or "B " to
        indicate which slot is being assigned.
        """
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

            count = int(settings.get("carousel_count", defs["carousel_count"]))
            count = min(CAROUSEL_COUNTS, key=lambda c: abs(c - count))

            def tier_val(key, tier):
                return int(settings.get(f"carousel_{key}_{tier}", defs[f"carousel_{key}_{tier}"]))

            sizes = {t: max(1, tier_val("size", t)) for t in (1, 2, 3)}
            opacities = {t: max(0, min(100, tier_val("opacity", t))) for t in (1, 2, 3)}
            xs = {t: tier_val("x", t) for t in (1, 2, 3)}
            ys = {t: tier_val("y", t) for t in (1, 2, 3)}

            base_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

            def paste_icon(idx, x, y, size, opacity_pct):
                """Load and composite a carousel icon at the given position."""
                path = self._carousel_icon_path(self.carousel_devices[idx])
                try:
                    pil_img = icons.load_icon_as_pil(path, size)
                    if opacity_pct < 100:
                        # Apply opacity by scaling the alpha channel.
                        pil_img.putalpha(pil_img.getchannel("A").point(
                            lambda p: int(p * (opacity_pct / 100.0))))
                    if x < 0 or y < 0:
                        # alpha_composite rejects negative destinations, so
                        # crop the part that falls outside the canvas.
                        crop_x, crop_y = max(0, -x), max(0, -y)
                        if crop_x >= pil_img.width or crop_y >= pil_img.height:
                            return
                        pil_img = pil_img.crop((crop_x, crop_y, pil_img.width, pil_img.height))
                        x, y = max(0, x), max(0, y)
                    base_img.alpha_composite(pil_img, (x, y))
                except Exception as e:
                    log.debug("Error drawing carousel icon: %s", e)

            # Build the side slots: alternate right/left by increasing distance,
            # never showing more icons than there are devices.
            slots = []
            remaining = num_devs - 1
            for dist in range(1, count // 2 + 1):
                for side in (+1, -1):
                    if remaining <= 0:
                        break
                    slots.append((dist, side))
                    remaining -= 1

            # Paint from the outside in so inner icons draw on top.
            # The left X for distance N extrapolates the tier-2 → tier-3 spacing.
            x_step = xs[2] - xs[3]
            for dist, side in sorted(slots, key=lambda s: -s[0]):
                tier = 2 if dist == 1 else 3
                size = sizes[tier]
                left_x = xs[2] - (dist - 1) * x_step
                x = left_x if side < 0 else width - left_x - size
                paste_icon((center_idx + side * dist) % num_devs,
                           x, ys[tier], size, opacities[tier])
            paste_icon(center_idx, xs[1], ys[1], sizes[1], opacities[1])

            fd = FontDefaults.from_global()
            current = self.carousel_devices[center_idx]
            vol_pct = self.pulse_service.get_volume_pct(current["dev"]) if current["dev"] else 0
            margin = defs.get("bar_x", int(width * 0.05))

            draw_text_section(ctx, settings, "carousel_name", current["name"], fd, defs=defs,
                              default_y=defs["pos_y_carousel_name"],
                              margin=margin, default_max_w=width - margin * 2)

            prefix = ("A " if self.carousel_target == "a" else "B ") if settings.get("dual_mode", False) else ""
            draw_text_section(ctx, settings, "carousel_pct", f"{prefix}{vol_pct}%", fd, defs=defs,
                              default_y=defs["pos_y_carousel_pct"],
                              margin=margin, default_max_w=width - margin * 2,
                              anchor_bottom=True)

            cairo_img = self._surface_to_pil(surface, width, height)
            cairo_img.alpha_composite(base_img)
            self.set_media(image=cairo_img)
            self._update_preview(cairo_img)
        except Exception as e:
            log.error("Error drawing carousel: %s\n%s", e, traceback.format_exc())
            # Fallback: a red error image so the user knows something went wrong.
            err_img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
            ImageDraw.Draw(err_img).text((5, 5), "ERROR", fill=(255, 255, 255))
            self.set_media(image=err_img)

    # ------------------------------------------------------------------
    # Geometry defaults
    # ------------------------------------------------------------------

    def get_calculated_defaults(self):
        """Extend the base geometry defaults with carousel-specific positions."""
        defs, width, height = super().get_calculated_defaults(self.get_input())

        margin = defs.get("bar_x", int(width * 0.05))
        # Carousel text positions mirror the normal name/pct positions.
        defs["pos_y_carousel_name"] = defs.get("pos_y_name", int(round(height * 0.05)))
        defs["pos_y_carousel_pct"] = height - int(round(height * 0.05))
        defs["pos_x_carousel_name"] = margin
        defs["pos_x_carousel_pct"] = margin
        defs["width_carousel_name"] = width - (margin * 2)
        defs["width_carousel_pct"] = width - (margin * 2)

        # Carousel icon tiers: 1 = centre (selected), 2 = adjacent, 3 = outer.
        # Sizes scale with the smaller canvas dimension so the layout adapts
        # to any key resolution (100x100, 117x180, 100x200, ...).
        ref = min(width, height)
        size_1 = int(round(ref * 0.48))
        size_2 = int(round(ref * 0.28))
        size_3 = int(round(ref * 0.18))
        defs["carousel_count"] = 5
        defs["carousel_size_1"] = size_1
        defs["carousel_size_2"] = size_2
        defs["carousel_size_3"] = size_3
        defs["carousel_opacity_1"] = 100
        defs["carousel_opacity_2"] = 80
        defs["carousel_opacity_3"] = 50
        # X positions are for the LEFT icon of each pair; the right icon is
        # mirrored automatically.  Tier 1 is the centre icon (absolute).
        defs["carousel_x_1"] = (width - size_1) // 2
        defs["carousel_y_1"] = (height - size_1) // 2
        defs["carousel_x_2"] = int(round(width * 0.10))
        defs["carousel_y_2"] = (height - size_2) // 2
        defs["carousel_x_3"] = max(0, int(round(width * 0.02)))
        defs["carousel_y_3"] = (height - size_3) // 2

        return defs, width, height

    # ------------------------------------------------------------------
    # Configuration UI
    # ------------------------------------------------------------------

    def get_config_rows(self):
        """Build and return the Adwaita preferences rows for the settings panel.

        Returns a list of Adw.PreferencesGroup / Adw.ExpanderRow widgets.
        Falls back to a single error row on any exception so the UI never crashes.
        """
        try:
            settings = self.get_settings()
            lm = self.plugin_base.lm

            # Live preview of the dial face, updated on every redraw.
            grp_preview = Adw.PreferencesGroup(title=lm.get("config.preview.title", "Preview"))
            self._preview_picture = Gtk.Picture()
            self._preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self._preview_picture.set_size_request(-1, 130)
            self._preview_picture.set_margin_top(8)
            self._preview_picture.set_margin_bottom(8)
            preview_row = Adw.PreferencesRow(activatable=False)
            preview_row.set_child(self._preview_picture)
            grp_preview.add(preview_row)

            # Devices group: the mixer toggle lives at the same level as the
            # device expanders — enabling it reveals Device B.
            grp_devices = Adw.PreferencesGroup(title=lm.get("config.mixer.devices", "Devices"))
            self.switch_dual = Adw.SwitchRow(title=lm.get("config.mixer.dual_mode", "Enable Mixer"))
            self.switch_dual.set_active(settings.get("dual_mode", False))
            self.switch_dual.connect("notify::active", self.on_dual_mode_change)
            grp_devices.add(self.switch_dual)

            # Device A expander
            self.exp_dev_a = Adw.ExpanderRow(title=lm.get("config.mixer.device_a", "Device A (Left)"))
            self.exp_dev_a.add_row(DeviceConfigGroup(settings, self, suffix="a"))
            grp_devices.add(self.exp_dev_a)

            # Device B expander (hidden when dual mode is off)
            self.exp_dev_b = Adw.ExpanderRow(title=lm.get("config.mixer.device_b", "Device B (Right)"))
            self.exp_dev_b.add_row(DeviceConfigGroup(settings, self, suffix="b"))
            grp_devices.add(self.exp_dev_b)

            # Misc settings group
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

            # Volume Monitor Group
            grp_monitor = Adw.PreferencesGroup(title=lm.get("config.monitor.title", "Volume Monitor"))

            self.switch_monitor = Adw.SwitchRow(title=lm.get("config.monitor.enable", "Enable Volume Monitor"))
            self.switch_monitor.set_active(settings.get("monitor_enabled", False))
            self.switch_monitor.connect("notify::active", lambda sw, p: self.save_setting("monitor_enabled", sw.get_active()))
            grp_monitor.add(self.switch_monitor)

            self.exp_mon_settings = Adw.ExpanderRow(title=lm.get("config.monitor.settings.title", "Monitor Settings"))
            self.exp_mon_settings.add_row(VolumeMonitorSettingsRow(settings, self))
            grp_monitor.add(self.exp_mon_settings)

            self.exp_mon_bar = Adw.ExpanderRow(title=lm.get("config.bar.format", "Bar Format"))
            self.exp_mon_bar.add_row(VolumeMonitorBarRow(settings, self))
            grp_monitor.add(self.exp_mon_bar)

            def _update_monitor_vis(sw, *args):
                active = sw.get_active()
                self.exp_mon_bar.set_visible(active)
                self.exp_mon_settings.set_visible(active)
            self.switch_monitor.connect("notify::active", _update_monitor_vis)
            _update_monitor_vis(self.switch_monitor)

            # Carousel Group
            grp_carousel = Adw.PreferencesGroup(title=lm.get("config.carousel.title", "Carousel"))

            self.switch_carousel = Adw.SwitchRow(title=lm.get("config.carousel.enable", "Enable Device Carousel"))
            self.switch_carousel.set_active(settings.get("carousel_enabled", False))
            self.switch_carousel.connect("notify::active", lambda sw, p: self.save_setting("carousel_enabled", sw.get_active()))
            grp_carousel.add(self.switch_carousel)

            self.exp_car_settings = Adw.ExpanderRow(title=lm.get("config.carousel.settings.title", "Carousel Settings"))
            self.exp_car_settings.add_row(CarouselSettingsRow(settings, self))
            grp_carousel.add(self.exp_car_settings)

            self.exp_car_name = Adw.ExpanderRow(title=lm.get("config.carousel.name_format", "Name Format"))
            self.exp_car_name.add_row(CustomLabelRow(lm.get("config.format.name.top", "Top Name"),
                                                     settings, "carousel_name", self, show_text_input=False))
            grp_carousel.add(self.exp_car_name)

            self.exp_car_icons = Adw.ExpanderRow(title=lm.get("config.carousel.icons.title", "Icons"))
            self.exp_car_icons.add_row(CarouselIconsRow(settings, self))
            grp_carousel.add(self.exp_car_icons)

            self.exp_car_pct = Adw.ExpanderRow(title=lm.get("config.carousel.pct_format", "Volume Format"))
            self.exp_car_pct.add_row(CustomLabelRow(lm.get("config.format.pct.text", "Pct"),
                                                    settings, "carousel_pct", self, show_text_input=False))
            grp_carousel.add(self.exp_car_pct)

            def _update_carousel_vis(sw, *args):
                active = sw.get_active()
                self.exp_car_settings.set_visible(active)
                self.exp_car_name.set_visible(active)
                self.exp_car_icons.set_visible(active)
                self.exp_car_pct.set_visible(active)
            self.switch_carousel.connect("notify::active", _update_carousel_vis)
            _update_carousel_vis(self.switch_carousel)

            # Sync visibility of B-related rows with the current dual-mode state.
            self.exp_dev_b.set_visible(settings.get("dual_mode", False))
            self.exp_icon_b.set_visible(settings.get("dual_mode", False))

            # Fill the preview once the panel is mapped.
            GLib.timeout_add(250, self._preview_first_fill)

            return [grp_preview, grp_devices, grp_misc, grp_monitor, grp_carousel]
        except Exception as e:
            log.error("Error building config rows: %s\n%s", e, traceback.format_exc())
            return [Adw.ActionRow(title=f"ERROR: {e}")]

    def _preview_first_fill(self):
        """One-shot timer: render once so the preview is populated on open."""
        self.draw_image()
        return False

    def save_setting(self, key, value):
        """Persist a single setting key and trigger a redraw."""
        settings = self.get_settings()
        settings[key] = value
        self.set_settings(settings)
        self.draw_image()

    def on_dual_mode_change(self, switch, pspec):
        """Toggle dual-device mode and show/hide the device B / icon B expanders."""
        self.save_setting("dual_mode", switch.get_active())
        self.exp_dev_b.set_visible(switch.get_active())
        self.exp_icon_b.set_visible(switch.get_active())
