import queue
import time

import pytest

from app.audio.activity import ActivitySnapshot, RecordingReminder, SpeechMonitor
from test_recorder_controller import spin_until


def test_reminder_two_minutes_snooze_resume_and_disable():
    reminder = RecordingReminder(0, 120, 0)
    quiet = ActivitySnapshot(0, {}, True)
    assert reminder.due(119.99, quiet) is None
    assert reminder.due(120, quiet) == "silence"
    assert reminder.due(121, quiet) is None
    reminder.snooze(121)
    assert reminder.due(420, quiet) is None
    assert reminder.due(421, quiet) == "silence"
    assert reminder.due(422, ActivitySnapshot(422, {}, True)) is None
    assert reminder.pending is None
    reminder.disable()
    assert reminder.due(1000, quiet) is None


def test_missing_audio_is_not_silence_and_long_reminder_independent():
    missing = ActivitySnapshot(0, {}, False)
    reminder = RecordingReminder(0, 120, 60)
    assert reminder.due(120, missing) is None
    assert reminder.due(3600, missing) == "long"
    off = RecordingReminder(0, 0, 0)
    assert off.due(99999, ActivitySnapshot(0, {}, True)) is None


@pytest.mark.parametrize("rate,channels", [(16000, 1), (48000, 2), (44100, 2)])
def test_native_vad_synthetic_silence_both_tracks_and_stale_data(rate, channels):
    now = time.monotonic()
    monitor = SpeechMonitor(now, queue.Queue())
    monitor.start()
    try:
        monitor.feed("remote", bytes(rate // 50 * channels * 2), rate, channels, 0)
        spin_until(lambda: "remote" in monitor.observed)
        assert not monitor.snapshot(now + 0.03).healthy
        monitor.feed("mic", bytes(rate // 50 * channels * 2), rate, channels, 0)
        spin_until(lambda: "mic" in monitor.observed)
        snapshot = monitor.snapshot(now + 0.03)
        assert snapshot.healthy and snapshot.last_speech == now
        assert snapshot.levels == {"remote": 0, "mic": 0}
        assert not monitor.snapshot(now + 3).healthy
    finally:
        monitor.close()
    assert not monitor.thread.is_alive()


def test_vad_speech_updates_clock_and_handles_split_pcm(monkeypatch):
    import webrtcvad
    monkeypatch.setattr(webrtcvad.Vad, "is_speech", lambda *args: True)
    now = time.monotonic()
    monitor = SpeechMonitor(now, queue.Queue())
    monitor.start()
    try:
        for i in range(6):
            monitor.feed("mic", b"\x01\x00" * 160, 16000, 1, i * 0.01)
        spin_until(lambda: monitor.last_speech > now)
        assert monitor.last_speech == pytest.approx(now + 0.06)
    finally:
        monitor.close()


def test_vad_failure_disables_silence_and_reports_without_capture_error(monkeypatch):
    import webrtcvad
    def fail(*args): raise RuntimeError("synthetic")
    monkeypatch.setattr(webrtcvad, "Vad", fail)
    events = queue.Queue()
    monitor = SpeechMonitor(time.monotonic(), events)
    monitor.start()
    assert events.get(timeout=2) == ("speech_monitor_unavailable", None)
    assert not monitor.snapshot().healthy
    monitor.close()
