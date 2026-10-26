"""Read complete PCM16 WAV payloads before trusting their headers."""
import math
from pathlib import Path
import wave

import numpy as np


def inspect_wav(path: Path, expected: dict) -> dict:
    with wave.open(str(path), "rb") as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        width, frames = wav.getsampwidth(), wav.getnframes()
        if width != 2 or wav.getcomptype() != "NONE":
            raise ValueError("Expected uncompressed PCM16")
        for field, actual in (("rate", rate), ("channels", channels),
                              ("sample_width", width), ("frames", frames)):
            if expected.get(field) != actual:
                raise ValueError(f"WAV metadata mismatch: {field}")
        duration = frames / rate
        if not math.isfinite(expected["offset"]) or expected["offset"] < 0:
            raise ValueError("Invalid segment offset")
        if not math.isfinite(expected["duration"]) or abs(expected["duration"] - duration) > 1 / rate:
            raise ValueError("WAV metadata mismatch: duration")
        count = peak = clipped = 0
        squares = 0.0
        while block := wav.readframes(65536):
            if len(block) % (channels * width):
                raise ValueError("Incomplete PCM frame")
            samples = np.frombuffer(block, dtype="<i2").astype(np.float64)
            count += samples.size
            peak = max(peak, float(np.abs(samples).max()))
            clipped += int(np.count_nonzero(np.abs(samples) >= 32767))
            squares += float(np.dot(samples, samples))
        if count != frames * channels:
            raise ValueError("Truncated WAV payload")
        if not frames:
            raise ValueError("Empty WAV")
    return {"frames": frames, "rate": rate, "channels": channels, "duration": duration,
            "peak": peak / 32768, "rms": math.sqrt(squares / count) / 32768,
            "clipped_fraction": clipped / count, "all_zero": peak == 0}
