"""Remove only a session's owned audio after a durable, nonempty transcription.

The deletion manifest is committed before unlinking. A restart can finish the
same manifest without recording, retranscribing, or widening the deletion set.
"""
import re

from app.processing.archive import NAMES, digest
from app.storage.session_store import safe_child, has_unindexed_audio, utc_now


def _owned_path(store, name):
    wav = re.fullmatch(r"audio/(remote|mic)_[0-9]{4,}\.wav", name)
    mp3 = name in NAMES.values() or name in {n + ".part" for n in NAMES.values()}
    if not (wav or mp3):
        raise ValueError("Refusing to delete audio outside the generated filename allowlist")
    lexical = store.directory / name
    path = safe_child(store.directory, name)
    expected_parent = store.directory / "audio" if wav else store.directory
    redirected = any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
                     for p in (lexical, expected_parent))
    if redirected or path.parent != expected_parent.resolve():
        raise ValueError("Refusing redirected audio deletion")
    return path


def _manifest(store, transcription):
    candidates = {}
    selected = set(store.metadata.get("selected_tracks", []))
    if not selected or selected != {i["track"] for i in store.metadata["segments"]}:
        raise ValueError("Selected source tracks are incomplete; preserving audio")
    fingerprints = transcription.get("audio_fingerprints", {})
    if not fingerprints:
        raise ValueError("No verified transcription inputs")
    for name, checksum in fingerprints.items():
        path = _owned_path(store, name)
        if not path.is_file() or digest(path) != checksum:
            raise ValueError("Transcribed audio changed or disappeared")
        candidates[name] = checksum
    archive = store.metadata.get("archive", {})
    for item in archive.get("original_segments", []):
        path = _owned_path(store, item["file"])
        if path.exists():
            checksum = item["source_sha256"]
            if digest(path) != checksum:
                raise ValueError("Original WAV changed since export")
            candidates[item["file"]] = checksum
    for item in archive.get("files", []):
        if item["file"] != NAMES.get(item["track"]):
            raise ValueError("Invalid archive file")
        path = _owned_path(store, item["file"])
        if path.exists():
            if digest(path) != item["sha256"]:
                raise ValueError("Export changed; preserve user edits")
            candidates[item["file"]] = item["sha256"]
    # Export ownership is persisted before writing. Include abandoned app-created
    # compressed outputs/partials, but never scan or recursively delete a folder.
    for name in archive.get("owned_files", []):
        if name not in NAMES.values():
            raise ValueError("Invalid export ownership manifest")
        for filename in (name, name + ".part"):
            path = _owned_path(store, filename)
            if path.exists() and filename not in candidates:
                candidates[filename] = digest(path)
    return [{"file": name, "sha256": checksum} for name, checksum in sorted(candidates.items())]


def delete_transcribed_audio(store):
    policy = store.metadata.get("storage_policy", {})
    if policy.get("delete_audio_after_transcription") is not True:
        return
    cleanup = store.metadata.get("audio_cleanup", {})
    if cleanup.get("status") == "complete":
        return
    transcription = store.metadata.get("transcription", {})
    transcript = safe_child(store.directory, "transcript.md")
    reason = None
    if store.metadata.get("errors") or store.metadata.get("gaps") or has_unindexed_audio(store.directory, store.metadata):
        reason = "capture_or_recovery_requires_review"
    elif transcription.get("status") != "complete":
        reason = "transcription_incomplete"
    elif not transcription.get("segment_count", 0):
        reason = "no_recognized_speech"
    elif (not transcript.is_file() or not transcription.get("sha256")
          or digest(transcript) != transcription["sha256"]):
        reason = "transcript_not_verified"
    if reason:
        # Never discard a partially executed deletion manifest.
        if cleanup.get("status") == "deleting":
            raise ValueError("Cannot resume audio cleanup without a verified transcript")
        store.update(audio_cleanup={"status": "waiting" if reason == "transcription_incomplete" else "retained",
                                    "reason": reason})
        return
    if cleanup.get("status") == "deleting":
        files = cleanup["files"]
        if cleanup["transcript_sha256"] != transcription["sha256"]:
            raise ValueError("Transcript changed during interrupted cleanup")
    else:
        files = _manifest(store, transcription)
        # All remaining indexed sources must be covered by the verified manifest.
        if not {i["file"] for i in store.metadata["segments"]} <= {i["file"] for i in files}:
            raise ValueError("Unverified audio sources remain")
        cleanup = {"status": "deleting", "files": files, "transcript_sha256": transcription["sha256"],
                   "started_at": utc_now()}
    paths = []
    for item in files:
        path = _owned_path(store, item["file"])
        if path.exists() and digest(path) != item["sha256"]:
            raise ValueError("Audio changed during interrupted cleanup")
        paths.append(path)
    store.update(segments=[], audio_cleanup=cleanup)
    for path in paths:
        path.unlink(missing_ok=True)
    archive = store.metadata.get("archive", {})
    store.update(audio_cleanup={**cleanup, "status": "complete", "completed_at": utc_now()},
                 archive={**archive, "status": "deleted", "cleanup": "complete"})
    store.log("transcribed_audio_removed", count=len(paths))
