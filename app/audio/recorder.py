from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta

from app.audio.loopback import select_loopback
from app.audio.microphone import select_microphone
from app.audio.segment_writer import SegmentWriter
from app.audio.activity import SpeechMonitor
from app.storage.session_store import utc_now


class Recorder:
    """Owns both streams. Opening, writing and closing happen outside the GUI thread."""
    def __init__(self, store, settings, backend_factory=None):
        self.store, self.settings = store, settings
        self.backend_factory = backend_factory
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.events = queue.Queue()
        self.thread = None
        self.origin = store.metadata["monotonic_start"]
        self.tracks = {}
        self.activity = SpeechMonitor(self.origin, self.events, settings.selected_tracks)

    def start(self):
        if self.thread is not None:
            raise RuntimeError("Recorder already started")
        self.thread = threading.Thread(target=self._run, name="audio-capture", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_requested.set()

    def wait(self, timeout=None):
        return self.done.wait(timeout)

    def _report(self, code, track=None):
        self.events.put((code, track))
        try:
            self.store.error(code, track)
        except OSError:
            self.events.put(("storage_failed", track))
            self.stop_requested.set()

    def _callback(self, track, pa):
        def callback(data, frame_count, timing, status):
            if self.stop_requested.is_set() or track["failed"]:
                return (None, pa.paComplete)
            now = time.monotonic()
            # Translate PortAudio's ADC timestamp to our monotonic session clock.
            current, adc = timing.get("current_time", 0), timing.get("input_buffer_adc_time", 0)
            delay = current - adc
            offset = max(0.0, now - self.origin - (delay if adc > 0 and 0 <= delay < 5 else frame_count / track["rate"]))
            try:
                track["queue"].put_nowait((data, offset, status))
            except queue.Full:
                track["dropped"] += frame_count
            return (None, pa.paContinue)
        return callback

    def _write(self, name, track):
        writer = SegmentWriter(self.store, name, track["rate"], track["channels"], self.settings.segment_seconds)
        try:
            first = True
            while not track["closing"].is_set() or not track["queue"].empty():
                try:
                    data, offset, status = track["queue"].get(timeout=0.1)
                except queue.Empty:
                    continue
                if first:
                    sample_time = datetime.fromisoformat(self.store.metadata["started_at"]) + timedelta(seconds=offset)
                    self.store.track(name, first_sample_offset=offset, first_sample_wall_clock=sample_time.isoformat())
                    first = False
                if status:
                    self.store.append("gaps", {"track": name, "start": offset, "end": None,
                                              "kind": "portaudio_status", "flags": status})
                    self.events.put(("buffer_overflow", name))
                writer.write(data, offset)
                self.activity.feed(name, data, track["rate"], track["channels"], offset)
        except Exception:
            track["failed"] = True
            self._report("track_write_failed", name)
        finally:
            try:
                writer.close()
            except Exception:
                self._report("segment_finalize_failed", name)

    def _report_drops(self, name, track):
        total = track["dropped"]
        delta = total - track.get("reported_dropped", 0)
        if delta:
            self.store.append("gaps", {"track": name, "kind": "queue_overflow", "frames": delta,
                                      "end": time.monotonic() - self.origin})
            track["reported_dropped"] = total
            self.events.put(("buffer_overflow", name))

    def _run(self):
        pa = None
        com_initialized = False
        try:
            self.activity.start()
            if self.backend_factory is None:
                import comtypes
                comtypes.CoInitialize()
                com_initialized = True
            endpoint_ids = None
            next_device_check = 0.0
            if self.backend_factory:
                module, pa = self.backend_factory()
            else:
                import pyaudiowpatch as module
                pa = module.PyAudio()
            selectors = {"remote": lambda: select_loopback(pa),
                         "mic": lambda: select_microphone(pa, module.paWASAPI, self.settings.microphone_index)}
            for name, select in selectors.items():
                if name not in self.settings.selected_tracks:
                    continue
                if self.stop_requested.is_set():
                    break
                try:
                    device = select()
                    rate = int(device["defaultSampleRate"])
                    channels = min(2, int(device["maxInputChannels"]))
                    track = {"queue": queue.Queue(maxsize=256), "closing": threading.Event(),
                             "rate": rate, "channels": channels, "failed": False, "dropped": 0,
                             "device_index": device["index"], "device_name": device["name"]}
                    self.tracks[name] = track
                    self.store.track(name, device=device["name"], device_index=device["index"],
                                     rate=rate, channels=channels, codec="PCM16", resampling=False,
                                     opened_at=utc_now(), opened_offset=time.monotonic() - self.origin)
                    track["stream"] = pa.open(format=module.paInt16, channels=channels, rate=rate,
                                              input=True, input_device_index=device["index"],
                                              frames_per_buffer=1024, start=False,
                                              stream_callback=self._callback(track, module))
                    track["worker"] = threading.Thread(target=self._write, args=(name, track), daemon=True)
                    track["worker"].start()
                    track["stream"].start_stream()
                except Exception:
                    if name in self.tracks:
                        self.tracks[name]["failed"] = True
                    self._report("device_open_failed", name)
            self.ready.set()
            if self.backend_factory is None:
                try:
                    from app.audio.device_monitor import default_endpoint_ids
                    endpoint_ids = default_endpoint_ids()
                except Exception:
                    self.events.put(("device_monitor_unavailable", None))
            while not self.stop_requested.wait(0.25):
                if endpoint_ids is not None and time.monotonic() >= next_device_check:
                    next_device_check = time.monotonic() + 3
                    try:
                        current_ids = default_endpoint_ids()
                        if current_ids != endpoint_ids:
                            self.events.put(("default_device_changed", None))
                            self.store.log("default_device_changed")
                            endpoint_ids = current_ids
                    except Exception:
                        self.events.put(("device_monitor_unavailable", None))
                        endpoint_ids = None
                active = 0
                for name, track in self.tracks.items():
                    if track["failed"]:
                        continue
                    try:
                        if not track["stream"].is_active():
                            raise RuntimeError("Stream inactive")
                        active += 1
                    except Exception:
                        track["failed"] = True
                        self._report("device_disconnected", name)
                    self._report_drops(name, track)
                if not active:
                    break
        except Exception:
            self._report("audio_backend_failed")
        finally:
            self.ready.set()
            for name, track in self.tracks.items():
                stream = track.get("stream")
                if stream:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        self._report("stream_close_failed", name)
                track["closing"].set()
                if "worker" in track:
                    track["worker"].join()
                try:
                    self._report_drops(name, track)
                except OSError:
                    self.events.put(("storage_failed", name))
                if not any(segment["track"] == name for segment in self.store.metadata["segments"]):
                    self._report("track_has_no_samples", name)
            if pa:
                try:
                    pa.terminate()
                except Exception:
                    self._report("backend_close_failed")
            self.activity.close()
            try:
                self.store.update(ended_at=utc_now())
            except OSError:
                self.events.put(("storage_failed", None))
            if com_initialized:
                comtypes.CoUninitialize()
            self.done.set()
