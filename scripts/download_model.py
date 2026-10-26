"""Explicit one-time model download. Never reads or uploads recordings."""
import argparse
from pathlib import Path
from app.paths import data_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["tiny", "base", "small", "medium", "large-v3"], default="small")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    from huggingface_hub import snapshot_download
    destination = args.output or data_root() / "models" / args.model
    snapshot_download(repo_id="Systran/faster-whisper-" + args.model, local_dir=destination,
                      allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"])
    print(f"Local model installed: {destination}")


if __name__ == "__main__":
    main()
