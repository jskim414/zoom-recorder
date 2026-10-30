from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import queue
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSystemTrayIcon, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.controller import Controller
from app.storage.policy import StoragePolicy
from app.audio.activity import RecordingReminder
from app.storage.history import recent_sessions
from app.detection.session_manager import CandidateTracker, State
from app.detectors.browser_bridge import Bridge
from app.detectors.service import DetectionService
from app.storage.session_store import SESSION_NAME, SessionStore, find_recovery, suggest_folder_name
from app.ui.preferences import PreferencesDialog
from app.ui.reminder import ReminderPopup
from app.ui.theme import STYLE
from app.paths import default_config


LABELS = {State.IDLE: "녹음 준비", State.CANDIDATE: "회의 가능성을 발견했어요",
          State.PROMPTED: "시작 전 동의를 확인하세요", State.RECORDING: "녹음 중",
          State.STOP_CONFIRM: "녹음 중 · 회의 종료 여부 확인 필요", State.FINALIZING: "오디오 저장 중",
          State.TRANSCRIBING: "새 녹음을 시작할 수 있어요", State.COMPLETE: "녹음을 저장했어요",
          State.SKIPPED: "녹음 준비", State.ERROR: "확인이 필요해요 · 녹음 OFF",
          State.RECOVERY_REQUIRED: "복구 확인 필요 · 녹음 OFF"}
ERRORS = {
    "archive_complete": "선택한 음성 저장·삭제 처리를 마쳤습니다. 최근 회의의 상태를 확인하세요.",
    "audio_deleted": "전사문을 저장하고 해당 녹음의 음성을 삭제했습니다. 음성 재생·재전사는 불가능합니다.",
    "audio_retained": "전사 결과·녹음 누락·파일 검증을 확인해야 하므로 음성을 보존했습니다.",
    "wav_retained": "오류·누락 또는 미완료 전사 때문에 임시 WAV를 보존했습니다. 원인 확인 후 재시도하세요.",
    "speech_monitor_unavailable": "발화 감지를 사용할 수 없습니다. 녹음은 계속됩니다. 종료 여부를 직접 확인하세요.",
    "device_open_failed": "장치를 열 수 없습니다. 장치 선택과 Windows 마이크 권한을 확인하세요. 다른 트랙만 녹음 중일 수 있습니다.",
    "device_disconnected": "장치 연결이 끊겼습니다. 다른 트랙만 녹음 중일 수 있습니다. 자동 전환하지 않습니다.",
    "default_device_changed": "기본 오디오 장치가 변경되었습니다. 기존 장치를 유지합니다. 새 장치로 녹음하려면 중지 후 다시 승인하세요.",
    "track_write_failed": "한 트랙 저장에 실패했습니다. 디스크 공간을 확인하세요. 녹음은 불완전할 수 있습니다.",
    "buffer_overflow": "오디오 버퍼 누락이 있습니다. metadata.json의 gaps를 확인하세요.",
    "model_missing": "로컬 전사 모델이 없습니다. 녹음 파일은 보존했습니다. 모델 설치 후 전사를 재시도하세요.",
    "engine_missing": "로컬 전사 엔진이 없습니다. Local 배포본 또는 전사 패키지를 설치하세요. 녹음 파일은 보존했습니다.",
    "transcription_complete": "전사를 완료했습니다. transcript.md를 확인하세요.",
    "recovery_finished": "복구 점검을 마쳤습니다. 원본 파일을 보존했습니다. 청취 후 전사를 재시도할 수 있습니다.",
    "no_audio_saved": "저장된 오디오가 없습니다. 장치와 권한을 확인하세요.",
    "track_has_no_samples": "이 트랙에 샘플이 없습니다. 무음 장치 또는 캡처 실패일 수 있습니다. 다른 트랙과 장치 상태를 확인하세요.",
    "device_monitor_unavailable": "장치 변경 감시를 사용할 수 없습니다. 입력/출력 장치를 바꿨다면 직접 중지 후 확인하세요.",
    "processing_failed": "변환·검증·전사 또는 임시 파일 정리에 실패했습니다. 남은 원본은 보존했습니다. 저장 공간·모델·연결을 확인 후 재시도하세요.",
}


def status_icon(recording: bool):
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#db3545" if recording else "#587184"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, 26, 26)
    painter.end()
    return QIcon(pixmap)


