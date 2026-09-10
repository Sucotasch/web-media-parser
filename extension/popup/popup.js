/**
 * Popup script for Web Media Parser extension.
 * Scans the current page for media, shows previews, sends to desktop app.
 */

const mediaItems = [];
let activeDomainFilter = "";
let activeSourceFilter = "";
let concurrentLimit = 2;

// FULLSIZE_SOURCES / LINKS_CAP come from shared.js (EXT-9).

// DOM elements
const scanBtn = document.getElementById("scan-btn");
const downloadBtn = document.getElementById("download-btn");
const selectAllCheckbox = document.getElementById("select-all");
const oneShotCheckbox = document.getElementById("one-shot");
const mediaList = document.getElementById("media-list");
const countSpan = document.getElementById("count");
const selectedCountSpan = document.getElementById("selected-count");
const statusDiv = document.getElementById("status");
const pageInfoDiv = document.getElementById("page-info");
const resultsDiv = document.getElementById("results");
const emptyDiv = document.getElementById("empty");
const errorDiv = document.getElementById("error");
const domainFilter = document.getElementById("domain-filter");
const sourceFilter = document.getElementById("source-filter");
const chromeDownloadBtn = document.getElementById("chrome-download-btn");
const chromeCountSpan = document.getElementById("chrome-count");
const deepParseWarning = document.getElementById("deep-parse-warning");
const concurrentLimitInput = document.getElementById("concurrent-limit");

function updateOneShotMode() {
  const isDeepParse = !oneShotCheckbox.checked;
  deepParseWarning.classList.toggle("hidden", !isDeepParse);
  chromeDownloadBtn.disabled = isDeepParse || getVisibleCheckboxes().length === 0;
  downloadBtn.textContent = isDeepParse ? "Parse Page" : "Download";
  downloadBtn.disabled = false;
  concurrentLimitInput.disabled = isDeepParse;
}

oneShotCheckbox.addEventListener("change", updateOneShotMode);

// --- Connection check ---

async function checkConnection() {
  try {
    const resp = await chrome.runtime.sendMessage({ action: "getStatus" });
    if (resp && !resp.error) {
      const parts = [`Q: ${resp.queue_length}`];
      if (resp.files_downloaded > 0) parts.push(`D: ${resp.files_downloaded}`);
      statusDiv.textContent = parts.join(" | ");
      statusDiv.className = "status online";
      return true;
    }
  } catch (e) {}
  statusDiv.textContent = "Disconnected";
  statusDiv.className = "status offline";
  return false;
}

// --- Domain helpers ---

function extractDomain(url) {
  try {
    return new URL(url).hostname;
  } catch (e) {
    return url;
  }
}

function populateDomainFilter() {
  const domains = new Map();
  mediaItems.forEach((item) => {
    const domain = extractDomain(item.url);
    domains.set(domain, (domains.get(domain) || 0) + 1);
  });

  domainFilter.innerHTML = `<option value="">All domains (${mediaItems.length})</option>`;
  const sorted = [...domains.entries()].sort((a, b) => b[1] - a[1]);
  sorted.forEach(([domain, count]) => {
    const opt = document.createElement("option");
    opt.value = domain;
    opt.textContent = `${domain} (${count})`;
    domainFilter.appendChild(opt);
  });
}

// --- Scan page ---
// ERR-4: the scan runs in the background service worker, not the popup.
// Action popups are closed by Chrome on blur (moving the mouse away kills
// the popup); keeping mediaItems only in popup memory meant a mid-scan blur
// lost everything and the user had to start over. The SW scan writes progress
// to storage.scanProgress and the result to storage.scanResult; this popup
// listens to storage.onChanged and re-renders live, and re-opening restores
// the last result for the current tab.

