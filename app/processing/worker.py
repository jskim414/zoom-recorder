"""One child per transcription: native model memory is released on process exit."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from app.settings import Settings
from app.paths import source_root
from app.storage.session_store import SessionStore


class ProcessTranscriber:
    def __init__(self, settings):
        self.settings = settings

    def run(self, store, *, api_approved=False):
        if self.settings.transcription_backend == "openai" and api_approved is not True:
            raise PermissionError("API upload requires per-session approval")
        command = ([sys.executable, "--transcription-worker"] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "app.processing.worker"])
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if sys.platform == "win32" else 0
        environment = dict(os.environ)
        for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            environment[variable] = str(self.settings.cpu_threads)
        result = subprocess.run(command, input=json.dumps({"settings": asdict(self.settings),
                                "directory": str(store.directory), "api_approved": api_approved}),
                                text=True, encoding="utf-8", stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=flags, env=environment,
                                cwd=Path(sys.executable).parent if getattr(sys, "frozen", False) else source_root())
        # Child owns metadata while running. Reload even on failure; never overwrite it from stale UI state.
        store.metadata = SessionStore(store.directory).metadata
        if result.returncode:
            code = store.metadata.get("transcription", {}).get("code")
            if code == "model_missing":
                raise FileNotFoundError("Local transcription model missing")
            if code == "engine_missing":
                raise ImportError("Local transcription engine missing; install Local package")
            if store.metadata.get("state") == "TRANSCRIBING":
                store.update(state="ERROR", transcription={"status": "failed", "code": "worker_failed"})
            raise RuntimeError("Transcription worker failed; original WAV preserved")
        return store.directory / "transcript.md"


def main():
    from app.processing.transcribe import Transcriber
    payload = json.loads(sys.stdin.read())
    settings = Settings(**payload["settings"]).validate()
    store = SessionStore(Path(payload["directory"]))
    try:
        Transcriber(settings).run(store, api_approved=payload.get("api_approved") is True)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
