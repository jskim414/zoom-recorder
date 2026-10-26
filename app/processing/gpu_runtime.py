"""Find optional Windows CUDA wheels without loading them into the idle UI."""
import ctypes.util
import os
from pathlib import Path
import sys
import sysconfig

from app.paths import data_root


_dll_handles = []
_registered = set()


def gpu_runtime_directories() -> list[Path]:
    roots = [Path(sysconfig.get_path("purelib")), data_root() / "cuda"]
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        roots.append(Path(bundled))
    directories = []
    for root in roots:
        for candidate in [root] + [root / "nvidia" / package / "bin"
                                   for package in ("cuda_runtime", "cuda_nvrtc", "cublas", "cudnn")]:
            if candidate.is_dir() and any(candidate.glob("*.dll")):
                resolved = candidate.resolve()
                if resolved not in directories:
                    directories.append(resolved)
    return directories


def missing_gpu_libraries() -> list[str]:
    if sys.platform != "win32":
        return []
    directories = gpu_runtime_directories()
    return [name for name in ("cublas64_12", "cudnn64_9")
            if not any((directory / (name + ".dll")).is_file() for directory in directories)
            and not ctypes.util.find_library(name)]


def configure_gpu_runtime() -> list[Path]:
    """Call only in a GPU transcription process, before importing faster-whisper."""
    if sys.platform != "win32":
        return []
    directories = gpu_runtime_directories()
    additions = []
    for directory in directories:
        value = str(directory)
        if value not in _registered:
            # Retain handles for CUDA libraries loaded later during inference.
            _dll_handles.append(os.add_dll_directory(value))
            _registered.add(value)
            additions.append(value)
    if additions:
        # Native LoadLibrary also uses PATH. This affects only this worker process.
        os.environ["PATH"] = os.pathsep.join(additions + [os.environ.get("PATH", "")])
    return directories
