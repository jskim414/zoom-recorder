import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.settings import Settings, save_settings


@pytest.mark.parametrize("background", [False, True])
def test_relaunch_waits_for_existing_window_to_finish_starting(tmp_path, background):
    """Simulate a window that owns the lock before its local pipe is ready."""
    root = tmp_path / "recordings"
    config = tmp_path / "settings.yaml"
    save_settings(Settings(recordings_dir=str(root), auto_detect=False), config)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    code = '''
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QTimer
from app.storage.instance_lock import InstanceLock
from app.ui.single_instance import listen_for_launch
app = QApplication([])
root = Path(sys.argv[1])
class Window(QWidget):
    def reveal(self):
        (root.parent / "revealed").write_text("yes")
        QTimer.singleShot(200, app.quit)
window = Window()
with InstanceLock(root):
    (root.parent / "locked").write_text("yes")
    def listen():
        window.server = listen_for_launch(root, window)
    QTimer.singleShot(1000, listen)
    QTimer.singleShot(2500 if sys.argv[2] == "background" else 8000, app.quit)
    app.exec()
'''
    provider = subprocess.Popen([sys.executable, "-c", code, str(root), "background" if background else "normal"], env=env,
                                cwd=Path(__file__).resolve().parents[1],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "locked").exists():
            assert provider.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        client = subprocess.run([sys.executable, "-m", "app.main", "--config", str(config)] +
                                (["--background"] if background else []),
                                env=env, cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, timeout=8)
        assert client.returncode == 0, client.stderr
        provider.communicate(timeout=5)
        assert (tmp_path / "revealed").exists() is not background
        assert not list(root.glob("*/audio"))
    finally:
        if provider.poll() is None:
            provider.kill()
        provider.communicate(timeout=5)


@pytest.mark.parametrize("background", [False, True])
def test_main_launch_visibility_without_capture(tmp_path, background):
    config = tmp_path / "settings.yaml"
    root = tmp_path / "recordings"
    save_settings(Settings(recordings_dir=str(root), auto_detect=False), config)
    code = '''
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
import app.startup
import app.ui.tray
from app.main import main
app.startup.migrate_startup = lambda: False
def unexpected_dialog(*args):
    raise AssertionError("Unexpected startup popup")
QMessageBox.warning = unexpected_dialog
Original = app.ui.tray.RecorderWindow
class Probe(Original):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        QTimer.singleShot(200, self.check)
    def check(self):
        Path(sys.argv[1]).write_text(str(self.isVisible()) + ':' + str(self.controller.recorder is None))
        self.quit()
app.ui.tray.RecorderWindow = Probe
raise SystemExit(main(sys.argv[2:]))
'''
    report = tmp_path / "visibility"
    result = subprocess.run([sys.executable, "-c", code, str(report), "--config", str(config)] +
                            (["--background"] if background else []),
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert report.read_text() == f"{not background}:True"
    assert not list(root.glob("*/audio"))


def test_background_startup_failure_has_log_and_no_popup(tmp_path):
    config = tmp_path / "invalid.yaml"
    config.write_text("cpu_threads: 0\n")
    code = '''
import sys
from pathlib import Path
from PySide6.QtWidgets import QMessageBox
import app.paths
from app.main import main
app.paths.logs_root = lambda: Path(sys.argv[1])
def unexpected_dialog(*args):
    raise AssertionError("Unexpected startup popup")
QMessageBox.warning = unexpected_dialog
raise SystemExit(main(["--background", "--config", sys.argv[2]]))
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(config)],
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), capture_output=True, timeout=10)
    assert result.returncode == 1
    assert (tmp_path / "startup-error.log").read_text(encoding="utf-8")
