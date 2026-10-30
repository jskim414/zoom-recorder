"""User-selected storage locations. Never move existing sessions."""
from pathlib import Path
import tempfile


def check_recording_root(value):
    root = Path(value).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # A real write probe catches read-only/network/disconnected storage errors.
    with tempfile.TemporaryFile(dir=root) as probe:
        probe.write(b"recording-location-check")
        probe.flush()
    return root
