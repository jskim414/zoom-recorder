(() => {
  // Do not read text, chat, participant names, URLs, meeting IDs or credentials.
  const session = crypto.randomUUID().replaceAll("-", "");
  const visible = selector => [...document.querySelectorAll(selector)].some(element => {
    const style = getComputedStyle(element);
    return element.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden";
  });
  function send() {
    const meeting = visible("#wc-container-right, .meeting-client, #meeting-client, #webclient");
    const leave = visible(".footer__leave-btn, .leave-meeting-options__btn, [data-testid='leave-button']");
    const media = visible(".join-audio-container, .footer__audio-btn, [data-testid='audio-button']") &&
                  visible(".video-container__btn, .footer__video-btn, [data-testid='video-button']");
    chrome.runtime.sendMessage({version: 1, kind: "heartbeat", session, domain: location.hostname,
      meeting_ui: meeting && leave, media_controls: media}).catch(() => {});
  }
  send();
  setInterval(send, 3000);
  addEventListener("pagehide", () => {
    chrome.runtime.sendMessage({version: 1, kind: "closed", session, domain: location.hostname,
      meeting_ui: false, media_controls: false}).catch(() => {});
  });
})();
