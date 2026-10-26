from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import wave


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def safe_child(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Path escapes session directory")
    return path


NEW_SESSION_NAME = re.compile(r"([0-9]{8})_([0-9]{2,})_([^<>:\"/\\|?*\x00-\x1f]*)")
SESSION_NAME = re.compile(
    r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}_[a-f0-9]{32}|"
    r"[0-9]{8}_[0-9]{2,}_[^<>:\"/\\|?*\x00-\x1f]*)(?<![ .])"
)


def validate_folder_name(name: str) -> str:
    match = NEW_SESSION_NAME.fullmatch(name)
    if not match or name.endswith((" ", ".")):
        raise ValueError('폴더명은 YYYYMMDD_nn_회의명 형식이며, \\ / : * ? " < > | 및 끝 공백·점은 사용할 수 없습니다.')
    if len(name.encode("utf-16-le")) // 2 > 120:
        raise ValueError("폴더명은 120자 이내로 입력하세요.")
    try:
        datetime.strptime(match[1], "%Y%m%d")
        if int(match[2]) < 1:
            raise ValueError
    except ValueError:
        raise ValueError("올바른 날짜와 01 이상의 순번을 입력하세요.") from None
    return name


def suggest_folder_name(root: Path, *, now: datetime | None = None) -> str:
    day = (now or datetime.now()).strftime("%Y%m%d")
    highest = 0
    if root.exists():
        for child in root.iterdir():
            match = NEW_SESSION_NAME.fullmatch(child.name)
            if match and match[1] == day:
                highest = max(highest, int(match[2]))
    return f"{day}_{highest + 1:02d}_"


def available_session_path(root: Path, name: str, *, current: Path | None = None) -> Path:
    validate_folder_name(name)
    root = root.resolve()
    destination = safe_child(root, name)
    if destination.parent != root:
        raise ValueError("녹음 폴더 바로 아래에 저장해야 합니다.")
    if root.exists():
        for child in root.iterdir():
            if child.name.casefold() == name.casefold() and (current is None or child != current):
                raise FileExistsError("같은 이름의 폴더가 있습니다. 다른 이름을 입력하세요.")
    return destination


class SessionStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.lock = threading.RLock()
        self.metadata = json.loads((self.directory / "metadata.json").read_text(encoding="utf-8"))

    @classmethod
    def create(cls, root: Path, source: str, consent_at: str, folder_name: str | None = None) -> SessionStore:
        if source not in {"manual", "desktop", "browser"} or not consent_at:
            raise ValueError("A recording source and consent timestamp are required")
        session_id = uuid.uuid4().hex
        directory = available_session_path(root, folder_name if folder_name is not None else suggest_folder_name(root))
        root.mkdir(parents=True, exist_ok=True)
        directory.mkdir(exist_ok=False)
        (directory / "audio").mkdir()
        atomic_json(directory / "metadata.json", {
            "schema_version": 1, "session_id": session_id, "source": source,
            "consent_at": consent_at, "started_at": utc_now(), "ended_at": None,
            "monotonic_start": time.monotonic(), "state": "RECORDING",
            "capture_scope": "system_output_all_apps_and_selected_microphone",
            "tracks": {}, "segments": [], "errors": [], "gaps": [],
            "transcription": {"status": "pending"},
        })
        store = cls(directory)
        store.log("consent_confirmed")
        return store

    def rename(self, name: str) -> None:
        """Called only after capture has closed all writers, before a worker starts."""
        with self.lock:
            if self.metadata["state"] not in {"FINALIZING", "COMPLETE", "ERROR", "RECOVERY_REQUIRED"}:
                raise RuntimeError("녹음 파일을 모두 닫은 뒤 폴더명을 변경할 수 있습니다.")
            destination = available_session_path(self.directory.parent, name, current=self.directory)
            if destination.name == self.directory.name:
                return
            # Windows rename refuses an existing destination; never merge or overwrite.
            self.directory.rename(destination)
            self.directory = destination

    def update(self, **values):
        with self.lock:
            self.metadata.update(values)
            atomic_json(self.directory / "metadata.json", self.metadata)

    def append(self, key: str, value: dict):
        with self.lock:
            self.metadata[key].append(value)
            atomic_json(self.directory / "metadata.json", self.metadata)

    def track(self, name: str, **values):
        with self.lock:
            self.metadata["tracks"].setdefault(name, {}).update(values)
            atomic_json(self.directory / "metadata.json", self.metadata)

    def log(self, event: str, **fields):
        # Callers supply fixed event codes, never exceptions/URLs/transcript text/tokens.
        with self.lock, (self.directory / "logs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"wall_clock": utc_now(), "monotonic": time.monotonic(),
                                "event": event, **fields}, ensure_ascii=False) + "\n")
            f.flush()

    def error(self, code: str, track: str | None = None):
        item = {"code": code, "track": track, "wall_clock": utc_now(),
                "offset": max(0, time.monotonic() - self.metadata["monotonic_start"])}
        self.append("errors", item)
        self.log("capture_error", code=code, track=track)


