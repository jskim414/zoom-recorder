"""Offscreen UI/event loop smoke; never clicks consent or starts recording."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import json
from pathlib import Path
import tempfile

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

# Run as python -m scripts.smoke_ui from project root.
from app.settings import Settings
from app.ui.tray import RecorderWindow


def main():
    with tempfile.TemporaryDirectory(prefix="zoom-agent-ui-smoke-") as temporary:
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = RecorderWindow(Settings(recordings_dir=temporary, bridge_port=0, extension_ids=["a" * 32]))
        window.show()
        def finish():
            assert window.controller.recorder is None
            assert not list(Path(temporary).glob("*/audio"))
            print(json.dumps({"ui_event_loop": "passed", "recording_started": False,
                              "desktop_error": window.detector_error,
                              "bridge_bound_loopback": window.bridge.server.server_address[0] == "127.0.0.1"}))
            window.quit()
            window.detectors.thread.join(timeout=3)
        QTimer.singleShot(2500, finish)
        app.exec()


if __name__ == "__main__":
    main()
