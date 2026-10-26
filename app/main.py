from pathlib import Path
import argparse
import sys
import time

from app.settings import load_settings
from app.storage.instance_lock import InstanceAlreadyRunning, InstanceLock
from app.storage.session_store import SESSION_NAME, SessionStore, apply_retention, recover_session


def main(argv=None):
    if (argv if argv is not None else sys.argv[1:]) == ["--transcription-worker"]:
        from app.processing.worker import main as worker_main
        return worker_main()
    parser = argparse.ArgumentParser(description="Consent-gated local Windows Zoom recorder")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--poc", action="store_true", help="M0: manual consent UI, no detection/transcription")
    parser.add_argument("--no-detection", action="store_true")
    parser.add_argument("--background", action="store_true", help="Start quietly in the system tray")
    parser.add_argument("--approve-api-upload", action="store_true",
                        help="Explicitly approve sending this --transcribe session to OpenAI (paid API)")
    parser.add_argument("--download-model", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Explicit one-time model download; no audio is uploaded")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--recover", type=Path)
    mode.add_argument("--transcribe", type=Path)
    args = parser.parse_args(argv)
    app = None
    try:
        if args.download_model:
            from scripts.download_model import main as download_model
            from app.paths import data_root
            download_model(["--model", args.download_model, "--output", str(data_root() / "models" / args.download_model)])
            return 0
        settings = load_settings(args.config)
        root = Path(settings.recordings_dir).resolve()
        if not (args.recover or args.transcribe):
            if sys.platform != "win32":
                raise RuntimeError("실제 녹음 앱은 Windows 11에서 실행하세요.")
            from PySide6.QtWidgets import QApplication
            from app.ui.single_instance import reveal_existing
            app = QApplication(sys.argv[:1])
            app.setQuitOnLastWindowClosed(False)
            if not args.background and reveal_existing(root):
                return 0
        with InstanceLock(root):
            if args.recover or args.transcribe:
                directory = (args.recover or args.transcribe).resolve()
                if directory.parent != root or not SESSION_NAME.fullmatch(directory.name):
                    raise ValueError("Select a session directly inside recordings_dir")
                if args.recover:
                    result = recover_session(directory)
                    print(f"Recovered {len(result['recovered'])} files; inspect metadata.json before retrying transcription.")
                else:
                    from app.processing.transcribe import Transcriber
                    print(Transcriber(settings).run(SessionStore(directory), api_approved=args.approve_api_upload))
                return 0
            apply_retention(root, settings.retention_days)
            from app.startup import migrate_startup
            migrate_startup()
            from app.ui.tray import RecorderWindow
            from app.ui.single_instance import listen_for_launch
            window = RecorderWindow(settings, poc=args.poc, enable_detection=not args.no_detection,
                                    background=args.background,
                                    config_path=args.config)
            server = listen_for_launch(root, window)
            if not args.background:
                window.show()
            result = app.exec()
            server.close()
            return result
    except (ValueError, RuntimeError, OSError, ImportError) as error:
        if isinstance(error, InstanceAlreadyRunning) and app is not None:
            # Do not connect to the reveal pipe: even a probe wakes older versions.
            if args.background:
                return 0
            # A near-simultaneous launch may arrive before the first UI has bound its pipe.
            for _ in range(30):
                if reveal_existing(root):
                    return 0
                time.sleep(0.1)
        if args.background:
            from app.paths import logs_root
            try:
                log = logs_root() / "startup-error.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(str(error), encoding="utf-8")
            except OSError:
                pass
        elif app is not None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(None, "Zoom Recorder", str(error))
        elif sys.stderr:
            print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
