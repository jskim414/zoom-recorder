"""Keep source, personal data and generated artifacts in distinct locations."""
import os
from pathlib import Path
import sys


def source_root():
    return Path(__file__).resolve().parents[1]


def workspace_root():
    root = source_root()
    if (root.parent / ".zoom-workspace").is_file():
        return root.parent
    if root.parent.name == "worktrees" and (root.parent.parent / ".zoom-workspace").is_file():
        return root.parent.parent
    return root


def data_root():
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ZoomRecorder"
    return workspace_root() / "data"


def logs_root():
    if getattr(sys, "frozen", False):
        return data_root() / "logs"
    return workspace_root() / "logs"


def artifacts_root():
    return logs_root() / "validation-artifacts"


def distribution_root():
    root = source_root()
    return workspace_root() / "releases" if workspace_root() != root else root / "dist"


def default_config():
    return data_root() / "config/settings.yaml"


def resolve_settings_paths(settings):
    for name in ("recordings_dir", "model"):
        value = Path(getattr(settings, name))
        if not value.is_absolute():
            setattr(settings, name, str(data_root() / value))
    return settings
