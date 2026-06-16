/**
 * Background service worker for Web Media Parser extension.
 * Loads sieve rules into storage and communicates with desktop app.
 */

const API_BASE = "http://127.0.0.1:19876";

// Load sieve rules from bundled file into storage
async function loadSieveRules() {
  try {
    const stored = await chrome.storage.local.get("sieveRules");
    if (stored.sieveRules) {
      console.info("Sieve rules already loaded");
      return;
    }
    const resp = await fetch(chrome.runtime.getURL("sieve.json"));
    const data = await resp.json();
    await chrome.storage.local.set({ sieveRules: JSON.stringify(data) });
    console.info(`Loaded ${Object.keys(data).length} sieve rules into storage`);
  } catch (e) {
    console.error("Failed to load sieve rules:", e);
  }
}

// Load rules on install and update
chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === "install" || details.reason === "update") {
    await loadSieveRules();
  }
});

// Also load on startup
loadSieveRules();

/**
 * Send media URLs to the desktop app.
 */
async function sendToDesktop(urls, oneShot = false) {
  try {
    const response = await fetch(`${API_BASE}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls, one_shot: oneShot }),
    });
    const data = await response.json();
    return data;
  } catch (e) {
    return { error: `Desktop app not reachable: ${e.message}` };
  }
}

/**
 * Get status from the desktop app.
 */
async function getStatus() {
  try {
    const response = await fetch(`${API_BASE}/api/status`);
    return await response.json();
  } catch (e) {
    return { error: `Desktop app not reachable: ${e.message}` };
  }
}

// Listen for messages from popup and content scripts
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "download") {
    sendToDesktop(request.urls, request.one_shot).then(sendResponse);
    return true;
  }
  if (request.action === "getStatus") {
    getStatus().then(sendResponse);
    return true;
  }
});
