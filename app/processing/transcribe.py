from __future__ import annotations

import os
from pathlib import Path

from app.audio.validation import inspect_wav

from app.processing.render_markdown import render_markdown
from app.processing.model_download import model_installed
from app.storage.session_store import SessionStore, safe_child, utc_now


class Transcriber:
    def __init__(self, settings, model_factory=None):
        self.settings = settings
        self.model_factory = model_factory

    def _model(self, device: str):
        # An explicit local directory is required, preventing implicit model downloads.
        if self.model_factory is None:
            path = Path(self.settings.model)
            if not model_installed(path):
                raise FileNotFoundError("로컬 모델이 없습니다. INSTALL_WINDOWS.md의 모델 설치 절차를 실행하세요.")
            if device == "cuda":
                from app.processing.gpu_runtime import configure_gpu_runtime
                configure_gpu_runtime()
            from faster_whisper import WhisperModel
            factory = WhisperModel
        else:
            factory = self.model_factory
        return factory(self.settings.model, device=device,
                       compute_type="int8" if device == "cpu" else "float16", local_files_only=True,
                       cpu_threads=self.settings.cpu_threads, num_workers=1)

    def _run(self, store: SessionStore, device: str) -> list[dict]:
        inputs = []
        for item in sorted(store.metadata["segments"], key=lambda s: (s["offset"], s["track"])):
            path = safe_child(store.directory, item["file"])
            if path.suffix != ".wav":
                raise ValueError("Only WAV segments can be transcribed")
            if item["frames"] == 0:
                continue
            inspect_wav(path, item)
            inputs.append((item, path))
        model = self._model(device)
        result = []
        for item, path in inputs:
            segments, _ = model.transcribe(str(path), language=self.settings.language,
                                          vad_filter=True, beam_size=self.settings.beam_size, condition_on_previous_text=False)
            # Iteration performs the actual inference; CUDA failures can happen here too.
            for segment in segments:
                start, end = max(0.0, segment.start), min(item["duration"], segment.end)
                if end <= start or not segment.text.strip():
                    continue
                result.append({"track": item["track"], "file": item["file"],
                               "start": item["offset"] + start, "end": item["offset"] + end,
                               "text": segment.text.strip()})
        return result

    def run(self, store: SessionStore, *, api_approved=False) -> Path:
        api = self.settings.transcription_backend == "openai"
        if api and api_approved is not True:
            raise PermissionError("API upload requires per-session approval")
        from app.processing.api_transcribe import API_MODEL
        model_name = API_MODEL if api else self.settings.model
        store.update(state="TRANSCRIBING", transcription={"status": "running", "model": model_name,
                                                         "backend": self.settings.transcription_backend,
                                                         "requested_device": self.settings.device})
        device = "api" if api else self.settings.device
        try:
            try:
                if api:
                    from app.processing.api_transcribe import transcribe_api
                    result = transcribe_api(store, approved=api_approved)
                else:
                    result = self._run(store, device)
            except Exception:
                if device != "cuda":
                    raise
                device = "cpu"
                store.log("transcription_cpu_fallback")
                result = self._run(store, device)
            destination = store.directory / "transcript.md"
            temp = destination.with_suffix(".md.tmp")
            with temp.open("w", encoding="utf-8") as f:
                f.write(render_markdown(store.metadata, result))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, destination)
            store.update(state="ERROR" if store.metadata["errors"] else "COMPLETE",
                         transcription={"status": "complete", "model": model_name,
                                        "backend": self.settings.transcription_backend,
                                        "device": device, "language": self.settings.language or "auto",
                                        "completed_at": utc_now(), "segment_count": len(result)})
            store.log("transcription_complete", device=device, segment_count=len(result))
            return destination
        except Exception as error:
            code = ("model_missing" if isinstance(error, FileNotFoundError) else
                    "engine_missing" if isinstance(error, ImportError) else "transcription_failed")
            store.update(state="ERROR", transcription={"status": "failed", "code": code,
                                                      "model": model_name, "device": device})
            store.log("transcription_failed", code=code)
            raise
