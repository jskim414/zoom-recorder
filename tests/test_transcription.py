from types import SimpleNamespace

import pytest

from app.audio.segment_writer import SegmentWriter
from app.processing.transcribe import Transcriber
from app.settings import Settings
from app.storage.session_store import SessionStore


@pytest.fixture
def audio_store(tmp_path):
    store = SessionStore.create(tmp_path, "manual", "now")
    for track, offset in [("remote", 2), ("mic", 0.5)]:
        writer = SegmentWriter(store, track, 100, 1, 60)
        writer.write(bytes(400), offset)
        writer.close()
    store.update(ended_at="2026-10-23T01:00:00+00:00")
    return store


def test_offset_order_and_local_only(audio_store):
    calls = []
    class Model:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)
        def transcribe(self, path, **kwargs):
            assert kwargs["vad_filter"] and kwargs["language"] is None
            return iter([SimpleNamespace(start=0, end=1, text="안녕하세요 <script>")]), None
    destination = Transcriber(Settings(), Model).run(audio_store)
    text = destination.read_text(encoding="utf-8")
    assert text.index("마이크**") < text.index("시스템 출력**")
    assert "[00:00:02–00:00:03]" in text
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert calls == [{"device": "cpu", "compute_type": "int8", "local_files_only": True,
                      "cpu_threads": 2, "num_workers": 1}]


def test_gpu_iteration_failure_retries_entire_job_on_cpu(audio_store):
    calls = []
    class Model:
        def __init__(self, *args, device, **kwargs):
            self.device = device
            calls.append(device)
        def transcribe(self, *args, **kwargs):
            def iterate():
                yield SimpleNamespace(start=0, end=1, text="hello")
                if self.device == "cuda":
                    raise RuntimeError("GPU unavailable")
            return iterate(), None
    path = Transcriber(Settings(device="cuda"), Model).run(audio_store)
    assert calls == ["cuda", "cpu"]
    assert path.read_text(encoding="utf-8").count("hello") == 2
    assert audio_store.metadata["transcription"]["device"] == "cpu"


def test_missing_model_preserves_audio_and_reports_retry(audio_store, tmp_path):
    with pytest.raises(FileNotFoundError):
        Transcriber(Settings(model=str(tmp_path / "missing"))).run(audio_store)
    assert audio_store.metadata["transcription"]["code"] == "model_missing"
    assert len(list((audio_store.directory / "audio").glob("*.wav"))) == 2
    assert not (audio_store.directory / "transcript.md").exists()


def test_real_decoder_accepts_device_rate_pcm_without_model_download(tmp_path):
    """Catch faster-whisper/PyAV API incompatibility without a fake decoder."""
    import struct
    import wave
    from faster_whisper.audio import decode_audio
    path = tmp_path / "device-rate.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(struct.pack("<9600h", *([1000] * 9600)))
    decoded = decode_audio(str(path), sampling_rate=16000)
    assert decoded.ndim == 1 and len(decoded) == 1600
    assert 0 < decoded.mean() < 1
