from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ReminderPopup(QWidget):
    """Silent notification visible even while the main window is in the tray."""
    def __init__(self, window):
        super().__init__(window, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.window = window
        self.setWindowTitle("녹음 종료 확인")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(430)
        layout = QVBoxLayout(self)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        for title, callback in (("녹음 중지", window.stop_recording),
                                ("5분 뒤 알림", window.keep_recording),
                                ("이번 녹음 알림 끄기", window.disable_reminder)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def notify(self, text):
        self.message.setText(text)
        self.adjustSize()
        screen = self.window.screen() or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 20, area.bottom() - self.height() - 20)
        self.show()

    def closeEvent(self, event):
        self.window.keep_recording()
        event.accept()
