import time
import queue

import pytest

from app.audio.activity import SpeechMonitor, RecordingReminder
from app.audio.recorder import Recorder
from app.controller import Controller
from app.settings import Settings, load_settings
from app.storage.session_store import SessionStore
from app.ui.tray import RecorderWindow
from test_recorder_controller import FakeBackend, backend_factory
from test_ui import qt_app, cleanup


@pytest.mark.parametrize("pc,mic", [(True, True), (True, False), (False, True)])
def test_only_selected_devices_are_opened(tmp_path, pc, mic):
    options = Settings(record_pc=pc, record_mic=mic)
    backend = FakeBackend(fail_mic=not mic)
    if not pc:
        backend.get_default_wasapi_loopback = lambda: pytest.fail("Unselected PC device queried")
    if not mic:
        backend.get_host_api_info_by_type = lambda _: pytest.fail("Unselected mic queried")
    store = SessionStore.create(tmp_path, "manual", "consent")
    recorder = Recorder(store, options, backend_factory(backend))
    recorder.start()
    assert recorder.ready.wait(2)
    recorder.stop()
    assert recorder.wait(3)
    assert not store.metadata["errors"]
    assert set(store.metadata["tracks"]) == set(options.selected_tracks)
    assert len(backend.streams) == int(pc) + int(mic)


def test_single_source_health_and_silence_reminder():
    now = time.monotonic()
    monitor = SpeechMonitor(now - 200, queue.Queue(), ("remote",))
    monitor.observed["remote"] = now
    snapshot = monitor.snapshot(now)
    assert snapshot.healthy
    assert RecordingReminder(now - 200, 120, 0).due(now, snapshot) == "silence"
    assert not monitor.snapshot(now + 3).healthy


def test_empty_selection_rejected_before_capture_or_storage(tmp_path):
    controller = Controller(Settings(recordings_dir=str(tmp_path / "recordings")))
    controller.manager.manual()
    with pytest.raises(ValueError):
        controller.start(next(iter(controller.manager.candidates)), True, True, record_pc=False, record_mic=False)
    assert controller.recorder is None and not (tmp_path / "recordings").exists()
    controller.close()


def test_prompt_selection_and_consent_are_both_required(qt_app, tmp_path):
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    window.manual()
    window.record_pc.setChecked(False)
    window.record_mic.setChecked(False)
    window.consent.setChecked(True)
    assert not window.start_button.isEnabled()
    window.record_mic.setChecked(True)
    assert window.start_button.isEnabled() and window.controller.recorder is None
    window.skip()
    window.manual()
    assert window.record_pc.isChecked() and window.record_mic.isChecked()
    assert not window.start_button.isEnabled()
    cleanup(window)


def test_location_change_keeps_old_sessions_after_restart(qt_app, tmp_path, monkeypatch):
    import app.ui.preferences as preferences
    monkeypatch.setattr(preferences, "startup_enabled", lambda: False)
    old_root, new_root = tmp_path / "old", tmp_path / "새 저장 폴더"
    store = SessionStore.create(old_root, "manual", "consent")
    store.update(state="COMPLETE")
    (store.directory / "transcript.md").write_text("existing", encoding="utf-8")
    config = tmp_path / "settings.yaml"
    window = RecorderWindow(Settings(recordings_dir=str(old_root)), enable_detection=False, config_path=config)
    dialog = window.settings_dialog
    dialog.recordings_path.setText(str(new_root))
    dialog.record_pc.setChecked(False)
    dialog.create_mix.setChecked(False)
    dialog.save()
    saved = load_settings(config)
    assert saved.recordings_dir == str(new_root.resolve())
    assert saved.previous_recordings_dirs == [str(old_root.resolve())]
    assert window.settings.recordings_dir == str(old_root)  # held instance lock remains valid
    assert not window.settings.record_pc and window.settings.record_mic
    cleanup(window)
    reopened = RecorderWindow(saved, enable_detection=False, config_path=config)
    assert reopened.history.topLevelItemCount() == 1
    assert reopened.selected_history_path() == store.directory
    assert reopened.history_retry_button.isEnabled()
    cleanup(reopened)


def test_invalid_location_does_not_replace_settings(qt_app, tmp_path, monkeypatch):
    import app.ui.preferences as preferences
    monkeypatch.setattr(preferences, "startup_enabled", lambda: False)
    config = tmp_path / "settings.yaml"
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path / "recordings")), enable_detection=False, config_path=config)
    window.settings_dialog.save()
    before = config.read_bytes()
    file_path = tmp_path / "not-a-folder"
    file_path.write_bytes(b"keep")
    window.settings_dialog.recordings_path.setText(str(file_path))
    window.settings_dialog.save()
    assert config.read_bytes() == before and file_path.read_bytes() == b"keep"
    cleanup(window)


def test_old_location_lock_blocks_new_app_before_recovery(tmp_path):
    from app.storage.instance_lock import InstanceLock, RecordingRootsLock, InstanceAlreadyRunning
    old, new = tmp_path / "old", tmp_path / "new"
    with InstanceLock(old):
        with pytest.raises(InstanceAlreadyRunning):
            with RecordingRootsLock(new, [str(old)]):
                pytest.fail("Old recorder still owns recordings")
        with InstanceLock(new):
            pass  # Partial acquisition was released on failure.
