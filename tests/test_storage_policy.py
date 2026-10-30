"""Storage combinations and irreversible cleanup use isolated synthetic files."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.processing.audio_cleanup import delete_transcribed_audio
from app.processing.transcribe import Transcriber
from app.processing.worker import run_pipeline
from app.settings import Settings, load_settings
from app.storage.session_store import SessionStore, recover_session
from app.storage.history import recent_sessions
from app.ui.tray import RecorderWindow
from test_mp3_archive import synthetic_store
from test_ui import qt_app, cleanup


@pytest.fixture
def model(monkeypatch):
    class Model:
        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(start=0, end=0.2, text="synthetic speech")]), None
    monkeypatch.setattr(Transcriber, "_model", lambda *args: Model())


def policy_store(root, convert, delete, auto=True):
    store = synthetic_store(root, transcribe=auto)
    store.update(storage_policy={**store.metadata["storage_policy"], "convert_to_mp3": convert,
                                 "delete_audio_after_transcription": delete})
    return store


@pytest.mark.parametrize("convert", [False, True])
@pytest.mark.parametrize("delete", [False, True])
def test_four_storage_combinations(tmp_path, model, convert, delete):
    store = policy_store(tmp_path, convert, delete)
    run_pipeline(store, Settings())
    assert (store.directory / "transcript.md").is_file()
    if delete:
        assert not list(store.directory.rglob("*.wav")) and not list(store.directory.rglob("*.mp3"))
        assert store.metadata["audio_cleanup"]["status"] == "complete"
        assert store.metadata["segments"] == []
        text = (store.directory / "transcript.md").read_text(encoding="utf-8")
        assert "원본: [" not in text and "synthetic speech" in text
        from scripts.inspect_session import inspect_session
        report = inspect_session(store.directory)
        assert report["file_checks_passed"] and report["audio_deleted"]
    else:
        assert len(list(store.directory.rglob("*.wav"))) == (0 if convert else 2)
        assert len(list(store.directory.rglob("*.mp3"))) == (3 if convert else 0)


@pytest.mark.parametrize("convert", [False, True])
def test_record_only_waits_and_later_transcription_uses_frozen_policy(tmp_path, model, convert):
    store = policy_store(tmp_path, convert, True, auto=False)
    # Changing global preferences does not change a recording's original policy.
    settings = Settings(convert_to_mp3=not convert, delete_audio_after_transcription=False)
    run_pipeline(store, settings, transcribe=False)
    assert store.metadata["audio_cleanup"]["status"] == "waiting"
    assert len(list(store.directory.rglob("*.mp3"))) == (3 if convert else 0)
    assert len(list(store.directory.rglob("*.wav"))) == (0 if convert else 2)
    run_pipeline(store, settings)
    assert store.metadata["audio_cleanup"]["status"] == "complete"
    assert not list(store.directory.rglob("*.mp3")) and not list(store.directory.rglob("*.wav"))


def test_immediate_delete_never_constructs_mp3_encoder(tmp_path, model, monkeypatch):
    import app.processing.archive as archive
    monkeypatch.setattr(archive, "create_archive", lambda *_: pytest.fail("Unnecessary MP3 export"))
    run_pipeline(policy_store(tmp_path, True, True), Settings())
    run_pipeline(policy_store(tmp_path, False, False), Settings())


@pytest.mark.parametrize("failure", ["transcription", "empty", "error", "gap", "unindexed"])
def test_failed_empty_or_incomplete_recording_preserves_audio(tmp_path, model, monkeypatch, failure):
    store = policy_store(tmp_path, True, True)
    before = {p: p.read_bytes() for p in (store.directory / "audio").glob("*.wav")}
    if failure == "transcription":
        def fail(*args):
            raise RuntimeError("synthetic inference failure")
        monkeypatch.setattr(Transcriber, "_run", fail)
    elif failure == "empty":
        monkeypatch.setattr(Transcriber, "_run", lambda *args: [])
    elif failure == "error":
        store.error("synthetic_capture_failure")
    elif failure == "gap":
        store.append("gaps", {"kind": "synthetic_gap"})
    else:
        (store.directory / "audio/mic_9999.wav.part").write_bytes(b"partial")
    if failure == "transcription":
        with pytest.raises(RuntimeError):
            run_pipeline(store, Settings())
    else:
        run_pipeline(store, Settings())
        assert store.metadata["audio_cleanup"]["status"] == "retained"
    assert all(p.read_bytes() == content for p, content in before.items())


def test_changed_transcript_or_audio_prevents_deletion(tmp_path, model):
    store = policy_store(tmp_path, False, True)
    Transcriber(Settings()).run(store)
    path = store.directory / "transcript.md"
    path.write_text("edited transcript", encoding="utf-8")
    delete_transcribed_audio(store)
    assert store.metadata["audio_cleanup"]["reason"] == "transcript_not_verified"
    assert len(list(store.directory.rglob("*.wav"))) == 2
    Transcriber(Settings()).run(store)
    wav = next((store.directory / "audio").glob("*.wav"))
    with wav.open("r+b") as stream:
        stream.seek(50)
        stream.write(b"changed")
    with pytest.raises(ValueError):
        delete_transcribed_audio(store)
    assert len(list(store.directory.rglob("*.wav"))) == 2


def test_partial_deletion_resumes_without_retranscription(tmp_path, model, monkeypatch):
    store = policy_store(tmp_path, False, True)
    original = Path.unlink
    calls = []
    def fail_second(path, **kwargs):
        if path.suffix == ".wav":
            calls.append(path)
            if len(calls) == 2:
                raise OSError("synthetic disconnect")
        return original(path, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(OSError):
        run_pipeline(store, Settings())
    restarted = SessionStore(store.directory)
    assert restarted.metadata["audio_cleanup"]["status"] == "deleting"
    assert restarted.metadata["transcription"]["status"] == "complete"
    assert restarted.metadata["segments"] == []
    monkeypatch.setattr(Path, "unlink", original)
    monkeypatch.setattr(Transcriber, "run", lambda *a, **kw: pytest.fail("Do not retranscribe missing audio"))
    recover_session(store.directory)
    run_pipeline(SessionStore(store.directory), Settings(), transcribe=False)
    assert SessionStore(store.directory).metadata["audio_cleanup"]["status"] == "complete"
    assert not list(store.directory.rglob("*.wav"))


def test_unrelated_audio_and_legacy_sessions_are_never_deleted(tmp_path, model):
    store = policy_store(tmp_path, False, True)
    foreign = store.directory / "audio/remote_9999.wav"
    foreign.write_bytes(b"user owned audio")
    run_pipeline(store, Settings())
    assert foreign.read_bytes() == b"user owned audio"
    legacy = synthetic_store(tmp_path)
    legacy.update(storage_policy={})
    run_pipeline(legacy, Settings(delete_audio_after_transcription=True))
    assert len(list(legacy.directory.rglob("*.wav"))) == 2


def test_deleted_audio_cannot_be_retranscribed_and_ui_explains(qt_app, tmp_path, model):
    store = policy_store(tmp_path, True, True)
    run_pipeline(store, Settings())
    before = (store.directory / "transcript.md").read_bytes()
    with pytest.raises(ValueError):
        Transcriber(Settings()).run(store)
    window = RecorderWindow(Settings(recordings_dir=str(tmp_path)), enable_detection=False)
    assert window.transcript_button.isEnabled()
    assert not window.history_retry_button.isEnabled() and not window.archive_retry_button.isEnabled()
    assert "음성 삭제됨" in recent_sessions(tmp_path)[0][2]
    with pytest.raises(ValueError):
        window.controller.retry_transcription(store)
    assert (store.directory / "transcript.md").read_bytes() == before
    cleanup(window)


def test_storage_controls_persist_and_legacy_defaults(qt_app, tmp_path, monkeypatch):
    import app.ui.preferences as preferences
    monkeypatch.setattr(preferences, "startup_enabled", lambda: False)
    config = tmp_path / "settings.yaml"
    config.write_text("auto_transcribe: true\n", encoding="utf-8")
    old = load_settings(config)
    assert old.convert_to_mp3 and not old.delete_audio_after_transcription
    settings = Settings(recordings_dir=str(tmp_path / "recordings"), auto_transcribe=True)
    window = RecorderWindow(settings, enable_detection=False, config_path=config)
    dialog = window.settings_dialog
    dialog.convert_to_mp3.setChecked(False)
    dialog.delete_audio_after_transcription.setChecked(True)
    assert not dialog.create_mix.isEnabled()
    assert "MP3 변환" in dialog.storage_note.text() and "생략" in dialog.storage_note.text()
    dialog.save()
    saved = load_settings(config)
    assert not saved.convert_to_mp3 and saved.delete_audio_after_transcription
    assert not window.settings.convert_to_mp3 and window.settings.delete_audio_after_transcription
    window.manual()
    assert "전사 후 음성 삭제" in window.storage_policy_label.text()
    assert not window.start_button.isEnabled()
    cleanup(window)


@pytest.mark.parametrize("field", ["convert_to_mp3", "delete_audio_after_transcription"])
def test_policy_requires_real_booleans(field):
    with pytest.raises(ValueError):
        Settings(**{field: 1}).validate()


def test_api_consent_precedes_all_processing_and_success_can_delete(tmp_path, monkeypatch):
    import app.processing.api_transcribe as api
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-only")
    monkeypatch.setattr(api, "upload_chunk", lambda *args: calls.append(True) or "synthetic text")
    store = policy_store(tmp_path, True, True)
    settings = Settings(transcription_backend="openai")
    with pytest.raises(PermissionError):
        run_pipeline(store, settings)
    assert not calls and len(list(store.directory.rglob("*.wav"))) == 2
    run_pipeline(store, settings, api_approved=True)
    assert len(calls) == 2 and store.metadata["audio_cleanup"]["status"] == "complete"


def test_controller_keeps_transcription_proof_after_delete_failure(tmp_path, model, monkeypatch):
    from app.controller import Controller
    from test_recorder_controller import spin_until
    original = Path.unlink
    calls = []
    def fail_second(path, **kwargs):
        if path.suffix == ".wav":
            calls.append(path)
            if len(calls) == 2:
                raise OSError("synthetic disconnect")
        return original(path, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    class Worker:
        def __init__(self, settings): self.settings = settings
        def run(self, store): run_pipeline(store, self.settings)
    store = policy_store(tmp_path, False, True)
    controller = Controller(Settings(), transcriber_factory=Worker)
    controller._enqueue(store, controller.settings, False)
    spin_until(lambda: (controller.poll() or not controller.busy))
    assert store.metadata["state"] == "ERROR"
    assert store.metadata["transcription"]["status"] == "complete"
    assert store.metadata["audio_cleanup"]["status"] == "deleting"
    controller.close()


def test_corrupt_deletion_manifest_cannot_escape_session(tmp_path, model):
    store = policy_store(tmp_path, False, True)
    Transcriber(Settings()).run(store)
    store.update(audio_cleanup={"status": "deleting", "transcript_sha256": store.metadata["transcription"]["sha256"],
                                "files": [{"file": "../outside.wav", "sha256": "invalid"}]})
    with pytest.raises(ValueError):
        delete_transcribed_audio(store)
    assert len(list(store.directory.rglob("*.wav"))) == 2
