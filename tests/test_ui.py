import os
from pathlib import Path
import json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.controller import Controller
from app.detection.session_manager import Candidate, State
from app.settings import Settings
from app.ui.tray import RecorderWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def cleanup(window):
    window.timer.stop()
    window.tray.hide()
    window.controller.close()
    window.allow_close = True
    window.close()


def test_ui_no_recording_at_launch_and_checkbox_resets(qt_app, tmp_path):
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    assert window.controller.manager.state == State.IDLE
    assert window.controller.recorder is None
    assert not window.stop_button.isEnabled()
    window.manual()
    assert not window.start_button.isEnabled()
    window.consent.setChecked(True)
    assert window.start_button.isEnabled()
    window.skip()
    window.manual()
    assert not window.consent.isChecked() and not window.start_button.isEnabled()
    assert not list(tmp_path.glob("*/audio"))
    cleanup(window)


def test_ui_multiple_candidates_require_selection(qt_app, tmp_path):
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    window.controller.manager.offer([Candidate("a", "desktop", "a"), Candidate("b", "browser", "b")])
    window.prepare_prompt()
    window.consent.setChecked(True)
    assert not window.start_button.isEnabled()
    window.candidate_combo.setCurrentIndex(2)
    assert window.start_button.isEnabled()
    assert window.candidate_combo.currentData() == "b"
    window.skip()
    cleanup(window)


def test_extension_permissions_are_limited_to_zoom_and_localhost():
    manifest = json.loads(Path("browser-extension/manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"storage"}
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert set(manifest["content_scripts"][0]["matches"]) == {"https://zoom.us/*", "https://*.zoom.us/*"}
    assert "externally_connectable" not in manifest


def test_simple_ui_requires_consent_then_shows_stop_with_fake_capture(qt_app, tmp_path, monkeypatch):
    from test_recorder_controller import FakeBackend, backend_factory, spin_until
    from app.audio.recorder import Recorder
    settings = Settings(recordings_dir=str(tmp_path), auto_transcribe=False)
    backend = FakeBackend()
    controller = Controller(settings, lambda store, options: Recorder(store, options, backend_factory(backend)))
    window = RecorderWindow(settings, enable_detection=False, controller=controller)
    from PySide6.QtWidgets import QInputDialog
    prompts = []
    def confirm(*args, **kwargs):
        assert controller.recorder is None and controller.save_pending
        prompts.append(kwargs["text"])
        return "20261026_01_테스트 회의", True
    monkeypatch.setattr(QInputDialog, "getText", confirm)
    window.show()
    qt_app.processEvents()
    assert window.manual_button.isVisible() and window.folder_button.isVisible()
    assert not window.stop_button.isVisible() and not window.token_field.isVisible()
    window.manual_button.click()
    assert not backend.streams and not window.manual_button.isVisible()
    assert not window.start_button.isEnabled()
    window.consent.setChecked(True)
    assert not backend.streams  # Checking alone never starts capture.
    window.start_button.click()
    assert controller.recorder.ready.wait(2)
    window.render()
    assert window.stop_button.isVisible() and not window.start_button.isVisible()
    window.stop_button.click()
    spin_until(lambda: (window.tick() or controller.manager.state == State.COMPLETE))
    assert len(prompts) == 1 and controller.store.directory.name == "20261026_01_테스트 회의"
    window.render()
    assert not window.stop_button.isVisible() and window.manual_button.isVisible()
    cleanup(window)


def test_preferences_save_applies_mode_and_rejects_bad_extension_id(qt_app, tmp_path, monkeypatch):
    import app.ui.preferences as preferences
    from app.settings import load_settings
    monkeypatch.setattr(preferences, "startup_enabled", lambda: False)
    startup_changes = []
    monkeypatch.setattr(preferences, "set_startup", startup_changes.append)
    path = tmp_path / "settings.yaml"
    settings = Settings(recordings_dir=str(tmp_path / "recordings"), auto_transcribe=True)
    window = RecorderWindow(settings, enable_detection=False, config_path=path)
    dialog = window.settings_dialog
    assert not dialog.startup.isChecked() and not startup_changes
    dialog.auto_detect.setChecked(False)
    dialog.recording_mode.setCurrentIndex(0)
    dialog.save()
    persisted = load_settings(path)
    assert not persisted.auto_detect and not persisted.auto_transcribe
    assert not window.settings.auto_transcribe  # Recording mode applies immediately between sessions.
    assert window.settings.auto_detect  # Detector transport applies on restart.
    assert not startup_changes
    before = path.read_bytes()
    dialog.extension_ids.setText("invalid ID")
    dialog.startup.setChecked(True)
    dialog.save()
    assert path.read_bytes() == before and not startup_changes
    dialog.extension_ids.setText("a" * 32)
    dialog.save()
    assert startup_changes == [True] and load_settings(path).extension_ids == ["a" * 32]
    cleanup(window)


def test_relaunch_only_reveals_existing_window(qt_app, tmp_path):
    from app.ui.single_instance import listen_for_launch, reveal_existing
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    server = listen_for_launch(tmp_path, window)
    try:
        assert reveal_existing(tmp_path)
        qt_app.processEvents()
        assert window.isVisible()
        assert window.controller.recorder is None and window.controller.manager.state == State.IDLE
    finally:
        server.close()
        cleanup(window)


def test_api_mode_requires_separate_unchecked_consent(qt_app, tmp_path):
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path), auto_transcribe=True,
                                     transcription_backend="openai"), enable_detection=False)
    window.manual()
    window.consent.setChecked(True)
    assert not window.api_consent.isChecked() and not window.start_button.isEnabled()
    assert not window.settings_dialog.save_button.isEnabled()
    window.api_consent.setChecked(True)
    assert window.start_button.isEnabled() and window.controller.recorder is None
    window.skip()
    window.manual()
    assert not window.api_consent.isChecked() and not window.start_button.isEnabled()
    window.skip()
    cleanup(window)


