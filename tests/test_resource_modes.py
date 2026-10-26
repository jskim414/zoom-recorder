import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.controller import Controller
from app.detection.session_manager import State
from app.processing.worker import ProcessTranscriber
from app.settings import Settings, load_settings
from test_transcription import audio_store


def test_new_install_record_only_and_legacy_selection_preserved(tmp_path):
    assert not Settings().auto_transcribe
    path = tmp_path / "settings.yaml"
    path.write_text("auto_transcribe: true\n", encoding="utf-8")
    assert load_settings(path).auto_transcribe


@pytest.mark.parametrize("values", [{"cpu_threads": 0}, {"cpu_threads": True}, {"cpu_threads": 17},
                                    {"beam_size": 2}, {"transcription_backend": "unknown"}])
def test_resource_settings_validation(values):
    with pytest.raises(ValueError):
        Settings(**values).validate()


def test_ui_import_does_not_load_ai_or_numpy():
    result = subprocess.run([sys.executable, "-c",
        "import app.ui.tray,sys,json; print(json.dumps([x for x in ('numpy','faster_whisper','ctranslate2','av','onnxruntime') if x in sys.modules]))"],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_no_zoom_does_not_query_audio_or_windows(monkeypatch):
    import psutil
    from app.detectors.desktop import DesktopDetector
    monkeypatch.setattr(psutil, "process_iter", lambda *args: [])
    # Imports themselves must be skipped, not merely empty COM results.
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", None)
    monkeypatch.setitem(sys.modules, "win32gui", None)
    detector = DesktopDetector(Settings())
    assert detector.poll() == [] and detector.error is None


def test_record_only_never_constructs_transcriber(tmp_path):
    from test_recorder_controller import FakeBackend, backend_factory, spin_until
    from app.audio.recorder import Recorder
    backend = FakeBackend()
    def forbidden(*args):
        pytest.fail("Record-only must not construct transcription worker")
    controller = Controller(Settings(recordings_dir=str(tmp_path)),
                            lambda store, options: Recorder(store, options, backend_factory(backend)), forbidden)
    controller.manager.manual()
    controller.start(next(iter(controller.manager.candidates)), True, True)
    assert controller.recorder.ready.wait(2)
    controller.stop()
    spin_until(lambda: (controller.poll() or controller.manager.state == State.COMPLETE))
    assert controller.future is None
    assert controller.store.metadata["transcription"]["status"] == "disabled"
    assert list((controller.store.directory / "audio").glob("*.wav"))
    controller.close()


def test_real_child_failure_reloads_metadata_preserves_wav(audio_store, tmp_path):
    before = {p: p.read_bytes() for p in (audio_store.directory / "audio").glob("*.wav")}
    with pytest.raises(FileNotFoundError):
        ProcessTranscriber(Settings(model=str(tmp_path / "missing"))).run(audio_store)
    assert audio_store.metadata["state"] == "ERROR"
    assert audio_store.metadata["transcription"]["code"] == "model_missing"
    assert all(p.read_bytes() == data for p, data in before.items())


def test_packaged_paths_independent_of_working_directory(tmp_path, monkeypatch):
    from app.paths import default_config
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = load_settings()
    assert default_config() == tmp_path / "ZoomRecorder/config/settings.yaml"
    assert Path(settings.recordings_dir) == tmp_path / "ZoomRecorder/recordings"
    assert Path(settings.model) == tmp_path / "ZoomRecorder/models/small"


def test_process_worker_rejects_api_without_spawning(audio_store, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Must not spawn"))
    with pytest.raises(PermissionError):
        ProcessTranscriber(Settings(transcription_backend="openai")).run(audio_store)


def test_source_package_excludes_private_user_data():
    from scripts.package_source import ROOT, source_files
    paths = [path.relative_to(ROOT).as_posix() for path in source_files()]
    assert paths and all((ROOT / path).is_file() for path in paths)
    for path in paths:
        assert not path.startswith(("recordings/", "models/", "validation-artifacts/", ".venv/"))
        assert path not in {"config/settings.yaml", ".env", "diagnostics.local.json", "Zoom Recorder.lnk"}
    assert "config/settings.example.yaml" in paths
