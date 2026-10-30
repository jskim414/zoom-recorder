"""Remove only a session's owned audio after a durable, nonempty transcription.

The deletion manifest is committed before unlinking. A restart can finish the
same manifest without recording, retranscribing, or widening the deletion set.
"""
from app.storage.audio_files import NAMES, digest, owned_audio_path
from app.storage.policy import StoragePolicy
from app.storage.session_store import safe_child, has_unindexed_audio, utc_now


def _manifest(store, transcription):
    candidates = {}
    selected = set(store.metadata.get("selected_tracks", []))
    if not selected or selected != {i["track"] for i in store.metadata["segments"]}:
        raise ValueError("Selected source tracks are incomplete; preserving audio")
    fingerprints = transcription.get("audio_fingerprints", {})
    if not fingerprints:
        raise ValueError("No verified transcription inputs")
    for name, checksum in fingerprints.items():
        path = owned_audio_path(store.directory, name)
        if not path.is_file() or digest(path) != checksum:
            raise ValueError("Transcribed audio changed or disappeared")
        candidates[name] = checksum
    archive = store.metadata.get("archive", {})
    for item in archive.get("original_segments", []):
        path = owned_audio_path(store.directory, item["file"])
        if path.exists():
            checksum = item["source_sha256"]
            if digest(path) != checksum:
                raise ValueError("Original WAV changed since export")
            candidates[item["file"]] = checksum
    for item in archive.get("files", []):
        if item["file"] != NAMES.get(item["track"]):
            raise ValueError("Invalid archive file")
        path = owned_audio_path(store.directory, item["file"])
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
            path = owned_audio_path(store.directory, filename)
            if path.exists() and filename not in candidates:
                candidates[filename] = digest(path)
    return [{"file": name, "sha256": checksum} for name, checksum in sorted(candidates.items())]


def _retention_reason(store) -> str | None:
    transcription = store.metadata.get("transcription", {})
    transcript = safe_child(store.directory, "transcript.md")
    if store.metadata.get("errors") or store.metadata.get("gaps") or has_unindexed_audio(store.directory, store.metadata):
        return "capture_or_recovery_requires_review"
    if transcription.get("status") != "complete":
        return "transcription_incomplete"
    if not transcription.get("segment_count", 0):
        return "no_recognized_speech"
    if (not transcript.is_file() or not transcription.get("sha256")
          or digest(transcript) != transcription["sha256"]):
        return "transcript_not_verified"
    return None


def _prepare_cleanup(store, cleanup):
    transcription = store.metadata["transcription"]
    if cleanup.get("status") == "deleting":
        if cleanup["transcript_sha256"] != transcription["sha256"]:
            raise ValueError("Transcript changed during interrupted cleanup")
        return cleanup
    files = _manifest(store, transcription)
    # All remaining indexed sources must be covered by the verified manifest.
    if not {i["file"] for i in store.metadata["segments"]} <= {i["file"] for i in files}:
        raise ValueError("Unverified audio sources remain")
    return {"status": "deleting", "files": files, "transcript_sha256": transcription["sha256"],
            "started_at": utc_now()}


def _validated_paths(store, files):
    """Validate the whole manifest before any metadata change or unlink."""
    paths = []
    for item in files:
        path = owned_audio_path(store.directory, item["file"])
        if path.exists() and digest(path) != item["sha256"]:
            raise ValueError("Audio changed during interrupted cleanup")
        paths.append(path)
    return paths


def delete_transcribed_audio(store):
    if not StoragePolicy.from_metadata(store.metadata).delete_audio_after_transcription:
        return
    cleanup = store.metadata.get("audio_cleanup", {})
    if cleanup.get("status") == "complete":
        return
    reason = _retention_reason(store)
    if reason:
        # Never discard a partially executed deletion manifest.
        if cleanup.get("status") == "deleting":
            raise ValueError("Cannot resume audio cleanup without a verified transcript")
        store.update(audio_cleanup={"status": "waiting" if reason == "transcription_incomplete" else "retained",
                                    "reason": reason})
        return
    cleanup = _prepare_cleanup(store, cleanup)
    paths = _validated_paths(store, cleanup["files"])
    store.update(segments=[], audio_cleanup=cleanup)
    for path in paths:
        path.unlink(missing_ok=True)
    archive = store.metadata.get("archive", {})
    store.update(audio_cleanup={**cleanup, "status": "complete", "completed_at": utc_now()},
                 archive={**archive, "status": "deleted", "cleanup": "complete"})
    store.log("transcribed_audio_removed", count=len(paths))
