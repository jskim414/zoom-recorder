import io
import wave

import pytest

from app.controller import Controller
from app.detection.session_manager import State
from app.processing.api_transcribe import transcribe_api, upload_chunk
from app.processing.transcribe import Transcriber
from app.settings import Settings
from test_transcription import audio_store


def test_api_consent_and_key_required_before_upload(audio_store, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    def forbidden(*args):
        pytest.fail("Upload attempted without consent/key")
    with pytest.raises(PermissionError):
        transcribe_api(audio_store, upload=forbidden)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        transcribe_api(audio_store, approved=True, upload=forbidden)
    with pytest.raises(PermissionError):
        Transcriber(Settings(transcription_backend="openai")).run(audio_store)


def test_api_validates_all_wav_before_first_upload(audio_store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    path = audio_store.directory / audio_store.metadata["segments"][-1]["file"]
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(ValueError):
        transcribe_api(audio_store, approved=True, upload=lambda *a: pytest.fail("Uploaded damaged session"))


def test_api_chunks_have_correct_offsets_and_bounded_size(tmp_path, monkeypatch):
    from app.audio.segment_writer import SegmentWriter
    from app.storage.session_store import SessionStore
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    store = SessionStore.create(tmp_path, "manual", "now")
    writer = SegmentWriter(store, "mic", 100, 1, 120)
    writer.write(bytes(100 * 2 * 65), 3.5)
    writer.close()
    lengths = []
    def upload(data, key):
        with wave.open(io.BytesIO(data)) as wav:
            lengths.append(wav.getnframes() / wav.getframerate())
        assert len(data) < 4_000_100
        return "synthetic speech"
    result = transcribe_api(store, approved=True, upload=upload)
    assert lengths == [30, 30, 5]
    assert [s["start"] for s in result] == [3.5, 33.5, 63.5]
    assert result[-1]["end"] == 68.5


def test_api_failure_preserves_audio_and_never_falls_back(audio_store, monkeypatch):
    import app.processing.api_transcribe as api
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    before = [p.read_bytes() for p in sorted((audio_store.directory / "audio").glob("*.wav"))]
    def fail(*args):
        raise RuntimeError("service unavailable")
    monkeypatch.setattr(api, "upload_chunk", fail)
    with pytest.raises(RuntimeError):
        Transcriber(Settings(transcription_backend="openai", device="cuda")).run(audio_store, api_approved=True)
    assert audio_store.metadata["transcription"]["status"] == "failed"
    assert before == [p.read_bytes() for p in sorted((audio_store.directory / "audio").glob("*.wav"))]
    assert "test-not-a-real-key" not in (audio_store.directory / "logs.jsonl").read_text()


def test_api_success_is_labeled_as_chunk_times(audio_store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr("app.processing.api_transcribe.upload_chunk", lambda *a: "hello")
    output = Transcriber(Settings(transcription_backend="openai")).run(audio_store, api_approved=True)
    assert "OpenAI API" in output.read_text(encoding="utf-8")
    assert audio_store.metadata["transcription"]["backend"] == "openai"


def test_controller_requires_fresh_api_approval_for_start_and_retry(audio_store, tmp_path):
    controller = Controller(Settings(recordings_dir=str(tmp_path), auto_transcribe=True, transcription_backend="openai"))
    controller.manager.manual()
    with pytest.raises(PermissionError):
        controller.start(next(iter(controller.manager.candidates)), True, True)
    assert controller.manager.state == State.PROMPTED and controller.recorder is None
    controller.manager.skip()
    with pytest.raises(PermissionError):
        controller.retry_transcription(audio_store)
    assert controller.future is None
    controller.close()


def test_http_upload_uses_fixed_https_no_redirect_and_bounded_response(monkeypatch):
    calls = []
    class Connection:
        def __init__(self, host, timeout): calls.append((host, timeout))
        def putrequest(self, *args): calls.append(args)
        def putheader(self, *args): calls.append(args)
        def endheaders(self): pass
        def send(self, data): pass
        def getresponse(self): return self
        status = 302
        def close(self): calls.append("closed")
    monkeypatch.setattr("http.client.HTTPSConnection", Connection)
    with pytest.raises(RuntimeError, match="302"):
        upload_chunk(b"fake-wav", "test-key")
    assert calls[0] == ("api.openai.com", 120)
    assert calls[1] == ("POST", "/v1/audio/transcriptions")
    assert calls[-1] == "closed"
