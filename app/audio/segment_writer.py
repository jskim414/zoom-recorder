from pathlib import Path
import os
import wave

from app.storage.session_store import SessionStore, atomic_json


class SegmentWriter:
    def __init__(self, store: SessionStore, track: str, rate: int, channels: int, seconds: int):
        if track not in {"remote", "mic"} or rate <= 0 or not 1 <= channels <= 32 or seconds <= 0:
            raise ValueError("Invalid PCM format")
        self.store, self.track, self.rate, self.channels = store, track, rate, channels
        self.limit = rate * seconds
        self.sequence = self.frames = 0
        self.wav = self.raw = None
        self.expected_offset = None

    def _open(self, offset: float):
        self.sequence += 1
        self.frames = 0
        name = f"{self.track}_{self.sequence:04d}.wav"
        self.destination = self.store.directory / "audio" / name
        self.partial = self.destination.with_suffix(".wav.part")
        self.info = {"track": self.track, "file": f"audio/{name}", "offset": offset,
                     "rate": self.rate, "channels": self.channels, "sample_width": 2, "codec": "PCM16"}
        atomic_json(self.partial.with_suffix(".json"), self.info)
        self.raw = self.partial.open("xb")
        self.wav = wave.open(self.raw, "wb")
        self.wav.setnchannels(self.channels)
        self.wav.setsampwidth(2)
        self.wav.setframerate(self.rate)
        self.wav.writeframes(b"")

    def write(self, data: bytes, offset: float):
        frame_size = 2 * self.channels
        if len(data) % frame_size or offset < 0:
            raise ValueError("Invalid PCM block")
        if not data:
            return
        if self.expected_offset is not None and abs(offset - self.expected_offset) > 0.15:
            self.close()
            self.store.append("gaps", {"track": self.track, "start": self.expected_offset,
                                      "end": offset, "kind": "capture_discontinuity"})
        remaining = memoryview(data)
        while remaining:
            if self.wav is None:
                self._open(offset)
            count = min(len(remaining) // frame_size, self.limit - self.frames)
            self.wav.writeframes(remaining[:count * frame_size])
            self.raw.flush()  # WAV header is updated on each writeframes call.
            self.frames += count
            offset += count / self.rate
            remaining = remaining[count * frame_size:]
            if self.frames == self.limit:
                self.close()
        self.expected_offset = offset

    def close(self):
        if self.wav is None:
            return
        try:
            self.wav.close()
            self.raw.flush()
            os.fsync(self.raw.fileno())
        finally:
            self.raw.close()
            self.wav = self.raw = None
        with wave.open(str(self.partial), "rb") as check:
            if check.getnframes() != self.frames:
                raise ValueError("WAV frame count mismatch")
        os.replace(self.partial, self.destination)
        self.info.update(frames=self.frames, duration=self.frames / self.rate)
        self.store.append("segments", self.info.copy())
        self.partial.with_suffix(".json").unlink(missing_ok=True)
