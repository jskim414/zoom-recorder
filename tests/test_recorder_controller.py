import queue
import threading
import time
from types import SimpleNamespace

import pytest

from app.audio.recorder import Recorder
from app.controller import Controller
from app.detection.session_manager import Candidate, State
from app.settings import Settings
from app.storage.session_store import SessionStore


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.active = False
        self.closed = False

    def start_stream(self):
        self.active = True
        self.callback(bytes(200), 100, {"current_time": 0, "input_buffer_adc_time": 0}, 0)

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, fail_mic=False):
        self.fail_mic = fail_mic
        self.streams = []
        self.terminated = False

    def get_default_wasapi_loopback(self):
        return dict(index=1, name="synthetic loopback", hostApi=0, isLoopbackDevice=True,
                    maxInputChannels=1, defaultSampleRate=1000)

    def get_host_api_info_by_type(self, _):
        return dict(index=0, defaultInputDevice=2)

    def get_device_info_by_index(self, index):
        if self.fail_mic:
            raise OSError("No microphone")
        return dict(index=2, name="synthetic mic", hostApi=0, isLoopbackDevice=False,
                    maxInputChannels=1, defaultSampleRate=1000)

    def open(self, **kwargs):
        assert kwargs["input"] and kwargs["start"] is False
        stream = FakeStream(kwargs["stream_callback"])
        self.streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True


def backend_factory(backend):
    return lambda: (SimpleNamespace(paWASAPI=1, paInt16=8, paComplete=1, paContinue=0), backend)


def spin_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for worker")
        time.sleep(0.01)


@pytest.mark.parametrize("fail_mic", [False, True])
def test_fake_dual_capture_and_partial_failure(tmp_path, fail_mic):
    backend = FakeBackend(fail_mic)
    store = SessionStore.create(tmp_path, "manual", "consented")
    recorder = Recorder(store, Settings(), backend_factory(backend))
    recorder.start()
    assert recorder.ready.wait(2)
    recorder.stop()
    assert recorder.wait(3)
    assert len(store.metadata["segments"]) == (1 if fail_mic else 2)
    assert bool(store.metadata["errors"]) is fail_mic
    assert all(stream.closed for stream in backend.streams) and backend.terminated
    assert store.metadata["ended_at"]


def test_capture_disconnect_reports_and_preserves_other_track(tmp_path):
    backend = FakeBackend()
    store = SessionStore.create(tmp_path, "manual", "consented")
    recorder = Recorder(store, Settings(), backend_factory(backend))
    recorder.start()
    assert recorder.ready.wait(2)
    backend.streams[0].active = False
    spin_until(lambda: bool(store.metadata["errors"]))
    assert not recorder.done.is_set()
    assert store.metadata["errors"][0]["code"] == "device_disconnected"
    recorder.stop()
    assert recorder.wait(3)


def test_queue_overflow_is_not_silent(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "consented")
    recorder = Recorder(store, Settings())
    track = {"queue": queue.Queue(maxsize=1), "failed": False, "rate": 1000, "dropped": 0}
    callback = recorder._callback(track, SimpleNamespace(paComplete=1, paContinue=0))
    callback(bytes(200), 100, {}, 0)
    callback(bytes(200), 100, {}, 0)
    assert track["dropped"] == 100
    recorder._report_drops("mic", track)
    recorder._report_drops("mic", track)
    assert [gap["frames"] for gap in store.metadata["gaps"]] == [100]
    track["dropped"] += 50
    recorder._report_drops("mic", track)
    assert [gap["frames"] for gap in store.metadata["gaps"]] == [100, 50]
    recorder.stop()
    assert callback(bytes(200), 100, {}, 0)[1] == 1


def test_end_to_end_candidate_consent_record_stop_transcript(tmp_path):
    backend = FakeBackend()
    opened = []
    def recorder_factory(store, settings):
        opened.append(True)
        return Recorder(store, settings, backend_factory(backend))
    from app.processing.transcribe import Transcriber
    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(start=0, end=0.05, text="synthetic test")]), None
    controller = Controller(Settings(recordings_dir=str(tmp_path), auto_transcribe=True), recorder_factory,
                            lambda settings: Transcriber(settings, Model))
    controller.manager.offer([Candidate("test", "desktop", "synthetic")])
    assert opened == []
    with pytest.raises(PermissionError):
        controller.start("test", False, True)
    assert opened == [] and not list(tmp_path.iterdir())
    controller.start("test", True, True)
    assert controller.recorder.ready.wait(2)
    controller.manager.suggest_stop()
    assert not controller.recorder.stop_requested.is_set()
    controller.stop()
    def complete():
        controller.poll()
        return controller.manager.state == State.COMPLETE
    spin_until(complete)
    assert (controller.store.directory / "transcript.md").exists()
    assert controller.store.metadata["consent_at"]
    assert len(controller.store.metadata["segments"]) == 2
    controller.close()


def test_instance_lock_prevents_duplicate_capture(tmp_path):
    from app.storage.instance_lock import InstanceLock
    with InstanceLock(tmp_path):
        with pytest.raises(RuntimeError):
            with InstanceLock(tmp_path):
                pass
    with InstanceLock(tmp_path):
        pass


def test_disk_write_failure_keeps_other_track_and_reports(tmp_path, monkeypatch):
    from app.audio.segment_writer import SegmentWriter
    original = SegmentWriter.write
    def failing_write(self, data, offset):
        if self.track == "mic":
            raise OSError("synthetic disk failure")
        return original(self, data, offset)
    monkeypatch.setattr(SegmentWriter, "write", failing_write)
    backend = FakeBackend()
    store = SessionStore.create(tmp_path, "manual", "consented")
    recorder = Recorder(store, Settings(), backend_factory(backend))
    recorder.start()
    assert recorder.ready.wait(2)
    spin_until(lambda: any(e["code"] == "track_write_failed" for e in store.metadata["errors"]))
    recorder.stop()
    assert recorder.wait(3)
    assert [s["track"] for s in store.metadata["segments"]] == ["remote"]


def test_stop_before_device_open_never_opens_stream(tmp_path):
    backend = FakeBackend()
    store = SessionStore.create(tmp_path, "manual", "consented")
    recorder = Recorder(store, Settings(), backend_factory(backend))
    recorder.stop()
    recorder.start()
    assert recorder.wait(3)
    assert backend.streams == [] and store.metadata["segments"] == []
