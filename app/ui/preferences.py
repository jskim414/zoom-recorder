from dataclasses import replace
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from app.settings import save_settings
from app.storage.locations import check_recording_root
from app.processing.model_download import (MODEL_NAMES, ModelDownload, local_engine_available,
                                            missing_gpu_libraries, model_directory, model_installed)
from app.startup import set_startup, startup_enabled
from app.ui.theme import STYLE


class PreferencesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.download_job = ModelDownload()
        self.download_timer = QTimer(self)
        self.download_timer.setInterval(500)
        self.download_timer.timeout.connect(self.poll_download)
        self.engine_available = local_engine_available()
        self.setWindowTitle("환경설정 · Zoom Recorder")
        self.resize(640, 580)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("나에게 맞는 녹음 환경")
        title.setObjectName("title")
        layout.addWidget(title)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        general = QWidget()
        form = QFormLayout(general)
        form.setSpacing(16)
        self.auto_detect = QCheckBox("Zoom 회의 가능성을 감지하면 알림")
        self.auto_detect.setChecked(window.saved_settings.auto_detect)
        self.recording_mode = QComboBox()
        self.recording_mode.addItem("녹음만 · MP3 저장", False)
        self.recording_mode.addItem("녹음 + 전사 · MP3와 텍스트 저장", True)
        self.recording_mode.setCurrentIndex(int(window.saved_settings.auto_transcribe))
        self.startup = QCheckBox("Windows 로그인 시 앱 실행")
        self.initial_startup = False
        try:
            self.initial_startup = startup_enabled()
            self.startup.setChecked(self.initial_startup)
        except Exception:
            self.startup.setEnabled(False)
            self.startup.setToolTip("Windows 시작프로그램 상태를 읽을 수 없습니다.")
        form.addRow("녹음 모드", self.recording_mode)
        mode_note = QLabel("녹음만 선택하면 AI 모델을 실행하지 않습니다.\n저장된 녹음은 나중에 전사할 수 있습니다.")
        mode_note.setObjectName("muted")
        form.addRow(mode_note)
        form.addRow("자동 실행", self.startup)
        form.addRow("회의 알림", self.auto_detect)
        self.silence = QComboBox()
        for seconds, label in ((120, "2분"), (300, "5분"), (600, "10분"), (0, "끄기")):
            self.silence.addItem(label, seconds)
        self.silence.setCurrentIndex(self.silence.findData(window.saved_settings.silence_seconds))
        form.addRow("발화 없음 알림", self.silence)
        self.foreground = QCheckBox("알림 시 앱 창 앞으로 표시")
        self.foreground.setChecked(window.saved_settings.reminder_foreground)
        form.addRow("알림 표시", self.foreground)
        self.long_recording = QComboBox()
        for minutes in (60, 30, 120, 0):
            self.long_recording.addItem(f"{minutes}분마다" if minutes else "끄기", minutes)
        self.long_recording.setCurrentIndex(self.long_recording.findData(window.saved_settings.long_recording_minutes))
        form.addRow("장시간 녹음 알림", self.long_recording)
        note = QLabel("앱이 자동 실행되어도 녹음은 꺼져 있습니다.\n매번 동의를 확인하고 시작을 눌러야 녹음합니다.")
        note.setWordWrap(True)
        form.addRow(note)
        self.recordings_path = QLineEdit(str(window.saved_settings.recordings_dir))
        location_row = QHBoxLayout()
        location_row.addWidget(self.recordings_path)
        choose_location = QPushButton("폴더 선택")
        choose_location.clicked.connect(self.choose_recordings)
        location_row.addWidget(choose_location)
        form.addRow("음성·전사 저장 위치", location_row)
        form.addRow(QLabel("저장 위치 변경은 앱을 종료한 뒤 다시 열면 적용됩니다. 이전 녹음도 목록에서 확인할 수 있습니다."))
        self.record_pc = QCheckBox("PC 소리 · 시스템 전체 출력")
        self.record_mic = QCheckBox("마이크")
        self.record_pc.setChecked(window.saved_settings.record_pc)
        self.record_mic.setChecked(window.saved_settings.record_mic)
        form.addRow("기본 녹음 대상", self.record_pc)
        form.addRow(self.record_mic)
        self.create_mix = QCheckBox("두 음성을 녹음하면 합본 MP3도 생성")
        self.create_mix.setChecked(window.saved_settings.create_mix)
        form.addRow("합본", self.create_mix)
        policy = QLabel("개별 MP3를 128kbps로 저장합니다. 녹음 중에는 임시 WAV 공간이 필요합니다.\n"
                        "MP3 검증과 필요한 전사가 끝나면 임시 WAV를 자동 삭제합니다. 실패·누락 시에는 보존합니다.")
        policy.setWordWrap(True)
        form.addRow(policy)
        for label in general.findChildren(QLabel):
            label.setWordWrap(True)
        general_scroll = QScrollArea()
        general_scroll.setWidgetResizable(True)
        general_scroll.setWidget(general)
        tabs.addTab(general_scroll, "일반")

        transcription = QWidget()
        form = QFormLayout(transcription)
        form.setSpacing(16)
        self.backend = QComboBox()
        self.backend.addItem("이 PC에서 처리 · 로컬", "local")
        self.backend.addItem("OpenAI API · 별도 전송 동의 필요", "openai")
        self.backend.setCurrentIndex(self.backend.findData(window.saved_settings.transcription_backend))
        form.addRow("전사 방식", self.backend)
        self.transcription_note = QLabel()
        self.transcription_note.setWordWrap(True)
        self.transcription_note.setObjectName("muted")
        form.addRow(self.transcription_note)
        self.device = QComboBox()
        self.device.addItem("CPU · small 추천", "cpu")
        self.device.addItem("GPU · large-v3 추천", "cuda")
        self.device.setCurrentIndex(self.device.findData(window.saved_settings.device))
        form.addRow("전사 처리", self.device)
        self.device_note = QLabel()
        self.device_note.setWordWrap(True)
        form.addRow(self.device_note)
        self.model_choice = QComboBox()
        for name in MODEL_NAMES:
            label = name + {"small": " · CPU 추천", "large-v3": " · GPU 추천"}.get(name, "")
            self.model_choice.addItem(label, name)
        self.model_choice.addItem("직접 설치한 모델 폴더", "custom")
        selected = next((name for name in MODEL_NAMES
                         if model_directory(name).resolve() == Path(window.saved_settings.model).resolve()), "custom")
        self.model_choice.setCurrentIndex(self.model_choice.findData(selected))
        form.addRow("로컬 전사 모델", self.model_choice)
        self.model = QLineEdit(window.saved_settings.model)
        self.model.textChanged.connect(self.update_model_status)
        self.model_path_container = QWidget()
        model_row = QHBoxLayout(self.model_path_container)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.addWidget(self.model)
        self.choose = QPushButton("폴더 선택")
        self.choose.clicked.connect(self.choose_model)
        model_row.addWidget(self.choose)
        self.model_path_label = QLabel("모델 폴더")
        form.addRow(self.model_path_label, self.model_path_container)
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        form.addRow(self.model_status)
        download_row = QHBoxLayout()
        self.download_button = QPushButton("선택 모델 다운로드")
        self.download_button.setToolTip("클릭하면 인터넷에서 모델만 내려받습니다. 녹음 파일은 전송하지 않습니다.")
        self.download_button.clicked.connect(self.download_model)
        self.cancel_download_button = QPushButton("다운로드 취소")
        self.cancel_download_button.clicked.connect(self.cancel_download)
        download_row.addWidget(self.download_button)
        download_row.addWidget(self.cancel_download_button)
        form.addRow(download_row)
        self.cpu_threads = QSpinBox()
        self.cpu_threads.setRange(1, 16)
        self.cpu_threads.setValue(window.saved_settings.cpu_threads)
        self.cpu_threads.setSuffix("개")
        form.addRow("CPU 스레드", self.cpu_threads)
        self.quality = QComboBox()
        self.quality.addItem("절전 · 빠른 전사", 1)
        self.quality.addItem("정밀 · 처리 시간 증가", 5)
        self.quality.setCurrentIndex(self.quality.findData(window.saved_settings.beam_size))
        form.addRow("처리 수준", self.quality)
        self.api_note = QLabel("OPENAI_API_KEY 환경변수: " + ("설정됨" if os.environ.get("OPENAI_API_KEY") else "미설정") +
                              "\n키는 앱 설정이나 배포 파일에 저장하지 않습니다.\nAPI는 인터넷과 별도 사용 요금이 필요합니다.\n전송 대상: 선택한 개별 녹음 음성 · OpenAI\nAPI 시간 표시는 최대 30초 조각 기준입니다.")
        self.api_note.setWordWrap(True)
        form.addRow(self.api_note)
        transcription_scroll = QScrollArea()
        transcription_scroll.setWidgetResizable(True)
        transcription_scroll.setWidget(transcription)
        tabs.addTab(transcription_scroll, "전사")
        self.backend.currentIndexChanged.connect(self.update_transcription_controls)
        self.recording_mode.currentIndexChanged.connect(self.update_transcription_controls)
        self.device.currentIndexChanged.connect(self.select_recommended_model)
        self.device.activated.connect(self.select_recommended_model)
        self.model_choice.currentIndexChanged.connect(self.select_model)

        browser = QWidget()
        browser_layout = QFormLayout(browser)
        browser_layout.setSpacing(14)
        window.browser_status = QLabel("브라우저 감지 꺼짐 · 확장을 연결하면 사용할 수 있습니다.")
        window.browser_status.setWordWrap(True)
        browser_layout.addRow(window.browser_status)
        guide = QLabel("Chrome/Edge에 browser-extension 폴더를 확장으로 설치한 뒤,\n확장 ID를 등록하고 아래 연결 토큰을 확장 설정에 붙여 넣으세요.")
        guide.setWordWrap(True)
        browser_layout.addRow(guide)
        self.extension_ids = QLineEdit(", ".join(window.saved_settings.extension_ids))
        self.extension_ids.setPlaceholderText("32자리 확장 ID · 여러 개는 쉼표로 구분")
        browser_layout.addRow("확장 ID", self.extension_ids)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(window.saved_settings.bridge_port)
        browser_layout.addRow("연결 포트", self.port)
        window.token_field = QLineEdit(window.bridge.token)
        window.token_field.setReadOnly(True)
        window.token_field.setEchoMode(QLineEdit.EchoMode.Password)
        token_row = QHBoxLayout()
        token_row.addWidget(window.token_field)
        copy = QPushButton("복사")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(window.bridge.token))
        token_row.addWidget(copy)
        browser_layout.addRow("연결 토큰", token_row)
        browser_layout.addRow(QLabel("앱을 다시 열면 연결 토큰도 바뀝니다."))
        tabs.addTab(browser, "브라우저 연결")

        support = QWidget()
        support_layout = QVBoxLayout(support)
        window.device_details = QLabel("녹음 장치는 녹음을 시작하면 표시됩니다.")
        window.device_details.setWordWrap(True)
        window.device_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        support_layout.addWidget(window.device_details)
        support_layout.addWidget(QLabel("중단된 녹음 복구"))
        window.recovery_combo = QComboBox()
        support_layout.addWidget(window.recovery_combo)
        window.recovery_button = QPushButton("선택한 녹음 복구")
        window.recovery_button.clicked.connect(window.recover)
        support_layout.addWidget(window.recovery_button)
        window.retry_button = QPushButton("저장된 녹음 전사하기")
        window.retry_button.clicked.connect(window.retry)
        support_layout.addWidget(window.retry_button)
        support_layout.addStretch()
        window.quit_button = QPushButton("앱 종료")
        window.quit_button.clicked.connect(window.quit)
        support_layout.addWidget(window.quit_button)
        tabs.addTab(support, "상태 및 복구")
        self.message = QLabel("녹음 대상·전사는 저장 즉시, 저장 위치·감지·브라우저 설정은 재시작 후 적용됩니다.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        actions.addStretch()
        close = QPushButton("닫기")
        close.clicked.connect(self.hide)
        self.save_button = QPushButton("설정 저장")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(close)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.update_transcription_controls()

    def update_transcription_controls(self, *_):
        local = self.backend.currentData() == "local"
        for widget in (self.model, self.choose, self.device, self.model_choice, self.cpu_threads, self.quality):
            widget.setEnabled(local)
        self.model.setReadOnly(self.model_choice.currentData() != "custom")
        custom = local and self.model_choice.currentData() == "custom"
        self.model_path_container.setVisible(custom)
        self.model_path_label.setVisible(custom)
        gpu = local and self.device.currentData() == "cuda"
        self.device_note.setVisible(gpu)
        if gpu:
            missing = missing_gpu_libraries()
            self.device_note.setText("CUDA/cuDNN 라이브러리를 찾지 못했습니다. GPU 실패 시 CPU로 전사합니다."
                                     if missing else "GPU 실행 환경은 실제 전사 시 확인됩니다.")
            self.device_note.setToolTip(", ".join(name + ".dll" for name in missing))
        self.api_note.setVisible(not local)
        self.transcription_note.setText(
            "CPU는 small, GPU는 large-v3를 추천합니다. 모델은 직접 바꿀 수 있습니다.\n"
            "GPU 실패 시 같은 모델을 CPU로 처리합니다. large-v3는 CPU에서 오래 걸릴 수 있습니다."
            if local else "녹음 시작 전 별도 전송 동의를 받습니다. 저장된 녹음 재전사에도 다시 동의해야 합니다.")
        self.update_model_status()

    def select_recommended_model(self, *_):
        name = "large-v3" if self.device.currentData() == "cuda" else "small"
        self.model_choice.setCurrentIndex(self.model_choice.findData(name))
        self.update_transcription_controls()

    def select_model(self, *_):
        name = self.model_choice.currentData()
        if name != "custom":
            self.model.setText(str(model_directory(name)))
        self.update_transcription_controls()

    def update_model_status(self, *_):
        if not hasattr(self, "download_button"):
            return
        local = self.backend.currentData() == "local"
        installed = model_installed(self.model.text())
        active = self.download_job.active
        if active:
            text = f"{self.download_job.name} 다운로드 중 · 앱을 종료하면 중단됩니다."
        elif not self.engine_available:
            text = "로컬 전사 엔진이 없습니다. Local 배포본을 사용하세요."
        elif installed:
            text = "설치됨 · 설정 저장 후 사용할 수 있습니다."
        else:
            text = "미설치 또는 설치 미완료 · 다운로드가 필요합니다. large-v3는 수 GB를 내려받습니다."
        self.model_status.setText(text)
        self.model_status.setToolTip(self.model.text())
        self.model_status.setVisible(local)
        self.download_button.setVisible(local)
        self.download_button.setEnabled(local and self.engine_available and not active and not installed
                                        and self.model_choice.currentData() != "custom")
        self.cancel_download_button.setVisible(active)

    def download_model(self):
        if not self.download_button.isEnabled():
            return
        try:
            self.download_job.start(self.model_choice.currentData())
        except (OSError, RuntimeError, ValueError) as error:
            self.message.setText("다운로드를 시작하지 못했습니다: " + str(error))
            return
        self.message.setText("모델만 다운로드합니다. 녹음 파일은 전송하지 않습니다. 완료 후 설정 저장을 누르세요.")
        self.download_timer.start()
        self.update_model_status()

    def poll_download(self):
        name = self.download_job.name
        code = self.download_job.poll()
        if code is None:
            return
        self.download_timer.stop()
        if code == 0 and model_installed(model_directory(name)):
            self.message.setText(f"{name} 다운로드를 완료했습니다. 원하는 모델을 선택하고 설정 저장을 누르세요.")
        else:
            self.message.setText("모델 다운로드에 실패했습니다. 인터넷·저장 공간 및 model-download.log를 확인하고 재시도하세요.")
        self.update_model_status()

    def cancel_download(self):
        self.download_job.cancel()
        self.download_timer.stop()
        self.message.setText("다운로드를 중단했습니다. 같은 모델을 다시 다운로드하면 이어받을 수 있습니다.")
        self.update_model_status()

    def shutdown(self):
        self.download_job.cancel()
        self.download_timer.stop()

    def choose_model(self):
        directory = QFileDialog.getExistingDirectory(self, "로컬 전사 모델 폴더", self.model.text())
        if directory:
            self.model_choice.setCurrentIndex(self.model_choice.findData("custom"))
            self.model.setText(directory)

    def choose_recordings(self):
        directory = QFileDialog.getExistingDirectory(self, "음성·전사 저장 폴더", self.recordings_path.text())
        if directory:
            self.recordings_path.setText(directory)

    def save(self):
        if self.window.controller.busy or self.window.controller.manager.state.value == "PROMPTED":
            self.message.setText("녹음과 처리가 끝난 뒤 설정을 저장하세요.")
            return
        changed_startup = False
        try:
            updated = replace(self.window.saved_settings,
                              recordings_dir=self.recordings_path.text().strip(),
                              record_pc=self.record_pc.isChecked(), record_mic=self.record_mic.isChecked(),
                              create_mix=self.create_mix.isChecked(),
                              auto_detect=self.auto_detect.isChecked(),
                              silence_seconds=self.silence.currentData(),
                              reminder_foreground=self.foreground.isChecked(),
                              long_recording_minutes=self.long_recording.currentData(),
                              auto_transcribe=self.recording_mode.currentData(),
                              transcription_backend=self.backend.currentData(),
                              cpu_threads=self.cpu_threads.value(), beam_size=self.quality.currentData(),
                              model=self.model.text().strip(), device=self.device.currentData(),
                              extension_ids=[value.strip() for value in self.extension_ids.text().split(",") if value.strip()],
                              bridge_port=self.port.value()).validate()
            updated.recordings_dir = str(check_recording_root(updated.recordings_dir))
            previous = list(self.window.saved_settings.previous_recordings_dirs)
            previous.append(str(Path(self.window.settings.recordings_dir).resolve()))
            updated.previous_recordings_dirs = list(dict.fromkeys(
                str(Path(p).resolve()) for p in previous if Path(p).resolve() != Path(updated.recordings_dir)))
            if self.startup.isEnabled() and self.startup.isChecked() != self.initial_startup:
                set_startup(self.startup.isChecked())
                changed_startup = True
            save_settings(updated, self.window.config_path)
        except Exception as error:
            if changed_startup:
                try:
                    set_startup(self.initial_startup)
                except Exception:
                    self.message.setText("설정 저장에 실패했습니다. Windows 시작프로그램 등록 상태도 확인하세요.")
                    return
            self.message.setText("설정을 저장하지 못했습니다: " + str(error))
            return
        self.initial_startup = self.startup.isChecked()
        self.window.saved_settings = updated
        # Recording settings can be applied safely between sessions. Detector transport stays fixed until restart.
        for name in ("auto_transcribe", "transcription_backend", "model", "device", "cpu_threads", "beam_size",
                     "silence_seconds", "reminder_foreground", "long_recording_minutes",
                     "record_pc", "record_mic", "create_mix"):
            setattr(self.window.settings, name, getattr(updated, name))
            setattr(self.window.controller.settings, name, getattr(updated, name))
        if self.window.poc:
            self.window.settings.auto_transcribe = False
            self.window.controller.settings.auto_transcribe = False
        self.message.setText("저장했습니다. 녹음 대상·전사 설정을 적용했습니다. 저장 위치·감지·브라우저 변경은 앱을 종료 후 다시 열어 주세요.")
        if updated.transcription_backend == "local" and not model_installed(updated.model):
            self.message.setText("설정을 저장했습니다. 선택한 모델을 다운로드해야 전사할 수 있습니다. 녹음 파일은 보존됩니다.")
        self.window.banner.setText("환경설정을 저장했습니다.")
        self.window.render()
