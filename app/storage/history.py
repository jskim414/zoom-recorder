"""Read-only recent-session summaries. Never recover or mutate live metadata."""
from datetime import datetime
from pathlib import Path

from app.storage.session_store import SESSION_NAME, SessionStore


def recent_sessions(root: Path, limit=30):
    if not root.exists():
        return []
    directories = sorted((p for p in root.iterdir() if SESSION_NAME.fullmatch(p.name)
                          and p.is_dir() and not p.is_symlink()), key=lambda p: p.name, reverse=True)[:limit]
    result = []
    for directory in directories:
        try:
            metadata = SessionStore(directory).metadata
            duration = ""
            if metadata.get("ended_at"):
                seconds = max(0, int((datetime.fromisoformat(metadata["ended_at"]) -
                                     datetime.fromisoformat(metadata["started_at"])).total_seconds()))
                duration = f"{seconds // 60}분 {seconds % 60}초"
            status = metadata.get("transcription", {}).get("status")
            state = metadata.get("state")
            if state == "RECORDING":
                label = "녹음 중"
            elif state == "FINALIZING":
                label = "저장 확인 중"
            elif state == "RECOVERY_REQUIRED":
                label = "복구 확인 필요"
            elif state == "TRANSCRIBING":
                label = "전사 대기" if status == "queued" else "전사 중"
            elif status == "failed":
                label = "전사 실패"
            elif (directory / "transcript.md").is_file():
                label = "전사 완료"
            elif state == "ERROR":
                label = "오류 확인 필요"
            else:
                label = "녹음 저장됨"
            archive = metadata.get("archive", {})
            if archive.get("status") == "building":
                label = "MP3 변환 재시도 필요"
            elif archive.get("cleanup") == "retained":
                label += " · 임시 WAV 보존"
            elif archive.get("cleanup") in {"pending", "deleting"}:
                label += " · MP3 저장 마무리 필요"
            if metadata.get("errors") or metadata.get("gaps"):
                label += " · 누락/오류 확인"
            result.append((directory, duration, label, state))
        except (OSError, ValueError, KeyError, TypeError):
            result.append((directory, "", "파일 확인 필요", "ERROR"))
    return result
