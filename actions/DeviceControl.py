"""Device Control action: set/adjust volume or toggle mute on one device.

CONTROLLER role in MVC: handles key presses (LCD keys only), reads state via
PulseService (MODEL), and delegates all drawing to the `rendering` package
(VIEW).  Three control modes:

    set    - pressing sets the device volume to a fixed percentage.
    adjust - pressing adds `volume_step` percent (negative steps lower it);
             holding the key repeats the adjustment.
    mute   - pressing toggles the device mute state.

Display: in adjust mode the key can show a fader (horizontal or vertical,
reusing BarRenderer) whose direction follows the step sign; otherwise a
static icon (custom, or auto: speaker / mic / application icon).  The peak
monitor (same component as the Audio Mixer) is available in every mode.
"""
import io
import math
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
from PIL import Image

from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

from .PipeWireActionBase import PipeWireActionBase
from .PeakMonitor import PeakMonitor
from .rendering import icons
from .rendering.bars import BarRenderer
from .rendering.colors import parse_color
from .UIComponents import (DeviceConfigGroup, CustomBarRow, CustomIconRow,
                           VolumeMonitorBarRow, VolumeMonitorSettingsRow,
                           make_dropdown, dropdown_get_id)

log = logging.getLogger(__name__)

# Hard ceiling for volume in percent (matches the mixer).
VOLUME_LIMIT_MAX = 150.0
# Timer interval used when the peak monitor is not running.
IDLE_TICK_INTERVAL_S = 0.2
# Minimum timer interval in milliseconds (prevents CPU spinning).
MIN_TICK_MS = 20
# Interval between repeated adjustments while the key is held.
HOLD_REPEAT_MS = 200

# Cross-key sync: configuration sections that can mirror between two keys.
# Each has a per-section switch in the Synchronization group.
SYNC_SECTIONS = ("device", "action", "fader", "geometry", "monitor", "icon")
SYNC_DEVICE_KEYS = {"device_type", "device_name", "auto_index", "volume_limit"}
SYNC_ACTION_KEYS = {"control_action", "set_volume", "icon_style", "volume_step"}
SYNC_GEOMETRY_KEYS = {"bar_width", "bar_height", "bar_x", "bar_y", "bar_radius"}
# Per-key settings that must never be mirrored (labels stay individual).
SYNC_LOCAL_KEYS = {"show_device_name", "name_label_pos", "device_alias",
                   "show_volume_pct", "pct_label_pos", "show_db", "db_label_pos"}


