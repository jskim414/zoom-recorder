"""Include build dependency metadata and upstream license texts with the archive."""
import importlib.metadata
from pathlib import Path
import re
import sys


def main():
    destination = Path(sys.argv[1]) / "THIRD_PARTY_LICENSES"
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", distribution.metadata["Name"])
        inventory.append(f"{name}=={distribution.version}")
        folder = destination / name
        folder.mkdir(exist_ok=True)
        metadata = distribution.read_text("METADATA") or distribution.read_text("PKG-INFO") or ""
        (folder / "METADATA.txt").write_text(metadata, encoding="utf-8")
        for index, entry in enumerate(distribution.files or []):
            if any(marker in str(entry).lower() for marker in ("license", "copying", "notice")):
                path = Path(distribution.locate_file(entry))
                if path.is_file():
                    (folder / f"{index}-{path.name}").write_bytes(path.read_bytes())
    (destination / "BUILD_ENVIRONMENT.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
