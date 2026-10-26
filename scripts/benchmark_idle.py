"""Measure an isolated Windows Qt process; no microphone or loopback capture."""
import argparse
import json
import os
from pathlib import Path
from app.paths import artifacts_root
import statistics
import subprocess
import sys
import tempfile
import time


def child(directory, detect):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.settings import Settings
    from app.ui.tray import RecorderWindow
    app = QApplication([])
    window = RecorderWindow(Settings(recordings_dir=str(directory / "recordings"), auto_detect=detect))
    window.show()
    (directory / "ready.json").write_text(json.dumps({
        "timer_ms": window.timer.interval(), "bridge_started": window.bridge.server is not None,
        "ai_modules_loaded": [name for name in ("numpy", "faster_whisper", "ctranslate2", "onnxruntime") if name in sys.modules],
        "recording_started": window.controller.recorder is not None}), encoding="utf-8")
    timer = QTimer()
    def check():
        if (directory / "stop").exists():
            window.quit()
    timer.timeout.connect(check)
    timer.start(1000)
    QTimer.singleShot(120_000, window.quit)
    app.exec()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", type=Path)
    parser.add_argument("--detect", action="store_true")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--output", type=Path, default=artifacts_root() / "idle-current.json")
    args = parser.parse_args()
    if args.child:
        return child(args.child, args.detect)
    if not 1 <= args.seconds <= 90:
        parser.error("seconds must be 1..90")
    import psutil
    with tempfile.TemporaryDirectory(prefix="zoom-resource-") as temporary:
        root = Path(temporary)
        command = [sys.executable, "-m", "scripts.benchmark_idle", "--child", str(root)]
        if args.detect:
            command.append("--detect")
        process = subprocess.Popen(command, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            deadline = time.monotonic() + 20
            while not (root / "ready.json").exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("UI probe failed to start")
                time.sleep(0.1)
            target = psutil.Process(process.pid)
            time.sleep(2)
            tracked = {}
            for member in [target, *target.children(recursive=True)]:
                tracked[member.pid] = member
                member.cpu_percent()
            samples = []
            for _ in range(args.seconds):
                time.sleep(1)
                cpu = rss = private = threads = 0
                for member in [target, *target.children(recursive=True)]:
                    if member.pid not in tracked:
                        tracked[member.pid] = member
                        member.cpu_percent()
                    member = tracked[member.pid]
                    cpu += member.cpu_percent()
                    memory = member.memory_info()
                    rss += memory.rss
                    private += getattr(memory, "private", memory.vms)
                    threads += member.num_threads()
                samples.append({"cpu_one_core_percent": cpu, "cpu_machine_percent": cpu / (psutil.cpu_count() or 1),
                                "working_set_mib": rss / 1024**2,
                                "private_commit_mib": private / 1024**2, "threads": threads})
            result = {"scope": "Isolated Windows offscreen Qt UI process tree; shared pages may be counted twice; no capture/transcription; 2s warmup",
                      "detection": args.detect, "seconds": args.seconds,
                      "probe": json.loads((root / "ready.json").read_text()),
                      "mean": {key: round(statistics.mean(s[key] for s in samples), 3) for key in samples[0]},
                      "samples": samples}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps({"output": str(args.output), "mean": result["mean"], "probe": result["probe"]}))
        finally:
            (root / "stop").touch()
            try:
                process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    main()
