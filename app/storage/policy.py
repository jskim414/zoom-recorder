"""Immutable per-recording policy; legacy metadata never opts into deletion."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.settings import Settings


@dataclass(frozen=True)
class StoragePolicy:
    temporary_wav: bool = False
    create_mix: bool = False
    convert_to_mp3: bool = True
    delete_audio_after_transcription: bool = False
    transcribe_required: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> StoragePolicy:
        return cls(temporary_wav=True, create_mix=settings.create_mix,
                   convert_to_mp3=settings.convert_to_mp3,
                   delete_audio_after_transcription=settings.delete_audio_after_transcription,
                   transcribe_required=settings.auto_transcribe)

    @classmethod
    def from_metadata(cls, metadata: dict) -> StoragePolicy:
        policy = metadata.get("storage_policy", {})
        return cls(temporary_wav=bool(policy.get("temporary_wav")),
                   create_mix=bool(policy.get("create_mix")),
                   convert_to_mp3=bool(policy.get("convert_to_mp3", True)),
                   delete_audio_after_transcription=policy.get("delete_audio_after_transcription") is True,
                   transcribe_required=bool(policy.get("transcribe_required")))

    def to_metadata(self) -> dict[str, bool]:
        return asdict(self)

    def delete_after(self, *, transcribe: bool, transcription_complete: bool = False) -> bool:
        """Plan cleanup; file and transcript verification still gate actual deletion."""
        return self.delete_audio_after_transcription and (transcribe or transcription_complete)

    def export_before(self, *, transcribe: bool, transcription_complete: bool = False) -> bool:
        return self.convert_to_mp3 and not self.delete_after(
            transcribe=transcribe, transcription_complete=transcription_complete)


def audio_deletion_started(metadata: dict) -> bool:
    return metadata.get("audio_cleanup", {}).get("status") in {"deleting", "complete"}
