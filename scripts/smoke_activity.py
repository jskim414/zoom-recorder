"""Exercise the native VAD against existing local synthetic speech; no capture."""
import argparse
import json
from pathlib import Path
import queue
import time
import wave

from app.audio.activity import SpeechMonitor
from app.paths import artifacts_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    metadata = json.loads((args.fixture / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("validation_kind") != "windows_local_tts_no_capture":
        raise ValueError("Only local synthetic TTS fixtures are allowed")
    outcomes = []
    for segment in metadata["segments"]:
        with wave.open(str(args.fixture / segment["file"]), "rb") as wav:
            assert wav.getsampwidth() == 2
            rate, channels = wav.getframerate(), wav.getnchannels()
            origin = time.monotonic() - wav.getnframes() / rate
            monitor = SpeechMonitor(origin, queue.Queue())
            monitor.start()
            deadline = time.monotonic() + 20
            frames = 0
            try:
                while data := wav.readframes(rate // 50):
                    while monitor.queue.qsize() > 8:
                        if time.monotonic() > deadline or monitor.failed:
                            raise RuntimeError("VAD did not consume frames")
                        time.sleep(0.002)
                    monitor.feed(segment["track"], data, rate, channels, frames / rate)
                    frames += len(data) // (2 * channels)
                while monitor.observed.get(segment["track"], 0) < origin + frames / rate - 0.001:
                    if time.monotonic() > deadline or monitor.failed:
                        raise RuntimeError("VAD did not complete")
                    time.sleep(0.002)
                assert monitor.last_speech > origin
                outcomes.append({"track": segment["track"], "sample_rate": rate, "speech_detected": True})
            finally:
                monitor.close()
    output = artifacts_root() / "speech-activity.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"capture_performed": False, "api_upload_performed": False,
                                  "synthetic_native_vad": outcomes}, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
