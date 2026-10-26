"""Read-only environment diagnostics. Does not open audio streams or download models."""
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def diagnose():
    result = {"os": platform.platform(), "python": platform.python_version(),
              "virtual_environment": sys.prefix != sys.base_prefix, "recording_performed": False,
              "packages": {}, "audio": {}, "zoom_processes": [], "browsers": {}, "gpu": {}}
    for package in ["PyAudioWPatch", "PySide6", "psutil", "pywin32", "pycaw", "faster-whisper", "ctranslate2", "av", "numpy"]:
        try:
            result["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["packages"][package] = "not_installed"
    try:
        import pyaudiowpatch as pyaudio
        from app.audio.loopback import select_loopback
        from app.audio.microphone import select_microphone
        with pyaudio.PyAudio() as pa:
            api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            result["audio"]["wasapi_devices"] = [{k: d[k] for k in
                ("index", "name", "maxInputChannels", "maxOutputChannels", "defaultSampleRate", "isLoopbackDevice")}
                for d in pa.get_device_info_generator() if d["hostApi"] == api["index"]]
            for name, selector in [("default_loopback", lambda: select_loopback(pa)),
                                    ("default_microphone", lambda: select_microphone(pa, pyaudio.paWASAPI))]:
                try:
                    device = selector()
                    result["audio"][name] = {k: device[k] for k in ("index", "name", "defaultSampleRate")}
                except Exception as error:
                    result["audio"][name] = {"status": "unavailable", "error_type": type(error).__name__}
    except Exception as error:
        result["audio"]["status"] = "unavailable"
        result["audio"]["error_type"] = type(error).__name__
    try:
        import psutil
        result["zoom_processes"] = [p.info["name"] for p in psutil.process_iter(["name"])
                                    if "zoom" in (p.info["name"] or "").lower()]
    except Exception:
        result["zoom_processes"] = "unknown"
    if os.name == "nt":
        import winreg
        for browser, executable, policy in [("chrome", "Google/Chrome/Application/chrome.exe", r"SOFTWARE\Policies\Google\Chrome"),
                                             ("edge", "Microsoft/Edge/Application/msedge.exe", r"SOFTWARE\Policies\Microsoft\Edge")]:
            roots = [os.environ.get(key, "") for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
            entry = {"installed": any(root and (Path(root) / executable).exists() for root in roots),
                     "extension_installation": "manual_verification_required", "block_all_policy": False}
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, policy + r"\ExtensionInstallBlocklist") as key:
                        for i in range(winreg.QueryInfoKey(key)[1]):
                            _, value, _ = winreg.EnumValue(key, i)
                            entry["block_all_policy"] |= value == "*"
                except OSError:
                    pass
            result["browsers"][browser] = entry
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            process = subprocess.run([executable, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                                     capture_output=True, text=True, timeout=10,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            result["gpu"]["nvidia"] = process.stdout.strip() if process.returncode == 0 else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            result["gpu"]["nvidia"] = "unavailable"
    result["gpu"]["cuda_inference"] = "not_tested; CPU is default"
    result["next_step"] = "앱에서 동의를 확인하고 시작을 클릭한 뒤 두 WAV를 직접 청취하세요."
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(diagnose(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
