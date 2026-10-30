"""Bounded-memory local MP3 export; deletion is a separate verified transaction.

Imports of AV/NumPy stay inside worker functions so idle UI stays lightweight.
Only sessions explicitly marked by the new recorder may retire their WAVs.
"""
from contextlib import ExitStack
import hashlib
import math
import os
from pathlib import Path
import re

from app.storage.session_store import safe_child, utc_now, has_unindexed_audio

RATE = 48000
BLOCK = 48000
NAMES = {"remote": "PC음성.mp3", "mic": "마이크음성.mp3", "mixed": "합본.mp3"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_mp3(path, expected_samples, expected_hash=None):
    import av
    if expected_hash and digest(path) != expected_hash:
        raise ValueError("MP3 checksum mismatch")
    count = 0
    with av.open(str(path)) as source:
        if len(source.streams.audio) != 1:
            raise ValueError("Expected one MP3 audio stream")
        stream = source.streams.audio[0]
        if stream.codec_context.name not in {"mp3", "mp3float"}:
            raise ValueError("Expected MP3 codec")
        for frame in source.decode(audio=0):
            if frame.sample_rate != RATE or len(frame.layout.channels) != 2:
                raise ValueError("Unexpected MP3 format")
            count += frame.samples
    # MP3 encoder priming/padding can add at most two frames without gapless tags.
    if not count or abs(count - expected_samples) > 2304:
        raise ValueError("MP3 duration mismatch / truncated output")
    return count


def validate_archive(store):
    archive = store.metadata.get("archive", {})
    if archive.get("status") != "verified" or not archive.get("files"):
        raise ValueError("MP3 archive is not verified")
    for item in archive["files"]:
        if item["file"] != NAMES.get(item["track"]):
            raise ValueError("Invalid archive filename")
        verify_mp3(safe_child(store.directory, item["file"]), item["frames"], item["sha256"])
    expected = set(store.metadata.get("selected_tracks", []))
    present = {i["track"] for i in archive["files"]}
    if not expected or not expected <= present:
        raise ValueError("Missing selected track archive")
    if len(expected) == 2 and store.metadata["storage_policy"].get("create_mix") and "mixed" not in present:
        raise ValueError("Missing mixed archive")
    return archive


def _chunks(store, items):
    import av
    for item in sorted(items, key=lambda i: i["offset"]):
        position = round(item["offset"] * RATE)
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
        with av.open(str(safe_child(store.directory, item["file"]))) as source:
            for frame in source.decode(audio=0):
                frame.pts = None
                for converted in resampler.resample(frame):
                    yield position, converted.to_ndarray()
                    position += converted.samples
            for converted in resampler.resample(None):
                yield position, converted.to_ndarray()
                position += converted.samples


class Timeline:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.current = next(self.chunks, None)

    def read(self, start, count):
        import numpy as np
        block = np.zeros((2, count), dtype=np.float32)
        stop = start + count
        while self.current:
            offset, data = self.current
            end = offset + data.shape[1]
            if offset >= stop:
                break
            left, right = max(start, offset), min(stop, end)
            if right > left:
                block[:, left - start:right - start] = data[:, left - offset:right - offset]
            if end > stop:
                break
            self.current = next(self.chunks, None)
        return block


def create_archive(store):
    if not store.metadata.get("storage_policy", {}).get("temporary_wav"):
        return None  # Older sessions are never silently converted/deleted.
    previous = store.metadata.get("archive", {})
    if previous.get("status") == "verified":
        try:
            return validate_archive(store)
        except (OSError, ValueError):
            originals = previous.get("original_segments", [])
            if not originals or not all(safe_child(store.directory, i["file"]).is_file() for i in originals):
                raise
            # Rebuild damaged exports only while all original WAVs still exist.
            store.update(segments=originals)
    import av
    import numpy as np
    from app.audio.validation import inspect_wav
    selected = store.metadata["selected_tracks"]
    originals = [dict(i) for i in store.metadata["segments"]]
    if not originals or any(i["track"] not in selected for i in originals):
        raise ValueError("Missing or unexpected source tracks")
    for item in originals:
        if not re.fullmatch(r"audio/(remote|mic)_[0-9]{4,}\.wav", item["file"]):
            raise ValueError("Not a temporary recording WAV")
        inspect_wav(safe_child(store.directory, item["file"]), item)
        item["source_sha256"] = digest(safe_child(store.directory, item["file"]))
        if not math.isfinite(item["offset"] + item["duration"]):
            raise ValueError("Invalid source timeline")
    if set(i["track"] for i in originals) != set(selected):
        raise ValueError("A selected source has no audio; WAVs preserved")
    samples = math.ceil(max(i["offset"] + i["duration"] for i in originals) * RATE)
    names = list(selected)
    if len(names) == 2 and store.metadata["storage_policy"].get("create_mix"):
        names.append("mixed")
    for name in names:
        if (store.directory / NAMES[name]).exists() and NAMES[name] not in previous.get("owned_files", []):
            raise FileExistsError("Archive destination already exists")
    # Persist ownership before publishing outputs, allowing an interrupted export to retry.
    store.update(archive={"status": "building", "owned_files": [NAMES[n] for n in names],
                          "cleanup": "pending"})
    timelines = {name: Timeline(_chunks(store, [i for i in originals if i["track"] == name])) for name in selected}
    with ExitStack() as stack:
        outputs = {}
        for name in names:
            path = safe_child(store.directory, NAMES[name] + ".part")
            output = stack.enter_context(av.open(str(path), "w", format="mp3"))
            stream = output.add_stream("libmp3lame", rate=RATE)
            stream.bit_rate = 128000
            stream.layout = "stereo"
            stream.codec_context.thread_count = 1
            outputs[name] = output, stream
        for start in range(0, samples, BLOCK):
            count = min(BLOCK, samples - start)
            blocks = {name: timeline.read(start, count) for name, timeline in timelines.items()}
            if "mixed" in outputs:
                blocks["mixed"] = sum(blocks.values()) / len(selected)
            for name, block in blocks.items():
                # Headroom after resampling; equal-weight mix avoids additive clipping.
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(np.clip(block, -0.95, 0.95)),
                                                   format="fltp", layout="stereo")
                frame.sample_rate = RATE
                frame.pts = start
                output, stream = outputs[name]
                for packet in stream.encode(frame):
                    output.mux(packet)
        for output, stream in outputs.values():
            for packet in stream.encode(None):
                output.mux(packet)
    files = []
    for name in names:
        temporary = safe_child(store.directory, NAMES[name] + ".part")
        verify_mp3(temporary, samples)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, safe_child(store.directory, NAMES[name]))
        files.append({"track": name, "file": NAMES[name], "offset": 0.0, "frames": samples,
                      "duration": samples / RATE, "rate": RATE, "channels": 2, "codec": "MP3",
                      "sha256": digest(store.directory / NAMES[name])})
    archive = {"status": "verified", "files": files, "original_segments": originals,
               "owned_files": [NAMES[n] for n in names], "verified_at": utc_now(), "cleanup": "pending"}
    store.update(archive=archive)
    return archive


