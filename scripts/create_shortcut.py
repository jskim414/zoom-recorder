"""Create the normal-mode launcher in the project folder; startup stays unchanged."""
from app.startup import PROJECT_ROOT, create_shortcut


if __name__ == "__main__":
    destination = PROJECT_ROOT / "Zoom Recorder.lnk"
    create_shortcut(destination)
    print(str(destination))
