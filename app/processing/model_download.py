"""Explicit model-only download in a disposable child process; never reads audio."""
import importlib.util
from pathlib import Path
import subprocess
import sys

from app.paths import data_root, logs_root, source_root
from app.processing.gpu_runtime import missing_gpu_libraries


MODEL_NAMES = ("tiny", "base", "small", "medium", "large-v3")


def model_directory(name: str) -> Path:
    if name not in MODEL_NAMES:
        raise ValueError("Unknown local model")
    return data_root() / "models" / name


def model_installed(directory: str | Path) -> bool:
    path = Path(directory)
    return all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))


def local_engine_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("faster_whisper", "huggingface_hub"))


class ModelDownload:
    def __init__(self):
        self.process = None
        self.name = None
        self.log = None

    @property
    def active(self):
        return self.process is not None

    def start(self, name: str):
        if self.active:
            raise RuntimeError("모델 다운로드가 진행 중입니다.")
        model_directory(name)  # Validate before launching a child.
        command = ([sys.executable] if getattr(sys, "frozen", False)
                   else [str(Path(sys.executable).with_name("pythonw.exe")) if sys.platform == "win32" else sys.executable,
                         "-m", "app.main"])
        command += ["--download-model", name]
        log_path = logs_root() / "model-download.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = log_path.open("wb")
        try:
            self.process = subprocess.Popen(command, cwd=Path(sys.executable).parent if getattr(sys, "frozen", False) else source_root(), stdin=subprocess.DEVNULL,
                                            stdout=self.log, stderr=self.log,
                                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            self.name = name
        except Exception:
            self.log.close()
            self.log = None
            raise

    def poll(self):
        if self.process is None:
            return None
        code = self.process.poll()
        if code is not None:
            self.process = None
            self.log.close()
            self.log = None
        return code

    def cancel(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.poll()
