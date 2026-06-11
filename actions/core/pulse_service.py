"""
PulseService - Punto único de acceso a PulseAudio/PipeWire (pulsectl).

Encapsula la conexión y el lock compartido (MODELO en términos MVC).
Las operaciones de alto nivel devuelven valores seguros (None / 0 / listas
vacías) si la llamada falla. Para operaciones compuestas que necesitan
atomicidad, usar el context manager `locked()`.
"""
import threading
import logging
from contextlib import contextmanager

import pulsectl

log = logging.getLogger(__name__)


class PulseService:

    def __init__(self, client_name="stream-controller-pipewire"):
        self._lock = threading.RLock()
        self._pulse = pulsectl.Pulse(client_name, threading_lock=True)

    @contextmanager
    def locked(self):
        """Acceso atómico a la conexión pulsectl para operaciones compuestas."""
        with self._lock:
            yield self._pulse

    def call(self, fn, default=None):
        """Ejecuta fn(pulse) bajo el lock; devuelve `default` si falla."""
        try:
            with self._lock:
                return fn(self._pulse)
        except Exception as e:
            log.warning("PulseAudio call failed: %s", e)
            return default

    # ---------- consultas ----------

    def sink_list(self):
        return self.call(lambda p: p.sink_list(), default=[])

    def source_list(self):
        return self.call(lambda p: p.source_list(), default=[])

    def sink_input_list(self):
        return self.call(lambda p: p.sink_input_list(), default=[])

    def server_info(self):
        return self.call(lambda p: p.server_info())

    @staticmethod
    def app_binary(stream):
        """Nombre identificativo de la aplicación dueña de un stream, o None."""
        proplist = getattr(stream, "proplist", None)
        if not proplist:
            return None
        return proplist.get("application.process.binary") or proplist.get("application.name")

    def get_active_applications(self):
        """Nombres únicos de aplicaciones con streams activos (entrada o salida)."""
        def _list(p):
            apps = []
            for stream in p.sink_input_list() + p.source_output_list():
                name = self.app_binary(stream)
                if name and name not in apps:
                    apps.append(name)
            return apps
        return self.call(_list, default=[])

    def get_default_device_description(self, device_type):
        """Descripción legible del sink/source por defecto, o None."""
        def _get(p):
            info = p.server_info()
            if device_type == "sink":
                target, devs = info.default_sink_name, p.sink_list()
            else:
                target, devs = info.default_source_name, p.source_list()
            for d in devs:
                if d.name == target:
                    return d.description
            return None
        return self.call(_get)

    # ---------- volumen / mute ----------

    def get_volume_pct(self, device, zero_if_muted=True):
        """Volumen en porcentaje entero (0 si está silenciado, por defecto)."""
        if not device:
            return 0

        def _get(p):
            if zero_if_muted and getattr(device, "mute", False):
                return 0
            return int(round(p.volume_get_all_chans(device) * 100))
        return self.call(_get, default=0)

    def set_volume_pct(self, device, pct):
        if not device:
            return
        self.call(lambda p: p.volume_set_all_chans(device, pct / 100.0))

    def set_mute(self, device, state):
        if not device:
            return
        self.call(lambda p: p.mute(device, state))

    # ---------- resolución de dispositivos ----------

    def resolve_device(self, device_type, device_name, auto_index=0, auto_prefix="Auto "):
        """Resuelve la configuración (tipo + nombre) al objeto pulsectl actual.

        device_type: "sink" | "source" | "application"
        device_name: nombre concreto, "default", "Auto" o "<auto_prefix>N".
        Devuelve el dispositivo/stream o None si no existe ahora mismo.
        """
        try:
            with self._lock:
                p = self._pulse
                if device_type == "application":
                    return self._resolve_application(p, device_name, auto_index, auto_prefix)

                info = p.server_info()
                if device_type == "sink":
                    devices = p.sink_list()
                    target_name = info.default_sink_name if device_name == "default" else device_name
                else:
                    devices = p.source_list()
                    target_name = info.default_source_name if device_name == "default" else device_name

                for dev in devices:
                    if dev.name == target_name:
                        return dev
                if device_name == "default" and devices:
                    return devices[0]
                return None
        except Exception as e:
            log.warning("Error resolving device %s/%s: %s", device_type, device_name, e)
            return None

    def _resolve_application(self, p, device_name, auto_index, auto_prefix):
        inputs = p.sink_input_list()
        target_app = None

        if device_name.startswith(auto_prefix) or device_name == "Auto":
            try:
                if device_name == "Auto":
                    idx = auto_index
                else:
                    idx = int(device_name.split(" ")[1]) - 1
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
        for src in inputs:
            if self.app_binary(src) == target_app:
                return src
        return None
