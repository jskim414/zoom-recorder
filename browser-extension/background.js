const allowedDomain = domain => /^(?:[a-z0-9-]+\.)*zoom\.us$/.test(domain);
chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0) return false;
  let source;
  try { source = new URL(sender.url); } catch { return false; }
  if (source.protocol !== "https:" || !allowedDomain(source.hostname) || message.domain !== source.hostname) return false;
  const keys = ["version", "kind", "session", "domain", "meeting_ui", "media_controls"];
  if (Object.keys(message).length !== keys.length || keys.some(key => !(key in message)) ||
      message.version !== 1 || !["heartbeat", "closed"].includes(message.kind) ||
      !/^[a-f0-9]{32}$/.test(message.session) || typeof message.meeting_ui !== "boolean" ||
      typeof message.media_controls !== "boolean") return false;
  (async () => {
    const {token, port = 17843} = await chrome.storage.local.get(["token", "port"]);
    if (!token || !Number.isInteger(port) || port < 1024 || port > 65535) {
      respond({ok: false}); return;
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/events`, {
        method: "POST", headers: {"Content-Type": "application/json", "Authorization": `Bearer ${token}`},
        body: JSON.stringify(message), credentials: "omit", cache: "no-store",
        redirect: "error", signal: AbortSignal.timeout(2000)
      });
      chrome.action.setBadgeText({text: response.ok ? "" : "!"});
      respond({ok: response.ok});
    } catch {
      chrome.action.setBadgeText({text: "!"});
      respond({ok: false});
    }
  })();
  return true;
});