scanBtn.addEventListener("click", async () => {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  mediaItems.length = 0;
  activeDomainFilter = "";
  activeSourceFilter = "fullsize";
  domainFilter.value = "";
  sourceFilter.value = "fullsize";
  resultsDiv.classList.add("hidden");
  emptyDiv.classList.add("hidden");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      showError("No active tab found");
      scanBtn.disabled = false;
      scanBtn.textContent = "Scan This Page";
      return;
    }

    // Drop the previous result so a stale list can't flash while scanning.
    try { await chrome.runtime.sendMessage({ action: "resetScan" }); } catch (e) {}
    const resp = await chrome.runtime.sendMessage({ action: "startScan", tabId: tab.id });
    if (resp && resp.error) {
      showError(`Scan failed: ${resp.error}`);
      scanBtn.disabled = false;
      scanBtn.textContent = "Scan This Page";
    }
    // Progress + final result arrive via storage.onChanged (below).
  } catch (e) {
    showError(`Scan failed: ${e.message}`);
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan This Page";
  }
});

// --- Scan/download progress via storage (survives popup close) ---

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.scanProgress) {
    const p = changes.scanProgress.newValue;
    if (!p) return;
    if (p.status === "scanning") {
      // The x/y counter counts LINKED PAGES probed for fullsize discovery,
      // capped at LINKS_CAP=50 — not media found. Surface the found count
      // too so the numbers mean something (Errors.txt #2).
      scanBtn.textContent = p.phase === "links" && p.total > 0
        ? `Scanning... ${p.scanned}/${p.total} pages${p.found ? `, ${p.found} found` : ""}`
        : "Scanning...";
    } else if (p.status === "error") {
      showError(`Scan failed: ${p.error || "unknown error"}`);
      scanBtn.disabled = false;
      scanBtn.textContent = "Scan This Page";
    }
  }
  if (changes.scanResult) {
    const r = changes.scanResult.newValue;
    if (r && Array.isArray(r.media)) {
      // Render only results for the CURRENT tab — a scan finishing for a
      // background tab must not paint its media into this popup (and a
      // stale result for a navigated-away tab must not re-appear).
      chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
        if (!tab || r.tabId !== tab.id) return;
        if (r.pageUrl && tab.url && r.pageUrl.split("#")[0] !== tab.url.split("#")[0]) return;
        applyScanResult(r);
      }).catch(() => {});
    }
  }
  if (changes.downloadProgress) {
    const d = changes.downloadProgress.newValue;
    if (d) updateDownloadProgressUI(d);
  }
});

function applyScanResult(result) {
  mediaItems.length = 0;
  mediaItems.push(...result.media);
  pageInfoDiv.textContent = result.title || result.pageUrl || "";
  pageInfoDiv.classList.remove("hidden");
  populateDomainFilter();
  // Fallback to "all" if no fullsize sources found
  const fsCount = mediaItems.filter(m => FULLSIZE_SOURCES.has(m.source)).length;
  if (fsCount === 0) {
    activeSourceFilter = "";
    sourceFilter.value = "";
  }
  renderMediaList();
  updateCount();
  scanBtn.disabled = false;
  scanBtn.textContent = `✓ ${mediaItems.length} found`;
  setTimeout(() => { scanBtn.textContent = "Scan This Page"; }, 2500);
}

function updateDownloadProgressUI(d) {
  if (!d || d.total === 0) return;
  if (d.status === "done") {
    const failed = (d.failed || 0);
    chromeDownloadBtn.innerHTML = `✓ Saved ${d.saved}${failed ? ` (${failed} failed)` : ""}`;
    chromeDownloadBtn.disabled = false;
    try {
      chrome.action.setBadgeText({ text: `${d.saved}` });
      chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
    } catch (e) {}
    setTimeout(() => {
      chromeDownloadBtn.innerHTML = `Save (Chrome) <span id="chrome-count">0</span>`;
      updateCount();
    }, 3000);
  } else if (d.status === "active") {
    chromeDownloadBtn.innerHTML = `Saving ${d.started}/${d.total} (${d.saved} ok)...`;
    chromeDownloadBtn.disabled = true;
  }
}

