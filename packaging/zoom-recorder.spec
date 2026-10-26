import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent
local = os.environ.get("ZOOM_BUILD_FLAVOR", "lite") == "local"
name = "ZoomRecorder-" + ("Local" if local else "Lite")
datas = []
binaries = []
hidden = ["win32timezone", "win32com.client", "comtypes", "pycaw.pycaw"]
hidden += collect_submodules("comtypes", filter=lambda name: not name.startswith("comtypes.test"))
excluded = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
            "tkinter", "pytest", "torch", "scipy", "matplotlib", "IPython"]
if local:
    for package in ("faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "av"):
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hidden += h
else:
    excluded += ["faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "av", "huggingface_hub"]
a = Analysis([str(root / "scripts/package_entry.py")], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hidden, excludes=excluded,
             hookspath=[str(root / "packaging/hooks")])
pyz = PYZ(a.pure)
# Hidden console bootloader keeps stdin pipes available to the transcription child.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=True,
          hide_console="hide-early", debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)