def test_idle_timer_and_no_unused_bridge(qt_app, tmp_path):
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    assert window.timer.interval() == 5000
    assert window.bridge.server is None
    first = window._render_signature
    window.tick()
    assert window._render_signature == first
    cleanup(window)


def test_start_name_validation_and_cancel_do_not_capture(qt_app, tmp_path):
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    window.manual()
    assert window.folder_name.text().endswith("_01_")
    window.folder_name.setText("../outside")
    window.consent.setChecked(True)
    window.start_button.click()
    assert window.controller.recorder is None and window.controller.manager.state == State.PROMPTED
    assert window.banner.text() and not window.consent.isChecked()
    window.skip_button.click()
    assert not list(tmp_path.glob("*/audio"))
    cleanup(window)


def test_save_dialog_retries_then_cancel_preserves_audio(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from test_recorder_controller import FakeBackend, backend_factory, spin_until
    from app.audio.recorder import Recorder
    settings = Settings(recordings_dir=str(tmp_path), auto_transcribe=False)
    controller = Controller(settings, lambda store, options: Recorder(store, options, backend_factory(FakeBackend())))
    window = RecorderWindow(settings, enable_detection=False, controller=controller)
    window.manual()
    window.consent.setChecked(True)
    window.start_button.click()
    assert controller.recorder.ready.wait(2)
    original = controller.store.directory
    calls = []
    def answer(*args, **kwargs):
        calls.append(kwargs["text"])
        assert controller.recorder is None and controller.save_pending
        window.tick()  # The nested event loop must not open a second save dialog.
        if len(calls) == 1:
            return "../outside", True
        return "ignored", False
    monkeypatch.setattr(QInputDialog, "getText", answer)
    window.stop_button.click()
    spin_until(lambda: (window.tick() or controller.manager.state == State.COMPLETE))
    assert len(calls) == 2 and controller.store.directory == original
    assert len(list((original / "audio").glob("*.wav"))) == 2
    cleanup(window)
