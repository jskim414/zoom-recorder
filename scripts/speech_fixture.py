"""Generate fixed synthetic speech directly to WAV with Windows' local SAPI voices."""
from pathlib import Path


def generate(directory: Path):
    import pythoncom
    from win32com.client import Dispatch

    directory.mkdir(parents=True, exist_ok=False)
    pythoncom.CoInitialize()
    try:
        voice = Dispatch("SAPI.SpVoice")
        voices = {item.GetAttribute("Name"): item for item in voice.GetVoices()}
        samples = [
            ("Microsoft Heami Desktop", "remote_0001.wav",
             "안녕하세요. 오늘 회의에서는 프로젝트 일정과 테스트 결과를 확인합니다. 다음 회의는 월요일 오전 열 시입니다."),
            ("Microsoft Zira Desktop", "mic_0001.wav",
             "Hello everyone. Today we will review the project schedule and the test results. "
             "The next meeting is on Monday at ten in the morning."),
        ]
        for name, filename, text in samples:
            if name not in voices:
                raise RuntimeError(f"Required local Windows voice not installed: {name}")
            stream = Dispatch("SAPI.SpFileStream")
            stream.Open(str((directory / filename).resolve()), 3, False)
            try:
                voice.Voice = voices[name]
                voice.AudioOutputStream = stream
                voice.Speak(text)
            finally:
                stream.Close()
    finally:
        pythoncom.CoUninitialize()
