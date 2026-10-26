import pytest

from app.detection.session_manager import Candidate, CandidateTracker, Observation, SessionManager, State
from app.detectors.desktop import qualifies


def candidate(key="a"):
    return Candidate(key, "desktop", "candidate")


@pytest.mark.parametrize("checked,clicked", [(False, False), (False, True), (True, False), (1, True)])
def test_consent_requires_checkbox_and_click(checked, clicked):
    manager = SessionManager()
    manager.offer([candidate()])
    with pytest.raises(PermissionError):
        manager.approve("a", checked, clicked)
    assert manager.state == State.PROMPTED and manager.consent_at is None


def test_full_lifecycle_and_stop_confirmation():
    manager = SessionManager()
    manager.offer([candidate()])
    manager.approve("a", True, True)
    manager.suggest_stop()
    assert manager.state == State.STOP_CONFIRM
    manager.keep_recording()
    manager.stop()
    manager.transcribe()
    manager.complete()
    assert manager.history == [State.IDLE, State.CANDIDATE, State.PROMPTED, State.RECORDING,
                               State.STOP_CONFIRM, State.RECORDING, State.FINALIZING,
                               State.TRANSCRIBING, State.COMPLETE]
    assert manager.consent_at is None


def test_invalid_transitions_duplicate_and_multi_session():
    manager = SessionManager()
    with pytest.raises(RuntimeError):
        manager.stop()
    manager.offer([candidate("a"), candidate("b")])
    with pytest.raises(RuntimeError):
        manager.approve("unknown", True, True)
    manager.approve("b", True, True)
    assert manager.selected.key == "b"
    with pytest.raises(RuntimeError):
        manager.manual()
    with pytest.raises(RuntimeError):
        manager.approve("a", True, True)


def test_stability_skip_dedup_cooldown():
    tracker = CandidateTracker(7, 30, 30)
    a, b = candidate("a"), candidate("b")
    assert tracker.update([Observation(a, True)], 0) == []
    assert tracker.update([Observation(a, True)], 6) == []
    assert tracker.update([Observation(a, True)], 7) == [a]
    tracker.mark_offered([a], 7)
    assert tracker.update([Observation(a, True), Observation(b, True)], 8) == []
    assert tracker.update([Observation(a, True), Observation(b, True)], 15) == []
    assert tracker.update([Observation(a, True), Observation(b, True)], 37) == [b]


def test_signal_flap_and_silent_meeting():
    tracker = CandidateTracker()
    a = candidate()
    tracker.update([Observation(a, True)], 0)
    tracker.update([Observation(a, False)], 6)
    assert tracker.update([Observation(a, True)], 7) == []
    tracker.update([Observation(a, False, True)], 100)
    assert not tracker.possibly_ended(a.key, 110)
    assert tracker.possibly_ended(a.key, 131)


@pytest.mark.parametrize("values,expected", [((True, False, False), False), ((True, True, False), False),
                                              ((True, False, True), False), ((True, True, True), True)])
def test_process_alone_never_qualifies(values, expected):
    assert qualifies(*values) is expected


def test_skip_and_recovery_never_record():
    manager = SessionManager()
    manager.manual()
    manager.skip()
    manager.recovery()
    assert manager.state == State.RECOVERY_REQUIRED
    assert manager.consent_at is None
