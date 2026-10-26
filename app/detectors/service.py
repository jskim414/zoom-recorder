import queue
import threading
import time

from app.detectors.desktop import DesktopDetector
from app.detectors.browser_bridge import BrowserDetector


class DetectionService:
    def __init__(self, settings, bridge):
        self.desktop = DesktopDetector(settings)
        self.browser = BrowserDetector(bridge)
        self.results = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        import comtypes
        comtypes.CoInitialize()
        try:
            while not self.stop_event.is_set():
                error = None
                try:
                    observations = self.desktop.poll()
                    error = self.desktop.error
                except Exception:
                    observations = []
                    error = "desktop_detector_unavailable"
                now = time.monotonic()
                observations.extend(self.browser.poll(now))
                try:
                    self.results.put_nowait((observations, now, error))
                except queue.Full:
                    pass
                self.stop_event.wait(2)
        finally:
            comtypes.CoUninitialize()
