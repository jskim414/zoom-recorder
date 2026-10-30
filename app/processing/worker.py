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
    transcribe = True
    def __init__(self, settings):
        self.settings = settings

    def run(self, store, *, api_approved=False):
        if self.transcribe and self.settings.transcription_backend == "openai" and api_approved is not True:
            raise PermissionError("API upload requires per-session approval")
        command = ([sys.executable, "--transcription-worker"] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "app.processing.worker"])
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if sys.platform == "win32" else 0
        environment = dict(os.environ)
        for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            environment[variable] = str(self.settings.cpu_threads)
        result = subprocess.run(command, input=json.dumps({"settings": asdict(self.settings),
                                "directory": str(store.directory), "api_approved": api_approved,
                                "transcribe": self.transcribe}),
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
            raise RuntimeError("Postprocessing failed; remaining audio preserved")
        return store.directory / "transcript.md" if self.transcribe else store.directory


class ProcessArchiver(ProcessTranscriber):
    transcribe = False


def main():
    payload = json.loads(sys.stdin.read())
    settings = Settings(**payload["settings"]).validate()
    store = SessionStore(Path(payload["directory"]))
    transcribe = payload.get("transcribe", True)
    try:
        run_pipeline(store, settings, transcribe=transcribe, api_approved=payload.get("api_approved") is True)
        return 0
    except Exception:
        status = store.metadata.get("transcription", {})
        store.update(state="ERROR", processing_error="postprocessing_failed",
                     transcription=status if status.get("status") in {"failed", "complete"} else
                     {**status, "status": "failed" if transcribe else "disabled"})
        return 1


def run_pipeline(store, settings, *, transcribe=True, api_approved=False):
    from app.processing.archive import create_archive, cleanup_wav
    if transcribe and settings.transcription_backend == "openai" and api_approved is not True:
        raise PermissionError("API upload requires per-session approval")
    create_archive(store)
    if transcribe:
        from app.processing.transcribe import Transcriber
        Transcriber(settings).run(store, api_approved=api_approved)
    else:
        # Do not erase a completed transcript status on a cleanup retry.
        if store.metadata.get("transcription", {}).get("status") != "complete":
            store.update(transcription={"status": "disabled"})
    cleanup_wav(store)
    store.update(state="ERROR" if store.metadata["errors"] else "COMPLETE", processing_error=None)


if __name__ == "__main__":
    raise SystemExit(main())