// Restore state when the popup re-opens (ERR-4): a finished scan for this
// tab is re-rendered without re-scanning; a running scan shows its progress;
// an in-flight download shows its counter.
async function restoreScanState() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const stored = await chrome.storage.local.get(["scanResult", "scanProgress", "downloadProgress"]);
    // Stale-state guard (Errors.txt follow-up): a cached scanResult belongs to
    // a specific PAGE. Matching only tabId left the previous page's list
    // "stuck forever" after an in-page (SPA) navigation, BFCache restore or
    // extension reload. Full reloads are cleared by the SW's tabs.onUpdated
    // handler; the URL check covers every other path. Hash is ignored — a
    // #-anchor change is the same document.
    const samePage = (a, b) => !!a && !!b && a.split("#")[0] === b.split("#")[0];
    const resultStale = stored.scanResult && (!tab || stored.scanResult.tabId !== tab.id
      || !samePage(stored.scanResult.pageUrl, tab.url));
    const progressStale = stored.scanProgress && stored.scanProgress.status === "scanning"
      && (!tab || stored.scanProgress.tabId !== tab.id);
    if (resultStale || progressStale) {
      chrome.storage.local.remove(["scanResult", "scanProgress"]).catch(() => {});
      stored.scanResult = null;
      stored.scanProgress = null;
    }
    if (stored.scanProgress && stored.scanProgress.status === "scanning") {
      const p = stored.scanProgress;
      scanBtn.disabled = true;
      scanBtn.textContent = p.phase === "links" && p.total > 0
        ? `Scanning... ${p.scanned}/${p.total} pages${p.found ? `, ${p.found} found` : ""}`
        : "Scanning...";
    }
    if (stored.scanResult && stored.scanResult.tabId === (tab && tab.id)) {
      applyScanResult(stored.scanResult);
    }
    if (stored.downloadProgress && stored.downloadProgress.status === "active") {
      updateDownloadProgressUI(stored.downloadProgress);
    }
  } catch (e) {}
}

// --- Domain filter ---

domainFilter.addEventListener("change", () => {
  activeDomainFilter = domainFilter.value;
  renderMediaList();
  updateCount();
});

sourceFilter.addEventListener("change", () => {
  activeSourceFilter = sourceFilter.value;
  renderMediaList();
  updateCount();
});

// --- Dimensions display ---
// The dims we record come from the DOM element, so they describe the FILE at
// item.url only when that URL is the very image the browser loaded, untouched:
// a plain, untransformed <img> with natural dimensions. In every other case
// (transformed/upgraded URLs — sieve/[Resize] strip; a-link/link-direct/
// sieve-res sources) the dims are the THUMBNAIL's — showing 192×240 while the
// file is really 1110×1375 misleads the user (Errors.txt follow-up).
// Unknown beats wrong: hide dims there.
function shouldShowDims(item) {
  return !!(item && item.source === "img" && !item.transformed
    && item.width > 0 && item.height > 0);
}

// --- Render media list ---

