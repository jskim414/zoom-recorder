"""Run extension JS against synthetic fixtures in isolated headless Chrome/Edge.

This is NOT an actual extension installation or a real Zoom meeting test.
No user browser profile, microphone, meeting page or network API is accessed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


BOOTSTRAP = r"""
const evidence = [];
const token = 'x'.repeat(43);
let handler, timer, pagehide, network = [], reports = [];
let positiveFixture = true;
const realQuery = document.querySelectorAll.bind(document);
document.querySelectorAll = selector => positiveFixture ? [document.getElementById('fixture')] : [];
const cryptoFixture = {randomUUID: () => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'};
const runtimeFixture = {
  id: 'a'.repeat(32),
  sendMessage: value => { reports.push(value); return Promise.resolve(); },
  openOptionsPage: () => {},
  onMessage: {addListener: callback => {handler = callback;}}
};
const chrome = {
  runtime: runtimeFixture,
  storage: {local: {
    setAccessLevel: () => Promise.resolve(),
    get: () => Promise.resolve({token, port: 17843}),
    set: () => Promise.resolve()
  }},
  action: {onClicked: {addListener: () => {}}, setBadgeText: () => {}}
};
const fetch = (url, options) => { network.push({url, options}); return Promise.resolve({ok: true}); };
function assert(condition, text) { if (!condition) throw Error(text); }
"""


CHECKS = r"""
(async () => {
  try {
    assert(reports.length === 1 && reports[0].meeting_ui && reports[0].media_controls, 'content positive');
    assert(Object.keys(reports[0]).length === 6 && !('url' in reports[0]), 'minimal payload');
    positiveFixture = false;
    timer();
    assert(!reports[1].meeting_ui && !reports[1].media_controls, 'homepage negative');
    pagehide();
    assert(reports[2].kind === 'closed', 'closed event');
    const sender = {id: chrome.runtime.id, frameId: 0, tab: {id: 7}, url: 'https://app.zoom.us/wc/test-fixture'};
    const message = {...reports[0], domain: 'app.zoom.us'};
    assert(handler(message, {...sender, url:'https://evil.example'}, () => {}) === false, 'foreign sender');
    assert(handler(message, {...sender, frameId:1}, () => {}) === false, 'iframe rejected');
    assert(handler({...message, kind:'start_recording'}, sender, () => {}) === false, 'record command rejected');
    const accepted = await new Promise(resolve => {
      assert(handler(message, sender, resolve) === true, 'async response retained');
    });
    assert(accepted.ok && network.length === 1, 'bridge request');
    assert(network[0].url === 'http://127.0.0.1:17843/events', 'loopback only');
    assert(network[0].options.headers.Authorization === 'Bearer ' + token, 'authorization');
    assert(network[0].options.redirect === 'error', 'redirect disabled');
    assert(document.getElementById('token').value === token, 'options loaded');
    document.getElementById('save').click();
    await Promise.resolve();
    assert(document.getElementById('status').textContent.length > 0, 'options saved');
    document.getElementById('result').textContent = 'PASS: content, background, options synthetic fixtures';
  } catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + error.message;
  }
})();
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=["chrome", "edge"], required=True)
    args = parser.parse_args()
    relative = {"chrome": "Google/Chrome/Application/chrome.exe", "edge": "Microsoft/Edge/Application/msedge.exe"}[args.browser]
    paths = [Path(os.environ[key]) / relative for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if key in os.environ]
    executable = next((path for path in paths if path.is_file()), None)
    if executable is None:
        raise SystemExit("Browser unavailable")
    extension = Path(__file__).resolve().parent.parent / "browser-extension"
    content = (extension / "content.js").read_text(encoding="utf-8")
    # Inject only browser APIs and a fake hostname into the content script's scope.
    content = "((crypto, location, setInterval, addEventListener) => {" + content + """
})(cryptoFixture, {hostname: 'app.zoom.us'}, callback => {timer = callback;},
   (event, callback) => {if (event === 'pagehide') pagehide = callback;});
"""
    page = """<!doctype html><meta charset='utf-8'>
<div id='fixture' style='width:10px;height:10px'></div><pre id='result'>PENDING</pre>
<div id='extensionId'></div><input id='token'><input id='port'><button id='save'></button><div id='status'></div>
<script>""" + BOOTSTRAP + content + (extension / "background.js").read_text(encoding="utf-8") + \
        (extension / "options.js").read_text(encoding="utf-8") + CHECKS + "</script>"
    with tempfile.TemporaryDirectory(prefix="zoom-agent-browser-smoke-") as temporary:
        root = Path(temporary)
        fixture = root / "fixture.html"
        fixture.write_text(page, encoding="utf-8")
        process = subprocess.run([str(executable), "--headless=new", "--disable-gpu", "--no-first-run",
                                  "--disable-background-networking", "--disable-component-update", "--disable-sync",
                                  "--no-default-browser-check", "--user-data-dir=" + str(root / "profile"),
                                  "--virtual-time-budget=2000", "--dump-dom", fixture.as_uri()],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        passed = 'PASS: content, background, options synthetic fixtures' in process.stdout
        print(json.dumps({"browser": args.browser, "synthetic_js_passed": passed,
                          "actual_extension_or_zoom_test": False, "exit_code": process.returncode}))
        if not passed:
            start = process.stdout.find('<pre id="result">')
            print(process.stdout[start:start + 250] if start >= 0 else process.stderr[-1000:])
            raise SystemExit(1)


if __name__ == "__main__":
    main()
