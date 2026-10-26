"""Create a source-only share archive using an explicit allowlist."""
import hashlib
from pathlib import Path
import zipfile
from app.paths import distribution_root

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("app", "scripts", "tests", "browser-extension", "packaging", ".github")
FILES = (".gitignore", ".gitattributes", "AGENTS.md", "README.md", "VERSION", "pyproject.toml", "requirements.txt", "requirements-core.txt",
         "requirements-dev.txt", "requirements-build.txt", "requirements-gpu.txt", "requirements-lock-windows-py314.txt",
         "config/settings.example.yaml")
EXTENSIONS = {".py", ".ps1", ".js", ".html", ".css", ".json", ".md", ".yaml", ".yml", ".spec"}


def source_files(root=ROOT):
    for name in FILES:
        yield root / name
    if (root / "LICENSE").is_file():
        yield root / "LICENSE"
    for directory in DIRECTORIES:
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and path.suffix in EXTENSIONS and "__pycache__" not in path.parts:
                yield path


def main():
    destination = distribution_root() / "ZoomRecorder-source.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            archive.write(path, path.relative_to(ROOT))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    print(destination)


if __name__ == "__main__":
    main()
