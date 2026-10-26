"""Render only the app's own offscreen widgets, with no desktop or audio capture."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import tempfile

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from app.settings import Settings
from app.ui.tray import RecorderWindow
from app.paths import artifacts_root
from app.storage.session_store import SessionStore


def main():
    destination = artifacts_root()
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    # Qt's offscreen Windows platform starts with an empty system font database.
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
    QFontDatabase.addApplicationFont(str(font))
    with tempfile.TemporaryDirectory(prefix="zoom-ui-preview-") as temporary:
        for index, name in enumerate(("기획 회의", "주간 회의"), 1):
            store = SessionStore.create(Path(temporary), "manual", "synthetic UI preview", f"20261026_{index:02d}_{name}")
            store.update(state="COMPLETE", ended_at=store.metadata["started_at"])
            (store.directory / "transcript.md").write_text("합성 UI 미리보기", encoding="utf-8")
        window = RecorderWindow(Settings(recordings_dir=temporary), enable_detection=False)
        window.show()
        app.processEvents()
        window.grab().save(str(destination / "ui-ready.png"))
        window.manual()
        app.processEvents()
        window.grab().save(str(destination / "ui-consent.png"))
        window.skip()
        window.open_preferences()
        app.processEvents()
        window.settings_dialog.grab().save(str(destination / "ui-preferences.png"))
        from PySide6.QtWidgets import QTabWidget
        window.settings_dialog.findChild(QTabWidget).setCurrentIndex(1)
        app.processEvents()
        window.settings_dialog.grab().save(str(destination / "ui-transcription.png"))
        window.settings_dialog.device.setCurrentIndex(window.settings_dialog.device.findData("cuda"))
        app.processEvents()
        window.settings_dialog.grab().save(str(destination / "ui-transcription-gpu.png"))
        window.reminder_popup.notify("2분 동안 말소리가 감지되지 않았어요. 녹음은 계속되고 있습니다.")
        app.processEvents()
        window.reminder_popup.grab().save(str(destination / "ui-silence-reminder.png"))
        window.reminder_popup.hide()
        window.quit()
    print("Rendered app-only previews; no recording performed.")


if __name__ == "__main__":
    main()
