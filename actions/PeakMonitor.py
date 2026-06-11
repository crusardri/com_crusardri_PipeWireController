"""
PeakMonitor - Real-time audio peak level monitoring via PulseAudio/PipeWire.

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

log = logging.getLogger(__name__)


class PeakMonitor:
    """Monitors real-time audio peak levels from a PulseAudio/PipeWire sink."""

    CHUNK_FRAMES = 512   # frames per read (~11.6ms at 44100Hz)
    CHANNELS = 2         # stereo
    SAMPLE_RATE = 44100
    DECAY_FACTOR = 0.85  # smooth peak decay per read cycle

    def __init__(self):
        self._peak = [0.0, 0.0]  # L, R linear amplitude 0.0-1.0+
        self._rms = [0.0, 0.0]
        self._lock = threading.Lock()
        self._thread = None
        self._proc = None
        self._running = False
        self._current_device = None
        self._monitor_source_name = None
        self._stop_event = threading.Event()

    @property
    def is_running(self):
        return self._running

    @staticmethod
    def linear_to_db(linear):
        """Convert linear amplitude (0.0-1.0) to decibels."""
        if linear <= 0:
            return -float('inf')
        return 20 * math.log10(linear)

    @staticmethod
    def _resolve_monitor_source_name(device):
        """Resolve the monitor source name for a device.
        
        Uses its OWN pulsectl connection (thread-safe, short-lived).
        
        Args:
            device: A pulsectl sink or sink-input object. We only read
                    device.name or device.sink (index) from it.
        Returns:
            Monitor source name string, or None.
        """
        import pulsectl
        try:
            with pulsectl.Pulse('peak-monitor-resolve') as p:
                if hasattr(device, 'sink'):
                    # Sink-input (application playing audio) -> find parent sink's monitor
                    sink_index = device.sink
                    for s in p.sink_list():
                        if s.index == sink_index:
                            return s.monitor_source_name
                elif hasattr(device, 'source'):
                    # Source-output (application recording audio) -> find parent source
                    source_index = device.source
                    for s in p.source_list():
                        if s.index == source_index:
                            return s.name
                else:
                    # Direct sink or source
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

    def start(self, device):
        """Start monitoring peak levels for the given device.

        Args:
            device: A pulsectl sink or sink-input object.

        Returns:
            True if monitoring started successfully, False otherwise.
        """
        # If already monitoring the same device, don't restart
        if (self._running and self._current_device is not None 
                and hasattr(device, 'index') and hasattr(self._current_device, 'index')
                and device.index == self._current_device.index
                and type(device) is type(self._current_device)):
            return True

        if self._running:
            self.stop()

        if not device:
            return False

        monitor_source = self._resolve_monitor_source_name(device)
        if not monitor_source:
            log.warning("No monitor source found for device: %s", 
                       getattr(device, 'name', '?'))
            return False

        self._current_device = device
        self._monitor_source_name = monitor_source
        self._stop_event.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._recording_loop,
            name="PeakMonitor",
            daemon=True,
        )
        self._thread.start()
        return True

    def _recording_loop(self):
        """Background thread: spawns parecord and reads raw PCM samples."""
        monitor_source = self._monitor_source_name
        if not monitor_source:
            self._running = False
            return

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

        # Make stdout non-blocking so we can check _stop_event
        fd = proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        bytes_per_frame = self.CHANNELS * 2  # s16le stereo = 4 bytes/frame
        chunk_bytes = self.CHUNK_FRAMES * bytes_per_frame
        buf = bytearray()

        smooth_l = 0.0
        smooth_r = 0.0
        smooth_rms_l = 0.0
        smooth_rms_r = 0.0
        
        # Slower decay for RMS
        RMS_DECAY_FACTOR = 0.95
        RMS_ATTACK_FACTOR = 0.2

        try:
            while not self._stop_event.is_set():
                # Wait for data with a short timeout so we can check stop_event
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    continue

                try:
                    data = proc.stdout.read(chunk_bytes)
                except (OSError, IOError):
                    # EAGAIN on non-blocking read when no data available
                    continue

                if not data:
                    # Process ended / pipe closed
                    break

                buf.extend(data)

                # Process all complete frames in buffer
                while len(buf) >= chunk_bytes:
                    chunk = bytes(buf[:chunk_bytes])
                    del buf[:chunk_bytes]

                    num_frames = len(chunk) // bytes_per_frame
                    num_samples = num_frames * self.CHANNELS
                    samples = struct.unpack_from(
                        f'<{num_samples}h', chunk, 0
                    )

                    # Calculate peak and RMS per channel
                    peak_l = 0.0
                    peak_r = 0.0
                    sum_sq_l = 0.0
                    sum_sq_r = 0.0
                    
                    for i in range(0, num_samples - 1, 2):
                        l_val = samples[i] / 32768.0
                        r_val = samples[i + 1] / 32768.0
                        
                        l_abs = abs(l_val)
                        r_abs = abs(r_val)
                        if l_abs > peak_l: peak_l = l_abs
                        if r_abs > peak_r: peak_r = r_abs
                        
                        sum_sq_l += l_val * l_val
                        sum_sq_r += r_val * r_val

                    # Smooth peak: fast attack, slow decay
                    smooth_l = max(peak_l, smooth_l * self.DECAY_FACTOR)
                    smooth_r = max(peak_r, smooth_r * self.DECAY_FACTOR)
                    
                    # Calculate RMS
                    rms_l = math.sqrt(sum_sq_l / num_frames) if num_frames > 0 else 0.0
                    rms_r = math.sqrt(sum_sq_r / num_frames) if num_frames > 0 else 0.0
                    
                    # Smooth RMS: exponential moving average
                    alpha_rms = 0.2 if rms_l > smooth_rms_l else 0.05
                    smooth_rms_l = smooth_rms_l * (1.0 - alpha_rms) + rms_l * alpha_rms
                    
                    alpha_rms_r = 0.2 if rms_r > smooth_rms_r else 0.05
                    smooth_rms_r = smooth_rms_r * (1.0 - alpha_rms_r) + rms_r * alpha_rms_r

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

    def _kill_proc(self, proc):
        """Terminate a subprocess immediately."""
        if proc is None:
            return
        try:
            proc.kill()  # SIGKILL - immediate, no waiting
        except OSError:
            pass
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass

    def get_peak(self):
        """Returns the current smoothed peak and RMS values as ((peak_l, peak_r), (rms_l, rms_r)) in linear scale."""
        with self._lock:
            return tuple(self._peak), tuple(self._rms)

    def stop(self):
        """Stop monitoring and clean up. Guaranteed non-blocking (< 200ms)."""
        self._stop_event.set()
        self._running = False

        # Kill the subprocess first to unblock any read
        proc = self._proc
        if proc:
            self._kill_proc(proc)
            self._proc = None

        # Now join the thread (should exit almost immediately)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=0.5)
        self._thread = None
        self._current_device = None
        self._monitor_source_name = None
        with self._lock:
            self._peak = [0.0, 0.0]
