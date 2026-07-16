/**
 * Background service worker for Web Media Parser extension.
 * Loads sieve rules into storage and communicates with desktop app.
 */

const API_BASE = "http://127.0.0.1:19876";
const SIEVE_VERSION = "2026.04.01"; // Bump when shipping new sieve.json

// Load sieve rules from bundled file into storage
async function loadSieveRules(force = false) {
  try {
    const stored = await chrome.storage.local.get(["sieveRules", "sieveVersion"]);
    if (!force && stored.sieveRules && stored.sieveVersion === SIEVE_VERSION) {
      console.info("Sieve rules already loaded (version " + SIEVE_VERSION + ")");
      return;
    }
    const resp = await fetch(chrome.runtime.getURL("sieve.json"));
    const data = await resp.json();
    await chrome.storage.local.set({
      sieveRules: JSON.stringify(data),
      sieveVersion: SIEVE_VERSION,
    });
    console.info(`Loaded ${Object.keys(data).length} sieve rules into storage (v${SIEVE_VERSION})`);
  } catch (e) {
    console.error("Failed to load sieve rules:", e);
  }
}

// Load rules on install and update
chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === "install" || details.reason === "update") {
    await loadSieveRules(true);
  }
});

// Also load on startup
loadSieveRules();

// --- Linked page discovery (CORS bypass via service worker) ---

// Parse sieve rules and extract res patterns (like Imagus cachedSieveRes)
let cachedSieveRes = {};

async function loadSieveResPatterns() {
  try {
    const result = await chrome.storage.local.get("sieveRules");
    if (!result.sieveRules) return;
    const data = JSON.parse(result.sieveRules);
    cachedSieveRes = {};
    for (const [name, rule] of Object.entries(data)) {
      if (!rule || typeof rule !== "object" || !rule.res || !rule.link) continue;
      try {
        const linkRegex = new RegExp(rule.link, "i");
        let resPattern;
        if (typeof rule.res === "string") {
          resPattern = new RegExp(rule.res, "i");
        } else if (Array.isArray(rule.res)) {
          resPattern = rule.res.map(r => new RegExp(r, "i"));
        }
        if (resPattern) cachedSieveRes[name] = { linkRegex, resPattern };
      } catch (e) {}
    }
    console.info(`Loaded ${Object.keys(cachedSieveRes).length} sieve res patterns`);
  } catch (e) {}
}

loadSieveResPatterns();

// Reload on storage changes
if (chrome.storage.onChanged) {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.sieveRules) {
      cachedSieveRes = {};
      loadSieveResPatterns();
    }
  });
}

const DISCOVER_CONCURRENCY = 5;

