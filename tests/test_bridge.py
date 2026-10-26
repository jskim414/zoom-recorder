import http.client
import json
import time

import pytest

from app.detectors.browser_bridge import Bridge, BrowserDetector, validate_message


EXTENSION = "a" * 32
ORIGIN = "chrome-extension://" + EXTENSION


def message(**kwargs):
    return dict(version=1, kind="heartbeat", session="b" * 32, domain="app.zoom.us",
                meeting_ui=True, media_controls=True, **kwargs)


@pytest.fixture
def bridge():
    bridge = Bridge(0, [EXTENSION])
    bridge.start()
    yield bridge
    bridge.stop()


def post(bridge, body=None, origin=ORIGIN, token=None, **extra):
    connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=3)
    headers = {"Origin": origin, "Authorization": "Bearer " + (bridge.token if token is None else token),
               "Content-Type": "application/json", **extra}
    connection.request("POST", "/events", json.dumps(message() if body is None else body), headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders())
    response.read()
    connection.close()
    return result


def test_authenticated_loopback_signal(bridge):
    assert bridge.server.server_address[0] == "127.0.0.1"
    status, headers = post(bridge)
    assert status == 204 and headers["Access-Control-Allow-Origin"] == ORIGIN
    detector = BrowserDetector(bridge)
    assert detector.poll(time.monotonic())[0].eligible


@pytest.mark.parametrize("origin,token", [("https://evil.example", None), ("null", None),
                                           ("chrome-extension://" + "c" * 32, None), (ORIGIN, "wrong")])
def test_bad_origin_or_token_rejected(bridge, origin, token):
    assert post(bridge, origin=origin, token=token)[0] == 403
    assert bridge.messages.empty()
    assert bridge.failures[-1]["code"] == "authentication_rejected"


@pytest.mark.parametrize("field,value", [("domain", "zoom.us.evil.com"), ("domain", "https://zoom.us/wc/123"),
                                         ("session", "../escape"), ("kind", "start_recording"),
                                         ("meeting_ui", 1), ("version", True)])
def test_schema_rejection(bridge, field, value):
    body = message()
    body[field] = value
    assert post(bridge, body)[0] == 400


def test_extra_fields_and_body_limit(bridge):
    assert post(bridge, message(url="https://zoom.us/secret"))[0] == 400
    assert post(bridge, message(extra="x" * 3000))[0] == 413


def test_host_rebinding_and_preflight(bridge):
    assert post(bridge, Host="evil.example")[0] == 403
    connection = http.client.HTTPConnection("127.0.0.1", bridge.port)
    connection.request("OPTIONS", "/events", headers={"Origin": ORIGIN,
                       "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    response = connection.getresponse()
    assert response.status == 204
    connection.close()


def test_url_alone_does_not_qualify_and_timeout_does_not_start(bridge):
    body = message()
    body["meeting_ui"] = False
    assert post(bridge, body)[0] == 204
    detector = BrowserDetector(bridge)
    now = time.monotonic()
    assert not detector.poll(now)[0].eligible
    assert not detector.poll(now + 21)[0].present


def test_no_implicit_extension_authorization():
    bridge = Bridge(0, [])
    assert not bridge.authenticated(ORIGIN, "Bearer " + bridge.token)
