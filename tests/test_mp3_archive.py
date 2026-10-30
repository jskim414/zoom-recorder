"""Real local encoders/decoders with synthetic tones; never access user audio."""
import io
from pathlib import Path
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from app.audio.segment_writer import SegmentWriter
from app.processing.archive import create_archive, cleanup_wav, validate_archive, source_items
from app.processing.transcribe import Transcriber
from app.processing.worker import run_pipeline, ProcessArchiver
from app.settings import Settings
from app.storage.session_store import SessionStore, recover_session


def synthetic_store(root, tracks=("remote", "mic"), transcribe=False, mix=True):
    store = SessionStore.create(root, "manual", "synthetic-consent")
    for index, track in enumerate(tracks):
        rate = 44100 if track == "mic" else 48000
        # Stereo PC tone / mono mic, different rates, split blocks, delayed microphone.
        channels = 1 if track == "mic" else 2
        samples = (np.sin(np.arange(rate // 2) * (440 + index * 440) * 2 * np.pi / rate) * 16000).astype("<i2")
        if channels == 2:
            samples = np.repeat(samples[:, None], 2, axis=1)
        writer = SegmentWriter(store, track, rate, channels, 60)
        writer.write(samples.tobytes(), 0.25 if track == "mic" else 0)
        writer.close()
    store.update(state="FINALIZING", selected_tracks=list(tracks), ended_at=store.metadata["started_at"],
                 storage_policy={"temporary_wav": True, "create_mix": mix, "transcribe_required": transcribe})
    return store


def decoded(path):
    import av
    with av.open(str(path)) as source:
        return np.concatenate([f.to_ndarray() for f in source.decode(audio=0)], axis=1)


@pytest.mark.parametrize("tracks,mix,count", [(("remote",), True, 1), (("mic",), True, 1),
    (("remote", "mic"), True, 3), (("remote", "mic"), False, 2)])
def test_real_mp3_conversion_and_scoped_cleanup(tmp_path, tracks, mix, count):
    store = synthetic_store(tmp_path, tracks, mix=mix)
    unrelated = store.directory / "audio/user-notes.wav"
    unrelated.write_bytes(b"user-owned")
    archive = create_archive(store)
    assert len(archive["files"]) == count
    assert all((store.directory / i["file"]).exists() for i in archive["original_segments"])
    cleanup_wav(store)
    assert store.metadata["archive"]["cleanup"] == "complete"
    assert unrelated.read_bytes() == b"user-owned"
    assert all(not (store.directory / i["file"]).exists() for i in archive["original_segments"])
    assert {i["track"] for i in source_items(store)} == set(tracks)
    validate_archive(SessionStore(store.directory))


def test_timing_mixed_signal_and_length(tmp_path):
    store = synthetic_store(tmp_path)
    files = {i["track"]: store.directory / i["file"] for i in create_archive(store)["files"]}
    pc, mic, mixed = (decoded(files[t]) for t in ("remote", "mic", "mixed"))
    assert abs(pc.shape[1] - 36000) < 2304
    assert pc.shape == mic.shape == mixed.shape
    assert np.max(np.abs(mic[:, :9600])) < 0.001  # delayed mic remains silent before 0.2 s
    assert np.sqrt(np.mean(mic[:, 16000:23000] ** 2)) > 0.1
    assert np.sqrt(np.mean(pc[:, :9000] ** 2)) > 0.1
    assert np.max(np.abs(mixed)) < 1
    # MP3 is lossy; correlation validates the actual mix content, not just headers.
    assert np.corrcoef(mixed.ravel(), ((pc + mic) / 2).ravel())[0, 1] > 0.98


def test_transcription_failure_keeps_wav_and_retry_uses_individual_mp3(tmp_path):
    store = synthetic_store(tmp_path, transcribe=True)
    with pytest.raises(FileNotFoundError):
        run_pipeline(store, Settings(model=str(tmp_path / "absent")))
    assert len(list((store.directory / "audio").glob("*.wav"))) == 2
    cleanup_wav(store)
    assert store.metadata["archive"]["retention_reason"] == "transcription_incomplete"
    class Model:
        def __init__(self, *args, **kwargs): pass
        def transcribe(self, audio, **kwargs):
            return iter([SimpleNamespace(start=0, end=0.2, text="출처 테스트")]), None
    Transcriber(Settings(), Model).run(store)
    cleanup_wav(store)
    assert not list((store.directory / "audio").glob("*.wav"))
    text = Transcriber(Settings(), Model).run(SessionStore(store.directory)).read_text(encoding="utf-8")
    assert text.count("출처 테스트") == 2 and "PC 음성" in text and "마이크**" in text
    assert ".wav" not in text and "합본.mp3" not in text


@pytest.mark.parametrize("damage", ["mp3", "wav", "gap", "error", "part"])
def test_never_delete_when_evidence_is_incomplete(tmp_path, damage):
    store = synthetic_store(tmp_path)
    archive = create_archive(store)
    if damage == "mp3":
        (store.directory / archive["files"][0]["file"]).write_bytes(b"truncated")
    elif damage == "wav":
        path = store.directory / archive["original_segments"][0]["file"]
        with path.open("r+b") as stream:
            stream.seek(50)
            stream.write(b"\x01\x02")
    elif damage == "gap":
        store.append("gaps", {"kind": "test"})
    elif damage == "error":
        store.error("synthetic_error")
    else:
        (store.directory / "audio/remote_9999.wav.part").write_bytes(b"unindexed")
    try:
        cleanup_wav(store)
    except (ValueError, OSError):
        pass
    assert len(list((store.directory / "audio").glob("*.wav"))) == 2


def test_corrupt_export_rebuilds_while_wav_exists(tmp_path):
    store = synthetic_store(tmp_path)
    archive = create_archive(store)
    (store.directory / archive["files"][0]["file"]).write_bytes(b"broken")
    create_archive(store)
    cleanup_wav(store)
    validate_archive(store)
    assert store.metadata["archive"]["cleanup"] == "complete"


def test_cleanup_interrupted_after_metadata_commit_can_resume(tmp_path, monkeypatch):
    store = synthetic_store(tmp_path)
    create_archive(store)
    original = Path.unlink
    calls = []
    def fail_second(path, **kwargs):
        if path.suffix == ".wav":
            calls.append(path)
            if len(calls) == 2:
                raise OSError("synthetic disk disconnect")
        return original(path, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(OSError):
        cleanup_wav(store)
    restarted = SessionStore(store.directory)
    assert restarted.metadata["archive"]["cleanup"] == "deleting"
    assert all(i["codec"] == "MP3" for i in restarted.metadata["segments"])
    monkeypatch.setattr(Path, "unlink", original)
    cleanup_wav(restarted)
    assert not list((store.directory / "audio").glob("*.wav"))
    assert recover_session(store.directory)["errors"] == []


def test_deletion_allowlist_and_legacy_preservation(tmp_path):
    legacy = synthetic_store(tmp_path)
    legacy.update(storage_policy={})
    assert create_archive(legacy) is None
    cleanup_wav(legacy)
    assert len(list((legacy.directory / "audio").glob("*.wav"))) == 2
    store = synthetic_store(tmp_path)
    create_archive(store)
    store.metadata["archive"]["original_segments"][0]["file"] = "../outside.wav"
    with pytest.raises(ValueError):
        cleanup_wav(store)
    assert len(list((store.directory / "audio").glob("*.wav"))) == 2


def test_real_child_record_only_and_api_retranscription_chunking(tmp_path, monkeypatch):
    store = synthetic_store(tmp_path)
    ProcessArchiver(Settings(transcription_backend="openai")).run(store)  # no API approval needed to archive locally
    assert store.metadata["archive"]["cleanup"] == "complete"
    from app.processing.api_transcribe import transcribe_api
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    calls = []
    def upload(audio, key):
        with wave.open(io.BytesIO(audio)) as wav:
            assert wav.getnchannels() == 1 and wav.getframerate() == 16000
        calls.append(audio)
        return "synthetic transcription"
    with pytest.raises(PermissionError):
        transcribe_api(store, upload=upload)
    result = transcribe_api(store, approved=True, upload=upload)
    assert len(calls) == 2 and {i["track"] for i in result} == {"remote", "mic"}


def test_no_overwrite_of_unrelated_export(tmp_path):
    store = synthetic_store(tmp_path)
    path = store.directory / "PC음성.mp3"
    path.write_bytes(b"user file")
    with pytest.raises(FileExistsError):
        create_archive(store)
    assert path.read_bytes() == b"user file"


def test_no_cleanup_without_committed_transcript(tmp_path):
    store = synthetic_store(tmp_path, transcribe=True)
    create_archive(store)
    store.update(transcription={"status": "complete"})
    cleanup_wav(store)
    assert store.metadata["archive"]["cleanup"] == "retained"
    assert len(list((store.directory / "audio").glob("*.wav"))) == 2


def test_segment_boundaries_and_long_mp3_retranscription_are_bounded(tmp_path):
    store = synthetic_store(tmp_path, tracks=("remote",))
    # Add a later segment; a long silent gap must keep the timestamp rather than concatenate.
    writer = SegmentWriter(store, "remote", 16000, 1, 60)
    writer.sequence = 1
    writer.write((np.ones(32000, dtype="<i2") * 10000).tobytes(), 121.0)
    writer.close()
    run_pipeline(store, Settings(), transcribe=False)
    calls = []
    class Model:
        def __init__(self, *a, **kw): pass
        def transcribe(self, audio, **kw):
            with wave.open(audio) as wav:
                duration = wav.getnframes() / wav.getframerate()
                assert duration <= 120
            calls.append(duration)
            return iter([SimpleNamespace(start=1, end=2, text="chunk")]), None
    path = Transcriber(Settings(), Model).run(store)
    assert len(calls) == 2 and calls[0] == 120 and abs(calls[1] - 3) < 0.05
    assert "[00:02:01–00:02:02]" in path.read_text(encoding="utf-8")
