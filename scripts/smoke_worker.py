"""Run a real local model in the isolated worker using an existing synthetic fixture."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
from pathlib import Path
from app.paths import artifacts_root, data_root
import shutil
import sys
import tempfile
import time

import psutil

from app.processing.worker import ProcessTranscriber
from app.settings import Settings
from app.storage.session_store import SessionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--model", type=Path, default=data_root() / "models/small")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--output", type=Path, default=artifacts_root() / "transcription-worker.json")
    args = parser.parse_args()
    metadata = json.loads((args.fixture / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("validation_kind") != "windows_local_tts_no_capture":
        raise ValueError("Only synthetic local TTS fixtures are allowed")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    parent = psutil.Process()
    before = parent.memory_info().rss
    peak = 0
    child_pids = set()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="zoom-worker-") as temporary:
        directory = Path(temporary) / "synthetic"
        shutil.copytree(args.fixture, directory)
        store = SessionStore(directory)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(ProcessTranscriber(Settings(model=str(args.model.resolve()), device=args.device)).run, store)
            while not future.done():
                rss = 0
                for member in parent.children(recursive=True):
                    try:
                        child_pids.add(member.pid)
                        rss += member.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                peak = max(peak, rss)
                time.sleep(0.1)
            output = future.result()
        content = output.read_text(encoding="utf-8")
        remaining = [p.pid for p in parent.children(recursive=True) if p.pid in child_pids]
        result = {"recording_performed": False, "api_upload_performed": False,
                  "requested_device": args.device, "actual_device": store.metadata["transcription"]["device"],
                  "synthetic_real_model_inference": True, "elapsed_seconds": round(time.monotonic() - started, 2),
                  "parent_before_mib": round(before / 1024**2, 2),
                  "parent_after_mib": round(parent.memory_info().rss / 1024**2, 2),
                  "child_peak_working_set_mib": round(peak / 1024**2, 2), "remaining_worker_pids": remaining,
                  "parent_ai_modules": [name for name in ("numpy", "faster_whisper", "ctranslate2") if name in sys.modules],
                  "korean_phrase": "프로젝트" in content, "english_phrase": "project" in content.lower(),
                  "state": store.metadata["state"], "hardware_acceptance_passed": False}
        assert result["korean_phrase"] and result["english_phrase"] and not remaining
        assert result["actual_device"] == args.device, "Requested device failed and used CPU fallback"
        assert not result["parent_ai_modules"]
        args.output.parent.mkdir(exist_ok=True, parents=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
