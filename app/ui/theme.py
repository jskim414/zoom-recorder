"""Shared native Qt styling; no web runtime or animation loop."""
STYLE = """
QMainWindow, QDialog { background: #f4f6fa; }
QWidget { font-family: 'Malgun Gothic'; font-size: 13px; color: #25334a; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #52718b; font-size: 11px; font-weight: 700; }
QLabel#title { color: #18283f; font-size: 26px; font-weight: 700; }
QLabel#muted { color: #68798e; font-size: 12px; }
QLabel#badge { color: #315c9c; background: #e7eefb; border-radius: 8px; padding: 7px 12px; }
QWidget#card { background: white; border: 1px solid #e0e6ef; border-radius: 16px; }
QLabel#timer { color: #18283f; font-family: 'Segoe UI'; font-size: 44px; font-weight: 600; }
QPushButton { background: white; border: 1px solid #d4ddea; border-radius: 8px; padding: 10px 16px; }
QPushButton:hover { background: #edf3ff; border-color: #a7bde5; }
QPushButton:focus { border: 2px solid #7198e5; }
QPushButton:disabled { color: #94a0b2; background: #edf0f5; border-color: #e1e6ef; }
QPushButton#primary { background: #285ed5; color: white; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #1e4fbe; }
QPushButton#primary:disabled { background: #dce5f5; color: #8594ae; }
QPushButton#stop { background: #c43e50; color: white; border: none; font-weight: 600; }
QPushButton#stop:hover { background: #a72e3e; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #d4ddea; border-radius: 6px; padding: 8px; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #94a0b2; background: #edf0f5; }
QCheckBox { spacing: 8px; padding: 4px 0; }
QTabWidget::pane { background: white; border: 1px solid #e0e6ef; border-radius: 8px; }
QTabBar::tab { padding: 12px 16px; color: #6b7a90; background: transparent; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #285ed5; border-bottom: 2px solid #285ed5; }
QToolTip { color: #25334a; background: white; border: 1px solid #d4ddea; padding: 5px; }
"""