async function discoverFullsize(links, pageUrl) {
  const discovered = [];
  const seen = new Set();
  const lock = { acquire() {}, release() {} }; // no-op — JS is single-threaded in SW

  async function processLink(linkUrl) {
    try {
      const resp = await fetch(linkUrl, {
        headers: { "Accept": "text/html" },
        signal: AbortSignal.timeout(8000),
      });
      if (!resp.ok) return [];
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("image/") || ct.includes("video/")) {
        return [{ url: linkUrl, type: ct.includes("video/") ? "video" : "image", pageUrl, source: "link-direct" }];
      }
      if (!ct.includes("text/html")) return [];

      const html = await resp.text();
      const results = [];

      // 1. Try sieve res patterns
      for (const [name, { linkRegex, resPattern }] of Object.entries(cachedSieveRes)) {
        if (!linkRegex.test(linkUrl.replace(/^https?:\/\//, "")) && !linkRegex.test(linkUrl)) continue;
        let foundAny = false;
        try {
          const patterns = Array.isArray(resPattern) ? resPattern : [resPattern];
          for (const pat of patterns) {
            pat.lastIndex = 0;
            const match = pat.exec(html);
            if (match && match[1]) {
              let imgUrl = match[1];
              if (!imgUrl.startsWith("http")) imgUrl = "https:" + imgUrl;
              if (!seen.has(imgUrl)) {
                seen.add(imgUrl);
                results.push({ url: imgUrl, type: "image", pageUrl, source: "sieve-res" });
                foundAny = true;
              }
            }
          }
        } catch (e) {}
        if (foundAny) break;
      }

      // 2. Fallback: scan <img> src from HTML
      const SRC_RE = /<img[^>]+src=["']([^"']+)["']/gi;
      let m;
      while ((m = SRC_RE.exec(html)) !== null) {
        let url = m[1];
        if (url.startsWith("//")) url = "https:" + url;
        if (url.startsWith("http") && !seen.has(url) && url !== linkUrl
            && /\.(jpe?g|webp|avif|heic|bmp|tiff?)$/i.test(url)) {
          seen.add(url);
          results.push({ url, type: "image", pageUrl, source: "linked-img" });
        }
      }
      return results;
    } catch (e) {
      return [];
    }
  }

  // Process links in chunks of DISCOVER_CONCURRENCY
  for (let i = 0; i < links.length; i += DISCOVER_CONCURRENCY) {
    const chunk = links.slice(i, i + DISCOVER_CONCURRENCY);
    const results = await Promise.all(chunk.map(processLink));
    for (const r of results) discovered.push(...r);
  }

  return { media: discovered };
}

// --- Chrome Downloads ---

async function resolveUrl(url) {
  try {
    const resp = await fetch(url, {
      headers: { "Accept": "text/html" },
      signal: AbortSignal.timeout(8000),
    });
    if (!resp.ok) return url;
    const ct = resp.headers.get("content-type") || "";
    if (!ct.includes("text/html")) return url;
    const html = await resp.text();
    const strippedUrl = url.replace(/^https?:\/\//, "");
    for (const [name, { linkRegex, resPattern }] of Object.entries(cachedSieveRes)) {
      if (!linkRegex.test(strippedUrl) && !linkRegex.test(url)) continue;
      try {
        const patterns = Array.isArray(resPattern) ? resPattern : [resPattern];
        for (const pat of patterns) {
          pat.lastIndex = 0;
          const match = pat.exec(html);
          if (match && match[1]) {
            let imgUrl = match[1];
            if (!imgUrl.startsWith("http")) imgUrl = "https:" + imgUrl;
            return imgUrl;
          }
        }
      } catch (e) {}
      // Only break if res found something; otherwise try next rule
    }
  } catch (e) {}
  return url;
}

async function chromeDownload(items) {
  let saved = 0;
  for (const item of items) {
    try {
      // Resolve gallery page URL to direct CDN image URL
      const resolved = await resolveUrl(item.url);

      const downloadId = await new Promise((resolve) => {
        chrome.downloads.download({
          url: resolved,
          filename: item.filename || undefined,
          conflictAction: "uniquify"
        }, (id) => {
          resolve(chrome.runtime.lastError ? null : id);
        });
      });
      if (downloadId) saved++;
    } catch (e) {
      console.error(`Download failed: ${item.url} — ${e.message}`);
    }
  }
  return { saved };
}

/**
 * Send media URLs to the desktop app.
 */
async function getPageContext(tabId) {
  const context = {};
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab?.url && /^https?:/i.test(tab.url)) {
      const cookies = await chrome.cookies.getAll({ url: tab.url });
      if (cookies.length > 0) {
        context.cookies = cookies.map(c => `${c.name}=${c.value}`).join("; ");
      }
    }
  } catch (e) {}
  try {
    const response = await chrome.tabs.sendMessage(tabId, { action: "getUA" });
    if (response && response.userAgent) context.user_agent = response.userAgent;
  } catch (e) {}
  return context;
}

async function sendToDesktop(urls, oneShot = false, context = {}) {
  try {
    const payload = { urls, one_shot: oneShot };
    if (context.user_agent) payload.user_agent = context.user_agent;
    if (context.cookies) payload.cookies = context.cookies;
    const response = await fetch(`${API_BASE}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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

// --- Keyboard commands ---

let commandBusy = false;

async function setBadge(text, color) {
  try {
    await chrome.action.setBadgeText({ text });
    await chrome.action.setBadgeBackgroundColor({ color });
  } catch (e) {}
}

async function commandScanAndProcess(action) {
  if (commandBusy) return;
  commandBusy = true;
  try {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  await setBadge("...", "#FFA000");

  let response;
  try {
    response = await chrome.tabs.sendMessage(tab.id, { action: "scanMedia" });
  } catch (e) {
    await setBadge("!", "#F44336");
    setTimeout(() => setBadge(""), 3000);
    return;
  }
  if (!response || !response.media) {
    await setBadge("0", "#F44336");
    setTimeout(() => setBadge(""), 3000);
    return;
  }

  let media = response.media;
  if (response.links && response.links.length > 0) {
    await setBadge("...", "#FFA000");
    const linked = await discoverFullsize(response.links.slice(0, 50), response.url);
    if (linked && linked.media) {
      media = media.concat(linked.media);
    }
  }

  const fullsize = media.filter(m => FULLSIZE_SOURCES.has(m.source));
  const items = fullsize.length > 0 ? fullsize : media;

  if (action === "save-chrome") {
    await setBadge(`${items.length}`, "#4CAF50");
    const toDownload = items.map(item => ({
      url: item.url,
      filename: item.url.split("/").pop().split("?")[0] || "",
      referer: response.url || "",
    }));
    await chromeDownload(toDownload);
    setTimeout(() => setBadge(""), 5000);
  } else if (action === "send-desktop") {
    await setBadge("✓", "#4CAF50");
    const context = await getPageContext(tab.id);
    await sendToDesktop([{ url: response.url }], false, context);
    setTimeout(() => setBadge(""), 3000);
  }
  } finally {
    commandBusy = false;
  }
}

const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct"]);

chrome.commands?.onCommand?.addListener((command) => {
  if (command === "save-chrome" || command === "send-desktop") {
    commandScanAndProcess(command);
  }
});

// Listen for messages from popup and content scripts
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "download") {
    sendToDesktop(request.urls, request.one_shot, request.context || {}).then(sendResponse);
    return true;
  }
  if (request.action === "discoverFullsize") {
    discoverFullsize(request.links, request.pageUrl).then(sendResponse);
    return true;
  }
  if (request.action === "chromeDownload") {
    chromeDownload(request.items).then(sendResponse);
    return true;
  }
  if (request.action === "getContext") {
    getPageContext(request.tabId).then(sendResponse);
    return true;
  }
  if (request.action === "clearBadgeAfter") {
    // Clear badge from background (persists after popup closes)
    setTimeout(() => setBadge(""), request.delay || 5000);
    sendResponse({ ok: true });
    return false;
  }
  if (request.action === "getStatus") {
    getStatus().then(sendResponse);
    return true;
  }
});
