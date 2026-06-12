"""Set Default Device action: makes a sink/source the system default and cycles.

CONTROLLER role in MVC: handles a single press (key or dial) and changes the
system default device via PulseService (MODEL).  Rendering is delegated
entirely to StreamController's native label and media handling (set_media with
the icon path + set_bottom_label) — no custom drawing.

Behaviour on press:
    * If the system default is NOT the device shown on screen, set the shown
      device as the default.
    * If the system default IS the shown device and more than one device is
      configured, cycle to the next configured device; unavailable devices are
      skipped so the next *available* one is selected instead.
    * If the target device is offline and cannot be set, an error is shown.

The configured device list is persistent: a device that goes offline is
remembered (by name + cached description) so it reappears once it returns.

Per device the user may configure an icon and an alias.  When cycling to a
device its configured alias/icon are shown; otherwise it falls back to the
device description/name and the default speaker (sink) or microphone (source)
icon.
"""
import os
import logging
import traceback

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib
import matplotlib.font_manager
from PIL import ImageFont

from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

import globals as gl

from .PipeWireActionBase import PipeWireActionBase
from .UIComponents import make_dropdown, dropdown_get_id, dropdown_set_options

log = logging.getLogger(__name__)


class SetDefaultDevice(PipeWireActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.has_configuration = True

        lm = self.plugin_base.lm
        # A single press handles both keys (SHORT_UP) and dials (SHORT_UP).
        self.add_event_assigner(EventAssigner(
            id="SetDefault",
            ui_label=lm.get("actions.set-default.event.press", "Set / Cycle Device"),
            default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
            callback=self.on_press
        ))

        # Idle refresh timer (keeps the volume label up to date); None when off.
        self._tick_source_id = None
        # Last pushed (top, center, bottom, icon) tuple, for change detection.
        self._last_signature = None
        # Cached PIL font used to measure label widths for ellipsizing.
        self._measure_font = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    # Idle redraw interval (ms): refreshes the volume label and tracks
    # default/volume changes made outside the app.
    TICK_INTERVAL_MS = 1000

    def on_ready(self):
        """Render the currently selected device and start the refresh timer."""
        self.update_display()
        if self._tick_source_id is None:
            self._tick_source_id = GLib.timeout_add(self.TICK_INTERVAL_MS, self._on_tick)

    def on_remove(self):
        """Stop the refresh timer when the action is removed."""
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None

    def _on_tick(self):
        """Repeating timer: redraw only when the rendered labels/icon change."""
        try:
            if self._render_signature(self.get_settings()) != self._last_signature:
                self.update_display()
        except Exception as e:
            log.debug("tick error: %s", e)
        return True  # keep the timer running

    # ------------------------------------------------------------------
    # Device list helpers
    # ------------------------------------------------------------------

    def get_devices(self, settings=None):
        """Return the configured device list (a list of dicts)."""
        if settings is None:
            settings = self.get_settings()
        devices = settings.get("devices", [])
        return devices if isinstance(devices, list) else []

    def get_device_type(self, settings=None):
        """Return the configured device type ('sink' or 'source')."""
        if settings is None:
            settings = self.get_settings()
        return settings.get("device_type", "sink")

    def _clamp_index(self, settings, devices=None):
        """Return a valid current index, clamped to the device list bounds."""
        if devices is None:
            devices = self.get_devices(settings)
        idx = int(settings.get("current_index", 0))
        if not devices:
            return 0
        return max(0, min(idx, len(devices) - 1))

    def _current_device(self, settings=None):
        """Return the device dict the cycle currently points at, or None."""
        if settings is None:
            settings = self.get_settings()
        devices = self.get_devices(settings)
        if not devices:
            return None
        return devices[self._clamp_index(settings, devices)]

    def _displayed_device(self, settings=None):
        """Return the device dict to render.

        When "show active device" is enabled this is the system's current
        default device: its configured entry (for alias/icon) when present, or a
        transient entry that falls back to the device description and the default
        icon.  Otherwise it is the device the cycle points at (`current_index`).
        """
        if settings is None:
            settings = self.get_settings()
        if settings.get("show_active_device", False):
            svc = self.pulse_service
            if svc:
                dtype = self.get_device_type(settings)
                active_name = svc.get_default_name(dtype)
                if active_name:
                    for d in self.get_devices(settings):
                        if d.get("name") == active_name:
                            return d
                    # Active device is not configured: build a transient entry.
                    desc = svc.device_description(dtype, active_name)
                    return {"name": active_name, "description": desc or active_name,
                            "icon": "", "alias": ""}
        return self._current_device(settings)

    def _device_label(self, device, settings=None):
        """Return the display text for a device: alias, then description, then name."""
        if not device:
            return self.plugin_base.lm.get("config.set-default.no_device", "No device")
        alias = (device.get("alias") or "").strip()
        if alias:
            return alias
        dtype = self.get_device_type(settings)
        # Prefer a fresh description (handles renamed devices); fall back to cached.
        svc = self.pulse_service
        if svc:
            desc = svc.device_description(dtype, device.get("name", ""))
            if desc:
                return desc
        return device.get("description") or device.get("name", "?")

    def _default_icon_path(self, dtype):
        """Return the built-in fallback icon path for the device type."""
        fname = "mic.svg" if dtype == "source" else "speaker.svg"
        return os.path.join(self.plugin_base.PATH, "assets", fname)

    def _device_icon_path(self, device, dtype):
        """Return the icon path for a device: configured override, then default."""
        custom = (device.get("icon") or "") if device else ""
        if custom and os.path.isfile(custom):
            return custom
        return self._default_icon_path(dtype)

    # ------------------------------------------------------------------
    # Rendering (native StreamController labels + media)
    # ------------------------------------------------------------------

    def _label_font(self):
        """Return the PIL font StreamController uses for default labels (cached).

        Mirrors LabelManager.inject_defaults + KeyLabel.get_font so width
        measurements match the scroll-label decision exactly.
        """
        if self._measure_font is None:
            defaults = gl.settings_manager.font_defaults
            family = defaults.get("font-family") or gl.fallback_font
            size = round(defaults.get("font-size") or 15)
            path = matplotlib.font_manager.findfont(
                matplotlib.font_manager.FontProperties(family=family, size=size))
            self._measure_font = ImageFont.truetype(path, size)
        return self._measure_font

    def _fit_label(self, text):
        """Ellipsize `text` so it fits the input's width and never scrolls.

        StreamController turns any label wider than the input area into a
        scrolling label, which forces a full touchscreen re-render at 30 FPS
        on dials (severe performance hit).  Trimming to the same width
        threshold (LabelManager.get_has_scroll_labels) avoids that entirely.
        """
        if not text:
            return text
        try:
            ctrl_input = self.get_input()
            if ctrl_input is None:
                return text
            max_w = ctrl_input.get_image_size()[0]
            if max_w <= 0:
                return text
            font = self._label_font()
            if font.getbbox(text)[2] <= max_w:
                return text
            while len(text) > 1 and font.getbbox(text + "…")[2] > max_w:
                text = text[:-1]
            return text.rstrip() + "…"
        except Exception as e:
            log.debug("label fit error: %s", e)
            return text

    def _volume_text(self, device, dtype):
        """Return the device volume as a percentage string, or '--' if offline."""
        svc = self.pulse_service
        if not svc or not device:
            return "--"
        dev = svc.resolve_device(dtype, device.get("name", ""))
        if dev is None:
            return "--"
        return f"{svc.get_volume_pct(dev, zero_if_muted=False)}%"

    def _compute_labels(self, settings):
        """Return ((top, center, bottom) label texts, icon_path) for the frame.

        The name label is placed at `name_position` (default top).  When volume
        display is enabled the volume goes at `volume_position` (default bottom);
        if both positions coincide they are merged as "name - volume".
        """
        labels = {"top": "", "center": "", "bottom": ""}
        dtype = self.get_device_type(settings)
        device = self._displayed_device(settings)

        if device is None:
            name_pos = settings.get("name_position", "top")
            labels[name_pos] = self.plugin_base.lm.get("config.set-default.no_device",
                                                       "No device")
            return labels, None

        name_pos = settings.get("name_position", "top")
        vol_pos = settings.get("volume_position", "bottom")
        name_text = self._device_label(device, settings)

        if settings.get("show_volume", False):
            vol_text = self._volume_text(device, dtype)
            if name_pos == vol_pos:
                labels[name_pos] = f"{name_text} - {vol_text}"
            else:
                labels[name_pos] = name_text
                labels[vol_pos] = vol_text
        else:
            labels[name_pos] = name_text

        # Trim every label to the input width so none becomes a scrolling
        # label (those force 30 FPS touchscreen redraws on dials).
        for pos in labels:
            labels[pos] = self._fit_label(labels[pos])

        return labels, self._device_icon_path(device, dtype)

    def _render_signature(self, settings):
        """Cheap snapshot of what _compute_labels would produce (for change detection)."""
        labels, icon = self._compute_labels(settings)
        return (labels["top"], labels["center"], labels["bottom"], icon)

    def update_display(self):
        """Push the current device's icon and labels to the deck natively."""
        try:
            settings = self.get_settings()
            labels, icon = self._compute_labels(settings)
            self.set_media(media_path=icon)
            self.set_top_label(labels["top"])
            self.set_center_label(labels["center"])
            self.set_bottom_label(labels["bottom"])
            self._last_signature = (labels["top"], labels["center"], labels["bottom"], icon)
        except Exception as e:
            log.error("Error updating display: %s\n%s", e, traceback.format_exc())

    # ------------------------------------------------------------------
    # Press / cycle logic
    # ------------------------------------------------------------------

    def on_press(self, data=None):
        """Set the shown device as default, or cycle to the next available one."""
        svc = self.pulse_service
        settings = self.get_settings()
        devices = self.get_devices(settings)
        dtype = self.get_device_type(settings)

        if not svc or not devices:
            self.show_error(duration=2)
            return

        default_name = svc.get_default_name(dtype)

        # Reference index whose "next" we cycle to.  In "show active" mode we
        # advance from the currently-active device (so a press moves on from
        # what is on screen); otherwise from the cycle's stored position.
        if settings.get("show_active_device", False):
            ref_idx = next((i for i, d in enumerate(devices)
                            if d.get("name") == default_name), None)
        else:
            ref_idx = self._clamp_index(settings, devices)

        if ref_idx is not None and devices[ref_idx].get("name") == default_name:
            # The reference device is already the default: cycle to the next one.
            target_idx = self._find_next_available(devices, ref_idx, dtype)
            if target_idx is None:
                # No other available device to switch to.
                self.show_error(duration=2)
                return
        elif ref_idx is not None:
            # Reference device is not the default yet: make it the default.
            target_idx = ref_idx
        else:
            # "Show active" mode and the active device is not configured:
            # fall back to the cycle's stored position.
            target_idx = self._clamp_index(settings, devices)

        if not svc.set_default(dtype, devices[target_idx].get("name", "")):
            log.warning("Could not set default %s: %s (offline)",
                        dtype, devices[target_idx].get("name", ""))
            self.show_error(duration=2)
            return

        settings["current_index"] = target_idx
        self.set_settings(settings)
        self.hide_error()
        self.update_display()

    def _find_next_available(self, devices, start_idx, dtype):
        """Return the index of the next available device after `start_idx`.

        Wraps around the list and skips offline devices.  Returns None if no
        other device (excluding start_idx) is currently available.
        """
        svc = self.pulse_service
        n = len(devices)
        for offset in range(1, n + 1):
            cand = (start_idx + offset) % n
            if cand == start_idx:
                break
            name = devices[cand].get("name", "")
            if svc.is_device_available(dtype, name):
                return cand
        return None

    # ------------------------------------------------------------------
    # Configuration UI
    # ------------------------------------------------------------------

    def get_config_rows(self):
        """Build the Adwaita preferences rows for the settings panel."""
        try:
            settings = self.get_settings()
            lm = self.plugin_base.lm

            # Device type.
            grp_type = Adw.PreferencesGroup(title=lm.get("config.set-default.type.title",
                                                         "Device Type"))
            self.type_combo = make_dropdown(
                [("sink", lm.get("config.type.sink", "Output (Sink)")),
                 ("source", lm.get("config.type.source", "Input (Source)"))],
                settings.get("device_type", "sink"), self.on_type_changed)
            type_row = Adw.ActionRow(title=lm.get("config.type.title", "Device Type"))
            self.type_combo.set_valign(Gtk.Align.CENTER)
            type_row.add_suffix(self.type_combo)
            grp_type.add(type_row)

            # Device list group.  The "add" row stays parented for the whole
            # session; only the per-device expander rows are rebuilt.
            self.grp_devices = Adw.PreferencesGroup(
                title=lm.get("config.set-default.devices.title", "Devices to Cycle"),
                description=lm.get("config.set-default.devices.subtitle",
                                   "Press cycles through these in order"))
            self._device_rows = []

            # Add-device selector: picking a device adds it automatically.
            self._add_dropdown = make_dropdown(
                [("", lm.get("config.set-default.add", "Add Device"))], "",
                self.on_device_selected)
            self._add_dropdown.set_valign(Gtk.Align.CENTER)
            self._add_dropdown.set_hexpand(True)
            add_row = Adw.ActionRow(title=lm.get("config.set-default.add", "Add Device"))
            add_row.add_suffix(self._add_dropdown)
            self.grp_devices.add(add_row)

            self._refresh_device_rows()

            # Display group: active-device mode, label positions + optional volume.
            grp_display = Adw.PreferencesGroup(
                title=lm.get("config.set-default.display.title", "Display"))

            self.sw_active = Adw.SwitchRow(
                title=lm.get("config.set-default.show_active", "Show Active Device"))
            self.sw_active.set_active(settings.get("show_active_device", False))
            self.sw_active.connect(
                "notify::active",
                lambda sw, p: self.save_setting("show_active_device", sw.get_active()))
            grp_display.add(self.sw_active)

            name_row = Adw.ActionRow(
                title=lm.get("config.set-default.name_position", "Name Position"))
            self.name_pos_combo = make_dropdown(
                self._position_options(), settings.get("name_position", "top"),
                lambda dd, p: self.save_setting("name_position", dropdown_get_id(dd) or "top"))
            self.name_pos_combo.set_valign(Gtk.Align.CENTER)
            name_row.add_suffix(self.name_pos_combo)
            grp_display.add(name_row)

            self.sw_volume = Adw.SwitchRow(
                title=lm.get("config.set-default.show_volume", "Show Volume"))
            self.sw_volume.set_active(settings.get("show_volume", False))
            self.sw_volume.connect("notify::active", self._on_show_volume)
            grp_display.add(self.sw_volume)

            self._vol_pos_row = Adw.ActionRow(
                title=lm.get("config.set-default.volume_position", "Volume Position"))
            self.vol_pos_combo = make_dropdown(
                self._position_options(), settings.get("volume_position", "bottom"),
                lambda dd, p: self.save_setting("volume_position", dropdown_get_id(dd) or "bottom"))
            self.vol_pos_combo.set_valign(Gtk.Align.CENTER)
            self._vol_pos_row.add_suffix(self.vol_pos_combo)
            self._vol_pos_row.set_sensitive(settings.get("show_volume", False))
            grp_display.add(self._vol_pos_row)

            return [grp_type, self.grp_devices, grp_display]
        except Exception as e:
            log.error("Error building config rows: %s\n%s", e, traceback.format_exc())
            return [Adw.ActionRow(title=f"ERROR: {e}")]

    def _position_options(self):
        """Return the (id, label) pairs for a label-position dropdown."""
        lm = self.plugin_base.lm
        return [("top", lm.get("config.position.top", "Top")),
                ("center", lm.get("config.position.center", "Center")),
                ("bottom", lm.get("config.position.bottom", "Bottom"))]

    def save_setting(self, key, value):
        """Persist a single setting and refresh the display."""
        settings = self.get_settings()
        settings[key] = value
        self.set_settings(settings)
        self.update_display()

    def _on_show_volume(self, sw, pspec):
        """Toggle volume display and enable/disable the volume-position row."""
        active = sw.get_active()
        self._vol_pos_row.set_sensitive(active)
        self.save_setting("show_volume", active)

    # ------------------------------------------------------------------
    # Device list UI management
    # ------------------------------------------------------------------

    def _refresh_device_rows(self):
        """Rebuild only the per-device expander rows; the add-row is left in place."""
        grp = getattr(self, "grp_devices", None)
        if grp is None:
            return
        settings = self.get_settings()
        dtype = self.get_device_type(settings)
        devices = self.get_devices(settings)

        # Remove existing device rows (the add-row is never removed).
        for row in list(getattr(self, "_device_rows", [])):
            grp.remove(row)
        self._device_rows = []

        # One expander per configured device.
        for i, device in enumerate(devices):
            row = self._build_device_row(i, device, dtype, settings)
            grp.add(row)
            self._device_rows.append(row)

        self._refresh_add_options(dtype, devices)

    def _refresh_add_options(self, dtype, devices):
        """Repopulate the add-dropdown, excluding already-configured devices.

        The first entry is a non-selectable placeholder so that picking any
        real device always fires a selection change (and thus auto-adds it).
        """
        lm = self.plugin_base.lm
        configured = {d.get("name") for d in devices}
        available = self.pulse_service.list_devices(dtype) if self.pulse_service else []
        remaining = [(name, desc) for name, desc in available if name not in configured]

        placeholder = lm.get("config.set-default.add", "Add Device") if remaining \
            else lm.get("config.set-default.no_more", "No more devices")
        options = [("", placeholder)] + remaining
        dropdown_set_options(self._add_dropdown, options, "")
        self._add_dropdown.set_sensitive(bool(remaining))

    def _build_device_row(self, index, device, dtype, settings):
        """Build one Adw.ExpanderRow for a configured device."""
        lm = self.plugin_base.lm
        available = self.pulse_service.is_device_available(dtype, device.get("name", "")) \
            if self.pulse_service else False

        title = self._device_label(device, settings)
        subtitle = device.get("name", "")
        if not available:
            subtitle = f"{subtitle}  ({lm.get('config.device.not_available', 'Not available')})"

        exp = Adw.ExpanderRow(title=title, subtitle=subtitle)

        # Reorder / remove buttons in the header.
        btn_up = Gtk.Button(icon_name="go-up-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"])
        btn_up.set_sensitive(index > 0)
        btn_up.connect("clicked", lambda *a, i=index: self._move_device(i, -1))
        btn_down = Gtk.Button(icon_name="go-down-symbolic", valign=Gtk.Align.CENTER,
                              css_classes=["flat"])
        btn_down.set_sensitive(index < len(self.get_devices(settings)) - 1)
        btn_down.connect("clicked", lambda *a, i=index: self._move_device(i, +1))
        btn_del = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                             css_classes=["flat"])
        btn_del.connect("clicked", lambda *a, i=index: self._remove_device(i))
        for b in (btn_up, btn_down, btn_del):
            exp.add_action(b)

        # Alias entry.
        alias_row = Adw.EntryRow(title=lm.get("config.set-default.alias", "Alias"))
        alias_row.set_text(device.get("alias", "") or "")
        alias_row.connect("changed", lambda r, i=index: self._set_device_field(
            i, "alias", r.get_text(), rebuild=False))
        exp.add_row(alias_row)

        # Icon picker.
        icon_row = Adw.ActionRow(title=lm.get("config.set-default.icon", "Icon"))
        custom_icon = device.get("icon", "") or ""
        icon_row.set_subtitle(os.path.basename(custom_icon) if custom_icon
                              else lm.get("config.icon.default", "Default"))
        btn_sel = Gtk.Button(label=lm.get("config.icon.select", "Select"),
                             valign=Gtk.Align.CENTER)
        btn_sel.connect("clicked", lambda *a, i=index: self._select_device_icon(i))
        icon_row.add_suffix(btn_sel)
        btn_clear = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                               css_classes=["flat"])
        btn_clear.connect("clicked", lambda *a, i=index: self._set_device_field(i, "icon", ""))
        icon_row.add_suffix(btn_clear)
        exp.add_row(icon_row)

        return exp

    def on_type_changed(self, dd, pspec):
        """Switch device type: reset the list (devices are type-specific)."""
        new_type = dropdown_get_id(dd) or "sink"
        settings = self.get_settings()
        if settings.get("device_type", "sink") == new_type:
            return
        settings["device_type"] = new_type
        settings["devices"] = []
        settings["current_index"] = 0
        self.set_settings(settings)
        self._refresh_device_rows()
        self.update_display()

    def on_device_selected(self, dd, pspec):
        """Auto-add the picked device (the empty placeholder is ignored)."""
        name = dropdown_get_id(dd)
        if not name:
            return
        # Defer the mutation: rebuilding the dropdown model from inside its own
        # selection-changed handler crashes GTK.  Run it once the signal returns.
        GLib.idle_add(self._add_device, name)

    def _add_device(self, name):
        """Append `name` to the cycle list and refresh the UI (idle callback)."""
        settings = self.get_settings()
        dtype = self.get_device_type(settings)
        devices = list(self.get_devices(settings))
        if not any(d.get("name") == name for d in devices):
            desc = self.pulse_service.device_description(dtype, name) \
                if self.pulse_service else None
            devices.append({"name": name, "description": desc or name, "icon": "", "alias": ""})
            settings["devices"] = devices
            self.set_settings(settings)
            self._refresh_device_rows()
            self.update_display()
        return False  # one-shot idle source

    def _remove_device(self, index):
        settings = self.get_settings()
        devices = list(self.get_devices(settings))
        if not (0 <= index < len(devices)):
            return
        devices.pop(index)
        settings["devices"] = devices
        settings["current_index"] = self._clamp_index(settings, devices)
        self.set_settings(settings)
        self._refresh_device_rows()
        self.update_display()

    def _move_device(self, index, direction):
        settings = self.get_settings()
        devices = list(self.get_devices(settings))
        new_index = index + direction
        if not (0 <= index < len(devices) and 0 <= new_index < len(devices)):
            return
        devices[index], devices[new_index] = devices[new_index], devices[index]
        settings["devices"] = devices
        # Keep the displayed device stable across reorders.
        cur = int(settings.get("current_index", 0))
        if cur == index:
            settings["current_index"] = new_index
        elif cur == new_index:
            settings["current_index"] = index
        self.set_settings(settings)
        self._refresh_device_rows()
        self.update_display()

    def _set_device_field(self, index, field, value, rebuild=True):
        settings = self.get_settings()
        devices = list(self.get_devices(settings))
        if not (0 <= index < len(devices)):
            return
        devices[index] = dict(devices[index])
        devices[index][field] = value
        settings["devices"] = devices
        self.set_settings(settings)
        if rebuild:
            self._refresh_device_rows()
        self.update_display()

    def _select_device_icon(self, index):
        settings = self.get_settings()
        devices = self.get_devices(settings)
        current = devices[index].get("icon", "") if 0 <= index < len(devices) else ""
        GLib.idle_add(gl.app.let_user_select_asset, current,
                      lambda path, i=index: self._set_device_field(i, "icon", path) if path else None)
