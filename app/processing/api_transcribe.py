"""Optional, explicitly approved OpenAI upload. No SDK/model import in the UI."""
import http.client
import io
import json
import os
import uuid
import wave

from app.audio.validation import inspect_wav
from app.storage.session_store import safe_child
from app.processing.archive import source_items, reference_file

API_MODEL = "gpt-transcribe"


def upload_chunk(audio: bytes, key: str) -> str:
    boundary = uuid.uuid4().hex
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
              f"{API_MODEL}\r\n--{boundary}\r\n"
              'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
              'Content-Type: audio/wav\r\n\r\n').encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    # Fixed HTTPS destination, no redirects, no automatic retries or response logging.
    connection = http.client.HTTPSConnection("api.openai.com", timeout=120)
    try:
        connection.putrequest("POST", "/v1/audio/transcriptions")
        connection.putheader("Authorization", "Bearer " + key)
        connection.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        connection.putheader("Content-Length", str(len(prefix) + len(audio) + len(suffix)))
        connection.endheaders()
        for data in (prefix, audio, suffix):
            connection.send(data)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"API transcription failed (HTTP {response.status}); WAV preserved")
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("API response too large")
        result = json.loads(body)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("Invalid transcription response")
        return result["text"].strip()
    finally:
        connection.close()


def transcribe_api(store, *, approved=False, upload=None):
    if approved is not True:
        raise PermissionError("이 세션의 OpenAI 오디오 전송에 별도 동의가 필요합니다.")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY를 설정하고 앱을 다시 실행하세요.")
    inputs = []
    # Validate the complete session before transmitting even one chunk.
    for item in source_items(store):
        path = safe_child(store.directory, item["file"])
        if path.suffix not in {".wav", ".mp3"}:
            raise ValueError("Only WAV or verified MP3 can be transcribed")
        if not item["frames"]:
            continue
        if path.suffix == ".wav":
            inspect_wav(path, item)
        inputs.append((item, path))
    result = []
    upload = upload or upload_chunk
    for item, path in inputs:
        if path.suffix == ".mp3":
            for offset, frames, audio in mp3_chunks(path):
                text = upload(audio, key)
                if text:
                    result.append({"track": item["track"], "file": reference_file(store, item),
                                   "start": offset / 16000, "end": (offset + frames) / 16000, "text": text})
            continue
        with wave.open(str(path), "rb") as source:
            rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
            # At most 30 s / 4 MB per request; bounded memory even for high-rate WAV.
            frames_per_chunk = min(rate * 30, 4_000_000 // (channels * width))
            offset = 0
            while pcm := source.readframes(frames_per_chunk):
                frames = len(pcm) // (channels * width)
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as output:
                    output.setnchannels(channels)
                    output.setsampwidth(width)
                    output.setframerate(rate)
                    output.writeframes(pcm)
                text = upload(buffer.getvalue(), key)
                if text:
                    result.append({"track": item["track"], "file": reference_file(store, item),
                                   "start": item["offset"] + offset / rate,
                                   "end": item["offset"] + (offset + frames) / rate, "text": text})
                offset += frames
    return result


def mp3_chunks(path, seconds=30):
    """Decode locally into bounded 30-second WAV requests; never upload the mix."""
    import av
    pending = bytearray()
    offset = 0
    chunk_frames = 16000 * seconds
    chunk_bytes = chunk_frames * 2
    def pack(pcm):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return buffer.getvalue()
    with av.open(str(path)) as source:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        def frames():
            for frame in source.decode(audio=0):
                yield from resampler.resample(frame)
            yield from resampler.resample(None)
        for frame in frames():
            pending.extend(frame.to_ndarray().tobytes())
            while len(pending) >= chunk_bytes:
                pcm = bytes(pending[:chunk_bytes])
                del pending[:chunk_bytes]
                yield offset, chunk_frames, pack(pcm)
                offset += chunk_frames
    if pending:
        yield offset, len(pending) // 2, pack(bytes(pending))