def has_unindexed_audio(directory: Path, metadata: dict) -> bool:
    indexed = {item["file"] for item in metadata["segments"]}
    recovered_originals = {item.get("recovered_from") for item in metadata["segments"]}
    for partial in (directory / "audio").glob("*.part"):
        if partial.relative_to(directory).as_posix() not in recovered_originals:
            return True
    for sidecar in (directory / "audio").glob("*.wav.json"):
        final = sidecar.with_suffix("")
        partial = final.with_suffix(".wav.part")
        if (final.relative_to(directory).as_posix() not in indexed
                and partial.relative_to(directory).as_posix() not in recovered_originals):
            return True
    return False


def find_recovery(root: Path) -> list[Path]:
    result = []
    if not root.exists():
        return result
    for directory in root.iterdir():
        if not SESSION_NAME.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            store = SessionStore(directory)
            if (store.metadata["state"] not in {"COMPLETE", "ERROR"}
                    or has_unindexed_audio(directory, store.metadata)):
                store.update(state="RECOVERY_REQUIRED")
                result.append(directory)
        except (OSError, ValueError, KeyError):
            result.append(directory)
    return result


def recover_session(directory: Path) -> dict:
    """Preserve originals; recover PCM16 .part files into new WAVs. Never record."""
    store = SessionStore(directory)
    recovered = []
    with store.lock:
        indexed = {item["file"] for item in store.metadata["segments"]}
        for sidecar in (directory / "audio").glob("*.wav.json"):
            try:
                safe_child(directory, sidecar.relative_to(directory).as_posix())
                info = json.loads(sidecar.read_text(encoding="utf-8"))
                final = sidecar.with_suffix("")
                if final.exists():
                    safe_child(directory, final.relative_to(directory).as_posix())
                    relative = final.relative_to(directory).as_posix()
                    if relative not in indexed:
                        with wave.open(str(final), "rb") as f:
                            frames = f.getnframes()
                        info.update(file=relative, frames=frames, duration=frames / info["rate"], recovered=True)
                        store.append("segments", info)
                        indexed.add(relative)
                        recovered.append(relative)
                    continue
                partial = final.with_suffix(".wav.part")
                safe_child(directory, partial.relative_to(directory).as_posix())
                info["recovered_from"] = partial.relative_to(directory).as_posix()
                # Our writer emits a standard 44-byte PCM16 WAV header.
                with partial.open("rb") as f:
                    header = f.read(44)
                    if header[:4] != b"RIFF" or header[8:16] != b"WAVEfmt " or header[36:40] != b"data":
                        raise ValueError("Unsupported WAV header")
                    destination = partial.with_name(partial.name.removesuffix(".wav.part") + "_recovered.wav")
                    relative = destination.relative_to(directory).as_posix()
                    if relative in indexed:
                        continue
                    if destination.exists():
                        # A previous recovery may have finished its WAV before metadata was committed.
                        with wave.open(str(destination), "rb") as out:
                            frames = out.getnframes()
                        info.update(file=relative, frames=frames, duration=frames / info["rate"], recovered=True)
                        store.append("segments", info)
                        indexed.add(relative)
                        recovered.append(relative)
                        continue
                    frame_size = info["channels"] * 2
                    frames = 0
                    with wave.open(str(destination), "wb") as out:
                        out.setnchannels(info["channels"])
                        out.setsampwidth(2)
                        out.setframerate(info["rate"])
                        while block := f.read(frame_size * 65536):
                            block = block[:len(block) // frame_size * frame_size]
                            out.writeframes(block)
                            frames += len(block) // frame_size
                info.update(file=destination.relative_to(directory).as_posix(), frames=frames,
                            duration=frames / info["rate"], recovered=True)
                store.append("segments", info)
                indexed.add(info["file"])
                recovered.append(info["file"])
            except (OSError, ValueError, KeyError, wave.Error):
                store.error("recovery_failed")
        for partial in (directory / "audio").glob("*.part"):
            if not partial.with_suffix(".json").exists():
                store.error("recovery_sidecar_missing")
        # Validate completed files without altering them.
        for item in store.metadata["segments"]:
            try:
                with wave.open(str(safe_child(directory, item["file"])), "rb") as f:
                    expected = item["frames"] * f.getnchannels() * f.getsampwidth()
                    actual = 0
                    while block := f.readframes(65536):
                        actual += len(block)
                    if actual != expected:
                        raise ValueError("Truncated WAV")
            except (OSError, ValueError, KeyError, wave.Error, EOFError):
                store.error("segment_invalid")
        store.update(state="RECOVERY_REQUIRED", recovered_at=utc_now())
        store.log("recovery_finished", recovered_count=len(recovered))
    return {"recovered": recovered, "errors": store.metadata["errors"]}


def apply_retention(root: Path, days: int | None) -> list[str]:
    if days is None or not root.exists():
        return []
    if type(days) is not int or days < 1:
        raise ValueError("Invalid retention period")
    removed = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for directory in root.iterdir():
        if not SESSION_NAME.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            if metadata["state"] == "COMPLETE" and datetime.fromisoformat(metadata["ended_at"]) < cutoff:
                resolved = directory.resolve()
                if resolved.parent != root.resolve():
                    continue
                shutil.rmtree(resolved)
                removed.append(directory.name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return removed
