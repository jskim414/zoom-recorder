from dataclasses import asdict, dataclass, field, fields
import os
from pathlib import Path
import re


@dataclass
class Settings:
    recordings_dir: str = "recordings"
    previous_recordings_dirs: list[str] = field(default_factory=list)
    record_pc: bool = True
    record_mic: bool = True
    create_mix: bool = True
    convert_to_mp3: bool = True
    delete_audio_after_transcription: bool = False
    segment_seconds: int = 120
    stable_seconds: float = 7.0
    end_grace_seconds: float = 30.0
    cooldown_seconds: float = 30.0
    microphone_index: int | None = None
    bridge_port: int = 17843
    extension_ids: list[str] = field(default_factory=list)
    model: str = "models/small"
    device: str = "cpu"
    language: str | None = None
    auto_transcribe: bool = False
    transcription_backend: str = "local"
    cpu_threads: int = 2
    beam_size: int = 1
    auto_detect: bool = True
    silence_seconds: int = 120
    reminder_foreground: bool = False
    long_recording_minutes: int = 60
    retention_days: int | None = None
    zoom_process_names: list[str] = field(default_factory=lambda: ["zoom.exe", "zoom", "zoomworkplace.exe"])
    meeting_title_patterns: list[str] = field(default_factory=lambda: [r"zoom meeting", r"zoom 회의", r"zoom 미팅", r"zoom workplace.*meeting"])

    def validate(self):
        for name in ("record_pc", "record_mic", "create_mix", "convert_to_mp3", "delete_audio_after_transcription"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if not (self.record_pc or self.record_mic):
            raise ValueError("PC 소리 또는 마이크를 하나 이상 선택하세요.")
        if (not isinstance(self.previous_recordings_dirs, list)
                or any(not isinstance(p, str) or not p.strip() for p in self.previous_recordings_dirs)):
            raise ValueError("Invalid previous recordings directories")
        if type(self.silence_seconds) is not int or self.silence_seconds not in {0, 120, 300, 600}:
            raise ValueError("silence_seconds must be 0, 120, 300 or 600")
        if type(self.long_recording_minutes) is not int or self.long_recording_minutes not in {0, 30, 60, 120}:
            raise ValueError("long_recording_minutes must be 0, 30, 60 or 120")
        if self.transcription_backend not in {"local", "openai"}:
            raise ValueError("Invalid transcription backend")
        if type(self.cpu_threads) is not int or not 1 <= self.cpu_threads <= 16:
            raise ValueError("cpu_threads must be 1..16")
        if type(self.beam_size) is not int or self.beam_size not in {1, 5}:
            raise ValueError("beam_size must be 1 or 5")
        for name in ("recordings_dir", "model"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a nonempty path")
        for name in ("segment_seconds", "bridge_port"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        for name in ("stable_seconds", "end_grace_seconds", "cooldown_seconds"):
            if type(getattr(self, name)) not in (int, float):
                raise ValueError(f"{name} must be numeric")
        if self.microphone_index is not None and (type(self.microphone_index) is not int or self.microphone_index < 0):
            raise ValueError("microphone_index must be null or a nonnegative integer")
        for name in ("zoom_process_names", "meeting_title_patterns"):
            value = getattr(self, name)
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
                raise ValueError(f"{name} must be a nonempty string list")
        if not 60 <= self.segment_seconds <= 300:
            raise ValueError("segment_seconds must be 60..300")
        if not 5 <= self.stable_seconds <= 10 or self.end_grace_seconds < 15 or self.cooldown_seconds < 0:
            raise ValueError("Invalid detection timing")
        if not 1024 <= self.bridge_port <= 65535:
            raise ValueError("Invalid bridge_port")
        if not isinstance(self.extension_ids, list) or any(not isinstance(x, str) or not re.fullmatch(r"[a-p]{32}", x) for x in self.extension_ids):
            raise ValueError("extension_ids must contain Chrome/Edge extension IDs")
        if self.device not in {"cpu", "cuda"} or self.language not in {None, "ko", "en"}:
            raise ValueError("Invalid transcription device/language")
        if self.retention_days is not None and (type(self.retention_days) is not int or self.retention_days < 1):
            raise ValueError("retention_days must be null or a positive integer")
        for name in ("auto_transcribe", "auto_detect", "reminder_foreground"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for pattern in self.meeting_title_patterns:
            re.compile(pattern)
        return self

    @property
    def selected_tracks(self):
        return tuple(name for name, enabled in (("remote", self.record_pc), ("mic", self.record_mic)) if enabled)

    @property
    def recording_roots(self):
        return list(dict.fromkeys(Path(p).resolve() for p in [self.recordings_dir, *self.previous_recordings_dirs]))


def load_settings(path: str | Path | None = None) -> Settings:
    from app.paths import default_config, resolve_settings_paths
    path = Path(path or default_config())
    if not path.exists():
        return resolve_settings_paths(Settings()).validate()
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or set(data) - {f.name for f in fields(Settings)}:
        raise ValueError("Unknown settings fields")
    return resolve_settings_paths(Settings(**data)).validate()


def save_settings(settings: Settings, path: str | Path):
    import yaml
    settings.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(asdict(settings), stream, allow_unicode=True, sort_keys=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
