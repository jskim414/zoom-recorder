from __future__ import annotations

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import queue
import re
import secrets
import threading
import time

from app.detection.session_manager import Candidate, Observation


def allowed_domain(domain: str) -> bool:
    return isinstance(domain, str) and bool(re.fullmatch(r"(?:[a-z0-9-]+\.)*zoom\.us", domain))


def validate_message(value: object) -> dict:
    keys = {"version", "kind", "session", "domain", "meeting_ui", "media_controls"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid_schema")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid_version")
    if value["kind"] not in {"heartbeat", "closed"}:
        raise ValueError("invalid_kind")
    if not isinstance(value["session"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["session"]):
        raise ValueError("invalid_session")
    if not allowed_domain(value["domain"]):
        raise ValueError("invalid_domain")
    if any(type(value[key]) is not bool for key in ("meeting_ui", "media_controls")):
        raise ValueError("invalid_signal")
    return value


class Bridge:
    def __init__(self, port: int, extension_ids: list[str], audit=None):
        self.port = port
        self.origins = {"chrome-extension://" + ident for ident in extension_ids}
        self.token = secrets.token_urlsafe(32)
        self.messages = queue.Queue(maxsize=128)
        self.failures = deque(maxlen=100)
        self.audit = audit
        self.last_heartbeat = 0.0
        self.server = None
        self.thread = None
        self._last_auth_log = 0.0

    def reject(self, code):
        self.failures.append({"code": code, "monotonic": time.monotonic()})
        # Rate-limited fixed codes only. No attacker headers, bodies or secrets.
        now = time.monotonic()
        if self.audit and now - self._last_auth_log >= 1:
            self.audit(code)
            self._last_auth_log = now

    def authenticated(self, origin, authorization) -> bool:
        return origin in self.origins and hmac.compare_digest((authorization or "").encode("utf-8"),
                                                             ("Bearer " + self.token).encode("utf-8"))

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LocalBridge"
            body_consumed = False

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass

            def respond(self, status, cors=False):
                # Windows may reset a connection closed with unread request bytes.
                # Drain only a small declared body, with a strict time/size bound;
                # rejected input is never parsed or enqueued.
                if status >= 400 and self.command == "POST" and not self.body_consumed:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if 0 < length <= 8192 and not self.headers.get("Transfer-Encoding"):
                            self.connection.settimeout(0.1)
                            self.rfile.read(length)
                    except (OSError, ValueError):
                        pass
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                if cors:
                    self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.end_headers()

            def valid_origin(self):
                return (self.headers.get("Origin") in bridge.origins
                        and self.headers.get("Host") == f"127.0.0.1:{bridge.port}"
                        and self.path == "/events")

            def do_OPTIONS(self):
                if not self.valid_origin():
                    bridge.reject("origin_rejected")
                    self.respond(403)
                    return
                if self.headers.get("Access-Control-Request-Method") != "POST":
                    self.respond(405)
                    return
                requested = {h.strip().lower() for h in self.headers.get("Access-Control-Request-Headers", "").split(",") if h.strip()}
                if requested - {"authorization", "content-type"}:
                    self.respond(403)
                    return
                self.respond(204, True)

            def do_POST(self):
                if not self.valid_origin() or not bridge.authenticated(self.headers.get("Origin"), self.headers.get("Authorization")):
                    bridge.reject("authentication_rejected")
                    self.respond(403)
                    return
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self.respond(415, True)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 2048:
                        self.respond(413, True)
                        return
                    raw = self.rfile.read(length)
                    self.body_consumed = True
                    if len(raw) != length:
                        raise ValueError("incomplete")
                    message = validate_message(json.loads(raw))
                    self.messages_put(message)
                except queue.Full:
                    self.respond(429, True)
                    return
                except (ValueError, TypeError, UnicodeError, TimeoutError):
                    bridge.reject("schema_rejected")
                    self.respond(400, True)
                    return
                self.respond(204, True)

            def messages_put(self, message):
                bridge.messages.put_nowait((self.headers["Origin"], message, time.monotonic()))
                bridge.last_heartbeat = time.monotonic()

        class BoundedServer(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False

            def __init__(self, *args):
                self.slots = threading.BoundedSemaphore(4)
                super().__init__(*args)

            def process_request(self, request, client_address):
                if not self.slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                super().process_request(request, client_address)

            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self.slots.release()

            def handle_error(self, *_):
                bridge.reject("request_failed")

        self.server = BoundedServer(("127.0.0.1", self.port), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)


class BrowserDetector:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.sessions = {}

    def poll(self, now: float) -> list[Observation]:
        while True:
            try:
                origin, message, received_at = self.bridge.messages.get_nowait()
            except queue.Empty:
                break
            key = "browser:" + origin + ":" + message["session"]
            self.sessions[key] = (message, received_at)
        result = []
        for key, (message, received_at) in list(self.sessions.items()):
            alive = message["kind"] != "closed" and now - received_at < 20
            candidate = Candidate(key, "browser", "Web 회의 후보 · " + message["session"][:6])
            eligible = alive and message["meeting_ui"] and message["media_controls"]
            # A negative DOM heartbeat does not end recording. Closed tab / lost heartbeat suggests a check only.
            result.append(Observation(candidate, eligible, alive))
            if now - received_at > 3600:
                self.sessions.pop(key)
        return result
