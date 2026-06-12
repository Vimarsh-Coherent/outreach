async function init() {
  const cur = await chrome.storage.local.get(["platformUrl", "channelId", "token"]);
  document.getElementById("platformUrl").value = cur.platformUrl || "http://localhost:8000";
  document.getElementById("channelId").value = cur.channelId || "";
  document.getElementById("token").value = cur.token || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const platformUrl = document.getElementById("platformUrl").value.trim();
  const channelId = Number(document.getElementById("channelId").value.trim());
  const token = document.getElementById("token").value.trim();
  await chrome.storage.local.set({ platformUrl, channelId, token });
  document.getElementById("status").textContent = "Saved.";
});

// Open the campaign dashboard in Chrome's side panel (needs a user gesture).
document.getElementById("dash").addEventListener("click", async () => {
  try {
    const win = await chrome.windows.getCurrent();
    await chrome.sidePanel.open({ windowId: win.id });
    window.close();
  } catch (e) {
    document.getElementById("status").textContent = "Side panel unavailable: " + e.message;
  }
});

init();
