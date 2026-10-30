"""Post-recording processing order, independent of child-process transport."""
from app.storage.policy import StoragePolicy, audio_deletion_started


def run_pipeline(store, settings, *, transcribe=True, api_approved=False):
    # Keep native engines out of the idle application's import graph.
    from app.processing.archive import create_archive, cleanup_wav
    from app.processing.audio_cleanup import delete_transcribed_audio

    if audio_deletion_started(store.metadata):
        if transcribe:
            raise ValueError("Audio deletion started; use storage cleanup retry instead of transcription")
        delete_transcribed_audio(store)
        store.update(state="COMPLETE", processing_error=None)
        return
    if transcribe and settings.transcription_backend == "openai" and api_approved is not True:
        raise PermissionError("API upload requires per-session approval")

    policy = StoragePolicy.from_metadata(store.metadata)
    completed = store.metadata.get("transcription", {}).get("status") == "complete"
    delete_after = policy.delete_after(transcribe=transcribe, transcription_complete=completed)
    if policy.export_before(transcribe=transcribe, transcription_complete=completed):
        create_archive(store)
    if transcribe:
        from app.processing.transcribe import Transcriber
        Transcriber(settings).run(store, api_approved=api_approved)
    elif not completed:
        # Do not erase a completed transcript's proof on a cleanup retry.
        store.update(transcription={"status": "disabled"})

    if policy.convert_to_mp3 and not delete_after:
        cleanup_wav(store)
    if policy.delete_audio_after_transcription:
        # With no transcript yet, this records a waiting status and preserves audio.
        delete_transcribed_audio(store)
    store.update(state="ERROR" if store.metadata["errors"] else "COMPLETE", processing_error=None)