class RecorderWindow(QMainWindow):
    def __init__(self, settings, *, poc=False, enable_detection=True, controller=None, config_path=None,
                 background=False):
        super().__init__()
        self.saved_settings = replace(settings)
        self.config_path = Path(config_path or default_config())
        self.poc = poc
        self.detection_enabled = enable_detection and settings.auto_detect and not poc
        if poc:
            settings = replace(settings, auto_transcribe=False)
        self.settings = settings
        self.controller = controller or Controller(settings)
        self.controller.require_save_confirmation = True
        self.save_dialog_open = False
        self.tracker = CandidateTracker(settings.stable_seconds, settings.end_grace_seconds, settings.cooldown_seconds)
        self.bridge = Bridge(settings.bridge_port, settings.extension_ids, self.audit_bridge)
        self.detectors = None
        self.allow_close = False
        self.stop_reminder_at = 0.0
        self.stop_snooze_until = 0.0
        self.last_state = None
        self.detector_error = None
        self.endpoint_ids = None
        self.reminder = None
        self.history_at = float("-inf")
        self.history_signature = None
        self.setWindowTitle("Zoom Recorder · 로컬 녹음")
        self.resize(600, 800)
        self.setMinimumWidth(520)
        self.setWindowIcon(status_icon(False))
        self.setStyleSheet(STYLE)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        brand = QLabel("ZOOM RECORDER  /  PERSONAL WORKSPACE")
        brand.setObjectName("eyebrow")
        layout.addWidget(brand)
        header = QHBoxLayout()
        heading = QLabel("회의 녹음")
        heading.setObjectName("title")
        header.addWidget(heading)
        header.addStretch()
        self.preferences_button = QPushButton("환경설정")
        self.preferences_button.clicked.connect(self.open_preferences)
        header.addWidget(self.preferences_button)
        layout.addLayout(header)
        self.mode_label = QLabel()
        self.mode_label.setWordWrap(True)
        self.mode_label.setObjectName("muted")
        layout.addWidget(self.mode_label)
        card = QWidget()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(16)
        self.recording_badge = QLabel()
        self.recording_badge.setObjectName("badge")
        card_layout.addWidget(self.recording_badge, alignment=Qt.AlignmentFlag.AlignLeft)
        self.status = QLabel(LABELS[State.IDLE])
        self.status.setWordWrap(True)
        card_layout.addWidget(self.status)
        self.elapsed_label = QLabel("00:00")
        self.elapsed_label.setObjectName("timer")
        card_layout.addWidget(self.elapsed_label)
        self.details = QLabel("녹음은 꺼져 있습니다. 준비되면 시작을 누르세요.")
        self.details.setWordWrap(True)
        card_layout.addWidget(self.details)
        self.levels_label = QLabel()
        card_layout.addWidget(self.levels_label)
        self.reminder_label = QLabel()
        self.reminder_label.setWordWrap(True)
        self.reminder_label.setStyleSheet("color:#a65b00;font-weight:600")
        card_layout.addWidget(self.reminder_label)
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color:#a52a2a")
        card_layout.addWidget(self.banner)
        self.manual_button = QPushButton("녹음 시작")
        self.manual_button.setObjectName("primary")
        self.manual_button.setMinimumHeight(48)
        self.manual_button.clicked.connect(self.manual)
        card_layout.addWidget(self.manual_button)
        self.consent_panel = QWidget()
        consent_layout = QVBoxLayout(self.consent_panel)
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(12)
        self.candidate_combo = QComboBox()
        consent_layout.addWidget(self.candidate_combo)
        folder_label = QLabel("저장 폴더명 (YYYYMMDD_nn_회의명)")
        self.folder_name = QLineEdit()
        self.folder_name.setMaxLength(120)
        self.folder_name.setPlaceholderText("20261026_01_회의명")
        folder_label.setBuddy(self.folder_name)
        consent_layout.addWidget(folder_label)
        consent_layout.addWidget(self.folder_name)
        self.record_pc = QCheckBox("PC 소리 녹음 · Zoom 외 시스템 소리 포함")
        self.record_mic = QCheckBox("마이크 녹음")
        self.record_pc.setChecked(settings.record_pc)
        self.record_mic.setChecked(settings.record_mic)
        consent_layout.addWidget(self.record_pc)
        consent_layout.addWidget(self.record_mic)
        self.storage_policy_label = QLabel()
        self.storage_policy_label.setWordWrap(True)
        consent_layout.addWidget(self.storage_policy_label)
        self.consent = QCheckBox("회의 참석자에게 녹음 사실을 알렸고 동의를 받았습니다")
        consent_layout.addWidget(self.consent)
        self.api_consent = QCheckBox("이 녹음에서 선택한 음성을 OpenAI에 전송하는 데 동의합니다")
        self.api_consent.setToolTip("녹음 종료 후 전송하며 API 사용 요금이 발생합니다.")
        self.api_consent.toggled.connect(self.update_start_gate)
        consent_layout.addWidget(self.api_consent)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("동의하고 녹음 시작")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(48)
        self.skip_button = QPushButton("취소")
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.skip_button)
        consent_layout.addLayout(buttons)
        self.start_button.clicked.connect(self.start_recording)
        self.skip_button.clicked.connect(self.skip)
        self.consent.toggled.connect(self.update_start_gate)
        self.record_pc.toggled.connect(self.update_start_gate)
        self.record_mic.toggled.connect(self.update_start_gate)
        self.candidate_combo.currentIndexChanged.connect(self.update_start_gate)
        card_layout.addWidget(self.consent_panel)
        self.consent_panel.hide()
        row = QHBoxLayout()
        self.stop_button = QPushButton("녹음 중지")
        self.stop_button.setObjectName("stop")
        self.stop_button.setMinimumHeight(48)
        self.stop_button.clicked.connect(self.stop_recording)
        self.continue_button = QPushButton("5분 뒤 다시 알림")
        self.continue_button.clicked.connect(self.keep_recording)
        row.addWidget(self.stop_button)
        row.addWidget(self.continue_button)
        card_layout.addLayout(row)
        self.dismiss_reminder_button = QPushButton("이번 녹음 알림 끄기")
        self.dismiss_reminder_button.clicked.connect(self.disable_reminder)
        card_layout.addWidget(self.dismiss_reminder_button)
        layout.addWidget(card)
        self.folder_button = QPushButton("녹음 폴더 보기")
        self.folder_button.clicked.connect(self.open_folder)
        layout.addWidget(self.folder_button)
        self.processing_label = QLabel()
        layout.addWidget(self.processing_label)
        layout.addWidget(QLabel("최근 회의"))
        self.history = QTreeWidget()
        self.history.setHeaderLabels(["회의", "녹음 시간", "상태"])
        self.history.setRootIsDecorated(False)
        self.history.setMinimumHeight(130)
        self.history.setMaximumHeight(200)
        self.history.setColumnWidth(0, 230)
        self.history.setColumnWidth(1, 90)
        self.history.itemSelectionChanged.connect(self.update_history_actions)
        layout.addWidget(self.history)
        actions = QHBoxLayout()
        self.transcript_button = QPushButton("전사문 열기")
        self.transcript_button.clicked.connect(self.open_transcript)
        self.copy_transcript_button = QPushButton("내용 복사")
        self.copy_transcript_button.clicked.connect(self.copy_transcript)
        self.history_retry_button = QPushButton("전사 / 재시도")
        self.history_retry_button.clicked.connect(self.retry_selected)
        self.history_folder_button = QPushButton("폴더 열기")
        self.history_folder_button.clicked.connect(self.open_history_folder)
        self.archive_retry_button = QPushButton("저장·삭제 재시도")
        self.archive_retry_button.clicked.connect(self.retry_archive_selected)
        for button in (self.transcript_button, self.copy_transcript_button, self.history_retry_button, self.history_folder_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        layout.addWidget(self.archive_retry_button)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setObjectName("muted")
        layout.addWidget(self.warning)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        self.settings_dialog = PreferencesDialog(self)
        self.reminder_popup = ReminderPopup(self)
        self.tray = QSystemTrayIcon(status_icon(False), self)
        menu = QMenu()
        show_action = QAction("상태 / 승인 화면", self)
        show_action.triggered.connect(self.reveal)
        menu.addAction(show_action)
        manual_action = QAction("수동 녹음 준비", self)
        manual_action.triggered.connect(self.manual)
        menu.addAction(manual_action)
        settings_action = QAction("환경설정", self)
        settings_action.triggered.connect(self.open_preferences)
        menu.addAction(settings_action)
        self.stop_action = QAction("즉시 중지", self)
        self.stop_action.triggered.connect(self.stop_recording)
        menu.addAction(self.stop_action)
        quit_action = QAction("종료", self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.reveal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.messageClicked.connect(self.reveal)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.refresh_recovery()
        self.refresh_history(force=True)
        if self.detection_enabled:
            try:
                if settings.extension_ids:
                    self.bridge.start()
            except OSError:
                self.banner.setText("브라우저 브리지 포트를 열 수 없습니다. Desktop/수동 녹음은 사용할 수 있습니다.")
            self.detectors = DetectionService(settings, self.bridge)
            self.detectors.start()
        if poc:
            self.browser_status.setText("M0 오디오 PoC 모드 · 자동 감지/전사 OFF")
            if not background:
                self.manual()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.timer.start(2000)
        self.render()

    def open_preferences(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def audit_bridge(self, code):
        root = Path(self.settings.recordings_dir)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "bridge-audit.jsonl"
        # Only fixed codes; bounded file growth. No token, Origin or request body.
        try:
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(root / "bridge-audit.previous.jsonl")
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"code": code, "monotonic": time.monotonic(), "wall_clock": time.time()}) + "\n")
        except OSError:
            pass

    def refresh_recovery(self):
        self.recovery_combo.clear()
        directories = []
        for root in self.settings.recording_roots:
            try:
                directories.extend(find_recovery(root))
            except OSError:
                continue
        for directory in directories:
            self.recovery_combo.addItem(directory.name, directory)
        if directories and self.controller.manager.can_offer:
            self.controller.manager.recovery()
            self.banner.setText("중단된 세션이 있습니다. 복구 점검 후 파일을 청취하세요. 자동 재녹음하지 않습니다.")

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def prepare_prompt(self):
        self.candidate_combo.clear()
        candidates = list(self.controller.manager.candidates.values())
        if len(candidates) > 1:
            self.candidate_combo.addItem("여러 후보가 있습니다. 세션을 선택하세요.", None)
        for candidate in candidates:
            self.candidate_combo.addItem(candidate.label, candidate.key)
        self.candidate_combo.setVisible(len(candidates) > 1)
        self.consent.setChecked(False)
        self.record_pc.setChecked(self.settings.record_pc)
        self.record_mic.setChecked(self.settings.record_mic)
        self.api_consent.setChecked(False)
        self.api_consent.setVisible(self.settings.auto_transcribe and self.settings.transcription_backend == "openai")
        try:
            self.folder_name.setText(suggest_folder_name(Path(self.settings.recordings_dir)))
        except (OSError, ValueError):
            self.folder_name.clear()
            self.banner.setText("저장 폴더를 확인할 수 없습니다. 경로와 접근 권한을 확인하세요.")
        self.consent_panel.show()
        self.update_start_gate()
        self.reveal()
        self.render()

    def update_start_gate(self, *_):
        requires_api = self.settings.auto_transcribe and self.settings.transcription_backend == "openai"
        self.start_button.setEnabled(self.consent.isChecked() and self.candidate_combo.currentData() is not None
                                     and (self.record_pc.isChecked() or self.record_mic.isChecked())
                                     and self.controller.manager.state == State.PROMPTED
                                     and (not requires_api or self.api_consent.isChecked()))

    def manual(self):
        if self.controller.capture_busy or not self.controller.manager.can_offer:
            self.reveal()
            return
        self.controller.manager.manual()
        self.prepare_prompt()

    def start_recording(self):
        try:
            self.controller.start(self.candidate_combo.currentData(), self.consent.isChecked(), True,
                                  api_approved=self.api_consent.isChecked(), folder_name=self.folder_name.text(),
                                  record_pc=self.record_pc.isChecked(), record_mic=self.record_mic.isChecked())
            self.banner.clear()
            self.stop_snooze_until = 0
            if self.controller.recorder:
                self.reminder = RecordingReminder(self.controller.store.metadata["monotonic_start"],
                                                  self.settings.silence_seconds, self.settings.long_recording_minutes)
                self.reminder_label.clear()
        except (OSError, ValueError, RuntimeError) as error:
            self.banner.setText(str(error))
        self.consent.setChecked(False)
        self.api_consent.setChecked(False)
        self.render()

    def skip(self):
        self.controller.manager.skip()
        self.consent.setChecked(False)
        self.render()

    def stop_recording(self):
        self.controller.stop()
        self.reminder_popup.hide()
        self.reminder_label.clear()
        self.render()

    def confirm_saved_folder(self):
        if not self.controller.save_pending or self.save_dialog_open:
            return
        self.save_dialog_open = True
        try:
            self.render()
            self.reveal()
            name = self.controller.store.directory.name
            error = ""
            while self.controller.save_pending:
                name, accepted = QInputDialog.getText(
                    self, "녹음 저장 폴더 확인",
                    "녹음이 중지되고 파일이 저장되었습니다. 폴더명을 확인하세요.\n"
                    "취소하면 시작할 때 지정한 폴더를 유지합니다.\n" + error,
                    text=name)
                try:
                    self.controller.confirm_save(name if accepted else None)
                    self.banner.clear()
                except (OSError, ValueError, RuntimeError) as exc:
                    error = str(exc) + "\n기존 녹음 파일은 보존되어 있습니다."
                    self.banner.setText(error)
            self.render()
        finally:
            self.save_dialog_open = False

    def keep_recording(self):
        if self.controller.manager.state == State.STOP_CONFIRM:
            self.controller.manager.keep_recording()
        now = time.monotonic()
        self.stop_snooze_until = now + 300
        if self.reminder:
            self.reminder.snooze(now)
        self.reminder_label.clear()
        self.reminder_popup.hide()
        self.render()

    def disable_reminder(self):
        self.keep_recording()
        if self.reminder:
            self.reminder.disable()
        self.stop_snooze_until = float("inf")
        self.render()

    def show_reminder(self, text):
        self.reminder_label.setText(text)
        self.reminder_popup.notify(text)
        # No notification sound: it would be captured by system loopback.
        QApplication.alert(self, 0)
        if self.settings.reminder_foreground:
            self.reveal()
        self.render()

    def selected_history_path(self):
        item = self.history.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def refresh_history(self, force=False):
        now = time.monotonic()
        if not force and now - self.history_at < 3:
            self.update_history_actions()
            return
        self.history_at = now
        try:
            rows = []
            for root in self.settings.recording_roots:
                try:
                    rows.extend(recent_sessions(root))
                except OSError:
                    continue
            rows = sorted(rows, key=lambda row: row[0].name, reverse=True)[:30]
        except OSError:
            return
        active = {task.store.directory: ("저장·전사 처리 중" if task.future.running() else "처리 대기")
                  for task in self.controller.transcriptions}
        rows = [(p, duration, active.get(p, label), state) for p, duration, label, state in rows]
        if rows != self.history_signature:
            selected = self.selected_history_path()
            self.history.clear()
            for path, duration, label, state in rows:
                item = QTreeWidgetItem([path.name, duration, label])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, state)
                self.history.addTopLevelItem(item)
                if path == selected:
                    self.history.setCurrentItem(item)
            if not self.history.currentItem() and self.history.topLevelItemCount():
                self.history.setCurrentItem(self.history.topLevelItem(0))
            self.history_signature = rows
        self.update_history_actions()

    def update_history_actions(self):
        if not hasattr(self, "transcript_button"):
            return
        path = self.selected_history_path()
        active = bool(path and self.controller.processing(path))
        text_exists = bool(path and (path / "transcript.md").is_file() and not active)
        self.transcript_button.setEnabled(text_exists)
        self.copy_transcript_button.setEnabled(text_exists)
        self.history_folder_button.setEnabled(path is not None)
        item = self.history.currentItem()
        state = item.data(0, Qt.ItemDataRole.UserRole + 1) if item else None
        cleanup_status = None
        if path:
            try:
                cleanup_status = SessionStore(path).metadata.get("audio_cleanup", {}).get("status")
            except (OSError, ValueError):
                pass
        recording_same = bool(path and self.controller.recorder and self.controller.store.directory == path)
        can_process = bool(path and not active and not recording_same
            and state in {"COMPLETE", "ERROR", "RECOVERY_REQUIRED"} and not self.controller.future
            and self.controller.manager.state not in {State.PROMPTED, State.FINALIZING})
        self.history_retry_button.setEnabled(can_process and cleanup_status not in {"deleting", "complete"})
        self.archive_retry_button.setEnabled(can_process and cleanup_status != "complete" and not self.controller.capture_busy)

    def open_transcript(self):
        path = self.selected_history_path()
        if path and self.transcript_button.isEnabled():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path / "transcript.md")))

    def copy_transcript(self):
        path = self.selected_history_path()
        if path and self.copy_transcript_button.isEnabled():
            try:
                QApplication.clipboard().setText((path / "transcript.md").read_text(encoding="utf-8"))
                self.banner.setText("전사문을 복사했습니다.")
            except (OSError, UnicodeError):
                self.banner.setText("전사문을 읽을 수 없습니다. 파일을 확인하세요.")
            self.render()

    def open_history_folder(self):
        path = self.selected_history_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def retry_selected(self):
        if self.history_retry_button.isEnabled():
            self.retry(directory=self.selected_history_path())

    def retry_archive_selected(self):
        if not self.archive_retry_button.isEnabled():
            return
        try:
            self.controller.retry_archive(SessionStore(self.selected_history_path()))
        except (OSError, ValueError, RuntimeError) as error:
            self.banner.setText(str(error))
        self.refresh_history(force=True)
        self.render()

    def recover(self):
        directory = self.recovery_combo.currentData()
        if directory and not self.controller.busy:
            self.controller.recover(directory)
            self.render()

    def retry(self, _checked=False, *, directory=None):
        if self.controller.future or self.controller.manager.state in {State.PROMPTED, State.FINALIZING}:
            return
        directory = directory or QFileDialog.getExistingDirectory(self, "전사할 녹음 세션 폴더", self.settings.recordings_dir)
        if not directory:
            return
        path = Path(directory).resolve()
        if path.parent not in self.settings.recording_roots or not SESSION_NAME.fullmatch(path.name):
            self.banner.setText("설정된 recordings 폴더 안의 세션을 선택하세요.")
            return
        try:
            api_approved = False
            if self.settings.transcription_backend == "openai":
                api_approved = QMessageBox.question(
                    self, "OpenAI 전송 동의", "선택한 녹음에 포함된 개별 음성을 OpenAI에 전송합니다.\n"
                    "API 사용 요금이 발생합니다. 이 녹음을 전송할까요?\n" + path.name,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
                if not api_approved:
                    return
            self.controller.retry_transcription(SessionStore(path), api_approved=api_approved)
        except (OSError, ValueError, RuntimeError):
            self.banner.setText("이 세션을 읽을 수 없습니다. 복구 점검을 먼저 실행하세요.")
        self.refresh_history(force=True)
        self.render()

    def open_folder(self):
        directory = self.controller.store.directory if self.controller.store else Path(self.settings.recordings_dir).resolve()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def tick(self):
        # Explorer's tray may become available after a login startup launch.
        if not self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.controller.poll()
        self.confirm_saved_folder()
        manager = self.controller.manager
        now = time.monotonic()
        if self.detectors:
            while True:
                try:
                    observations, observed_at, error = self.detectors.results.get_nowait()
                except queue.Empty:
                    break
                self.detector_error = error
                ready = self.tracker.update(observations, observed_at)
                if ready and manager.can_offer and not self.controller.capture_busy:
                    self.tracker.mark_offered(ready, now)
                    manager.offer(ready)
                    self.tray.showMessage("Zoom 회의 가능성이 감지되었습니다", "동의를 확인한 뒤 시작할 수 있습니다.")
                    self.prepare_prompt()
            if manager.state == State.RECORDING and manager.selected.source != "manual" and now > self.stop_snooze_until:
                if self.tracker.possibly_ended(manager.selected.key, now):
                    manager.suggest_stop()
                    self.stop_reminder_at = 0
        if manager.state == State.STOP_CONFIRM and now - self.stop_reminder_at > 300:
            self.show_reminder("회의가 끝났나요? 녹음은 계속되고 있습니다. 끝났다면 중지를 누르세요.")
            self.stop_reminder_at = now
        if self.controller.recorder and self.reminder and manager.state in {State.RECORDING, State.STOP_CONFIRM}:
            snapshot = self.controller.recorder.activity.snapshot(now)
            reason = self.reminder.due(now, snapshot)
            if reason:
                text = (f"{self.reminder.silence_seconds // 60}분 동안 말소리가 감지되지 않았어요. 녹음은 계속되고 있습니다."
                        if reason == "silence" else "장시간 녹음 중입니다. 회의가 끝났다면 중지를 눌러주세요.")
                self.show_reminder(text)
            elif not self.reminder.pending and manager.state != State.STOP_CONFIRM:
                self.reminder_label.clear()
                self.reminder_popup.hide()
        while True:
            try:
                code, track = self.controller.notifications.get_nowait()
            except queue.Empty:
                break
            text = ERRORS.get(code, "오류가 발생했습니다. 파일과 metadata.json을 확인하세요. 코드: " + code)
            if track:
                text = f"[{track}] " + text
            self.banner.setText(text)
            self.tray.showMessage("로컬 녹음 상태", text)
            if code not in {"transcription_complete", "recovery_finished"} and manager.state not in {State.RECORDING, State.STOP_CONFIRM}:
                self.reveal()
        self.refresh_history()
        self.render()

    def render(self):
        manager = self.controller.manager
        state = manager.state
        recording = state in {State.RECORDING, State.STOP_CONFIRM}
        self.levels_label.setVisible(recording)
        if recording and self.controller.recorder:
            snapshot = self.controller.recorder.activity.snapshot()
            self.levels_label.setText("   ".join(
                label + ": " + ("▮" * min(10, max(1, int(snapshot.levels[name] ** 0.5 * 30)))
                                  if name in snapshot.levels and snapshot.levels[name] > 0.0001
                                  else "조용함" if name in snapshot.levels else "수신 확인 중")
                for name, label in (("remote", "PC 소리"), ("mic", "마이크"))
                if name in self.controller.recorder.settings.selected_tracks))
        reminder_visible = recording and bool(self.reminder_label.text())
        self.reminder_label.setVisible(reminder_visible)
        if not recording:
            self.reminder_popup.hide()
        self.dismiss_reminder_button.setVisible(reminder_visible)
        self.continue_button.setVisible(reminder_visible or state == State.STOP_CONFIRM)
        running = sum(task.future.running() for task in self.controller.transcriptions)
        waiting = sum(not task.future.running() and not task.future.done() for task in self.controller.transcriptions)
        self.processing_label.setText(f"음성 저장·전사 처리 {running}건 · 대기 {waiting}건")
        self.processing_label.setVisible(bool(self.controller.transcriptions))
        self.update_history_actions()
        interval = 250 if self.controller.busy else (2000 if self.detection_enabled else 5000)
        if hasattr(self, "timer") and self.timer.interval() != interval:
            self.timer.setInterval(interval)
        store = self.controller.store
        options = self.settings
        policy = StoragePolicy.from_settings(options)
        if policy.delete_after(transcribe=options.auto_transcribe):
            policy_text = "WAV로 전사 후 음성 삭제 · MP3 생성 생략"
        elif policy.delete_audio_after_transcription:
            policy_text = ("MP3 보관" if policy.convert_to_mp3 else "WAV 보관") + " · 나중 전사 성공 후 음성 삭제"
        else:
            policy_text = "MP3 보관 · 검증 후 임시 WAV 삭제" if policy.convert_to_mp3 else "WAV 보관 · 음성 삭제 안 함"
        self.storage_policy_label.setText(policy_text + "\n실패·빈 전사·누락 시에는 음성을 보존합니다.")
        elapsed = max(0, int(time.monotonic() - store.metadata["monotonic_start"])) if recording and store else 0
        connected = time.monotonic() - self.bridge.last_heartbeat < 20
        signature = (state, self.controller.busy, elapsed, self.banner.text(), self.settings.auto_transcribe,
                     self.settings.convert_to_mp3, self.settings.delete_audio_after_transcription,
                     self.settings.transcription_backend, self.detector_error, connected,
                     self.recovery_combo.count(), self.controller.save_pending,
                     str(store.directory) if store else None,
                     (len(store.metadata["segments"]), len(store.metadata["errors"]), len(store.metadata["gaps"]),
                      repr(store.metadata["tracks"])) if store else None)
        if signature == getattr(self, "_render_signature", None):
            return
        self._render_signature = signature
        self.recording_badge.setText("녹음 + 전사  ·  " + ("로컬" if self.settings.transcription_backend == "local" else "OpenAI API")
                                    if self.settings.auto_transcribe else "녹음만  ·  " + ("MP3 저장" if self.settings.convert_to_mp3 else "WAV 저장"))
        self.warning.setText("PC 전체 소리와 마이크를 녹음하며, 다른 앱 소리도 포함됩니다.\n" +
                             ("API 전사 시 별도 동의 후 OpenAI에 오디오를 전송합니다." if self.settings.auto_transcribe and self.settings.transcription_backend == "openai"
                              else "파일은 이 PC에 저장됩니다. Zoom 공식 녹음과는 별개입니다."))
        self.elapsed_label.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        self.elapsed_label.setVisible(recording)
        if self.poc:
            mode_text = "테스트 모드 · 자동 감지와 전사 꺼짐"
        elif self.detection_enabled:
            mode_text = "Zoom 감지 켜짐 · 녹음은 직접 시작"
        else:
            mode_text = "수동 모드 · 녹음은 직접 시작"
        self.mode_label.setText(mode_text)
        self.status.setText(LABELS[state])
        if state != self.last_state:
            self.status.setStyleSheet(f"font-size:22px;font-weight:600;padding:4px 0;color:{'#c02030' if recording else '#18283f'}")
            self.tray.setIcon(status_icon(recording))
            self.setWindowIcon(status_icon(recording))
            self.last_state = state
        self.tray.setToolTip("Zoom Recorder · " + LABELS[state])
        self.stop_button.setEnabled(recording)
        self.stop_button.setVisible(recording)
        self.stop_action.setEnabled(recording)
        self.manual_button.setEnabled(manager.can_offer and not self.controller.capture_busy)
        self.manual_button.setVisible(manager.can_offer and not self.controller.capture_busy)
        self.consent_panel.setVisible(state == State.PROMPTED)
        self.retry_button.setEnabled(not self.controller.future and state not in {State.PROMPTED, State.FINALIZING})
        self.recovery_button.setEnabled(not self.controller.busy and state != State.PROMPTED and self.recovery_combo.count() > 0)
        self.quit_button.setEnabled(not self.controller.busy)
        self.settings_dialog.save_button.setEnabled(not self.controller.busy and state != State.PROMPTED)
        self.banner.setVisible(bool(self.banner.text()))
        store = self.controller.store
        if store:
            elapsed = max(0, int(time.monotonic() - store.metadata["monotonic_start"])) if recording else None
            devices = "\n".join(f"{'PC 소리' if name == 'remote' else '마이크'}: {info.get('device', '확인 중')}"
                                for name, info in list(store.metadata["tracks"].items()))
            partial = " · 불완전 녹음/누락 있음" if store.metadata["errors"] or store.metadata["gaps"] else ""
            if recording:
                selected = store.metadata.get("selected_tracks", ["remote", "mic"])
                sources = " + ".join("PC 소리" if n == "remote" else "마이크" for n in selected)
                text = f"{elapsed // 60:02d}:{elapsed % 60:02d} · {sources}{partial}"
            elif state == State.FINALIZING:
                text = ("녹음 파일을 저장했습니다. 폴더명을 확인하세요." if self.controller.save_pending
                        else "녹음을 멈췄습니다. 파일 저장을 마무리하고 있습니다.")
            elif state == State.TRANSCRIBING:
                text = "이전 녹음의 저장·전사·음성 정리를 처리 중입니다. 다음 녹음을 시작할 수 있습니다."
            elif state == State.COMPLETE:
                text = "녹음 폴더에서 파일을 확인하세요." + partial
            elif state == State.PROMPTED:
                text = "아직 녹음하지 않습니다. 참석자 동의 확인 후 시작하세요."
            else:
                text = "녹음은 꺼져 있습니다." + partial
            self.details.setText(text)
            self.device_details.setText(f"{devices}\n\n저장된 오디오 {len(store.metadata['segments'])}개\n{store.directory}")
        else:
            self.details.setText("아직 녹음하지 않습니다. 참석자 동의 확인 후 시작하세요."
                                 if state == State.PROMPTED else "녹음은 꺼져 있습니다. 준비되면 시작을 누르세요.")
        if self.detectors:
            connected = time.monotonic() - self.bridge.last_heartbeat < 20
            self.browser_status.setText(("브라우저 확장 연결됨 · 후보 신호만 수신" if connected else
                                        "브라우저 자동 감지 불가 · 확장 연결 전에는 수동 시작을 사용하세요.") +
                                       ("\nDesktop 감지 신호를 읽을 수 없습니다. 수동 시작을 사용하세요." if self.detector_error else ""))

    def quit(self):
        if self.controller.busy:
            self.banner.setText("녹음 중이면 즉시 중지를 누르고 저장/전사가 끝난 뒤 종료하세요.")
            self.reveal()
            return
        self.timer.stop()
        if self.detectors:
            self.detectors.stop()
        self.bridge.stop()
        self.controller.close()
        self.settings_dialog.shutdown()
        self.tray.hide()
        self.settings_dialog.hide()
        self.reminder_popup.hide()
        if QApplication.clipboard().text() == self.bridge.token:
            QApplication.clipboard().clear()
        self.allow_close = True
        self.close()
        QApplication.quit()

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        elif QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        elif self.controller.busy:
            event.ignore()
            self.banner.setText("트레이를 사용할 수 없습니다. 녹음/처리가 끝날 때까지 창을 유지합니다.")
        else:
            event.ignore()
            self.quit()
