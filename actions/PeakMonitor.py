"""PeakMonitor - Real-time audio peak level monitoring via PulseAudio/PipeWire.

Uses 'parecord' subprocess to capture raw PCM from a sink's monitor source.
Zero external dependencies beyond parecord (available in Flatpak runtime).

Thread-safety: This module never touches the shared pulsectl connection.
It creates its own short-lived connection to resolve the monitor source,
then uses parecord for the actual audio capture.
"""
import threading
import math
import logging
import struct
import subprocess
import os
import fcntl
import select

try:
    import numpy as _np
except ImportError:
    # numpy is optional; the pure-Python fallback will be used instead.
    _np = None

log = logging.getLogger(__name__)


class PeakMonitor:
    """Monitors real-time audio peak levels from a PulseAudio/PipeWire sink."""

    CHUNK_FRAMES = 512   # frames per read (~11.6 ms at 44100 Hz)
    CHANNELS = 2         # stereo
    SAMPLE_RATE = 44100
    DECAY_FACTOR = 0.85  # smooth peak decay multiplier applied each read cycle

    def __init__(self):
        # Left and right smoothed peak and RMS values (linear amplitude 0.0-1.0+).
        self._peak = [0.0, 0.0]
        self._rms = [0.0, 0.0]
        self._lock = threading.Lock()
        self._thread = None
        self._proc = None          # parecord subprocess
        self._running = False
        self._current_device = None
        self._monitor_source_name = None
        self._stop_event = threading.Event()

    @property
    def is_running(self):
        """True while the recording thread and subprocess are active."""
        return self._running

    # ------------------------------------------------------------------
    # Audio analysis  (static helpers, no I/O)
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_chunk_numpy(chunk):
        """Compute per-channel peak and RMS from a stereo s16le PCM chunk.

        Uses numpy vectorization: ~4x faster than the pure-Python fallback.
        Returns (peak_l, peak_r, rms_l, rms_r) in linear scale 0.0-1.0.
        """
        # Interpret raw bytes as signed 16-bit little-endian samples.
        samples = _np.frombuffer(chunk, dtype="<i2").astype(_np.float32) / 32768.0
        # Reshape to (N_frames, 2) so axis 0 = frames, axis 1 = L/R channel.
        pairs = samples.reshape(-1, 2)
        peaks = _np.abs(pairs).max(axis=0)
        rms = _np.sqrt((pairs ** 2).mean(axis=0))
        return float(peaks[0]), float(peaks[1]), float(rms[0]), float(rms[1])

    @staticmethod
    def _analyze_chunk_python(chunk):
        """Pure-Python fallback for when numpy is unavailable.

        Unpacks s16le samples with struct and iterates frame-by-frame.
        Returns (peak_l, peak_r, rms_l, rms_r) in linear scale 0.0-1.0.
        """
        num_samples = len(chunk) // 2
        samples = struct.unpack(f"<{num_samples}h", chunk)
        peak_l = peak_r = 0.0
        sum_sq_l = sum_sq_r = 0.0
        # Stereo interleaving: even indices = left, odd = right.
        for i in range(0, num_samples - 1, 2):
            l_val = samples[i] / 32768.0
            r_val = samples[i + 1] / 32768.0
            l_abs = abs(l_val)
            r_abs = abs(r_val)
            if l_abs > peak_l:
                peak_l = l_abs
            if r_abs > peak_r:
                peak_r = r_abs
            sum_sq_l += l_val * l_val
            sum_sq_r += r_val * r_val
        num_frames = num_samples // 2
        rms_l = math.sqrt(sum_sq_l / num_frames) if num_frames > 0 else 0.0
        rms_r = math.sqrt(sum_sq_r / num_frames) if num_frames > 0 else 0.0
        return peak_l, peak_r, rms_l, rms_r

    @staticmethod
    def linear_to_db(linear):
        """Convert a linear amplitude (0.0-1.0+) to decibels.

        Returns -inf for silence (linear <= 0).
        """
        if linear <= 0:
            return -float('inf')
        return 20 * math.log10(linear)

    # ------------------------------------------------------------------
    # Monitor source resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_monitor_source_name(device):
        """Find the PulseAudio monitor source name for the given device.

        Opens its own short-lived pulsectl connection so it never touches
        the shared PulseService connection (thread-safe).

        Args:
            device: A pulsectl sink or sink-input object.  Only
                    device.name / device.sink / device.source are read.
        Returns:
            Monitor source name string, or None if not found.
        """
        import pulsectl
        try:
            with pulsectl.Pulse('peak-monitor-resolve') as p:
                if hasattr(device, 'sink'):
                    # Sink-input (app stream) → find the parent sink's monitor.
                    sink_index = device.sink
                    for s in p.sink_list():
                        if s.index == sink_index:
                            return s.monitor_source_name
                elif hasattr(device, 'source'):
                    # Source-output (capture stream) → use the parent source directly.
                    source_index = device.source
                    for s in p.source_list():
                        if s.index == source_index:
                            return s.name
                else:
                    # Direct sink → monitor; direct source → itself.
                    for s in p.sink_list():
                        if s.name == device.name:
                            return s.monitor_source_name
                    for s in p.source_list():
                        if s.name == device.name:
                            return s.name
                return None
        except Exception as e:
            log.warning("Error resolving monitor source: %s", e)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, device):
        """Start monitoring peak levels for the given device.

        If the same device is already being monitored, this is a no-op.
        Stops any previously running monitor before starting a new one.

        Args:
            device: A pulsectl sink or sink-input object.
        Returns:
            True if monitoring started successfully, False otherwise.
        """
        # Skip restart if the same device (by index and type) is already active.
        if (self._running and self._current_device is not None
                and hasattr(device, 'index') and hasattr(self._current_device, 'index')
                and device.index == self._current_device.index
                and type(device) is type(self._current_device)):
            return True

        if self._running:
            self.stop()

        if not device:
            return False

        # Resolve the PulseAudio monitor source name (short-lived IPC call).
        monitor_source = self._resolve_monitor_source_name(device)
        if not monitor_source:
            log.warning("No monitor source found for device: %s",
                       getattr(device, 'name', '?'))
            return False

        self._current_device = device
        self._monitor_source_name = monitor_source
        self._stop_event.clear()
        self._running = True

        # Daemon thread: exits automatically when the main process ends.
        self._thread = threading.Thread(
            target=self._recording_loop,
            name="PeakMonitor",
            daemon=True,
        )
        self._thread.start()
        return True

    # ------------------------------------------------------------------
    # Recording loop (background thread)
    # ------------------------------------------------------------------

    def _recording_loop(self):
        """Background thread: spawns parecord and reads raw PCM samples.

        Selects the fastest available analysis function (numpy vs Python)
        once at startup and reuses it for the lifetime of the loop.
        Applies fast-attack / slow-decay smoothing before publishing values.
        """
        monitor_source = self._monitor_source_name
        if not monitor_source:
            self._running = False
            return

        # Build the parecord command for stereo s16le capture.
        cmd = [
            'parecord',
            '--raw',
            '--format=s16le',
            '--channels=2',
            '--rate=44100',
            '--latency-msec=30',
            '--process-time-msec=10',
            '--device=' + monitor_source,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            # Try the alternative binary name 'parec' before giving up.
            try:
                cmd[0] = 'parec'
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                log.error("Neither parecord nor parec found.")
                self._running = False
                return

        self._proc = proc
        log.info("PeakMonitor started on: %s (pid %d)", monitor_source, proc.pid)

        # Switch the stdout pipe to non-blocking mode so select() can be used
        # with a short timeout to allow _stop_event checks between reads.
        fd = proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        bytes_per_frame = self.CHANNELS * 2  # s16le stereo = 4 bytes/frame
        chunk_bytes = self.CHUNK_FRAMES * bytes_per_frame
        buf = bytearray()

        # Smoothed amplitude state (persists across chunks).
        smooth_l = 0.0
        smooth_r = 0.0
        smooth_rms_l = 0.0
        smooth_rms_r = 0.0

        # Choose the fastest available analysis function once, not per-chunk.
        analyze = self._analyze_chunk_numpy if _np is not None else self._analyze_chunk_python

        try:
            while not self._stop_event.is_set():
                # Poll with a 50 ms timeout so the stop event is checked often.
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    continue

                try:
                    data = proc.stdout.read(chunk_bytes)
                except (OSError, IOError):
                    # EAGAIN: the non-blocking read found no data yet.
                    continue

                if not data:
                    # Subprocess ended or the pipe was closed.
                    break

                buf.extend(data)

                # Process all complete frames accumulated in the buffer.
                while len(buf) >= chunk_bytes:
                    chunk = bytes(buf[:chunk_bytes])
                    del buf[:chunk_bytes]

                    peak_l, peak_r, rms_l, rms_r = analyze(chunk)

                    # Fast-attack, slow-decay smoothing for peak display.
                    smooth_l = max(peak_l, smooth_l * self.DECAY_FACTOR)
                    smooth_r = max(peak_r, smooth_r * self.DECAY_FACTOR)

                    # Exponential moving average for RMS: fast attack (0.2), slow release (0.05).
                    alpha_rms = 0.2 if rms_l > smooth_rms_l else 0.05
                    smooth_rms_l = smooth_rms_l * (1.0 - alpha_rms) + rms_l * alpha_rms

                    alpha_rms_r = 0.2 if rms_r > smooth_rms_r else 0.05
                    smooth_rms_r = smooth_rms_r * (1.0 - alpha_rms_r) + rms_r * alpha_rms_r

                    # Publish the smoothed values atomically for the main thread.
                    with self._lock:
                        self._peak = [smooth_l, smooth_r]
                        self._rms = [smooth_rms_l, smooth_rms_r]

        except Exception as e:
            log.error("PeakMonitor read error: %s", e)
        finally:
            self._kill_proc(proc)
            self._proc = None
            self._running = False
            log.info("PeakMonitor recording loop exited")

    # ------------------------------------------------------------------
    # Subprocess helpers
    # ------------------------------------------------------------------

    def _kill_proc(self, proc):
        """Send SIGKILL to the subprocess and wait briefly for it to exit."""
        if proc is None:
            return
        try:
            proc.kill()  # SIGKILL — immediate, no graceful shutdown needed
        except OSError:
            pass
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public read / stop
    # ------------------------------------------------------------------

    def get_peak(self):
        """Return the latest smoothed peak and RMS as ((peak_l, peak_r), (rms_l, rms_r)).

        All values are in linear scale (0.0-1.0+).  Thread-safe.
        """
        with self._lock:
            return tuple(self._peak), tuple(self._rms)

    def stop(self):
        """Stop monitoring and clean up resources.

        Guaranteed to return in under 200 ms.  Safe to call even if not running.
        """
        self._stop_event.set()
        self._running = False

        # Kill the subprocess first to unblock any pending read in the thread.
        proc = self._proc
        if proc:
            self._kill_proc(proc)
            self._proc = None

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=0.5)
        self._thread = None
        self._current_device = None
        self._monitor_source_name = None
        with self._lock:
            self._peak = [0.0, 0.0]