class DeviceControl(PipeWireActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True
        self.icon_cache = icons.IconCache(max_size=20)

        lm = self.plugin_base.lm
        self.add_event_assigner(EventAssigner(
            id="Trigger",
            ui_label=lm.get("actions.device-control.event.trigger", "Trigger"),
            default_events=[Input.Key.Events.SHORT_UP],
            callback=self.on_trigger_event
        ))
        self.add_event_assigner(EventAssigner(
            id="HoldStart",
            ui_label=lm.get("actions.device-control.event.hold_start", "Hold Start (repeat)"),
            default_events=[Input.Key.Events.HOLD_START],
            callback=self.on_hold_start
        ))
        self.add_event_assigner(EventAssigner(
            id="HoldStop",
            ui_label=lm.get("actions.device-control.event.hold_stop", "Hold Stop"),
            default_events=[Input.Key.Events.HOLD_STOP],
            callback=self.on_hold_stop
        ))

        # Last rendered state: used to detect changes and skip redundant redraws.
        self.last_state = {"vol": -1, "muted": False, "dev": None}
        self.last_tick_time = 0
        self._force_redraw = False
        # GLib timer source IDs; None when not scheduled.
        self._tick_source_id = None
        self._hold_source_id = None

        self.peak_monitor = PeakMonitor()
        self._monitor_display_active = False
        self._last_interaction_time = time.time()
        # Cache of the last native-label text per position, so we only call
        # set_label when the text actually changes (avoids redundant redraws).
        self._last_labels = {"top": None, "center": None, "bottom": None}
        # True while applying settings pushed by a synced partner; prevents
        # the set_settings hook from echoing the change back (infinite loop).
        self._sync_receiving = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_ready(self):
        """Render an initial frame and start the dynamic tick timer."""
        self._last_labels = {"top": None, "center": None, "bottom": None}
        self.plugin_base.register_device_control(self)
        self.draw_image()
        if self._tick_source_id is None:
            self._schedule_tick(IDLE_TICK_INTERVAL_S)

    def on_remove(self):
        """Cancel timers and stop the peak monitor thread to avoid leaks."""
        self.plugin_base.unregister_device_control(self)
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
        self._cancel_hold_timer()
        self.peak_monitor.stop()

    def on_removed_from_cache(self):
        """Deleting the action from a page goes through this hook (the page
        reload path never calls on_remove), so clean up here as well."""
        self.on_remove()

    def _schedule_tick(self, interval_s):
        """Schedule a one-shot GLib timer for `interval_s` seconds from now."""
        self._tick_source_id = GLib.timeout_add(max(MIN_TICK_MS, int(interval_s * 1000)),
                                                self._on_tick_timer)

    def _on_tick_timer(self):
        """GLib timer callback: calls on_tick() and reschedules with the returned interval."""
        self._tick_source_id = None
        try:
            interval = self.on_tick()
        except Exception as e:
            log.debug("tick error: %s", e)
            interval = IDLE_TICK_INTERVAL_S
        self._schedule_tick(interval or IDLE_TICK_INTERVAL_S)
        return False  # one-shot; rescheduled manually so the interval can vary

    def on_tick(self):
        """Main update loop: check for state changes and redraw if needed.

        Returns the desired interval in seconds until the next tick
        (shorter while the peak monitor is rendering).
        """
        current_time = time.time()
        settings = self.get_settings()
        monitor_enabled = settings.get("monitor_enabled", False)
        monitor_delay = settings.get("monitor_delay", 5)
        monitor_fps = settings.get("monitor_fps", 10)

        time_since_interaction = current_time - self._last_interaction_time
        should_monitor = monitor_enabled and time_since_interaction >= monitor_delay
        tick_interval = 1.0 / monitor_fps if should_monitor else IDLE_TICK_INTERVAL_S

        if current_time - self.last_tick_time < tick_interval:
            return tick_interval
        self.last_tick_time = current_time

        try:
            state = self._gather_state(settings)

            changed = (self.last_state["vol"] != state.vol or
                       self.last_state["muted"] != state.mut or
                       self.last_state["dev"] != state.nm)

            # If volume changed while the monitor was active, the change came
            # from another action (not this key press, which resets the timer
            # via _mark_interaction).  Reset the delay so this key briefly
            # switches to volume-bar display, mirroring the change.
            if changed and should_monitor and state.dev is not None:
                self._last_interaction_time = current_time
                should_monitor = False

            if should_monitor and state.dev is not None:
                self.peak_monitor.start(state.dev)
            elif self.peak_monitor.is_running:
                self.peak_monitor.stop()

            monitor_running = should_monitor and self.peak_monitor.is_running
            force_draw = monitor_running or self._force_redraw

            if changed or force_draw or self._monitor_display_active != monitor_running:
                self._force_redraw = False
                self._monitor_display_active = monitor_running
                self.last_state.update({"vol": state.vol, "muted": state.mut,
                                        "dev": state.nm})
                self.draw_image(state)
        except Exception as e:
            log.debug("on_tick error: %s", e)
        return tick_interval

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _mark_interaction(self):
        """Record the current time as the last user interaction and clear the cache."""
        self._last_interaction_time = time.time()
        if self.pulse_service:
            self.pulse_service.invalidate_snapshot()

    def _refresh(self):
        """Trigger an immediate tick (and potential redraw) without waiting."""
        self.last_tick_time = 0
        self.on_tick()

    def _resolve_target(self, settings):
        """Resolve the configured device to a pulsectl object (or None)."""
        svc = self.pulse_service
        if not svc:
            return None
        device_type = settings.get("device_type", "sink")
        device_name = settings.get("device_name") or \
            ("Auto" if device_type == "application" else "default")
        auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
        return svc.resolve_device(device_type, device_name,
                                  auto_index=settings.get("auto_index", 0),
                                  auto_prefix=auto_prefix)

    def _gather_state(self, settings=None):
        """Resolve the device and read volume/mute in a single lock acquire."""
        if settings is None:
            settings = self.get_settings()
        dev = self._resolve_target(settings)
        with self.pulse_service.locked() as pulse:
            vol = int(round(pulse.volume_get_all_chans(dev) * 100)) if dev else 0
            mut = bool(dev.mute) if dev else False
            nm = dev.name if dev else "OFFLINE"
        return SimpleNamespace(dev=dev, vol=vol, mut=mut, nm=nm)

    @staticmethod
    def _volume_limit(settings):
        """Return the effective volume ceiling (max 150%)."""
        return min(VOLUME_LIMIT_MAX, float(settings.get("volume_limit", 100)))

    def invalidate_render(self):
        """Reset cached volume and force a redraw on the next call.

        Called by configuration UI rows after any setting change.
        """
        self.last_state["vol"] = -1
        self.draw_image()

    # ------------------------------------------------------------------
    # Press / hold events
    # ------------------------------------------------------------------

    def on_trigger_event(self, data=None):
        """Single key press: perform the configured control action."""
        self._do_action()

    def on_hold_start(self, data=None):
        """Key held: start repeating the adjustment (adjust mode only)."""
        if self.get_settings().get("control_action", "set") != "adjust":
            return
        self._do_action()
        self._cancel_hold_timer()
        self._hold_source_id = GLib.timeout_add(HOLD_REPEAT_MS, self._on_hold_repeat)

    def on_hold_stop(self, data=None):
        """Key released after a hold: stop repeating."""
        self._cancel_hold_timer()

    def _cancel_hold_timer(self):
        if self._hold_source_id is not None:
            GLib.source_remove(self._hold_source_id)
            self._hold_source_id = None

    def _on_hold_repeat(self):
        """Repeating timer while the key is held: keep adjusting the volume."""
        if self.get_settings().get("control_action", "set") != "adjust":
            self._hold_source_id = None
            return False
        self._do_action()
        return True  # keep repeating until cancelled

    def _do_action(self):
        """Perform the configured action (set / adjust / mute) on the device."""
        self._mark_interaction()
        settings = self.get_settings()
        svc = self.pulse_service
        dev = self._resolve_target(settings)
        if not svc or dev is None:
            self.show_error(duration=2)
            return

        action = settings.get("control_action", "set")
        limit = self._volume_limit(settings)
        if action == "mute":
            svc.set_mute(dev, not dev.mute)
        elif action == "adjust":
            step = int(settings.get("volume_step", 10)) or 10
            vol = svc.get_volume_pct(dev, zero_if_muted=False)
            svc.set_volume_pct(dev, max(0.0, min(limit, vol + step)))
        else:
            target = float(settings.get("set_volume", 50))
            svc.set_volume_pct(dev, max(0.0, min(limit, target)))

        self.hide_error()
        self._refresh()
        # Wake every other key bound to this same device, even when the value
        # did not change (already at min/max) — a value-diff check would miss it.
        self.plugin_base.broadcast_device_interaction(dev.name, self)

    def on_external_interaction(self, device_name):
        """A sibling action touched a device; if it is ours, wake to volume.

        Mirrors a key press without changing the volume: resets the inactivity
        timer so this key briefly shows the volume display.
        """
        if self._resolve_matches(device_name):
            self._wake_to_volume()

    def _resolve_matches(self, device_name):
        """True when this action currently resolves to `device_name`."""
        try:
            dev = self._resolve_target(self.get_settings())
        except Exception:
            return False
        return dev is not None and dev.name == device_name

    def _wake_to_volume(self):
        """Reset the inactivity timer and redraw, as if this key were pressed."""
        self._mark_interaction()
        self._force_redraw = True
        self._refresh()

    # ------------------------------------------------------------------
    # Cross-key configuration sync
    #
    # Two keys can share their configuration (e.g. the two halves of a
    # split fader).  Every settings write goes through set_settings, so the
    # hook below mirrors the syncable sections to the partner key.  Labels
    # (SYNC_LOCAL_KEYS) and the sync settings themselves never travel;
    # volume_step syncs its magnitude but each key keeps its own sign.
    # ------------------------------------------------------------------

    def set_settings(self, settings: dict):
        super().set_settings(settings)
        if not self._sync_receiving:
            self._push_sync(settings)

    @staticmethod
    def _sync_section_of(key):
        """Classify a settings key into its sync section (None = never sync)."""
        if key.startswith("sync_") or key in SYNC_LOCAL_KEYS:
            return None
        if key in SYNC_DEVICE_KEYS:
            return "device"
        if key in SYNC_ACTION_KEYS:
            return "action"
        if key in SYNC_GEOMETRY_KEYS:
            return "geometry"
        if key.startswith("monitor"):
            return "monitor"
        if key == "show_icon" or key.startswith("icon_"):
            return "icon"
        if key.startswith("bar"):
            return "fader"
        return None

    def _sync_flags(self, settings):
        """Per-section sync switches (default: every section enabled)."""
        return {sec: bool(settings.get(f"sync_sec_{sec}", True))
                for sec in SYNC_SECTIONS}

    def _device_controls_on_page(self):
        """Yield (json_identifier, action) for every other DeviceControl key.

        Enumerated from the page's action_objects (the source of truth) so
        deleted actions never appear; the plugin registry may briefly hold
        stale instances between page reloads.
        """
        keys = self.page.action_objects.get(Input.Key.input_type, {})
        for ident, states in keys.items():
            for actions in states.values():
                for action in actions.values():
                    if isinstance(action, DeviceControl) and action is not self:
                        yield ident, action

    def _sync_partner_ident(self, settings):
        """Return the partner key's json identifier, resolving 'auto'.

        'auto' prefers the other half of the split fader (the key adjacent
        in the fill direction: vertical positive on top, partner below;
        horizontal positive on the right, partner on the left).  When this
        key is not a fader — or that cell is empty — it falls back to any
        neighbouring key that holds a DeviceControl action, so enabling
        sync on a fresh key next to a configured one still links them.
        """
        target = settings.get("sync_partner", "auto")
        if target != "auto":
            return target
        if not isinstance(self.input_ident, Input.Key):
            return None
        x, y = self.input_ident.coords
        candidates = []
        fader, vertical = self._is_fader(settings)
        if fader:
            step = int(settings.get("volume_step", 10))
            if vertical:
                candidates.append((x, y + 1 if step >= 0 else y - 1))
            else:
                candidates.append((x - 1 if step >= 0 else x + 1, y))
        candidates += [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]
        occupied = {ident for ident, _ in self._device_controls_on_page()}
        for cx, cy in candidates:
            if cx < 0 or cy < 0:
                continue
            ident = Input.Key.Coords_To_PageCoords((cx, cy))
            if ident in occupied:
                return ident
        return None

    def _resolve_sync_partner(self, settings):
        """Find the live DeviceControl instance for the configured partner."""
        ident = self._sync_partner_ident(settings)
        if not ident:
            return None
        for other_ident, action in self._device_controls_on_page():
            if other_ident == ident:
                return action
        return None

    def _key_length(self, settings):
        """Pixel length of the fader's axis (width if horizontal, height if vertical)."""
        try:
            w, h = self.get_input().get_image_size()
        except Exception:
            w = h = 100
        _, vertical = self._is_fader(settings)
        return max(32, int(h if vertical else w))

    def _geometry_mirror(self, src):
        """Mirror image of `src`'s bar geometry across the split axis.

        Returns {key: value-or-None}; None means delete (use the default).
        Only bar_x flips (`length - x - width`): the two split-fader halves
        read as reflections — moving the right bar +1 moves the left bar -1,
        and width removed from one side is removed from the mirrored side.
        The perpendicular keys (bar_y/height) and the radius/width copy
        straight across.  Non-fader configs copy geometry verbatim.
        """
        fader, _ = self._is_fader(src)
        if not fader:
            return {k: (src[k] if k in src else None) for k in SYNC_GEOMETRY_KEYS}
        length = self._key_length(src)
        src_w = int(src.get("bar_width", length))
        src_x = int(src.get("bar_x", 0))
        # Reflect the absolute box across the split axis: x -> length - x - w.
        # A full-width default bar (x=0, w=length) mirrors to itself.
        mirror_x = length - src_x - src_w
        out = {"bar_x": None if mirror_x == 0 else mirror_x,
               "bar_width": None if src_w == length else src_w}
        for k in ("bar_y", "bar_height", "bar_radius"):
            out[k] = src[k] if k in src else None
        return out

    @staticmethod
    def _pair_step_sign(dst_action, src_action, src_settings):
        """Step sign for `dst_action`, chosen by physical position.

        Split-fader convention: the right (horizontal) or top (vertical) key
        is positive, the left / bottom key negative.  Non-adjacent layouts
        fall back to the opposite of the source's sign, so a pair never ends
        up pointing the same way.
        """
        src_step = int(src_settings.get("volume_step", 10))
        fallback = -1 if src_step >= 0 else 1
        try:
            sx, sy = src_action.input_ident.coords
            dx, dy = dst_action.input_ident.coords
        except Exception:
            return fallback
        if dy == sy and dx != sx:
            return 1 if dx > sx else -1     # horizontal: right key positive
        if dx == sx and dy != sy:
            return 1 if dy < sy else -1     # vertical: top key positive (smaller y)
        return fallback

    def _merge_synced(self, src, dst, flags, dst_step_sign=None):
        """Return a copy of `dst` with the enabled sections taken from `src`.

        Deleted keys (settings cleaned back to defaults) are mirrored too.
        Geometry is reflected across the split axis (see _geometry_mirror);
        volume_step copies the magnitude only, with `dst_step_sign` choosing
        the direction (default: opposite of the source).
        """
        out = dict(dst)
        do_geom = flags.get("geometry", True)
        geom = self._geometry_mirror(src) if do_geom else None
        for key in set(src) | set(dst):
            sec = self._sync_section_of(key)
            if sec is None or not flags.get(sec, True) or key == "volume_step":
                continue
            if sec == "geometry":
                continue  # handled below via the mirror
            if key in src:
                out[key] = src[key]
            else:
                out.pop(key, None)
        if geom is not None:
            for k, v in geom.items():
                if v is None:
                    out.pop(k, None)
                else:
                    out[k] = v
        if flags.get("action", True):
            # Always materialise the step, even when neither key set it
            # explicitly (a fresh pair would otherwise both sit at +10).
            src_step = int(src.get("volume_step", 10))
            mag = abs(src_step) or 10
            if dst_step_sign is None:
                dst_step_sign = -1 if src_step >= 0 else 1
            out["volume_step"] = mag * dst_step_sign
        return out

    def _push_sync(self, settings):
        """Mirror this key's syncable settings to its partner (if linked)."""
        if not settings.get("sync_enabled", False):
            return
        partner = self._resolve_sync_partner(settings)
        if partner is None:
            return
        sign = self._pair_step_sign(partner, self, settings)
        merged = self._merge_synced(settings, partner.get_settings(),
                                    self._sync_flags(settings), dst_step_sign=sign)
        # The per-section switches are shared by the pair.
        for sec in SYNC_SECTIONS:
            flag = f"sync_sec_{sec}"
            if flag in settings:
                merged[flag] = settings[flag]
            else:
                merged.pop(flag, None)
        partner._apply_synced(merged)

    def _apply_synced(self, new_settings):
        """Persist settings pushed by the synced partner without echoing back.

        Mutates the existing settings dict in place rather than replacing it,
        so configuration rows holding a reference to it (CustomBarRow, etc.)
        stay valid; replacing the object orphans them and a later edit would
        push a stale, incomplete dict back — dropping keys like control_action.
        """
        self._sync_receiving = True
        try:
            current = self.get_settings()
            if current is not new_settings:
                current.clear()
                current.update(new_settings)
            self.set_settings(current)
        finally:
            self._sync_receiving = False
        self.invalidate_render()

    def _link_sync(self):
        """Adopt the partner's configuration and write the reciprocal link.

        Called when sync is enabled (or the partner changes): this key takes
        the partner's syncable sections, then the partner is pointed back at
        this key so changes flow both ways from a single gesture.
        """
        settings = self.get_settings()
        partner = self._resolve_sync_partner(settings)
        if partner is None:
            return False
        # 1. This key adopts the partner's syncable config (mirrored geometry,
        #    position-correct step sign).
        sign = self._pair_step_sign(self, partner, partner.get_settings())
        merged = self._merge_synced(partner.get_settings(), settings,
                                    self._sync_flags(settings), dst_step_sign=sign)
        self._apply_synced(merged)
        # 2. Write the reciprocal link on the partner, then push back so the
        #    partner mirrors this key (its own position-correct sign/geometry).
        p_settings = partner.get_settings()
        p_settings["sync_enabled"] = True
        p_settings["sync_partner"] = self.input_ident.json_identifier
        partner._apply_synced(p_settings)
        self._push_sync(self.get_settings())
        return True

    def _unlink_sync(self, partner=None):
        """Break the reciprocal link on the partner side."""
        if partner is None:
            partner = self._resolve_sync_partner(self.get_settings())
        if partner is None:
            return
        p_settings = partner.get_settings()
        if p_settings.get("sync_enabled", False):
            p_settings["sync_enabled"] = False
            partner._apply_synced(p_settings)

    # ------------------------------------------------------------------
    # Geometry defaults
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fader(settings):
        """Return (is_fader, is_vertical) for the current configuration."""
        if settings.get("control_action", "set") != "adjust":
            return False, False
        style = settings.get("icon_style", "fader_h")
        return style in ("fader_h", "fader_v"), style == "fader_v"

    def _fader_layout(self, settings, vertical=False):
        """Return (invert_fill, right_half) for the split-fader display.

        The fader is one continuous volume bar (twice the canvas length)
        split across two keys: the *negative*-step key shows the lower
        volume half (0–50%), the *positive*-step key the upper half (50–100%).
        'Invert Bar' mirrors the whole arrangement.

        Horizontal (no rotation):
          positive = right half, fill left→right (invert=False).
          negative = left half,  fill left→right.

        Vertical (context rotated 90° CW before drawing, so draw-space
        LEFT maps to screen TOP, draw-space RIGHT maps to screen BOTTOM):
          To make the bar fill bottom→top on screen, we need to fill
          RIGHT→LEFT in draw-space (invert=True).
          positive key is the TOP key → rounded cap at screen TOP = draw-space
          LEFT → bar_x=0 (left/negative half visible, right_half=False).
          negative key is the BOTTOM key → rounded cap at screen BOTTOM =
          draw-space RIGHT → right_half=True.
        """
        step = int(settings.get("volume_step", 10))
        is_positive = step >= 0
        bar_invert = bool(settings.get("bar_invert", False))
        if vertical:
            right_half = (not is_positive) ^ bar_invert
            invert_fill = not bar_invert
        else:
            right_half = is_positive ^ bar_invert
            invert_fill = bar_invert
        return invert_fill, right_half

    def get_calculated_defaults(self):
        """Extend base geometry with key-sized fader, icon and text defaults.

        In fader mode the bar defaults describe the *drawing space*: for the
        vertical fader the context is rotated 90°, so length runs along the
        key height and the perpendicular axis along the key width.

        The fader bar spans the full container by default.  Each key of a
        split pair shows half the volume range (negative key 0-50%, positive
        50-100%); the corners on the split side are drawn flat (corner_flags
        in draw_image).  All geometry defaults are absolute pixel values and
        independent of each other — bar_x does not track bar_width, so editing
        one never resizes or shifts the bar via the other.
        """
        defs, width, height = super().get_calculated_defaults(self.get_input())
        settings = self.get_settings()
        fader, vertical = self._is_fader(settings)

        if fader:
            length, perp = (height, width) if vertical else (width, height)
            thick = max(8, int(round(perp * 0.30)))
            defs["bar_width"] = length
            defs["bar_x"] = 0
            defs["bar_height"] = thick
            defs["bar_y"] = (perp - thick) // 2
            defs["bar_radius"] = thick // 2

        # Centred icon (non-suffixed keys for CustomIconRow with suffix="").
        icon_size = int(round(min(width, height) * 0.55))
        defs["icon_height"] = icon_size
        defs["icon_x"] = (width - icon_size) // 2
        defs["icon_y"] = (height - icon_size) // 2
        defs["icon_out_width"] = 1
        defs["icon_out_color"] = "#000000"

        # The volume % and dB readouts use native StreamController labels
        # (set_label), not cairo-drawn text, so no text geometry is needed here.

        return defs, width, height

    # ------------------------------------------------------------------
    # Icon helpers (mirrors the mixer's auto-icon flow, unsuffixed keys)
    # ------------------------------------------------------------------

    def _default_icon_path(self, settings, dtype):
        """Return the path to the built-in icon for the given device type."""
        fname = "mic.svg" if dtype == "source" else "speaker.svg"
        if settings.get("control_action", "set") == "adjust":
            if int(settings.get("volume_step", 10)) < 0:
                fname = "mic-less.svg" if dtype == "source" else "speaker-less.svg"
        return os.path.join(self.plugin_base.PATH, "assets", fname)

    def _resolve_auto_icon_path(self, settings, dev):
        """Choose the automatic icon: app icon for applications, asset otherwise."""
        dtype = settings.get("device_type", "sink")
        if dtype == "application":
            auto_prefix = self.plugin_base.lm.get("config.device.auto", "Auto") + " "
            dev_name = settings.get("device_name") or "Auto"
            if dev:
                found = icons.lookup_app_icon(getattr(dev, "proplist", None))
            elif dev_name != "Auto" and not dev_name.startswith(auto_prefix):
                found = icons.lookup_theme_icon(dev_name)
            else:
                found = None
            if found:
                return found
        return self._default_icon_path(settings, dtype)

    def _get_icon_data(self, settings, defs, dev):
        """Load, process, and cache the icon.

        Returns (pil_image, x, y, palette).  Always called (even in fader
        mode) because the palette feeds the bar's auto colour modes.
        """
        icon_path = settings.get("icon_path", "")
        icon_h = settings.get("icon_height", defs["icon_height"])
        icon_x = settings.get("icon_x", defs["icon_x"])
        icon_y = settings.get("icon_y", defs["icon_y"])
        icon_out_w = settings.get("icon_out_width", defs["icon_out_width"])
        icon_out_c = parse_color(settings.get("icon_out_color", defs["icon_out_color"]))

        if not icon_path or not os.path.isfile(icon_path):
            icon_path = self._resolve_auto_icon_path(settings, dev)

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
        return pil_img, icon_x, icon_y, pal

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _surface_to_pil(surface, width, height):
        """Convert a cairo ARGB32 surface to a PIL RGBA image."""
        return Image.frombuffer("RGBA", (width, height),
                                surface.get_data().tobytes(), "raw", "BGRA", 0, 1)

    def draw_image(self, state=None):
        """Render the complete key face and push it to the device.

        Pipeline: bar (fader or monitor meter) → icon overlay → text overlays.
        The vertical fader is achieved by rotating the cairo context 90°
        before drawing, so BarRenderer needs no vertical-specific code.
        """
        try:
            settings = self.get_settings()
            if state is None:
                state = self._gather_state(settings)
            defs, width, height = self.get_calculated_defaults()
            fader, vertical = self._is_fader(settings)

            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            ctx = cairo.Context(surface)

            # Display volume: 0 when muted, None when the device is offline.
            vol_display = (0 if state.mut else state.vol) if state.dev else None

            icon_data = self._get_icon_data(settings, defs, state.dev)
            palettes = {"a": icon_data[3]}

            monitor_active = self._monitor_display_active and self.peak_monitor.is_running

            # Bar: fader volume bar, or peak meter while the monitor runs.
            if fader or monitor_active:
                if fader and vertical:
                    # Rotate so a left→right bar renders top→bottom.
                    ctx.save()
                    ctx.translate(width, 0)
                    ctx.rotate(math.pi / 2)
                bars = BarRenderer(ctx, settings, defs,
                                   is_single_mode=True,
                                   is_monitor_active=monitor_active,
                                   palettes=palettes)
                if fader:
                    if int(settings.get("volume_step", 10)) >= 0:
                        bars.fill_lo, bars.fill_hi = 0.5, 1.0   # upper half
                    else:
                        bars.fill_lo, bars.fill_hi = 0.0, 0.5   # lower half

                if monitor_active:
                    if fader:
                        # The monitor shares the fader bar's box (size/position
                        # come from the same bar_* settings); it shows this
                        # key's half of one continuous meter, remapped to fill
                        # the box, with the same flat split edge — geometrically
                        # identical to the volume bar.  Colours stay separate
                        # (monitor_* settings).
                        invert_fill, right_half = self._fader_layout(settings, vertical=vertical)

                        bars.corner_flags = ((False, True, True, False) if right_half
                                             else (True, False, False, True))
                        # monitor_meter reads 'monitor_invert' from settings,
                        # not bars.invert, so inject the fader's fill direction
                        # into a temporary copy (nothing is persisted).  For a
                        # vertical fader the context is already rotated 90° CW,
                        # and _fader_layout accounts for that.
                        mon_settings = dict(settings)
                        mon_settings["monitor_invert"] = invert_fill
                        bars.settings = mon_settings
                    self._draw_monitor_bars(bars, state)
                else:
                    # Split fader: the bar spans the whole key; the negative
                    # key shows 0-50% of the volume, the positive key 50-100%.
                    # The corners on the split side are flat, the outer cap
                    # stays rounded.  'Invert Bar' mirrors the arrangement.
                    bars.invert, right_half = self._fader_layout(settings, vertical=vertical)
                    bars.corner_flags = ((False, True, True, False) if right_half
                                         else (True, False, False, True))
                    vol_local = None
                    if vol_display is not None:
                        if int(settings.get("volume_step", 10)) >= 0:
                            vol_local = max(0.0, min(100.0, (vol_display - 50) * 2))
                        else:
                            vol_local = max(0.0, min(100.0, vol_display * 2))
                    bars.marker(bars.single_bar(vol_local))
                if fader and vertical:
                    ctx.restore()

            # Volume % and dB use native StreamController labels, not drawn text.
            self._update_labels(settings, state, vol_display, monitor_active)

            cairo_img = self._surface_to_pil(surface, width, height)

            # Icon overlay: hidden in fader mode (the fader replaces it).
            if not fader and settings.get("show_icon", True):
                icon, x, y, _ = icon_data
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                overlay.alpha_composite(icon, (max(0, x), max(0, y)))
                if state.mut:
                    icons.draw_mute_cross(overlay, x, y, icon.width, icon.height)
                cairo_img.alpha_composite(overlay)

            self.set_media(image=cairo_img)
            self._update_preview(cairo_img)
        except Exception as e:
            log.error("Error drawing DeviceControl: %s\n%s", e, traceback.format_exc())

    def _draw_monitor_bars(self, bars, state):
        """Draw the peak meter (mono or stereo) via BarRenderer."""
        (peak_l, peak_r), (rms_l, rms_r) = self.peak_monitor.get_peak()
        if state.mut:
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

    def _device_label_text(self, settings, state):
        """Friendly device name for the label: alias if set, else description."""
        alias = (settings.get("device_alias") or "").strip()
        if alias:
            return alias
        if state.dev is not None:
            dtype = settings.get("device_type", "sink")
            if dtype == "application":
                name = self.pulse_service.app_binary(state.dev)
                if name:
                    return name
            return getattr(state.dev, "description", None) or state.nm
        # Offline / unresolved: fall back to the configured target name.
        return settings.get("device_name") or \
            self.plugin_base.lm.get("config.device.default", "Default")

    def _update_labels(self, settings, state, vol_display, monitor_active):
        """Set the native StreamController labels for name, volume % and dB.

        Each readout has a configurable position (top / center / bottom):
        the device name defaults to the top, the volume to the bottom, the
        decibels to the top.  Readouts sharing a position merge into a single
        line in name → volume → dB order ("Speaker - 100% - -32 dB").  Only
        positions whose text changed are pushed, so repeated frames don't
        trigger redundant redraws.
        """
        parts = {"top": [], "center": [], "bottom": []}

        if settings.get("show_device_name", True):
            pos = settings.get("name_label_pos", "top")
            parts.setdefault(pos, []).append(self._device_label_text(settings, state))
        if settings.get("show_volume_pct", False):
            pct = "--" if vol_display is None else f"{vol_display}%"
            pos = settings.get("pct_label_pos", "bottom")
            parts.setdefault(pos, []).append(pct)
        if settings.get("show_db", False) and monitor_active:
            pos = settings.get("db_label_pos", "top")
            parts.setdefault(pos, []).append(self._build_db_text(state))

        for pos in ("top", "center", "bottom"):
            text = " - ".join(parts.get(pos, []))
            prev = self._last_labels.get(pos)
            if prev == text:
                continue
            # Never had a label here and still don't: leave any user-set label
            # untouched (only clear positions this action actually wrote to).
            if not text and prev in (None, ""):
                self._last_labels[pos] = ""
                continue
            self._last_labels[pos] = text
            try:
                self.set_label(text, position=pos)
            except Exception as e:
                log.debug("set_label(%s) failed: %s", pos, e)

    def _build_db_text(self, state):
        """Build the dB readout string from the current peak monitor values."""
        if state.mut:
            return "--"
        (peak_l, peak_r), _ = self.peak_monitor.get_peak()
        peak_db = PeakMonitor.linear_to_db((peak_l + peak_r) / 2.0)
        if peak_db <= -60:
            return "-inf dB"
        return f"{peak_db:.1f} dB"

    def _update_preview(self, pil_img):
        """Mirror the rendered face into the config panel's live preview."""
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

    # ------------------------------------------------------------------
    # Configuration UI
    # ------------------------------------------------------------------

    def get_config_rows(self):
        """Build the Adwaita preferences rows for the settings panel."""
        try:
            settings = self.get_settings()
            lm = self.plugin_base.lm

            # Live preview.
            grp_preview = Adw.PreferencesGroup(title=lm.get("config.preview.title", "Preview"))
            self._preview_picture = Gtk.Picture()
            self._preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self._preview_picture.set_size_request(-1, 130)
            self._preview_picture.set_margin_top(8)
            self._preview_picture.set_margin_bottom(8)
            preview_row = Adw.PreferencesRow(activatable=False)
            preview_row.set_child(self._preview_picture)
            grp_preview.add(preview_row)

            # Device selector (type / device / auto-index / limit).
            grp_device = Adw.PreferencesGroup(title=lm.get("config.device.title", "Device"))
            exp_device = Adw.ExpanderRow(title=lm.get("config.device.title", "Device"))
            exp_device.add_row(DeviceConfigGroup(settings, self, suffix=""))
            grp_device.add(exp_device)

            # Action group.
            grp_action = Adw.PreferencesGroup(
                title=lm.get("config.device-control.action.title", "Action"))

            action_row = Adw.ActionRow(title=lm.get("config.device-control.action.title", "Action"))
            self.action_combo = make_dropdown(
                [("set", lm.get("config.device-control.action.set", "Set Volume")),
                 ("adjust", lm.get("config.device-control.action.adjust", "Adjust Volume")),
                 ("mute", lm.get("config.device-control.action.mute", "Mute/Unmute"))],
                settings.get("control_action", "set"), self.on_action_changed)
            self.action_combo.set_valign(Gtk.Align.CENTER)
            action_row.add_suffix(self.action_combo)
            grp_action.add(action_row)

            # Set-volume specific: target volume.
            self.row_set_volume = Adw.SpinRow(
                title=lm.get("config.device-control.set_volume", "Volume (%)"))
            self.row_set_volume.set_adjustment(Gtk.Adjustment(
                value=settings.get("set_volume", 50), lower=0, upper=100, step_increment=5))
            self.row_set_volume.connect(
                "notify::value",
                lambda spin, p: self.save_setting("set_volume", int(spin.get_value())))
            grp_action.add(self.row_set_volume)

            # Adjust specific: icon style, steps, fader format.
            self.row_icon_style = Adw.ActionRow(
                title=lm.get("config.device-control.icon_style", "Icon Style"))
            self.style_combo = make_dropdown(
                [("fader_h", lm.get("config.device-control.style.fader_h", "Fader (Horizontal)")),
                 ("fader_v", lm.get("config.device-control.style.fader_v", "Fader (Vertical)")),
                 ("static", lm.get("config.device-control.style.static", "Static"))],
                settings.get("icon_style", "fader_h"),
                self.on_icon_style_changed)
            self.style_combo.set_valign(Gtk.Align.CENTER)
            self.row_icon_style.add_suffix(self.style_combo)
            grp_action.add(self.row_icon_style)

            self._last_step = int(settings.get("volume_step", 10)) or 10
            self.row_step = Adw.SpinRow(
                title=lm.get("config.device-control.steps", "Steps (%)"))
            self.row_step.set_adjustment(Gtk.Adjustment(
                value=self._last_step, lower=-100, upper=100, step_increment=1))
            self.row_step.connect("notify::value", self.on_step_changed)
            grp_action.add(self.row_step)

            self.exp_fader = Adw.ExpanderRow(
                title=lm.get("config.device-control.fader_format", "Fader Format"))
            self.custom_bar_row = CustomBarRow(settings, self, hide_dual_style=True)
            self.exp_fader.add_row(self.custom_bar_row)
            grp_action.add(self.exp_fader)

            # Synchronization group: link this key's config with another key.
            grp_sync = Adw.PreferencesGroup(
                title=lm.get("config.device-control.sync.title", "Synchronization"))

            self.sw_sync = Adw.SwitchRow(
                title=lm.get("config.device-control.sync.enable", "Synchronize With Another Key"),
                subtitle=lm.get("config.device-control.sync.enable_sub",
                                "Adopts the partner's configuration; changes then flow both ways"))
            self.sw_sync.set_active(settings.get("sync_enabled", False))
            self.sw_sync.connect("notify::active", self.on_sync_toggled)
            grp_sync.add(self.sw_sync)

            self.row_sync_partner = Adw.ActionRow(
                title=lm.get("config.device-control.sync.partner", "Partner Key"))
            self.dd_sync_partner = make_dropdown(
                self._sync_partner_options(settings),
                settings.get("sync_partner", "auto"),
                self.on_sync_partner_changed)
            self.dd_sync_partner.set_valign(Gtk.Align.CENTER)
            self.row_sync_partner.add_suffix(self.dd_sync_partner)
            grp_sync.add(self.row_sync_partner)

            self.exp_sync_sections = Adw.ExpanderRow(
                title=lm.get("config.device-control.sync.sections", "Synced Sections"),
                subtitle=lm.get("config.device-control.sync.sections_sub",
                                "Disabled sections stay individual per key"))
            sec_titles = {
                "device": lm.get("config.device.title", "Device"),
                "action": lm.get("config.device-control.action.title", "Action"),
                "fader": lm.get("config.device-control.fader_format", "Fader Format"),
                "geometry": lm.get("config.device-control.sync.sec.geometry", "Size & Position"),
                "monitor": lm.get("config.monitor.title", "Volume Monitor"),
                "icon": lm.get("config.device-control.sync.sec.icon", "Icon"),
            }
            for sec in SYNC_SECTIONS:
                row = Adw.SwitchRow(title=sec_titles[sec])
                row.set_active(settings.get(f"sync_sec_{sec}", True))
                row.connect(
                    "notify::active",
                    lambda sw, p, s=sec: self.save_setting(f"sync_sec_{s}", sw.get_active()))
                self.exp_sync_sections.add_row(row)
            grp_sync.add(self.exp_sync_sections)

            self._update_sync_visibility()

            # Volume Monitor group (available in every mode).
            grp_monitor = Adw.PreferencesGroup(title=lm.get("config.monitor.title", "Volume Monitor"))
            self.switch_monitor = Adw.SwitchRow(
                title=lm.get("config.monitor.enable", "Enable Volume Monitor"))
            self.switch_monitor.set_active(settings.get("monitor_enabled", False))
            self.switch_monitor.connect(
                "notify::active",
                lambda sw, p: self.save_setting("monitor_enabled", sw.get_active()))
            grp_monitor.add(self.switch_monitor)

            self.exp_mon_settings = Adw.ExpanderRow(
                title=lm.get("config.monitor.settings.title", "Monitor Settings"))
            self.exp_mon_settings.add_row(VolumeMonitorSettingsRow(settings, self, show_db_switch=False))
            grp_monitor.add(self.exp_mon_settings)

            self.exp_mon_bar = Adw.ExpanderRow(title=lm.get("config.bar.format", "Bar Format"))
            self.exp_mon_bar.add_row(VolumeMonitorBarRow(settings, self))
            grp_monitor.add(self.exp_mon_bar)

            def _update_monitor_vis(sw, *args):
                active = sw.get_active()
                self.exp_mon_settings.set_visible(active)
                self.exp_mon_bar.set_visible(active)
            self.switch_monitor.connect("notify::active", _update_monitor_vis)
            _update_monitor_vis(self.switch_monitor)

            # Display group: icon + text toggles.
            grp_display = Adw.PreferencesGroup(
                title=lm.get("config.set-default.display.title", "Display"))

            self.exp_icon = Adw.ExpanderRow(
                title=lm.get("config.icon.format", "Icon Format"),
                subtitle=lm.get("config.device-control.icon_overridden",
                                "Overridden by the fader"))
            self.exp_icon.add_row(CustomIconRow(settings, self))
            grp_display.add(self.exp_icon)

            # Native labels: a toggle plus a position selector (top/center/
            # bottom) for each readout.  Two readouts sharing a position merge
            # into one label.  Defaults: name top, volume bottom, decibels top.
            pos_opts = [("top", lm.get("config.device-control.pos.top", "Top")),
                        ("center", lm.get("config.device-control.pos.center", "Center")),
                        ("bottom", lm.get("config.device-control.pos.bottom", "Bottom"))]

            self.sw_name = Adw.SwitchRow(
                title=lm.get("config.device-control.show_name", "Show Device Name"))
            self.sw_name.set_active(settings.get("show_device_name", True))
            self.sw_name.connect(
                "notify::active",
                lambda sw, p: self.save_setting("show_device_name", sw.get_active()))
            self.dd_name_pos = make_dropdown(
                pos_opts, settings.get("name_label_pos", "top"),
                lambda dd, p: self.save_setting("name_label_pos", dropdown_get_id(dd) or "top"))
            self.dd_name_pos.set_valign(Gtk.Align.CENTER)
            self.sw_name.add_suffix(self.dd_name_pos)
            grp_display.add(self.sw_name)

            self.row_alias = Adw.EntryRow(
                title=lm.get("config.device-control.alias", "Alias (overrides name)"))
            self.row_alias.set_text(settings.get("device_alias", "") or "")
            self.row_alias.connect(
                "changed",
                lambda row: self.save_setting("device_alias", row.get_text().strip()))
            grp_display.add(self.row_alias)

            self.sw_pct = Adw.SwitchRow(
                title=lm.get("config.device-control.show_pct", "Show Volume Percentage"))
            self.sw_pct.set_active(settings.get("show_volume_pct", False))
            self.sw_pct.connect(
                "notify::active",
                lambda sw, p: self.save_setting("show_volume_pct", sw.get_active()))
            self.dd_pct_pos = make_dropdown(
                pos_opts, settings.get("pct_label_pos", "bottom"),
                lambda dd, p: self.save_setting("pct_label_pos", dropdown_get_id(dd) or "bottom"))
            self.dd_pct_pos.set_valign(Gtk.Align.CENTER)
            self.sw_pct.add_suffix(self.dd_pct_pos)
            grp_display.add(self.sw_pct)

            self.sw_db = Adw.SwitchRow(
                title=lm.get("config.monitor.show_db", "Show Decibels"))
            self.sw_db.set_active(settings.get("show_db", False))
            self.sw_db.connect(
                "notify::active",
                lambda sw, p: self.save_setting("show_db", sw.get_active()))
            self.dd_db_pos = make_dropdown(
                pos_opts, settings.get("db_label_pos", "top"),
                lambda dd, p: self.save_setting("db_label_pos", dropdown_get_id(dd) or "top"))
            self.dd_db_pos.set_valign(Gtk.Align.CENTER)
            self.sw_db.add_suffix(self.dd_db_pos)
            grp_display.add(self.sw_db)

            self._update_action_visibility(settings.get("control_action", "set"))

            GLib.timeout_add(250, self._preview_first_fill)
            return [grp_preview, grp_device, grp_action, grp_sync,
                    grp_monitor, grp_display]
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
        self.invalidate_render()

    def on_action_changed(self, dd, pspec):
        """Switch the control action and adapt row visibility."""
        action = dropdown_get_id(dd) or "set"
        self.save_setting("control_action", action)
        self._update_action_visibility(action)
        if hasattr(self, "custom_bar_row"):
            self.custom_bar_row.update_dynamic_defaults()
            self.custom_bar_row.update_geometry_labels()

    def on_icon_style_changed(self, dd, pspec):
        self.save_setting("icon_style", dropdown_get_id(dd) or "fader_h")
        if hasattr(self, "custom_bar_row"):
            self.custom_bar_row.update_dynamic_defaults()
            self.custom_bar_row.update_geometry_labels()

    # --- Synchronization UI handlers ---

    def _sync_partner_options(self, settings):
        """Dropdown options: Auto + every other DeviceControl key on this page."""
        lm = self.plugin_base.lm
        opts = [("auto", lm.get("config.device-control.sync.auto", "Auto (adjacent key)"))]
        key_word = lm.get("config.device-control.sync.key", "Key")
        seen = set()
        for ident, action in self._device_controls_on_page():
            if ident in seen:
                continue
            seen.add(ident)
            x, y = action.input_ident.coords
            opts.append((ident, f"{key_word} {x},{y}"))
        # Keep a stored partner visible even when its key no longer exists.
        current = settings.get("sync_partner", "auto")
        if current not in {oid for oid, _ in opts}:
            not_available = lm.get("config.device.not_available", "Not Available")
            opts.append((current, f"{current} ({not_available})"))
        return opts

    def on_sync_toggled(self, sw, pspec):
        """Enable/disable sync; links or unlinks the partner reciprocally."""
        enabled = sw.get_active()
        settings = self.get_settings()
        if enabled != settings.get("sync_enabled", False):
            if enabled:
                settings["sync_enabled"] = True
                self._apply_synced(settings)  # persist without pushing yet
                if not self._link_sync():     # adopt partner + reciprocal link
                    # No partner found (empty neighbours / deleted key):
                    # flash the error indicator so the user knows.
                    self.show_error(duration=2)
            else:
                partner = self._resolve_sync_partner(settings)
                settings["sync_enabled"] = False
                self._apply_synced(settings)
                self._unlink_sync(partner)
        self._update_sync_visibility()

    def on_sync_partner_changed(self, dd, pspec):
        """Re-link when the partner selection changes."""
        new = dropdown_get_id(dd) or "auto"
        settings = self.get_settings()
        if new == settings.get("sync_partner", "auto"):
            return
        if settings.get("sync_enabled", False):
            self._unlink_sync()  # break the link with the previous partner
        settings["sync_partner"] = new
        self._apply_synced(settings)
        if settings.get("sync_enabled", False):
            self._link_sync()

    def _update_sync_visibility(self):
        """Partner selector and section switches only show while sync is on."""
        active = self.sw_sync.get_active()
        self.row_sync_partner.set_visible(active)
        self.exp_sync_sections.set_visible(active)

    def _update_action_visibility(self, action):
        """Show only the rows that apply to the selected action."""
        is_set = action == "set"
        is_adjust = action == "adjust"
        self.row_set_volume.set_visible(is_set)
        self.row_icon_style.set_visible(is_adjust)
        self.row_step.set_visible(is_adjust)
        self.exp_fader.set_visible(is_adjust)

    def on_step_changed(self, spin, pspec):
        """Persist the step value, snapping past zero (0 is not allowed).

        Crossing zero continues in the travel direction: coming down from +1
        jumps to -1, coming up from -1 jumps to +1.
        """
        value = int(spin.get_value())
        if value == 0:
            value = -1 if self._last_step > 0 else 1
            spin.set_value(value)
            return  # set_value re-fires this handler with the snapped value
        self._last_step = value
        self.save_setting("volume_step", value)
