"""Audio naming and integrity checks without encoder or transcription imports."""
import hashlib
from pathlib import Path
import re

from app.storage.session_store import safe_child

NAMES = {"remote": "PC음성.mp3", "mic": "마이크음성.mp3", "mixed": "합본.mp3"}
_WAV_NAME = re.compile(r"audio/(remote|mic)_[0-9]{4,}\.wav")
_EXPORT_NAMES = frozenset(NAMES.values()) | {name + ".part" for name in NAMES.values()}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def is_temporary_wav(name: str) -> bool:
    return _WAV_NAME.fullmatch(name) is not None


def owned_audio_path(directory: Path, name: str) -> Path:
    """Constrain a manifest entry; callers must separately prove file ownership."""
    wav = is_temporary_wav(name)
    if not wav and name not in _EXPORT_NAMES:
        raise ValueError("Refusing to delete audio outside the generated filename allowlist")
    lexical = directory / name
    path = safe_child(directory, name)
    expected_parent = directory / "audio" if wav else directory
    redirected = any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
                     for p in (lexical, expected_parent))
    if redirected or path.parent != expected_parent.resolve():
        raise ValueError("Refusing redirected audio deletion")
    return path
