import threading
import time

from PySide6.QtWidgets import QApplication

from app.audio.activity import ActivitySnapshot, RecordingReminder
from app.audio.recorder import Recorder
from app.controller import Controller
from app.detection.session_manager import State
from app.settings import Settings
from app.storage.history import recent_sessions
from app.storage.session_store import SessionStore
from app.ui.tray import RecorderWindow
from test_recorder_controller import FakeBackend, backend_factory, spin_until
from test_ui import qt_app, cleanup


def test_recent_transcript_copy_retry_and_read_only_history(qt_app, tmp_path):
    store = SessionStore.create(tmp_path, "manual", "consent")
    store.update(state="COMPLETE", ended_at=store.metadata["started_at"])
    (store.directory / "transcript.md").write_text("합성 테스트", encoding="utf-8")
    before = (store.directory / "metadata.json").read_bytes()
    assert recent_sessions(tmp_path)[0][2] == "전사 완료"
    assert (store.directory / "metadata.json").read_bytes() == before
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    assert window.history.topLevelItemCount() == 1
    assert window.transcript_button.isEnabled() and window.history_retry_button.isEnabled()
    window.copy_transcript()
    assert QApplication.clipboard().text() == "합성 테스트"
    QApplication.clipboard().clear()
    cleanup(window)


def test_manual_recording_reminder_visible_while_hidden_and_never_auto_stops(qt_app, tmp_path):
    options = Settings(recordings_dir=str(tmp_path), long_recording_minutes=0)
    controller = Controller(options, lambda store, settings: Recorder(store, settings, backend_factory(FakeBackend())))
    window = RecorderWindow(options, enable_detection=False, controller=controller)
    try:
        window.manual()
        window.consent.setChecked(True)
        window.start_recording()
        assert controller.recorder.ready.wait(2)
        now = time.monotonic()
        window.reminder = RecordingReminder(now - 121, 120, 0)
        controller.recorder.activity.snapshot = lambda *args: ActivitySnapshot(now - 121, {}, True)
        window.hide()
        window.tick()
        assert window.reminder_popup.isVisible() and not window.isVisible()
        assert "2분" in window.reminder_popup.message.text()
        assert controller.manager.state == State.RECORDING and not controller.recorder.stop_requested.is_set()
        window.keep_recording()
        assert not window.reminder_popup.isVisible()
        window.tick()
        assert not window.reminder_popup.isVisible()
        window.disable_reminder()
        assert window.reminder.disabled
        window.stop_recording()
        controller.require_save_confirmation = False
        spin_until(lambda: (controller.poll() or not controller.busy))
    finally:
        controller.require_save_confirmation = False
        controller.stop()
        spin_until(lambda: (controller.poll() or not controller.busy))
        cleanup(window)


def test_new_record_button_available_during_previous_transcription(qt_app, tmp_path):
    release = threading.Event()
    class Worker:
        def __init__(self, settings): pass
        def run(self, store):
            assert release.wait(5)
            store.update(state="COMPLETE")
    options = Settings(recordings_dir=str(tmp_path), auto_transcribe=True)
    controller = Controller(options, lambda store, settings: Recorder(store, settings, backend_factory(FakeBackend())), Worker)
    window = RecorderWindow(options, enable_detection=False, controller=controller)
    controller.require_save_confirmation = False
    try:
        window.manual()
        window.consent.setChecked(True)
        window.start_recording()
        assert controller.recorder.ready.wait(2)
        window.stop_recording()
        spin_until(lambda: (controller.poll() or bool(controller.transcriptions)))
        window.render()
        assert window.manual_button.isEnabled() and not window.manual_button.isHidden()
        assert "전사" in window.processing_label.text()
        window.manual()
        assert not window.consent.isChecked() and not window.start_button.isEnabled()
        release.set()
        spin_until(lambda: (controller.poll() or not controller.busy))
        assert controller.manager.state == State.PROMPTED
        window.skip()
    finally:
        release.set()
        controller.stop()
        spin_until(lambda: (controller.poll() or not controller.busy))
        cleanup(window)
