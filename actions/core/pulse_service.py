"""PulseService - Single point of access to PulseAudio/PipeWire (pulsectl).

Encapsulates the shared connection and its RLock (MODEL in MVC terms).
High-level operations return safe defaults (None / 0 / empty lists) when
a call fails, so callers never need to handle pulsectl exceptions directly.
For compound operations that require atomicity, use the `locked()` context manager.
"""
import time
import threading
import logging
from contextlib import contextmanager

import pulsectl

log = logging.getLogger(__name__)

# How long topology snapshots are cached (sink/source lists, server_info).
# Must be <= the idle tick interval (0.2 s) so stale data is never visible.
SNAPSHOT_TTL_S = 0.15


class PulseService:

    def __init__(self, client_name="stream-controller-pipewire"):
        # A single RLock serializes all access; pulsectl's own threading_lock
        # is therefore unnecessary and has been omitted.
        self._lock = threading.RLock()
        self._pulse = pulsectl.Pulse(client_name)
        # Cache dict: key -> (timestamp, value)
        self._snapshot = {}

    # ------------------------------------------------------------------
    # Internal cache helpers
    # ------------------------------------------------------------------

    def _snap(self, key, fetch):
        """Return a cached result for `key`, refreshing it when expired.

        Must be called while the lock is already held.
        `fetch` is a zero-argument callable that queries pulsectl.
        """
        now = time.monotonic()
        hit = self._snapshot.get(key)
        if hit is not None and now - hit[0] < SNAPSHOT_TTL_S:
            return hit[1]
        val = fetch()
        self._snapshot[key] = (now, val)
        return val

    def invalidate_snapshot(self):
        """Force fresh reads on the next query.

        Called after any write (volume, mute) or user interaction so that
        the next frame reflects the new state instead of stale cached data.
        """
        self._snapshot.clear()

    # ------------------------------------------------------------------
    # Lock / raw access
    # ------------------------------------------------------------------

    @contextmanager
    def locked(self):
        """Yield the raw pulsectl connection for atomic compound operations."""
        with self._lock:
            yield self._pulse

    def call(self, fn, default=None):
        """Execute fn(pulse) under the lock and return its result.

        Returns `default` on any exception so callers stay exception-free.
        """
        try:
            with self._lock:
                return fn(self._pulse)
        except Exception as e:
            log.warning("PulseAudio call failed: %s", e)
            return default

    # ------------------------------------------------------------------
    # Topology queries  (all use the snapshot cache)
    # ------------------------------------------------------------------

    def sink_list(self):
        """Return the list of available audio sinks (output devices)."""
        return self.call(lambda p: self._snap("sinks", p.sink_list), default=[])

    def source_list(self):
        """Return the list of available audio sources (input devices)."""
        return self.call(lambda p: self._snap("sources", p.source_list), default=[])

    def sink_input_list(self):
        """Return the list of active sink-inputs (streams from applications)."""
        return self.call(lambda p: self._snap("sink_inputs", p.sink_input_list), default=[])

    def server_info(self):
        """Return server info including the default sink/source names."""
        return self.call(lambda p: self._snap("server_info", p.server_info))

    @staticmethod
    def app_binary(stream):
        """Extract a human-readable application name from a pulsectl stream object.

        Tries 'application.process.binary' first, then 'application.name'.
        Returns None if neither property is present.
        """
        proplist = getattr(stream, "proplist", None)
        if not proplist:
            return None
        return proplist.get("application.process.binary") or proplist.get("application.name")

    def get_active_applications(self):
        """Return a deduplicated list of binary names with active audio streams.

        Includes both sink-inputs (playback) and source-outputs (capture).
        """
        def _list(p):
            apps = []
            for stream in p.sink_input_list() + p.source_output_list():
                name = self.app_binary(stream)
                if name and name not in apps:
                    apps.append(name)
            return apps
        return self.call(_list, default=[])

    def get_default_device_description(self, device_type):
        """Return a human-readable description for the default sink or source.

        Returns None if the default device cannot be determined.
        """
        def _get(p):
            info = self._snap("server_info", p.server_info)
            if device_type == "sink":
                target, devs = info.default_sink_name, self._snap("sinks", p.sink_list)
            else:
                target, devs = info.default_source_name, self._snap("sources", p.source_list)
            for d in devs:
                if d.name == target:
                    return d.description
            return None
        return self.call(_get)

    def get_default_name(self, device_type):
        """Return the system default sink or source name (or None)."""
        info = self.server_info()
        if not info:
            return None
        return info.default_sink_name if device_type == "sink" else info.default_source_name

    def list_devices(self, device_type, skip_monitors=True):
        """Return [(name, description)] for the given type's available devices.

        For sources, monitor devices are skipped by default (they mirror sinks
        and are rarely useful as a capture default).
        """
        devices = self.sink_list() if device_type == "sink" else self.source_list()
        out = []
        for d in devices:
            name = getattr(d, "name", "")
            if device_type == "source" and skip_monitors and (
                    name.endswith(".monitor")
                    or getattr(d, "description", "").startswith("Monitor of")):
                continue
            out.append((name, getattr(d, "description", name)))
        return out

    def device_description(self, device_type, name):
        """Return the description for a device name, or None if it is offline."""
        for n, desc in self.list_devices(device_type, skip_monitors=False):
            if n == name:
                return desc
        return None

    def is_device_available(self, device_type, name):
        """Return True when a sink/source with `name` is currently present."""
        return any(n == name for n, _ in self.list_devices(device_type, skip_monitors=False))

    def set_default(self, device_type, name):
        """Set the system default sink/source by name.

        Returns True on success, False if the device is offline or the call
        fails.  Invalidates the snapshot so the next read sees the new default.
        """
        if not name or not self.is_device_available(device_type, name):
            return False

        def _set(p):
            if device_type == "sink":
                p.sink_default_set(name)
            else:
                p.source_default_set(name)
            return True
        ok = self.call(_set, default=False)
        self.invalidate_snapshot()
        return bool(ok)

    # ------------------------------------------------------------------
    # Volume / mute
    # ------------------------------------------------------------------

    def get_volume_pct(self, device, zero_if_muted=True):
        """Return the device volume as an integer percentage (0-150+).

        If `zero_if_muted` is True (default) and the device is muted,
        returns 0 regardless of the stored volume level.
        """
        if not device:
            return 0

        def _get(p):
            if zero_if_muted and getattr(device, "mute", False):
                return 0
            return int(round(p.volume_get_all_chans(device) * 100))
        return self.call(_get, default=0)

    def set_volume_pct(self, device, pct):
        """Set the device volume to `pct` percent and invalidate the snapshot."""
        if not device:
            return
        self.call(lambda p: p.volume_set_all_chans(device, pct / 100.0))
        # Invalidate so the next frame reads the freshly written value.
        self.invalidate_snapshot()

    def set_mute(self, device, state):
        """Set the mute state of `device` and invalidate the snapshot."""
        if not device:
            return
        self.call(lambda p: p.mute(device, state))
        self.invalidate_snapshot()

    # ------------------------------------------------------------------
    # Device resolution
    # ------------------------------------------------------------------

    def resolve_device(self, device_type, device_name, auto_index=0, auto_prefix="Auto "):
        """Resolve a (type, name) pair to the current pulsectl object.

        device_type: "sink" | "source" | "application"
        device_name: concrete name, "default", "Auto", or "<auto_prefix>N".

        Returns the matching pulsectl object, or None if it is offline.
        All lookups use the snapshot cache to avoid redundant IPC.
        """
        try:
            with self._lock:
                p = self._pulse
                if device_type == "application":
                    return self._resolve_application(p, device_name, auto_index, auto_prefix)

                info = self._snap("server_info", p.server_info)
                if device_type == "sink":
                    devices = self._snap("sinks", p.sink_list)
                    # "default" is resolved via server_info to avoid hardcoding a name.
                    target_name = info.default_sink_name if device_name == "default" else device_name
                else:
                    devices = self._snap("sources", p.source_list)
                    target_name = info.default_source_name if device_name == "default" else device_name

                for dev in devices:
                    if dev.name == target_name:
                        return dev
                # Fall back to the first available device when "default" is not found.
                if device_name == "default" and devices:
                    return devices[0]
                return None
        except Exception as e:
            log.warning("Error resolving device %s/%s: %s", device_type, device_name, e)
            return None

    def _resolve_application(self, p, device_name, auto_index, auto_prefix):
        """Resolve an application device name to its active sink-input stream.

        "Auto" / "<auto_prefix>N" selects by position in the active-app list.
        A concrete name matches the first stream with that binary name.
        """
        inputs = self._snap("sink_inputs", p.sink_input_list)
        target_app = None

        if device_name.startswith(auto_prefix) or device_name == "Auto":
            try:
                # "Auto" uses auto_index directly; "Auto N" extracts the 1-based index.
                if device_name == "Auto":
                    idx = auto_index
                else:
                    idx = int(device_name.split(" ")[1]) - 1
                # Build an ordered, deduplicated list of active app binaries.
                apps = []
                seen = set()
                for src in inputs:
                    binary = self.app_binary(src)
                    if binary and binary not in seen:
                        seen.add(binary)
                        apps.append(binary)
                if idx < len(apps):
                    target_app = apps[idx]
            except (ValueError, IndexError) as e:
                log.debug("Error getting auto index: %s", e)
        else:
            target_app = device_name

        if not target_app:
            return None
        # Return the first stream that belongs to the resolved application.
        for src in inputs:
            if self.app_binary(src) == target_app:
                return src
        return None
