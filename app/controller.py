from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
import queue

from app.audio.recorder import Recorder
from app.detection.session_manager import SessionManager, State
from app.processing.worker import ProcessTranscriber
from app.storage.session_store import SessionStore, available_session_path, recover_session, utc_now


@dataclass
class TranscriptionJob:
    store: SessionStore
    future: object


class Controller:
    """Application orchestration independent of Qt, with injectable recording/transcription."""
    def __init__(self, settings, recorder_factory=Recorder, transcriber_factory=ProcessTranscriber):
        self.settings = settings
        self.manager = SessionManager()
        self.recorder_factory = recorder_factory
        self.transcriber_factory = transcriber_factory
        self.recorder = None
        self.store = None
        self.future = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="postprocessing")
        self.notifications = queue.Queue()
        self.job = None
        self.api_approved = False
        self.require_save_confirmation = False
        self.save_pending = False
        self.transcriptions = []
        self.session_settings = None

    @property
    def capture_busy(self):
        return self.future is not None or self.manager.state in {
            State.RECORDING, State.STOP_CONFIRM, State.FINALIZING}

    @property
    def busy(self):
        return self.capture_busy or bool(self.transcriptions)

    def processing(self, directory):
        return any(job.store.directory == Path(directory).resolve() for job in self.transcriptions)

    def start(self, key, checked, explicit_click, *, api_approved=False, folder_name=None):
        if self.capture_busy:
            raise RuntimeError("녹음 저장이 끝난 뒤 시작하세요.")
        requires_api = self.settings.auto_transcribe and self.settings.transcription_backend == "openai"
        if requires_api and api_approved is not True:
            raise PermissionError("OpenAI에 녹음 파일을 전송하려면 별도 동의가 필요합니다.")
        if folder_name is not None:
            available_session_path(Path(self.settings.recordings_dir), folder_name)
        self.manager.approve(key, checked, explicit_click)
        self.session_settings = replace(self.settings)
        self.save_pending = False
        self.api_approved = requires_api and api_approved is True
        self.store = None
        self.recorder = None
        try:
            self.store = SessionStore.create(Path(self.settings.recordings_dir), self.manager.selected.source,
                                             self.manager.consent_at, folder_name)
            if self.api_approved:
                self.store.log("api_upload_approved", provider="openai", approved_at=utc_now())
            self.recorder = self.recorder_factory(self.store, self.session_settings)
            self.recorder.start()
        except Exception:
            if self.recorder:
                self.recorder.stop()
            self.manager.stop()
            self.manager.fail()
            self.notifications.put(("recording_start_failed", None))

    def stop(self):
        if self.manager.state not in {State.RECORDING, State.STOP_CONFIRM}:
            return
        # Signal first so stopping does not wait on filesystem I/O.
        self.recorder.stop()
        self.manager.stop()
        try:
            self.store.log("stop_requested")
        except OSError:
            self.notifications.put(("storage_failed", None))

    def _transcribe(self):
        self._enqueue(self.store, self.session_settings or self.settings, self.api_approved)
        self.manager.transcribe()
        self.api_approved = False  # A retry must get a new approval.

    def _enqueue(self, store, settings, approved):
        if self.processing(store.directory):
            raise RuntimeError("이미 전사 중이거나 대기 중인 회의입니다.")
        options = replace(settings)
        if options.transcription_backend == "openai" and approved is not True:
            raise PermissionError("API upload requires per-session approval")
        worker = self.transcriber_factory(options)
        store.update(state="TRANSCRIBING", transcription={"status": "queued", "backend": options.transcription_backend})
        kwargs = {"api_approved": True} if options.transcription_backend == "openai" and approved else {}
        future = self.executor.submit(worker.run, store, **kwargs)
        self.transcriptions.append(TranscriptionJob(store, future))

    def confirm_save(self, folder_name: str | None = None):
        if not self.save_pending or self.manager.state != State.FINALIZING or self.recorder is not None:
            raise RuntimeError("녹음 파일 저장이 끝난 뒤 폴더명을 확인하세요.")
        if folder_name is not None:
            self.store.rename(folder_name)
        self.save_pending = False
        self._finish_recording()

    def _finish_recording(self):
        try:
            if (self.session_settings or self.settings).auto_transcribe:
                self._transcribe()
            else:
                self.store.update(state="ERROR" if self.store.metadata["errors"] else "COMPLETE",
                                  transcription={"status": "disabled"})
                if self.store.metadata["errors"]:
                    self.manager.fail()
                else:
                    self.manager.complete()
        except Exception:
            self.manager.fail()
            self.notifications.put(("finalization_failed", None))

    def retry_transcription(self, store, *, api_approved=False):
        if self.settings.transcription_backend == "openai" and api_approved is not True:
            raise PermissionError("API upload requires per-session approval")
        if self.future or self.manager.state in {State.PROMPTED, State.FINALIZING}:
            raise RuntimeError("Another session is active")
        if self.recorder and self.store.directory == store.directory:
            raise RuntimeError("녹음 중인 회의는 전사할 수 없습니다.")
        if store.metadata.get("state") not in {"COMPLETE", "ERROR", "RECOVERY_REQUIRED"}:
            raise RuntimeError("녹음이 종료된 세션만 전사할 수 있습니다. 복구 점검을 먼저 실행하세요.")
        approved = self.settings.transcription_backend == "openai" and api_approved is True
        if approved:
            store.log("api_upload_approved", provider="openai", approved_at=utc_now())
        self._enqueue(store, self.settings, approved)

    def recover(self, directory):
        if self.busy or self.manager.state == State.PROMPTED:
            raise RuntimeError("Another session is active")
        self.manager.recovery()
        self.job = "recovery"
        self.future = self.executor.submit(recover_session, directory)

    def poll(self):
        if self.recorder:
            while True:
                try:
                    self.notifications.put(self.recorder.events.get_nowait())
                except queue.Empty:
                    break
            if self.recorder.done.is_set():
                if self.manager.state in {State.RECORDING, State.STOP_CONFIRM}:
                    self.stop()
                if self.manager.state == State.FINALIZING:
                    self.recorder = None
                    try:
                        self.store.update(state="FINALIZING")
                        if not self.store.metadata["segments"]:
                            self.store.update(state="ERROR")
                            self.manager.fail()
                            self.notifications.put(("no_audio_saved", None))
                        elif self.require_save_confirmation:
                            self.save_pending = True
                        else:
                            self._finish_recording()
                    except Exception:
                        self.manager.fail()
                        self.notifications.put(("finalization_failed", None))
        if self.future and self.future.done():
            try:
                result = self.future.result()
                self.notifications.put(("recovery_finished", None))
            except Exception as error:
                self.manager.fail()
                code = ("model_missing" if isinstance(error, FileNotFoundError) else
                        "engine_missing" if isinstance(error, ImportError) else "processing_failed")
                self.notifications.put((code, None))
            finally:
                self.future = None
                self.job = None
        for task in list(self.transcriptions):
            if not task.future.done():
                continue
            selected = self.store is task.store and self.manager.state == State.TRANSCRIBING
            try:
                task.future.result()
                if selected:
                    self.manager.fail() if task.store.metadata["errors"] else self.manager.complete()
                self.notifications.put(("transcription_complete", task.store.directory.name))
            except Exception as error:
                if selected:
                    self.manager.fail()
                code = ("model_missing" if isinstance(error, FileNotFoundError) else
                        "engine_missing" if isinstance(error, ImportError) else "processing_failed")
                try:
                    task.store.update(state="ERROR", transcription={"status": "failed", "code": code})
                except OSError:
                    code = "storage_failed"
                self.notifications.put((code, task.store.directory.name))
            finally:
                self.transcriptions.remove(task)

    def close(self):
        if self.busy:
            raise RuntimeError("Stop recording and wait for background work before exiting")
        self.executor.shutdown(wait=False)
