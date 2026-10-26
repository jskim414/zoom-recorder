from __future__ import annotations

import re

from app.detection.session_manager import Candidate, Observation


def qualifies(process_matches: bool, meeting_window: bool, audio_session_active: bool) -> bool:
    return process_matches and meeting_window and audio_session_active


class DesktopDetector:
    def __init__(self, settings):
        self.names = {name.lower() for name in settings.zoom_process_names}
        self.patterns = [re.compile(p, re.IGNORECASE) for p in settings.meeting_title_patterns]
        self.known: dict[str, Candidate] = {}
        self.error: str | None = None

    def poll(self) -> list[Observation]:
        import psutil

        processes = {}
        for process in psutil.process_iter(["pid", "name", "create_time"]):
            if (process.info["name"] or "").lower() in self.names:
                processes[process.pid] = process.info["create_time"]
        active = set()
        self.error = None
        # No Zoom: do not load/query Core Audio or enumerate windows.
        if not processes:
            return [Observation(candidate, False, False) for candidate in self.known.values()]
        import win32gui
        import win32process
        from pycaw.pycaw import AudioUtilities
        try:
            for session in AudioUtilities.GetAllSessions():
                if session.ProcessId in processes and session.State == 1:
                    active.add(session.ProcessId)
        except Exception:
            self.error = "desktop_audio_signal_unavailable"
        result = []
        present_keys = set()

        def inspect(hwnd, _):
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in processes:
                    return
                key = f"desktop:{pid}:{processes[pid]}:{hwnd}"
                # Read only to match locally. Never retain or log a window title.
                meeting_window = any(p.search(win32gui.GetWindowText(hwnd)) for p in self.patterns)
                visible = win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd)
                eligible = qualifies(True, bool(meeting_window and visible), pid in active)
                if eligible or key in self.known:
                    candidate = self.known.setdefault(key, Candidate(key, "desktop", f"Desktop 회의 후보 · 창 {hwnd}"))
                    present_keys.add(key)
                    result.append(Observation(candidate, eligible, True))
            except (OSError, psutil.Error):
                pass

        win32gui.EnumWindows(inspect, None)
        for key in set(self.known) - present_keys:
            result.append(Observation(self.known[key], False, False))
        return result
