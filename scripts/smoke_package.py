"""Check packaged GUI/worker in a temporary user profile; never records or uploads."""
from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from app.ui.single_instance import reveal_existing
    from app.settings import Settings
    from app.storage.session_store import SessionStore
    from app.audio.segment_writer import SegmentWriter
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="zoom-package-") as temporary:
        root = Path(temporary)
        environment = dict(os.environ, LOCALAPPDATA=str(root), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        windows = Path(os.environ.get("WINDIR", "C:/Windows"))
        environment["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
        # Use EXE defaults from an unrelated cwd, exercising per-user storage and Qt plugins.
        process = subprocess.Popen([str(args.exe.resolve()), "--no-detection"] +
                                   (["--background"] if args.background else []), cwd=root, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        recording_root = root / "ZoomRecorder/recordings"
        try:
            deadline = time.monotonic() + 20
            ready = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Packaged GUI exited: " + process.communicate()[1].decode(errors="replace"))
                if reveal_existing(recording_root):
                    ready = True
                    break
                app.processEvents()
                time.sleep(0.1)
            assert ready, "Packaged GUI did not establish its local IPC"
            assert not list(recording_root.glob("*/audio"))
        finally:
            # Only our isolated, nonrecording test process is stopped.
            process.terminate()
            process.communicate(timeout=10)
        if args.fixture:
            fixture_metadata = json.loads((args.fixture / "metadata.json").read_text(encoding="utf-8"))
            if fixture_metadata.get("validation_kind") != "windows_local_tts_no_capture":
                raise ValueError("Only synthetic local TTS fixtures are allowed")
            directory = root / "synthetic"
            shutil.copytree(args.fixture, directory)
            store = SessionStore(directory)
            store.update(transcription={"status": "pending"})
        else:
            store = SessionStore.create(root / "synthetic", "manual", "test-only")
            writer = SegmentWriter(store, "mic", 16000, 1, 60)
            writer.write(bytes(32000), 0)
            writer.close()
        settings = Settings(model=str(args.model.resolve()) if args.model else str(root / "missing-model"))
        payload = json.dumps({"settings": asdict(settings), "directory": str(store.directory)})
        started = time.monotonic()
        worker = subprocess.run([str(args.exe.resolve()), "--transcription-worker"], input=payload,
                                text=True, encoding="utf-8", capture_output=True, cwd=root,
                                env=environment, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        metadata = SessionStore(store.directory).metadata
        if args.model:
            assert worker.returncode == 0, metadata
            assert metadata["transcription"]["status"] == "complete"
        else:
            assert worker.returncode == 1, worker.stderr
            assert metadata["transcription"]["code"] == "model_missing", metadata
        result = {"exe": args.exe.name, "gui_ipc_ready": ready, "background_launch_requested": args.background,
                  "external_python_and_agent_path_removed": True,
                  "recording_performed": False,
                  "api_upload_performed": False, "worker_exit_code": worker.returncode,
                  "worker_seconds": round(time.monotonic() - started, 2),
                  "transcription": metadata["transcription"], "hardware_acceptance_passed": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
