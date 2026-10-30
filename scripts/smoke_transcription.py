"""Real local Whisper inference on generated speech, without opening capture devices."""
import argparse
import json
import os
from pathlib import Path
from app.paths import artifacts_root, data_root
import sys
import time
import uuid
import wave

from app.storage.session_store import SessionStore, atomic_json, utc_now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=data_root() / "models/small")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    if not (args.model / "model.bin").is_file():
        parser.error("Install the local model first with scripts/download_model.py")
    directory = artifacts_root() / ("synthetic-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    from scripts.speech_fixture import generate
    generate(directory / "audio")
    segments = []
    for track, offset in (("remote", 0.0), ("mic", 12.0)):
        path = directory / "audio" / f"{track}_0001.wav"
        with wave.open(str(path), "rb") as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            segments.append({"track": track, "file": path.relative_to(directory).as_posix(),
                             "offset": offset, "frames": frames, "rate": rate,
                             "channels": wav.getnchannels(), "sample_width": wav.getsampwidth(),
                             "codec": "PCM16", "duration": frames / rate})
    atomic_json(directory / "metadata.json", {
        "schema_version": 1, "session_id": directory.name, "source": "synthetic_validation",
        "validation_kind": "windows_local_tts_no_capture", "consent_at": None,
        "started_at": utc_now(), "ended_at": utc_now(), "state": "COMPLETE",
        "tracks": {}, "segments": segments, "errors": [], "gaps": [],
        "transcription": {"status": "pending"}})
    # This is a process-level Python socket guard, not an OS firewall test.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    attempts = []
    def deny_network(event, arguments):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            attempts.append(event)
            raise RuntimeError("Network disabled for transcription validation")
    sys.addaudithook(deny_network)
    from app.processing.transcribe import Transcriber
    from app.settings import Settings
    from scripts.inspect_session import inspect_session
    started = time.monotonic()
    store = SessionStore(directory)
    transcript = Transcriber(Settings(model=str(args.model.resolve()), device=args.device)).run(store)
    content = transcript.read_text(encoding="utf-8")
    checks = {"korean_phrase": "프로젝트" in content, "english_phrase": "project" in content.lower(),
              "both_tracks": "시스템 출력**" in content and "**마이크**" in content}
    file_report = inspect_session(directory)
    result = {"fixture": str(directory), "recording_performed": False,
              "real_model_inference": True, "requested_device": args.device,
              "actual_device": store.metadata["transcription"]["device"],
              "elapsed_seconds": round(time.monotonic() - started, 2),
              "python_network_attempts": attempts, "os_network_disconnected": False,
              "checks": checks, "file_checks_passed": file_report["file_checks_passed"],
              "hardware_acceptance_passed": False}
    atomic_json(directory / "validation.json", result)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if all(checks.values()) and file_report["file_checks_passed"] and not attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