function renderMediaList() {
  mediaList.innerHTML = "";

  const filtered = mediaItems.filter((item) => {
    if (activeDomainFilter && extractDomain(item.url) !== activeDomainFilter) return false;
    if (activeSourceFilter === "fullsize" && !FULLSIZE_SOURCES.has(item.source)) return false;
    if (activeSourceFilter === "thumbnail" && FULLSIZE_SOURCES.has(item.source)) return false;
    return true;
  });

  if (filtered.length === 0) {
    resultsDiv.classList.add("hidden");
    emptyDiv.classList.remove("hidden");
    return;
  }

  emptyDiv.classList.add("hidden");
  resultsDiv.classList.remove("hidden");

  filtered.forEach((item) => {
    const globalIndex = mediaItems.indexOf(item);
    const div = document.createElement("div");
    div.className = "media-item";

    // Checkbox
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.index = globalIndex;
    checkbox.addEventListener("change", updateCount);

    // Thumbnail or placeholder
    const thumb = document.createElement("div");
    thumb.className = "thumb placeholder";
    thumb.textContent = item.type === "video" ? "\u25B6" : "\uD83D\uDCF7";

    // Lazy-load thumbnail for images
    if (item.type === "image" && item.url) {
      const img = new Image();
      img.src = item.url;
      img.className = "thumb";
      img.style.display = "none";
      img.onload = () => {
        thumb.style.display = "none";
        img.style.display = "block";
      };
      img.onerror = () => {};
      div.appendChild(img);
    }
    div.appendChild(thumb);

    // Info
    const info = document.createElement("div");
    info.className = "info";

    const urlDiv = document.createElement("div");
    urlDiv.className = "url";
    urlDiv.textContent = item.url;
    urlDiv.title = item.url;

    const metaDiv = document.createElement("div");
    metaDiv.className = "meta";

    const typeBadge = document.createElement("span");
    typeBadge.className = `type-badge type-${item.type}`;
    typeBadge.textContent = item.type.toUpperCase();
    metaDiv.appendChild(typeBadge);

    if (shouldShowDims(item)) {
      const sizeSpan = document.createElement("span");
      sizeSpan.textContent = ` \u00B7 ${item.width}\u00D7${item.height}`;
      metaDiv.appendChild(sizeSpan);
    }
    if (item.source) {
      const sourceSpan = document.createElement("span");
      sourceSpan.textContent = ` \u00B7 ${item.source}`;
      metaDiv.appendChild(sourceSpan);
    }

    // Domain tag
    const domainSpan = document.createElement("span");
    domainSpan.className = "domain-tag";
    domainSpan.textContent = ` \u00B7 ${extractDomain(item.url)}`;
    metaDiv.appendChild(domainSpan);

    if (item.transformed) {
      const tSpan = document.createElement("span");
      tSpan.className = "transformed";
      tSpan.textContent = " \u2726 transformed";
      metaDiv.appendChild(tSpan);
    }

    info.appendChild(urlDiv);
    info.appendChild(metaDiv);
    div.appendChild(checkbox);
    div.appendChild(info);
    mediaList.appendChild(div);
  });
}

function getVisibleCheckboxes() {
  return mediaList.querySelectorAll("input[type='checkbox']");
}

function updateCount() {
  const checked = getVisibleCheckboxes();
  let checkedCount = 0;
  checked.forEach((cb) => { if (cb.checked) checkedCount++; });

  const total = mediaItems.filter((item) => {
    if (activeDomainFilter && extractDomain(item.url) !== activeDomainFilter) return false;
    if (activeSourceFilter === "fullsize" && !FULLSIZE_SOURCES.has(item.source)) return false;
    if (activeSourceFilter === "thumbnail" && FULLSIZE_SOURCES.has(item.source)) return false;
    return true;
  }).length;

  selectedCountSpan.textContent = checkedCount;
  // Re-query chromeCountSpan in case innerHTML was replaced
  const cc = document.getElementById("chrome-count");
  if (cc) cc.textContent = checkedCount;
  countSpan.textContent = `${checkedCount} / ${total}`;
  downloadBtn.disabled = checkedCount === 0;
  chromeDownloadBtn.disabled = !oneShotCheckbox.checked || checkedCount === 0;
  selectAllCheckbox.checked = checkedCount === total && total > 0;
}

// --- Select all ---

selectAllCheckbox.addEventListener("change", () => {
  getVisibleCheckboxes().forEach((cb) => {
    cb.checked = selectAllCheckbox.checked;
  });
  updateCount();
});

// --- Chrome Download ---

