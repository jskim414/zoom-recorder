import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

from app.processing import model_download
from app.settings import Settings, load_settings
from app.ui import preferences
from app.ui.tray import RecorderWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    qt_app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(model_download, "data_root", lambda: tmp_path)
    monkeypatch.setattr(preferences, "local_engine_available", lambda: True)
    monkeypatch.setattr(preferences, "missing_gpu_libraries", lambda: ["cublas64_12"])
    settings = Settings(recordings_dir=str(tmp_path / "recordings"), model=str(tmp_path / "models/small"))
    result = RecorderWindow(settings, enable_detection=False, config_path=tmp_path / "settings.yaml")
    yield result
    result.settings_dialog.shutdown()
    result.timer.stop()
    result.tray.hide()
    result.controller.close()
    result.allow_close = True
    result.close()
    qt_app.processEvents()


def install_fixture(path):
    path.mkdir(parents=True, exist_ok=True)
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (path / name).write_bytes(b"test fixture, not a real model")


def test_device_selects_recommended_model_without_download_or_save(window, tmp_path, monkeypatch):
    dialog = window.settings_dialog
    monkeypatch.setattr(dialog.download_job, "start", lambda *args: pytest.fail("Unexpected download"))
    assert dialog.model_choice.currentData() == "small"
    dialog.device.setCurrentIndex(dialog.device.findData("cuda"))
    assert dialog.model_choice.currentData() == "large-v3"
    assert Path(dialog.model.text()) == tmp_path / "models/large-v3"
    assert "CUDA" in dialog.device_note.text()
    assert dialog.download_button.isEnabled() and "미설치" in dialog.model_status.text()
    assert not window.config_path.exists() and not (tmp_path / "models").exists()
    dialog.model_choice.setCurrentIndex(dialog.model_choice.findData("medium"))
    assert Path(dialog.model.text()) == tmp_path / "models/medium"
    dialog.device.setCurrentIndex(dialog.device.findData("cpu"))
    assert dialog.model_choice.currentData() == "small"
    install_fixture(tmp_path / "models/small")
    dialog.update_model_status()
    assert not dialog.download_button.isEnabled() and "설치됨" in dialog.model_status.text()


def test_gpu_model_save_and_reopen_preserve_explicit_choice(window, tmp_path):
    dialog = window.settings_dialog
    dialog.device.setCurrentIndex(dialog.device.findData("cuda"))
    dialog.model_choice.setCurrentIndex(dialog.model_choice.findData("medium"))
    dialog.save()
    saved = load_settings(window.config_path)
    assert saved.device == "cuda" and Path(saved.model) == tmp_path / "models/medium"
    assert window.controller.settings.model == saved.model
    assert "다운로드해야" in dialog.message.text()
    reopened = preferences.PreferencesDialog(window)
    assert reopened.model_choice.currentData() == "medium" and reopened.device.currentData() == "cuda"
    reopened.shutdown()
    reopened.close()


def test_custom_path_and_api_mode(window, tmp_path, monkeypatch):
    dialog = window.settings_dialog
    custom = tmp_path / "my-model"
    monkeypatch.setattr(preferences.QFileDialog, "getExistingDirectory", lambda *args: str(custom))
    dialog.choose_model()
    assert dialog.model_choice.currentData() == "custom" and dialog.model.text() == str(custom)
    assert not dialog.download_button.isEnabled()
    dialog.save()
    assert load_settings(window.config_path).model == str(custom)
    dialog.backend.setCurrentIndex(dialog.backend.findData("openai"))
    assert not dialog.device.isEnabled() and not dialog.model_choice.isEnabled()
    assert not dialog.download_button.isEnabled()


def test_lite_explains_missing_engine(window):
    dialog = window.settings_dialog
    dialog.engine_available = False
    dialog.update_model_status()
    assert not dialog.download_button.isEnabled() and "Local" in dialog.model_status.text()


@pytest.mark.parametrize("successful", [True, False])
def test_explicit_download_success_or_failure_without_changing_settings(window, tmp_path, successful):
    dialog = window.settings_dialog
    class Job:
        active = False
        name = None
        def start(self, name):
            self.active, self.name = True, name
        def poll(self):
            self.active = False
            return 0 if successful else 1
        def cancel(self):
            self.active = False
    dialog.download_job = Job()
    dialog.device.setCurrentIndex(dialog.device.findData("cuda"))
    dialog.download_button.click()
    assert dialog.download_job.active and dialog.download_job.name == "large-v3"
    assert dialog.download_timer.isActive() and not dialog.download_button.isEnabled()
    if successful:
        install_fixture(tmp_path / "models/large-v3")
    dialog.poll_download()
    assert not dialog.download_timer.isActive() and not window.config_path.exists()
    assert ("완료" if successful else "실패") in dialog.message.text()


def test_download_child_is_explicit_hidden_and_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(model_download, "data_root", lambda: tmp_path)
    monkeypatch.setattr(model_download, "logs_root", lambda: tmp_path)
    calls = []
    class Process:
        code = None
        def poll(self):
            return self.code
        def terminate(self):
            self.code = 1
        def wait(self, timeout):
            return self.code
    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return Process()
    monkeypatch.setattr(model_download.subprocess, "Popen", launch)
    job = model_download.ModelDownload()
    assert not job.active and not calls
    with pytest.raises(ValueError):
        job.start("../../outside")
    assert not calls
    job.start("large-v3")
    assert calls[0][0][-2:] == ["--download-model", "large-v3"]
    assert calls[0][1]["creationflags"] == model_download.subprocess.CREATE_NO_WINDOW
    assert job.poll() is None
    with pytest.raises(RuntimeError):
        job.start("small")
    job.cancel()
    assert not job.active and job.log is None


def test_packaged_download_uses_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(model_download, "data_root", lambda: tmp_path)
    monkeypatch.setattr(model_download, "logs_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    commands = []
    monkeypatch.setattr(model_download.subprocess, "Popen",
                        lambda command, **kwargs: commands.append(command) or SimpleNamespace(poll=lambda: 0))
    job = model_download.ModelDownload()
    job.start("small")
    assert commands == [[sys.executable, "--download-model", "small"]]
    assert job.poll() == 0 and not job.active


def test_incomplete_model_is_rejected_before_loading_engine(tmp_path, monkeypatch):
    from app.processing.transcribe import Transcriber
    (tmp_path / "model.bin").write_bytes(b"incomplete fixture")
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(FileNotFoundError):
        Transcriber(Settings(model=str(tmp_path)))._model("cpu")


def test_reselecting_same_gpu_applies_recommendation(window):
    dialog = window.settings_dialog
    gpu = dialog.device.findData("cuda")
    dialog.device.setCurrentIndex(gpu)
    dialog.model_choice.setCurrentIndex(dialog.model_choice.findData("small"))
    dialog.device.activated.emit(gpu)
    assert dialog.model_choice.currentData() == "large-v3"
