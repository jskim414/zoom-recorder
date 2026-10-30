"""User-selected Windows shortcuts. No startup registration on import or launch."""
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def launcher_executable():
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    local = PROJECT_ROOT / ".venv/Scripts/pythonw.exe"
    return local if local.is_file() else Path(sys.executable).with_name("pythonw.exe")


def create_shortcut(destination: Path, *, background=False):
    from win32com.client import Dispatch
    frozen = getattr(sys, "frozen", False)
    python = launcher_executable()
    if not python.is_file():
        raise FileNotFoundError("먼저 가상환경에 앱을 설치하세요.")
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(destination))
    shortcut.TargetPath = str(python)
    shortcut.Arguments = "" if frozen else "-m app.main"
    if "--config" in sys.argv:
        index = sys.argv.index("--config")
        if index + 1 < len(sys.argv):
            config = str(Path(sys.argv[index + 1]).resolve())
            shortcut.Arguments = (shortcut.Arguments + " " + subprocess.list2cmdline(["--config", config])).strip()
    if background:
        shortcut.Arguments = (shortcut.Arguments + " --background").strip()
    shortcut.WorkingDirectory = str(PROJECT_ROOT)
    shortcut.Description = "Zoom Recorder · 동의 확인 후 녹음"
    shortcut.Save()


def startup_path() -> Path:
    from win32com.client import Dispatch
    return Path(Dispatch("WScript.Shell").SpecialFolders("Startup")) / "Zoom Recorder Agent.lnk"


def startup_enabled() -> bool:
    from win32com.client import Dispatch
    path = startup_path()
    if not path.exists():
        return False
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(path))
    return Path(shortcut.WorkingDirectory).resolve() == PROJECT_ROOT


def set_startup(enabled: bool):
    path = startup_path()
    if path.exists() and not startup_enabled():
        raise RuntimeError("다른 설치 폴더의 시작 바로가기가 있습니다. 기존 바로가기를 먼저 확인하세요.")
    if enabled:
        create_shortcut(path, background=True)
    elif path.exists():
        path.unlink()


def migrate_startup() -> bool:
    """Upgrade only this installation's existing, uncustomized startup shortcut."""
    from win32com.client import Dispatch
    if not startup_enabled():
        return False
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(startup_path()))
    frozen = getattr(sys, "frozen", False)
    expected_target = launcher_executable()
    if (Path(shortcut.TargetPath).resolve() != expected_target.resolve()
            or shortcut.Arguments.strip() != ("" if frozen else "-m app.main")):
        return False
    shortcut.Arguments = (shortcut.Arguments + " --background").strip()
    shortcut.Save()
    return True
