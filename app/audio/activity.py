"""Bounded, local-only speech observation; never controls capture or writes audio."""
from dataclasses import dataclass
import queue
import threading
import time


@dataclass(frozen=True)
class ActivitySnapshot:
    last_speech: float
    levels: dict
    healthy: bool


class SpeechMonitor:
    def __init__(self, origin, events, tracks=("remote", "mic")):
        self.origin = origin
        self.tracks = tuple(tracks)
        self.events = events
        self.queue = queue.Queue(maxsize=32)
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.last_speech = origin
        self.observed = {}
        self.levels = {}
        self.failed = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="speech-activity", daemon=True)
        self.thread.start()

    def feed(self, name, data, rate, channels, offset):
        try:
            self.queue.put_nowait((name, data, rate, channels, offset))
        except queue.Full:
            # Unknown audio is never evidence of silence.
            with self.lock:
                self.last_speech = time.monotonic()

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            healthy = not self.failed and all(0 <= now - self.observed.get(n, float("-inf")) < 2
                                              for n in self.tracks)
            levels = {n: v for n, v in self.levels.items() if now - self.observed.get(n, 0) < 2}
            return ActivitySnapshot(self.last_speech, levels, healthy)

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        try:
            # Lazy imports keep idle UI lightweight. WebRTC accepts 20 ms mono PCM16.
            import numpy as np
            import webrtcvad
            detectors = {name: webrtcvad.Vad(2) for name in self.tracks}
            buffers = {name: b"" for name in detectors}
            positions = {}
            voiced = {name: 0 for name in detectors}
            while not self.closed.is_set():
                try:
                    name, data, rate, channels, offset = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                start = self.origin + offset
                end = start + len(data) / (2 * channels * rate)
                if abs(start - positions.get(name, start)) > 0.05:
                    buffers[name] = b""
                    voiced[name] = 0
                    with self.lock:
                        self.last_speech = max(self.last_speech, start)
                positions[name] = end
                pcm = np.frombuffer(data, dtype="<i2").astype(np.float32)
                level = min(1.0, float(np.sqrt(np.mean(pcm * pcm))) / 32768) if pcm.size else 0.0
                buffers[name] += data
                frame_samples = rate // 50
                frame_bytes = frame_samples * channels * 2
                speech = False
                while len(buffers[name]) >= frame_bytes:
                    block, buffers[name] = buffers[name][:frame_bytes], buffers[name][frame_bytes:]
                    samples = np.frombuffer(block, dtype="<i2").reshape(-1, channels).astype(np.float32)
                    # Use the stronger channel; opposing stereo phases must not cancel speech.
                    mono = samples[:, int(np.argmax(np.mean(samples * samples, axis=0)))]
                    if rate not in {8000, 16000, 32000, 48000}:
                        mono = np.interp(np.arange(320) * rate / 16000, np.arange(frame_samples), mono)
                        vad_rate = 16000
                    else:
                        vad_rate = rate
                    detected = detectors[name].is_speech(mono.astype("<i2").tobytes(), vad_rate)
                    voiced[name] = voiced[name] + 1 if detected else 0
                    speech |= voiced[name] >= 3  # Ignore isolated very short clicks.
                with self.lock:
                    self.observed[name] = end
                    self.levels[name] = level
                    if speech:
                        self.last_speech = max(self.last_speech, end)
        except Exception:
            with self.lock:
                self.failed = True
            self.events.put(("speech_monitor_unavailable", None))


class RecordingReminder:
    """Clock-driven policy shared by manual and detected recordings."""
    def __init__(self, started, silence_seconds=120, long_minutes=60):
        self.started = started
        self.silence_seconds = silence_seconds
        self.long_seconds = long_minutes * 60
        self.next_long = started + self.long_seconds if self.long_seconds else float("inf")
        self.snooze_until = started
        self.disabled = False
        self.pending = None
        self.last_notified = float("-inf")

    def due(self, now, snapshot):
        if self.disabled or now < self.snooze_until:
            return None
        if self.pending == "silence" and snapshot.healthy and snapshot.last_speech > self.last_notified:
            self.pending = None
        if self.pending:
            if now - self.last_notified < 300:
                return None
            self.last_notified = now
            return self.pending
        reason = None
        if (self.silence_seconds and snapshot.healthy
                and now - max(self.started, snapshot.last_speech) >= self.silence_seconds):
            reason = "silence"
        elif now >= self.next_long:
            reason = "long"
        if reason:
            self.pending = reason
            self.last_notified = now
        return reason

    def snooze(self, now):
        self.pending = None
        self.snooze_until = now + 300
        if self.long_seconds:
            self.next_long = now + self.long_seconds

    def disable(self):
        self.disabled = True
        self.pending = None
