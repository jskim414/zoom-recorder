document.getElementById("extensionId").textContent = chrome.runtime.id;
chrome.storage.local.get(["token", "port"]).then(({token = "", port = 17843}) => {
  document.getElementById("token").value = token;
  document.getElementById("port").value = port;
});
document.getElementById("save").addEventListener("click", async () => {
  const token = document.getElementById("token").value.trim();
  const port = Number(document.getElementById("port").value);
  const status = document.getElementById("status");
  if (!/^[A-Za-z0-9_-]{40,128}$/.test(token) || !Number.isInteger(port) || port < 1024 || port > 65535) {
    status.textContent = "앱 토큰과 포트를 확인하세요."; return;
  }
  await chrome.storage.local.set({token, port});
  status.textContent = "저장했습니다. Zoom 탭을 새로고침하고 앱의 연결 상태를 확인하세요.";
});
