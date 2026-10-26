import json
import struct
import wave

import pytest

from app.audio.segment_writer import SegmentWriter
from app.storage.session_store import SessionStore, safe_child, find_recovery, recover_session, apply_retention


@pytest.fixture
def store(tmp_path):
    return SessionStore.create(tmp_path, "manual", "2026-10-23T00:00:00+00:00")


def test_segment_rotation_and_offsets(store):
    writer = SegmentWriter(store, "remote", 100, 1, 1)
    writer.write(struct.pack("<250h", *range(250)), 0.5)
    writer.close()
    segments = store.metadata["segments"]
    assert [x["frames"] for x in segments] == [100, 100, 50]
    assert [x["offset"] for x in segments] == [0.5, 1.5, 2.5]
    for item in segments:
        with wave.open(str(store.directory / item["file"])) as f:
            assert f.getsampwidth() == 2
            assert len(f.readframes(999)) == item["frames"] * 2


def test_discontinuity_is_preserved(store):
    writer = SegmentWriter(store, "mic", 100, 1, 60)
    writer.write(bytes(100), 0)
    writer.write(bytes(100), 3)
    writer.close()
    assert [x["offset"] for x in store.metadata["segments"]] == [0, 3]
    assert store.metadata["gaps"][0]["start"] == 0.5


def test_partial_recovery_preserves_original(store):
    writer = SegmentWriter(store, "mic", 100, 1, 60)
    writer.write(bytes(200), 1)
    writer.wav.close()
    writer.raw.close()
    writer.wav = None
    partial = writer.partial
    assert find_recovery(store.directory.parent) == [store.directory]
    result = recover_session(store.directory)
    assert len(result["recovered"]) == 1 and partial.exists()
    assert recover_session(store.directory)["recovered"] == []
    with wave.open(str(store.directory / result["recovered"][0])) as f:
        assert f.getnframes() == 100


def test_bad_pcm_and_paths(store):
    with pytest.raises(ValueError):
        SegmentWriter(store, "../escape", 100, 1, 60)
    with pytest.raises(ValueError):
        SegmentWriter(store, "mic", 100, 1, 60).write(b"x", 0)
    with pytest.raises(ValueError):
        safe_child(store.directory, "../outside.wav")


def test_logs_have_both_clocks(store):
    row = json.loads((store.directory / "logs.jsonl").read_text().splitlines()[0])
    assert row["wall_clock"] and isinstance(row["monotonic"], float)


def test_retention_defaults_off_and_preserves_incomplete(store):
    store.update(ended_at="2000-01-01T00:00:00+00:00")
    assert apply_retention(store.directory.parent, None) == []
    assert apply_retention(store.directory.parent, 1) == []
    store.update(state="COMPLETE")
    assert apply_retention(store.directory.parent, 1) == [store.directory.name]
