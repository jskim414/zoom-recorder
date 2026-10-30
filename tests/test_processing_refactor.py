"""Regression checks at the shared policy and irreversible cleanup boundaries."""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.processing.archive import create_archive, cleanup_wav
from app.processing.audio_cleanup import delete_transcribed_audio
from app.processing.pipeline import run_pipeline
from app.processing.transcribe import Transcriber
from app.settings import Settings
from app.storage.audio_files import digest
from app.storage.policy import StoragePolicy
from app.storage.session_store import SessionStore
from test_mp3_archive import synthetic_store
from test_storage_policy import model, policy_store


def test_recording_policy_snapshot_preserves_metadata_contract():
    settings = Settings(auto_transcribe=True, convert_to_mp3=False, delete_audio_after_transcription=True)
    policy = StoragePolicy.from_settings(settings)
    expected = {"temporary_wav": True, "create_mix": settings.create_mix, "convert_to_mp3": False,
                "delete_audio_after_transcription": True, "transcribe_required": True}
    settings.convert_to_mp3 = True
    settings.delete_audio_after_transcription = False
    assert policy.to_metadata() == expected
    assert StoragePolicy.from_metadata({"storage_policy": expected}) == policy
    assert not StoragePolicy.from_metadata({}).temporary_wav
    assert not StoragePolicy.from_metadata({}).delete_audio_after_transcription
    with pytest.raises(FrozenInstanceError):
        policy.convert_to_mp3 = True


@pytest.mark.parametrize("value", [None, False, 1, "true", "false"])
def test_untrusted_metadata_never_enables_audio_deletion(tmp_path, value):
    store = synthetic_store(tmp_path)
    store.update(storage_policy={"delete_audio_after_transcription": value})
    before = {p: p.read_bytes() for p in (store.directory / "audio").glob("*.wav")}
    delete_transcribed_audio(store)
    assert "audio_cleanup" not in store.metadata
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("cleanup_kind", ["wav", "all_audio"])
def test_both_cleanup_paths_reject_redirected_audio_directory(tmp_path, model, monkeypatch, cleanup_kind):
    store = policy_store(tmp_path, True, cleanup_kind == "all_audio", auto=False)
    if cleanup_kind == "wav":
        create_archive(store)
    else:
        Transcriber(Settings()).run(store)
    before = {p: p.read_bytes() for p in (store.directory / "audio").glob("*.wav")}
    # Simulate Windows junction detection without creating or deleting filesystem links.
    monkeypatch.setattr(Path, "is_junction", lambda path: path == store.directory / "audio", raising=False)
    with pytest.raises(ValueError, match="redirected"):
        (cleanup_wav if cleanup_kind == "wav" else delete_transcribed_audio)(store)
    assert all(p.read_bytes() == data for p, data in before.items())


def test_resumed_cleanup_validates_last_entry_before_deleting_first(tmp_path, model):
    store = policy_store(tmp_path, False, True)
    Transcriber(Settings()).run(store)
    files = [{"file": item["file"], "sha256": digest(store.directory / item["file"])}
             for item in store.metadata["segments"]]
    files[-1]["sha256"] = "changed-after-interruption"
    store.update(segments=[], audio_cleanup={"status": "deleting", "files": files,
                 "transcript_sha256": store.metadata["transcription"]["sha256"]})
    metadata_before = (store.directory / "metadata.json").read_bytes()
    audio_before = {p: p.read_bytes() for p in (store.directory / "audio").glob("*.wav")}
    with pytest.raises(ValueError, match="changed"):
        run_pipeline(store, Settings(), transcribe=False)
    assert (store.directory / "metadata.json").read_bytes() == metadata_before
    assert all(p.read_bytes() == data for p, data in audio_before.items())


def test_completed_cleanup_retry_is_idempotent(tmp_path, model, monkeypatch):
    store = policy_store(tmp_path, True, True)
    run_pipeline(store, Settings())
    transcript_before = (store.directory / "transcript.md").read_bytes()
    cleanup_before = store.metadata["audio_cleanup"]
    monkeypatch.setattr(Transcriber, "run", lambda *a, **kw: pytest.fail("Must not retranscribe"))
    import app.processing.archive as archive
    monkeypatch.setattr(archive, "create_archive", lambda *a: pytest.fail("Must not encode"))
    restarted = SessionStore(store.directory)
    run_pipeline(restarted, Settings(), transcribe=False)
    assert restarted.metadata["audio_cleanup"] == cleanup_before
    assert (store.directory / "transcript.md").read_bytes() == transcript_before
    assert not list(store.directory.rglob("*.wav")) and not list(store.directory.glob("*.mp3"))
