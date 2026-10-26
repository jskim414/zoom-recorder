import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.processing import gpu_runtime
from app.processing.transcribe import Transcriber
from app.settings import Settings


def test_wheel_and_portable_runtime_discovery_is_read_only(tmp_path, monkeypatch):
    site = tmp_path / "site"
    data = tmp_path / "data"
    monkeypatch.setattr(gpu_runtime.sysconfig, "get_path", lambda *args: str(site))
    monkeypatch.setattr(gpu_runtime, "data_root", lambda: data)
    monkeypatch.setattr(gpu_runtime.ctypes.util, "find_library", lambda name: None)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    before = os.environ.get("PATH")
    assert gpu_runtime.missing_gpu_libraries() == ["cublas64_12", "cudnn64_9"]
    cublas = site / "nvidia/cublas/bin"
    cudnn = data / "cuda/nvidia/cudnn/bin"
    for directory, name in ((cublas, "cublas64_12.dll"), (cudnn, "cudnn64_9.dll")):
        directory.mkdir(parents=True)
        (directory / name).touch()
    assert gpu_runtime.gpu_runtime_directories() == [cublas, cudnn]
    assert gpu_runtime.missing_gpu_libraries() == []
    assert os.environ.get("PATH") == before


def test_registration_retains_handles_and_is_idempotent(tmp_path, monkeypatch):
    calls = []
    handle = object()
    monkeypatch.setattr(gpu_runtime, "gpu_runtime_directories", lambda: [tmp_path])
    monkeypatch.setattr(gpu_runtime, "_registered", set())
    monkeypatch.setattr(gpu_runtime, "_dll_handles", [])
    monkeypatch.setattr(os, "add_dll_directory", lambda path: calls.append(path) or handle)
    monkeypatch.setenv("PATH", "existing-path")
    gpu_runtime.configure_gpu_runtime()
    gpu_runtime.configure_gpu_runtime()
    assert calls == [str(tmp_path)] and gpu_runtime._dll_handles == [handle]
    assert os.environ["PATH"] == str(tmp_path) + os.pathsep + "existing-path"


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_runtime_is_registered_only_for_real_gpu_model_load(tmp_path, monkeypatch, device):
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).touch()
    events = []
    monkeypatch.setattr(gpu_runtime, "configure_gpu_runtime", lambda: events.append("runtime"))
    def construct(*args, **kwargs):
        events.append(kwargs["device"])
        return "model"
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=construct))
    assert Transcriber(Settings(model=str(tmp_path)))._model(device) == "model"
    assert events == (["runtime", "cuda"] if device == "cuda" else ["cpu"])
