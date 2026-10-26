import sys

import pytest
from win32com.client import Dispatch

from app import startup


@pytest.mark.parametrize("frozen", [False, True])
def test_startup_shortcut_migration_preserves_manual_launcher(tmp_path, monkeypatch, frozen):
    # Real Windows .lnk files, isolated from the user's Startup folder.
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(startup, "PROJECT_ROOT", tmp_path)
    executable = tmp_path / ("ZoomRecorder.exe" if frozen else ".venv/Scripts/pythonw.exe")
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    if frozen:
        monkeypatch.setattr(sys, "executable", str(executable))
    login = tmp_path / "login.lnk"
    manual = tmp_path / "manual.lnk"
    monkeypatch.setattr(startup, "startup_path", lambda: login)
    read = lambda path: Dispatch("WScript.Shell").CreateShortcut(str(path))
    assert not startup.migrate_startup() and not login.exists()
    startup.create_shortcut(manual)
    startup.create_shortcut(login)
    assert startup.migrate_startup()
    assert "--background" in read(login).Arguments
    assert "--background" not in read(manual).Arguments
    assert not startup.migrate_startup()
    startup.set_startup(False)
    assert not login.exists()
    startup.set_startup(True)
    assert "--background" in read(login).Arguments
    # A customized launch command must not be rewritten.
    shortcut = read(login)
    shortcut.Arguments = "--config custom.yaml"
    shortcut.Save()
    assert not startup.migrate_startup()
    assert read(login).Arguments == "--config custom.yaml"
    # Foreign installations remain untouched.
    shortcut.WorkingDirectory = str(tmp_path.parent)
    shortcut.Save()
    assert not startup.migrate_startup()
    with pytest.raises(RuntimeError):
        startup.set_startup(True)