def cleanup_wav(store):
    """Commit replacement indexes BEFORE deleting only exact owned temporary files."""
    if not store.metadata.get("storage_policy", {}).get("temporary_wav"):
        return
    archive = validate_archive(store)
    policy = store.metadata["storage_policy"]
    reason = None
    if store.metadata["errors"] or store.metadata["gaps"] or has_unindexed_audio(store.directory, store.metadata):
        reason = "capture_or_recovery_requires_review"
    elif policy.get("transcribe_required") and (
            store.metadata.get("transcription", {}).get("status") != "complete"
            or not (store.directory / "transcript.md").is_file()):
        reason = "transcription_incomplete"
    if reason:
        store.update(archive={**archive, "cleanup": "retained", "retention_reason": reason})
        return
    pending = []
    # Validate the entire deletion set, including path containment, before the first unlink.
    for item in archive["original_segments"]:
        if not re.fullmatch(r"audio/(remote|mic)_[0-9]{4,}\.wav", item["file"]):
            raise ValueError("Refusing to delete non-temporary WAV")
        path = safe_child(store.directory, item["file"])
        if path.parent != (store.directory / "audio").resolve() or (store.directory / item["file"]).is_symlink():
            raise ValueError("Refusing redirected WAV deletion")
        if path.exists():
            from app.audio.validation import inspect_wav
            inspect_wav(path, item)
            if digest(path) != item["source_sha256"]:
                raise ValueError("Temporary WAV changed since MP3 export; preserving it")
        pending.append(path)
    sources = [i for i in archive["files"] if i["track"] != "mixed"]
    store.update(segments=sources, archive={**archive, "cleanup": "deleting"})
    for path in pending:
        path.unlink(missing_ok=True)
    store.update(archive={**archive, "cleanup": "complete", "cleaned_at": utc_now()})
    store.log("temporary_wav_removed", count=len(pending))


def source_items(store):
    """WAV until committed; archived tracks after cleanup, never the mixed export."""
    items = sorted(store.metadata["segments"], key=lambda s: (s["offset"], s["track"]))
    if any(i.get("codec") == "MP3" for i in items):
        archive = validate_archive(store)
        return [i for i in archive["files"] if i["track"] != "mixed"]
    return items


def reference_file(store, item):
    archive = store.metadata.get("archive", {})
    if archive.get("status") == "verified":
        return next(i["file"] for i in archive["files"] if i["track"] == item["track"])
    return item["file"]
