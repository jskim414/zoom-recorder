"""Read-only WAV/session inspection; does not record, play, repair, or transcribe."""
import argparse
import json
from pathlib import Path
import wave

from app.audio.validation import inspect_wav
from app.storage.session_store import safe_child


def inspect_session(directory: Path) -> dict:
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    report = {"session": directory.name, "state": metadata["state"],
              "validation_kind": metadata.get("validation_kind", "user_recording"),
              "segments": [], "issues": [], "warnings": [],
              "capture_errors": metadata.get("errors", []), "gaps": metadata.get("gaps", []),
              "listening_verified": False, "hardware_acceptance_passed": False}
    if metadata["state"] in {"RECORDING", "FINALIZING", "TRANSCRIBING"}:
        report["issues"].append("Session is still active; stop and wait before inspection")
        report["file_checks_passed"] = False
        return report
    seen = set()
    tracks = set()
    for item in metadata["segments"]:
        row = {"file": item["file"], "track": item["track"], "offset": item["offset"]}
        try:
            if item["file"] in seen:
                raise ValueError("Duplicate segment entry")
            seen.add(item["file"])
            row.update(inspect_wav(safe_child(directory, item["file"]), item))
            tracks.add(item["track"])
            if row["all_zero"]:
                report["warnings"].append(f"All-zero audio: {item['file']}")
            if row["clipped_fraction"] > 0.001:
                report["warnings"].append(f"Possible clipping: {item['file']}")
        except (OSError, ValueError, KeyError, EOFError, wave.Error) as error:
            row["error"] = str(error)
            report["issues"].append(f"Invalid WAV: {item['file']}")
        report["segments"].append(row)
    if tracks != {"remote", "mic"}:
        report["issues"].append("Both valid remote and mic tracks are required")
    for track in sorted(tracks):
        rows = sorted((row for row in report["segments"] if row["track"] == track and "error" not in row),
                      key=lambda row: row["offset"])
        for previous, current in zip(rows, rows[1:]):
            difference = current["offset"] - previous["offset"] - previous["duration"]
            if abs(difference) > 0.15:
                report["warnings"].append(f"{track} segment timeline discontinuity: {difference:.3f}s")
    if report["capture_errors"] or report["gaps"]:
        report["warnings"].append("Capture errors/gaps require review")
    report["file_checks_passed"] = not report["issues"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    report = inspect_session(args.session)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["file_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
