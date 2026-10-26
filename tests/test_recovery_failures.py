import os
import wave

from app.audio.segment_writer import SegmentWriter
from app.storage.session_store import SessionStore, recover_session, find_recovery


def test_crash_between_wav_rename_and_metadata_append(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "consented")
    writer = SegmentWriter(store, "remote", 100, 1, 60)
    writer.write(bytes(200), 2)
    writer.wav.close()
    writer.raw.close()
    writer.wav = None
    os.replace(writer.partial, writer.destination)
    assert not store.metadata["segments"]
    result = recover_session(store.directory)
    assert len(result["recovered"]) == 1
    reopened = SessionStore(store.directory)
    assert reopened.metadata["segments"][0]["offset"] == 2
    assert recover_session(store.directory)["recovered"] == []


def test_recovery_handles_stale_header_and_partial_frame(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "consented")
    writer = SegmentWriter(store, "mic", 100, 2, 60)
    writer.write(bytes(400), 0)
    writer.wav.close()
    writer.raw.close()
    writer.wav = None
    with writer.partial.open("r+b") as f:
        f.seek(40)
        f.write((0).to_bytes(4, "little"))
        f.seek(0, 2)
        f.write(b"x")
    result = recover_session(store.directory)
    with wave.open(str(store.directory / result["recovered"][0])) as f:
        assert f.getnframes() == 100
    assert writer.partial.exists()


def test_completed_recovered_session_is_not_flagged_again(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "consented")
    writer = SegmentWriter(store, "mic", 100, 1, 60)
    writer.write(bytes(200), 0)
    writer.wav.close()
    writer.raw.close()
    writer.wav = None
    recover_session(store.directory)
    SessionStore(store.directory).update(state="COMPLETE")
    assert find_recovery(tmp_path) == []
    assert writer.partial.exists()


def test_orphan_partial_reports_error(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "consented")
    (store.directory / "audio" / "mic_0001.wav.part").write_bytes(b"broken")
    result = recover_session(store.directory)
    assert any(error["code"] == "recovery_sidecar_missing" for error in result["errors"])


def test_real_child_process_kill_preserves_completed_and_partial_synthetic_audio(tmp_path):
    """Terminate only our synthetic writer process; never open capture hardware."""
    import subprocess
    import sys
    import time
    from pathlib import Path
    from scripts.inspect_session import inspect_session
    child_code = '''
import sys, time
from pathlib import Path
from app.audio.segment_writer import SegmentWriter
from app.storage.session_store import SessionStore
root = Path(sys.argv[1])
store = SessionStore.create(root, "manual", "synthetic fixture; no participant recording")
store.update(validation_kind="synthetic_process_kill_no_capture")
writers = []
for track in ("remote", "mic"):
    writer = SegmentWriter(store, track, 16000, 1, 1)
    writer.write(b"\\x01\\x00" * 20000, 0)
    writers.append(writer)
(root / "ready").write_text(store.directory.name, encoding="utf-8")
while True:
    time.sleep(1)
'''
    process = subprocess.Popen([sys.executable, "-c", child_code, str(tmp_path)],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read().decode(errors="replace"))
            if time.monotonic() >= deadline:
                raise AssertionError("Synthetic writer did not become ready")
            time.sleep(0.02)
        directory = tmp_path / (tmp_path / "ready").read_text(encoding="utf-8")
        originals = {path: path.read_bytes() for path in (directory / "audio").iterdir()}
        process.kill()
        process.wait(timeout=5)
        assert find_recovery(tmp_path) == [directory]
        result = recover_session(directory)
        assert len(result["recovered"]) == 2 and not result["errors"]
        assert all(path.read_bytes() == data for path, data in originals.items())
        report = inspect_session(directory)
        assert report["file_checks_passed"] and len(report["segments"]) == 4
        assert not report["hardware_acceptance_passed"]
        assert recover_session(directory)["recovered"] == []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
