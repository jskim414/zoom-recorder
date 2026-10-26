import struct

import pytest

from app.audio.segment_writer import SegmentWriter
from app.audio.validation import inspect_wav
from app.processing.transcribe import Transcriber
from app.settings import Settings
from app.storage.session_store import SessionStore
from scripts.inspect_session import inspect_session


@pytest.fixture
def session(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "synthetic-test")
    for track in ("remote", "mic"):
        writer = SegmentWriter(store, track, 100, 1, 60)
        writer.write(struct.pack("<100h", *([1000, -1000] * 50)), 0)
        writer.close()
    store.update(state="COMPLETE")
    return store


def test_inspection_is_read_only_and_does_not_claim_hardware_acceptance(session):
    paths = list(session.directory.rglob("*"))
    before = {path: path.read_bytes() for path in paths if path.is_file()}
    report = inspect_session(session.directory)
    assert report["file_checks_passed"] and not report["hardware_acceptance_passed"]
    assert not report["listening_verified"] and not report["warnings"]
    assert all(row["rms"] > 0 for row in report["segments"])
    assert all(path.read_bytes() == data for path, data in before.items())


def test_truncated_payload_with_intact_header_is_rejected_before_inference(session):
    item = session.metadata["segments"][0]
    path = session.directory / item["file"]
    path.write_bytes(path.read_bytes()[:-20])
    with pytest.raises(ValueError, match="Truncated"):
        inspect_wav(path, item)
    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            pytest.fail("Corrupt audio reached the recognizer")
    with pytest.raises(ValueError, match="Truncated"):
        Transcriber(Settings(), Model).run(session)
    assert session.metadata["transcription"]["status"] == "failed"
    assert path.exists() and not (session.directory / "transcript.md").exists()


@pytest.mark.parametrize("field,value", [("rate", 200), ("channels", 2), ("sample_width", 1),
                                        ("duration", 3), ("offset", float("nan"))])
def test_mismatched_audio_metadata_is_rejected(session, field, value):
    item = dict(session.metadata["segments"][0], **{field: value})
    with pytest.raises(ValueError):
        inspect_wav(session.directory / item["file"], item)


def test_missing_track_and_broken_header_are_reported(session):
    path = session.directory / session.metadata["segments"][0]["file"]
    path.write_bytes(b"invalid header")
    report = inspect_session(session.directory)
    assert not report["file_checks_passed"]
    assert len(report["issues"]) == 2


def test_active_session_is_not_read_as_completed(session):
    session.update(state="RECORDING")
    report = inspect_session(session.directory)
    assert not report["file_checks_passed"] and report["segments"] == []