chromeDownloadBtn.addEventListener("click", async () => {
    const selected = [];
    mediaList.querySelectorAll("input[type='checkbox']:checked").forEach((cb) => {
      const item = mediaItems[parseInt(cb.dataset.index)];
      if (item) {
        const baseName = item.url.split("/").pop().split("?")[0] || "";
        selected.push({
          url: item.url,
          referer: item.pageUrl || "",
          filename: baseName,
          // ERR-5: "link-direct" items were network-verified during the scan
          // (processLink fetched them and saw image/video) — the background
          // skips the resolve round-trip so downloads start immediately.
          verified: item.source === "link-direct",
        });
      }
    });

  if (selected.length === 0) return;

  chromeDownloadBtn.disabled = true;
  chromeDownloadBtn.innerHTML = `Saving <span>${selected.length}</span>...`;

  // Send to background for download. Live per-file progress arrives via
  // storage.onChanged (downloadProgress) — the popup is not blocked until
  // every file finishes, and a popup close mid-download loses nothing.
  try {
    await chrome.action.setBadgeText({ text: `${selected.length}` });
    await chrome.action.setBadgeBackgroundColor({ color: "#FFA000" });
    const resp = await chrome.runtime.sendMessage({ action: "chromeDownload", items: selected, concurrentLimit });
    const saved = resp && resp.saved ? resp.saved : 0;
    if (saved > 0) {
      chromeDownloadBtn.innerHTML = `\u2713 Saved ${saved}`;
      try {
        await chrome.action.setBadgeText({ text: `${saved}` });
        await chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
      } catch (e) {}
      chrome.runtime.sendMessage({ action: "clearBadgeAfter", delay: 5000 });
      setTimeout(() => {
        chromeDownloadBtn.innerHTML = `Save (Chrome) <span id="chrome-count">0</span>`;
        chromeDownloadBtn.disabled = false;
        updateCount();
      }, 3000);
    }
  } catch (e) {
    showError(`Download failed: ${e.message}`);
    updateCount();
  }
});

// --- Download ---

downloadBtn.addEventListener("click", async () => {
  const oneShot = oneShotCheckbox.checked;

  if (!oneShot) {
    // Deep parse: just send the page URL to desktop, no scan needed
    downloadBtn.disabled = true;
    downloadBtn.innerHTML = "Sending...";
    await chrome.action.setBadgeText({ text: "..." });
    await chrome.action.setBadgeBackgroundColor({ color: "#2196F3" });
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const pageUrl = tab ? tab.url : "";
    let context = {};
    try { context = await chrome.runtime.sendMessage({ action: "getContext", tabId: tab?.id }); } catch (e) {}
    const resp = await chrome.runtime.sendMessage({ action: "download", urls: [{ url: pageUrl }], one_shot: false, context });
    if (resp && resp.ok) {
      downloadBtn.innerHTML = "\u2713 Sent to app";
      await chrome.action.setBadgeText({ text: "\u2713" });
      await chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
      chrome.runtime.sendMessage({ action: "clearBadgeAfter", delay: 3000 });
    } else if (resp && resp.error) {
      showError(resp.error);
      await chrome.action.setBadgeText({ text: "!" });
      await chrome.action.setBadgeBackgroundColor({ color: "#F44336" });
      chrome.runtime.sendMessage({ action: "clearBadgeAfter", delay: 3000 });
    }
    setTimeout(() => {
      downloadBtn.innerHTML = "Download";
      downloadBtn.disabled = false;
    }, 1500);
    await checkConnection();
    return;
  }

  // Page only: scan first, then send selected items
  const selected = [];
  mediaList.querySelectorAll("input[type='checkbox']:checked").forEach((cb) => {
    const item = mediaItems[parseInt(cb.dataset.index)];
    if (item) selected.push({
      url: item.url,
      // EXT-4: `source` in the scan results means ORIGIN (img/srcset/sieve-res);
      // the POST payload needs the page as REFERER — rename to avoid the clash.
      referer: item.pageUrl || "",
      type: item.type,
      // EXT-2: the sieve-transform path stores the pre-transform URL in
      // item.original_url — `item.original` never existed (always null).
      original_url: item.original_url || null,
      transformed: !!item.transformed,
    });
  });

  if (selected.length === 0) return;

  downloadBtn.disabled = true;
  downloadBtn.innerHTML = `Sending <span>${selected.length}</span>...`;

  // Get browser context (cookies + UA)
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let context = {};
  try { context = await chrome.runtime.sendMessage({ action: "getContext", tabId: tab?.id }); } catch (e) {}

  const resp = await chrome.runtime.sendMessage({ action: "download", urls: selected, one_shot: true, context });

  if (resp && resp.error) {
    showError(resp.error);
  } else if (resp && resp.ok) {
    downloadBtn.innerHTML = `\u2713 Added ${resp.added}`;
    setTimeout(() => {
      downloadBtn.textContent = "Download";
      downloadBtn.disabled = false;
      updateCount();
    }, 1500);
  }

  await checkConnection();
});

