"""Synthetic SAPI speech -> MP3/transcript -> WAV cleanup -> MP3 retranscription.

Never opens a microphone/loopback stream or sends audio to an external service.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

from app.settings import Settings
from app.storage.session_store import SessionStore
from scripts.speech_fixture import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--delete-audio", action="store_true")
    parser.add_argument("--no-mp3", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    generated = root / "generated"
    generate(generated)
    store = SessionStore.create(root / "sessions", "manual", "synthetic-test-only")
    segments = []
    for track, offset in (("remote", 0.0), ("mic", 12.0)):
        name = track + "_0001.wav"
        path = store.directory / "audio" / name
        path.write_bytes((generated / name).read_bytes())
        with wave.open(str(path)) as wav:
            segments.append({"track": track, "file": "audio/" + name, "offset": offset,
                "frames": wav.getnframes(), "rate": wav.getframerate(), "channels": wav.getnchannels(),
                "sample_width": 2, "codec": "PCM16", "duration": wav.getnframes() / wav.getframerate()})
    store.update(state="FINALIZING", segments=segments, selected_tracks=["remote", "mic"],
                 ended_at=store.metadata["started_at"], validation_kind="windows_local_tts_no_capture",
                 storage_policy={"temporary_wav": True, "create_mix": True, "transcribe_required": bool(args.model),
                                 "convert_to_mp3": not args.no_mp3, "delete_audio_after_transcription": args.delete_audio})
    settings = Settings(model=str(args.model.resolve()) if args.model else "models/missing", cpu_threads=2)
    command = [str(args.exe.resolve()), "--transcription-worker"] if args.exe else [sys.executable, "-m", "app.processing.worker"]
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    environment.pop("OPENAI_API_KEY", None)
    results = []
    for attempt in range(2 if args.model and not args.delete_audio else 1):
        started = time.monotonic()
        result = subprocess.run(command, input=json.dumps({"settings": asdict(settings),
            "directory": str(store.directory), "transcribe": bool(args.model)}),
            text=True, encoding="utf-8", capture_output=True, env=environment, timeout=240,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        store = SessionStore(store.directory)
        assert result.returncode == 0, (store.metadata.get("processing_error"), result.stderr)
        deleted = bool(args.delete_audio and args.model)
        mp3_count = len(list(store.directory.glob("*.mp3")))
        wav_count = len(list((store.directory / "audio").glob("*.wav")))
        assert mp3_count == (0 if deleted or args.no_mp3 else 3)
        assert wav_count == (2 if args.no_mp3 and not deleted else 0)
        if deleted:
            assert store.metadata["audio_cleanup"]["status"] == "complete"
        checks = {}
        if args.model:
            text = (store.directory / "transcript.md").read_text(encoding="utf-8")
            checks = {"korean_phrase": "프로젝트" in text, "english_phrase": "project" in text.lower(),
                      "pc_source": "PC 음성" in text, "mic_source": "**마이크**" in text,
                      "no_deleted_wav_links": args.no_mp3 and not deleted or ".wav" not in text,
                      "no_mix_transcription": "합본.mp3" not in text}
            assert all(checks.values()), checks
        results.append({"input": "WAV" if attempt == 0 or args.no_mp3 else "MP3", "seconds": round(time.monotonic() - started, 2),
                        "checks": checks, "wav_removed": wav_count == 0, "mp3_count": mp3_count,
                        "audio_deleted": deleted})
    from scripts.inspect_session import inspect_session
    inspection = inspect_session(store.directory)
    assert inspection["file_checks_passed"], inspection
    report = {"recording_performed": False, "external_audio_upload": False,
              "hardware_acceptance_passed": False, "results": results, "file_checks_passed": True}
    (root / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
