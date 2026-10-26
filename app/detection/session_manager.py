from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import uuid

from app.storage.session_store import utc_now


class State(StrEnum):
    IDLE = "IDLE"
    CANDIDATE = "CANDIDATE"
    PROMPTED = "PROMPTED"
    RECORDING = "RECORDING"
    STOP_CONFIRM = "STOP_CONFIRM"
    FINALIZING = "FINALIZING"
    TRANSCRIBING = "TRANSCRIBING"
    COMPLETE = "COMPLETE"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class Candidate:
    key: str
    source: str
    label: str


@dataclass(frozen=True)
class Observation:
    candidate: Candidate
    eligible: bool
    present: bool = True


class ConsentGate:
    @staticmethod
    def confirm(checked: bool, explicit_click: bool) -> str:
        if checked is not True or explicit_click is not True:
            raise PermissionError("참석자 동의 확인과 시작 버튼 클릭이 모두 필요합니다.")
        return utc_now()


class SessionManager:
    def __init__(self):
        self.state = State.IDLE
        self.candidates: dict[str, Candidate] = {}
        self.selected: Candidate | None = None
        self.consent_at: str | None = None
        self.history = [self.state]

    def _transition(self, target: State, allowed: set[State]):
        if self.state not in allowed:
            raise RuntimeError(f"Invalid transition: {self.state} -> {target}")
        self.state = target
        self.history.append(target)

    @property
    def can_offer(self):
        return self.state in {State.IDLE, State.COMPLETE, State.SKIPPED, State.ERROR, State.RECOVERY_REQUIRED, State.TRANSCRIBING}

    def offer(self, candidates: list[Candidate]):
        if not candidates:
            raise ValueError("No candidate")
        self._transition(State.CANDIDATE, {State.IDLE, State.COMPLETE, State.SKIPPED, State.ERROR, State.RECOVERY_REQUIRED, State.TRANSCRIBING})
        self.candidates = {c.key: c for c in candidates}
        self.selected = None
        self.consent_at = None
        self._transition(State.PROMPTED, {State.CANDIDATE})

    def manual(self):
        self.offer([Candidate("manual:" + uuid.uuid4().hex, "manual", "수동 녹음")])

    def approve(self, key: str, checked: bool, explicit_click: bool):
        if self.state != State.PROMPTED or key not in self.candidates:
            raise RuntimeError("Select an offered session first")
        consent_at = ConsentGate.confirm(checked, explicit_click)
        self.selected = self.candidates[key]
        self.consent_at = consent_at
        self._transition(State.RECORDING, {State.PROMPTED})

    def skip(self):
        self._transition(State.SKIPPED, {State.PROMPTED})
        self.consent_at = None

    def suggest_stop(self):
        self._transition(State.STOP_CONFIRM, {State.RECORDING})

    def keep_recording(self):
        self._transition(State.RECORDING, {State.STOP_CONFIRM})

    def stop(self):
        self._transition(State.FINALIZING, {State.RECORDING, State.STOP_CONFIRM})

    def transcribe(self):
        self._transition(State.TRANSCRIBING, {State.FINALIZING, State.RECOVERY_REQUIRED, State.ERROR, State.COMPLETE})

    def complete(self):
        self._transition(State.COMPLETE, {State.FINALIZING, State.TRANSCRIBING})
        self.consent_at = None

    def fail(self):
        self._transition(State.ERROR, set(State) - {State.RECORDING, State.STOP_CONFIRM})
        self.consent_at = None

    def recovery(self):
        self._transition(State.RECOVERY_REQUIRED, {State.IDLE, State.COMPLETE, State.ERROR, State.SKIPPED, State.RECOVERY_REQUIRED})


class CandidateTracker:
    """Debounce positive evidence; losing audio activity alone cannot end a session."""
    def __init__(self, stable_seconds=7, end_grace_seconds=30, cooldown_seconds=30):
        self.stable_seconds = stable_seconds
        self.end_grace_seconds = end_grace_seconds
        self.cooldown_seconds = cooldown_seconds
        self.first: dict[str, float] = {}
        self.last_present: dict[str, float] = {}
        self.seen: set[str] = set()
        self.last_offer = float("-inf")

    def update(self, observations: list[Observation], now: float) -> list[Candidate]:
        positive = set()
        ready = []
        for observation in observations:
            key = observation.candidate.key
            if observation.present:
                self.last_present[key] = now
            if not observation.eligible or not observation.present:
                continue
            positive.add(key)
            self.first.setdefault(key, now)
            if key not in self.seen and now - self.first[key] >= self.stable_seconds:
                ready.append(observation.candidate)
        for key in set(self.first) - positive:
            self.first.pop(key)
        return ready if now - self.last_offer >= self.cooldown_seconds else []

    def mark_offered(self, candidates: list[Candidate], now: float):
        self.seen.update(c.key for c in candidates)
        self.last_offer = now

    def possibly_ended(self, key: str, now: float) -> bool:
        return key in self.last_present and now - self.last_present[key] >= self.end_grace_seconds
