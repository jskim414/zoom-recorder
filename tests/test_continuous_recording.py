import threading
import time

import pytest

from app.audio.recorder import Recorder
from app.controller import Controller
from app.detection.session_manager import State
from app.settings import Settings
from app.storage.session_store import SessionStore
from test_recorder_controller import FakeBackend, backend_factory, spin_until


def start(controller):
    controller.manager.manual()
    key = next(iter(controller.manager.candidates))
    with pytest.raises(PermissionError):
        controller.start(key, False, True)
    controller.start(key, True, True)
    assert controller.recorder.ready.wait(2)
    return controller.store


@pytest.mark.parametrize("fail", [False, True])
def test_previous_completion_or_failure_never_changes_new_capture(tmp_path, fail):
    release = threading.Event()
    entered = threading.Event()
    class Worker:
        def __init__(self, settings): pass
        def run(self, store):
            entered.set()
            assert release.wait(5)
            if fail:
                raise RuntimeError("synthetic failure")
            (store.directory / "transcript.md").write_text("first meeting", encoding="utf-8")
            store.update(state="COMPLETE")
    controller = Controller(Settings(recordings_dir=str(tmp_path), auto_transcribe=True),
        lambda store, settings: Recorder(store, settings, backend_factory(FakeBackend())), Worker)
    try:
        first = start(controller)
        controller.stop()
        spin_until(lambda: (controller.poll() or bool(controller.transcriptions)))
        assert entered.wait(2) and controller.busy and not controller.capture_busy
        controller.settings.auto_transcribe = False
        second = start(controller)
        assert second.directory != first.directory
        release.set()
        spin_until(lambda: (controller.poll() or not controller.transcriptions))
        assert controller.manager.state == State.RECORDING
        assert not controller.recorder.stop_requested.is_set()
        assert second.metadata["state"] == "RECORDING"
        assert SessionStore(first.directory).metadata["state"] == ("ERROR" if fail else "COMPLETE")
        controller.stop()
        spin_until(lambda: (controller.poll() or not controller.busy))
        assert second.metadata["transcription"]["status"] == "disabled"
    finally:
        release.set()
        controller.stop()
        spin_until(lambda: (controller.poll() or not controller.busy))
        controller.close()


def test_queue_is_serial_and_freezes_each_sessions_settings_and_approval(tmp_path):
    release = threading.Event()
    calls = []
    class Worker:
        def __init__(self, settings): self.settings = settings
        def run(self, store, **kwargs):
            calls.append((store.directory, self.settings.model, self.settings.transcription_backend, kwargs))
            assert release.wait(5)
            store.update(state="COMPLETE")
    options = Settings(recordings_dir=str(tmp_path), auto_transcribe=True)
    controller = Controller(options, lambda store, settings: Recorder(store, settings, backend_factory(FakeBackend())), Worker)
    try:
        first = start(controller)
        controller.stop()
        spin_until(lambda: (controller.poll() or bool(controller.transcriptions)))
        spin_until(lambda: len(calls) == 1)
        second = start(controller)
        # Changing global settings must not affect a recording already approved.
        options.transcription_backend = "openai"
        options.model = "different"
        controller.stop()
        spin_until(lambda: (controller.poll() or len(controller.transcriptions) == 2))
        assert len(calls) == 1
        with pytest.raises(RuntimeError):
            controller.close()
        with pytest.raises(PermissionError):
            controller.retry_transcription(first)
        release.set()
        spin_until(lambda: (controller.poll() or not controller.busy))
        assert [c[0] for c in calls] == [first.directory, second.directory]
        assert all(c[1:] == ("models/small", "local", {}) for c in calls)
    finally:
        release.set()
        controller.stop()
        spin_until(lambda: (controller.poll() or not controller.busy))
        controller.close()


def test_pending_api_permission_does_not_apply_to_next_session(tmp_path):
    release = threading.Event()
    approvals = []
    class Worker:
        def __init__(self, settings): pass
        def run(self, store, **kwargs):
            approvals.append(kwargs)
            assert release.wait(5)
            store.update(state="COMPLETE")
    options = Settings(recordings_dir=str(tmp_path), auto_transcribe=True, transcription_backend="openai")
    controller = Controller(options, lambda store, settings: Recorder(store, settings, backend_factory(FakeBackend())), Worker)
    try:
        controller.manager.manual()
        controller.start(next(iter(controller.manager.candidates)), True, True, api_approved=True)
        assert controller.recorder.ready.wait(2)
        controller.stop()
        spin_until(lambda: (controller.poll() or bool(controller.transcriptions)))
        controller.manager.manual()
        with pytest.raises(PermissionError):
            controller.start(next(iter(controller.manager.candidates)), True, True)
        assert controller.recorder is None
        release.set()
        spin_until(lambda: (controller.poll() or not controller.busy))
        assert controller.manager.state == State.PROMPTED
        assert approvals == [{"api_approved": True}]
    finally:
        release.set()
        controller.stop()
        spin_until(lambda: (controller.poll() or not controller.busy))
        controller.close()