function showError(msg) {
  errorDiv.textContent = msg;
  errorDiv.classList.remove("hidden");
  setTimeout(() => errorDiv.classList.add("hidden"), 5000);
}

// Init
checkConnection();
updateSieveInfo();
restoreScanState();

// Load concurrent limit setting
chrome.storage.local.get("concurrentLimit", (data) => {
  if (data.concurrentLimit) {
    concurrentLimit = data.concurrentLimit;
    concurrentLimitInput.value = concurrentLimit;
  }
});

// Save concurrent limit on change
concurrentLimitInput.addEventListener("change", () => {
  concurrentLimit = parseInt(concurrentLimitInput.value) || 2;
  chrome.storage.local.set({ concurrentLimit });
});

// --- Sieve rules management ---

async function updateSieveInfo() {
  const infoEl = document.getElementById("sieve-info");
  try {
    const stored = await chrome.storage.local.get("sieveRules");
    if (stored.sieveRules) {
      const data = JSON.parse(stored.sieveRules);
      const count = Object.keys(data).length;
      // EXT-7: MV3 CSP blocks JS sieve rules in the popup — surface that
      // honestly instead of claiming all N rules are active.
      const jsCount = typeof countJsRules === "function" ? countJsRules(data) : 0;
      infoEl.textContent = jsCount > 0
        ? `Sieve: ${count} rules (${jsCount} JS — skipped in popup)`
        : `Sieve: ${count} rules loaded`;
    } else {
      infoEl.textContent = "Sieve: no rules loaded";
    }
  } catch (e) {
    infoEl.textContent = "Sieve: error reading rules";
  }
}

document.getElementById("sieve-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const count = Object.keys(data).length;
    if (count === 0) {
      showError("File contains no rules");
      return;
    }
    await chrome.storage.local.set({ sieveRules: text });
    const jsCount = typeof countJsRules === "function" ? countJsRules(data) : 0;
    document.getElementById("sieve-info").textContent = jsCount > 0
      ? `Sieve: ${count} rules (${jsCount} JS — skipped in popup)`
      : `Sieve: ${count} rules loaded`;
    showError(`Loaded ${count} sieve rules from ${file.name}`);
    setTimeout(() => document.getElementById("error").classList.add("hidden"), 3000);
  } catch (e) {
    showError(`Failed to load: ${e.message}`);
  }
  e.target.value = "";
});

// C-1: online sieve update — runs in the service worker (which can fetch
// cross-origin), then the popup just reflects the result.
document.getElementById("sieve-update-btn").addEventListener("click", async () => {
  const infoEl = document.getElementById("sieve-info");
  const btn = document.getElementById("sieve-update-btn");
  btn.textContent = "Updating…";
  try {
    const resp = await chrome.runtime.sendMessage({ action: "updateSieve" });
    infoEl.textContent = resp && resp.message ? resp.message : "Update failed";
  } catch (e) {
    infoEl.textContent = `Update error: ${e.message}`;
  }
  btn.textContent = "Update sieve now";
  setTimeout(updateSieveInfo, 1500);
});
