/**
 * Background service worker for Web Media Parser extension.
 * Loads sieve rules into storage and communicates with desktop app.
 */

// EXT-9: shared constants (FULLSIZE_SOURCES, LINKS_CAP) — single source.
importScripts("shared.js");

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
      if (!rule || typeof rule !== "object") continue;
      // Require link + (res OR url) — url alone can be a redirect/transform
      if (!rule.link || (!rule.res && !rule.url)) continue;
      try {
        const linkRegex = new RegExp(rule.link, "i");
        let resPattern = undefined;
        if (rule.res) {
          if (typeof rule.res === "string") {
            resPattern = new RegExp(rule.res, "i");
          } else if (Array.isArray(rule.res)) {
            resPattern = rule.res.map(r => new RegExp(r, "i"));
          }
        }
        // Parse url property (string transform or JS expression)
        let urlPattern = null;
        if (rule.url && typeof rule.url === "string") {
          if (rule.url.startsWith(":")) {
            // JS expression — not supported in service worker, skip
            urlPattern = { type: "js", template: rule.url };
          } else {
            // String transform: $1, $2 replacements + optional POST data after " :"
            const postMatch = rule.url.match(/(\s+):(.+)$/);
            const urlTemplate = postMatch ? rule.url.slice(0, postMatch.index) : rule.url;
            const postData = postMatch ? postMatch[2].trim() : null;
            urlPattern = { type: "string", template: urlTemplate, postData };
          }
        }
        cachedSieveRes[name] = { linkRegex, resPattern, urlPattern };
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

// Semaphore for limiting concurrent downloads
class Semaphore {
  constructor(max) {
    this.max = max;
    this.running = 0;
    this.queue = [];
  }
  
  async acquire() {
    if (this.running < this.max) {
      this.running++;
      return;
    }
    return new Promise(resolve => this.queue.push(resolve));
  }
  
  release() {
    this.running--;
    if (this.queue.length > 0) {
      this.running++;
      this.queue.shift()();
    }
  }
}

// Apply string url transform: replace $1, $2 with regex match groups
function applyUrlTransform(template, matchGroups) {
  if (!template || !matchGroups) return null;
  try {
    let result = template;
    // Replace $1, $2, etc. with captured groups (literal string replacement)
    for (let i = 1; i < matchGroups.length; i++) {
      const placeholder = `$${i}`;
      if (result.includes(placeholder) && matchGroups[i] !== undefined) {
        // Use split/join to avoid regex escaping issues with $
        result = result.split(placeholder).join(matchGroups[i]);
      }
    }
    // $& means full match
    if (result.includes("$&")) {
      result = result.split("$&").join(matchGroups[0] || "");
    }
    return result || null;
  } catch (e) {
    return null;
  }
}

async function discoverFullsize(links, pageUrl) {
  const discovered = [];
  const seen = new Set();

  async function processLink(linkUrl) {
    try {
      // Try to apply sieve url pattern (string transforms + optional POST data)
      let fetchOptions = {
        headers: { "Accept": "text/html" },
        signal: AbortSignal.timeout(8000),
      };
      let matchedRuleName = null;
      let matchedGroups = null;

      // Find matching sieve rule for this URL
      for (const [name, { linkRegex, resPattern, urlPattern }] of Object.entries(cachedSieveRes)) {
        const strippedUrl = linkUrl.replace(/^https?:\/\//, "");
        // Reset lastIndex to avoid test() skipping due to global flag
        linkRegex.lastIndex = 0;
        if (linkRegex.test(strippedUrl) || (linkRegex.lastIndex = 0, linkRegex.test(linkUrl))) {
          matchedRuleName = name;
          matchedGroups = strippedUrl.match(linkRegex) || linkUrl.match(linkRegex);
          // Apply url transform if present and is string type
          if (urlPattern && urlPattern.type === "string") {
            const transformed = applyUrlTransform(urlPattern.template, matchedGroups);
            if (transformed) {
              // Preserve protocol if template doesn't include it
              if (!transformed.startsWith("http") && !transformed.startsWith("//")) {
                const protocol = linkUrl.startsWith("https") ? "https://" : "http://";
                linkUrl = protocol + transformed;
              } else {
                linkUrl = transformed.replace(/^\/\//, "https://");
              }
              // If POST data specified, switch to POST
              if (urlPattern.postData) {
                fetchOptions.method = "POST";
                fetchOptions.headers["Content-Type"] = "application/x-www-form-urlencoded";
                fetchOptions.body = urlPattern.postData;
              }
            }
          }
          break;
        }
      }

      const resp = await fetch(linkUrl, fetchOptions);
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
        if (!resPattern) continue; // Skip rules without res (url-only rules)
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
      if (!resPattern) continue; // Skip rules without res
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
      // Note: we return on the FIRST rule whose res regex finds a match —
      // there is no "try next rule" fallthrough here (rules are tried in order
      // and the first hit wins).
    }
  } catch (e) {}
  return url;
}

async function chromeDownload(items, concurrentLimit = 2) {
  return new Promise((resolve) => {
    let saved = 0;
    let started = 0;
    let completed = 0;
    const total = items.length;
    let activeCount = 0; // Track how many downloads are in progress

    function startNext() {
      // If all items processed, check if we're done
      if (started >= total) {
        if (completed >= total) resolve({ saved });
        return;
      }
      // If at limit, wait (will be called again when one completes)
      if (activeCount >= concurrentLimit) return;

      const item = items[started++];
      activeCount++; // Increment immediately to reserve the slot

      resolveUrl(item.url).then(resolved => {
        const url = resolved || item.url;
        chrome.downloads.download({
          url: url,
          filename: item.filename || undefined,
          conflictAction: "uniquify"
        }, (downloadId) => {
          if (chrome.runtime.lastError || !downloadId) {
            console.error(`Download failed to start: ${item.url} — ${chrome.runtime.lastError?.message}`);
            activeCount--; // Release the slot
            completed++;
            startNext();
            return;
          }

          const listener = (delta) => {
            if (delta.id === downloadId && delta.state?.current) {
              const state = delta.state.current;
              if (state === 'complete' || state === 'interrupted') {
                clearTimeout(watchdog);
                chrome.downloads.onChanged.removeListener(listener);
                activeCount--; // Release the slot
                if (state === 'complete') saved++;
                completed++;
                startNext(); // Start next download now that we have a free slot
              }
            }
          };
          chrome.downloads.onChanged.addListener(listener);
          // EXT-5: safety net — if onChanged never fires (download cancelled
          // from Chrome's shelf, extension reloaded), don't hang the promise
          // (and the popup's "Saving…") forever.
          const watchdog = setTimeout(() => {
            chrome.downloads.onChanged.removeListener(listener);
            activeCount--; // Release the slot
            completed++;
            startNext();
          }, 10 * 60 * 1000);
          setTimeout(startNext, 0); // Try to start next (async to avoid stack overflow)
        });
      }).catch(e => {
        console.error(`Resolve URL failed: ${item.url} — ${e.message}`);
        activeCount--; // Release the slot
        completed++;
        startNext();
      });
    }

    // Start the chain (it will keep calling itself until limit is reached)
    startNext();
  });
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
    // EXT-6: the desktop app may be half-alive (port open, no response) —
    // a 5s timeout keeps the popup/command from hanging on "Sending...".
    const response = await fetch(`${API_BASE}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(5000),
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
    // EXT-6: bounded wait — same half-alive-app protection as sendToDesktop.
    const response = await fetch(`${API_BASE}/api/status`, {
      signal: AbortSignal.timeout(5000),
    });
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

  if (action === "send-desktop") {
    // EXT-3: the desktop app crawls the page itself — the old code ran the
    // full scan pipeline (up to 50 linked pages) and then threw it all away,
    // sending only the page URL. Send the URL and show the badge immediately.
    const context = await getPageContext(tab.id);
    await sendToDesktop([{ url: tab.url }], false, context);
    await setBadge("✓", "#4CAF50");
    setTimeout(() => setBadge(""), 3000);
    return;
  }

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
    const linked = await discoverFullsize(response.links.slice(0, LINKS_CAP), response.url);
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
  }
  } finally {
    commandBusy = false;
  }
}

// FULLSIZE_SOURCES now comes from shared.js (EXT-9).

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
    const limit = request.concurrentLimit || 2;
    chromeDownload(request.items, limit).then(sendResponse);
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
