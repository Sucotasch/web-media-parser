/**
 * Popup script for Web Media Parser extension.
 * Scans the current page for media, shows previews, sends to desktop app.
 */

const mediaItems = [];
let activeDomainFilter = "";
let activeSourceFilter = "";
let concurrentLimit = 2;

// FULLSIZE_SOURCES / DISCOVER_QUARANTINE_MS come from shared.js (EXT-9).

// DOM elements
const scanBtn = document.getElementById("scan-btn");
const deepCrawlBtn = document.getElementById("deep-crawl-btn");
const copyLinksBtn = document.getElementById("copy-links-btn");
const downloadBtn = document.getElementById("download-btn");
const selectAllCheckbox = document.getElementById("select-all");
const mediaList = document.getElementById("media-list");
const countSpan = document.getElementById("count");
// #selected-count / #chrome-count are NOT cached: their spans are rewritten by
// innerHTML elsewhere, so they are re-queried where they are used.
const statusDiv = document.getElementById("status");
const pageInfoDiv = document.getElementById("page-info");
const resultsDiv = document.getElementById("results");
const emptyDiv = document.getElementById("empty");
const errorDiv = document.getElementById("error");
const domainFilter = document.getElementById("domain-filter");
const sourceFilter = document.getElementById("source-filter");
const chromeDownloadBtn = document.getElementById("chrome-download-btn");
const chromeCountSpan = document.getElementById("chrome-count");

// ERR-9: a bucket item whose URL was PROVEN not to be media (404/410, or a 2xx
// text/* answer) is not part of "Full size": the count on the buttons and what
// Chrome saves must describe the same set. Measured on reactor.cc/post/6382634:
// 9 promised rows included i.li.ru/ext/.../disney_33.jpg (404 text/html) and the
// save ended "Saved 8 of 9 (1 failed)". "unknown" is deliberately NOT filtered —
// that is a probe that could not answer (403/timeout), which is what
// hotlink-protected hosts return for real images; dropping those would lose
// real media silently.
function isNotMedia(item) {
  return !!item && item.mediaVerdict === "not-media";
}
const concurrentLimitInput = document.getElementById("concurrent-limit");

// Errors.txt #3 (popup closed and re-opened): the two module filter variables were only
// ever assigned by the select `change` listeners, the scan handler and applyScanResult's
// "no fullsize sources" fallback. A REOPENED popup renders through restoreScanState →
// applyScanResult, so it filtered by the initial "" (all sources) while the <select> still
// showed its HTML default ("fullsize"): the list and the Save count covered every image
// (measured: 141 before the popup closed, 429 after re-opening) until the user toggled the
// filter, whose change handler finally copied the visible value. The DOM select is what
// the user sees — start from it, and `change` keeps the pair in sync afterwards.
activeDomainFilter = domainFilter.value;
activeSourceFilter = sourceFilter.value;
let isAppConnected = false;

function updateAppButtonsState() {
  if (deepCrawlBtn) {
    deepCrawlBtn.disabled = !isAppConnected;
    deepCrawlBtn.title = isAppConnected
      ? "Send page URL to desktop app for recursive deep link traversal"
      : "Desktop app not running — click Disconnected above to set up";
  }
  updateCount();
}

// --- Connection check ---

async function checkConnection() {
  try {
    const resp = await chrome.runtime.sendMessage({ action: "getStatus" });
    if (resp && !resp.error) {
      const parts = [`Q: ${resp.queue_length}`];
      if (resp.files_downloaded > 0) parts.push(`D: ${resp.files_downloaded}`);
      statusDiv.textContent = parts.join(" | ");
      statusDiv.className = "status online";
      // "Q: 2 | D: 5" is the product's own shorthand - spell it out on hover.
      statusDiv.title = "Desktop app connected — Q: queued tasks, D: files downloaded";
      isAppConnected = true;
      updateAppButtonsState();
      return true;
    }
  } catch (e) {}
  statusDiv.textContent = "Disconnected";
  statusDiv.className = "status offline";
  statusDiv.title = "Desktop app not running — click to view setup guide & download";
  isAppConnected = false;
  updateAppButtonsState();
  return false;
}

// Clicking Disconnected opens the GitHub project setup guide in a new tab
statusDiv.addEventListener("click", () => {
  if (statusDiv.classList.contains("offline")) {
    chrome.tabs.create({ url: "https://github.com/Sucotasch/web-media-parser#readme" });
  }
});

function formatAppError(err) {
  if (!err) return "Unknown error";
  if (/not reachable|failed to fetch/i.test(err)) {
    return 'Desktop app is not running. Start Web Media Parser or click "Disconnected" above.';
  }
  return err;
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

// D-02: the background reports the END of a scan on a second channel as well.
// The popup renders from storage, so when the final storage write could not
// land (quota, a broken storage area) "Scanning..." stayed on screen forever —
// and a scan that ended in an error had no way to say so.
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (!request || request.action !== "scanFinished") return false;
  if (request.ok === false) {
    showError(`Scan failed: ${request.error || "unknown error"}`);
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan This Page";
  } else if (!mediaItems.length) {
    // Nothing was rendered (no result arrived) — make the button usable again.
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan This Page";
  }
  sendResponse({ ok: true });
  return false;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.scanProgress) {
    const p = changes.scanProgress.newValue;
    if (!p) return;
    if (p.status === "scanning") {
      // The x/y counter counts LINKED PAGES probed for fullsize discovery — no content
      // cap, only the 45s page budget and the postponed-tier sub-budget end it. It is not
      // "media found"; surface that count too so the numbers mean something (Errors.txt #2).
      scanBtn.textContent = p.phase === "links" && p.total > 0
        ? `Scanning... ${p.scanned}/${p.total} pages${p.found ? `, ${p.found} found` : ""}`
        : p.phase === "verify"
          ? "Checking media..."
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
  // ERR-9: explain the verification in one line — a shorter list than before
  // must read as "these were checked out", not as missing media.
  const v = result.verdicts || null;
  const vParts = [];
  if (v && v.notMedia) vParts.push(`${v.notMedia} non-media hidden`);
  if (v && v.unknown) vParts.push(`${v.unknown} unconfirmed`);
  const vNote = vParts.join(" \u00B7 ");
  pageInfoDiv.textContent = [result.title || result.pageUrl || "", vNote].filter(Boolean).join(" \u00B7 ");
  // The line is ellipsized at 480px; keep the full value reachable.
  pageInfoDiv.title = [result.title, result.pageUrl, vNote].filter(Boolean).join("\n");
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

// Errors.txt #2: a download must not resize the Save button. Its width is exactly what the
// actions row reacts to (two labels + two buttons on a 480px popup, slack ≈ 0): re-writing
// the label to "Saving 12/141 (3 ok)..." grew the button, the row wrapped to a second line,
// the buttons landed at the LEFT edge (measured: left 230 -> 14) and space-between threw
// "Parallel:" to the right edge (94 -> 371) — i.e. "the two left controls vanish and the
// buttons take their place". The row is now pinned in CSS, and this button's label is
// CONSTANT: state travels by colour (data-state + popup.css) and by the status line.
function setSaveState(state, statusText) {
  if (state === "idle") {
    chromeDownloadBtn.removeAttribute("data-state");
    checkConnection(); // put "Q: … | D: …" / "Disconnected" back
    return;
  }
  chromeDownloadBtn.dataset.state = state;
  if (state === "saving") {
    statusDiv.className = "status saving";
  } else if (state === "done") {
    statusDiv.className = "status online";
  }
  if (statusText) {
    statusDiv.textContent = statusText;
    statusDiv.title = statusText;
  }
}

function updateDownloadProgressUI(d) {
  if (!d || d.total === 0) return;
  if (d.status === "done") {
    const failed = (d.failed || 0);
    setSaveState("done", `\u2713 Saved ${d.saved} of ${d.total}${failed ? ` (${failed} failed)` : ""}`);
    chromeDownloadBtn.disabled = false;
    downloadBtn.disabled = false;
    try {
      chrome.action.setBadgeText({ text: `${d.saved}` });
      chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
    } catch (e) {}
    setTimeout(() => {
      setSaveState("idle");
      updateCount();
    }, 3000);
  } else if (d.status === "active") {
    setSaveState("saving", `Saving ${d.started}/${d.total}\u2026 (${d.saved} saved)`);
    chromeDownloadBtn.disabled = true;
    downloadBtn.disabled = true;
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
        : p.phase === "verify"
          ? "Checking media..."
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
    if (activeSourceFilter === "fullsize" && (!FULLSIZE_SOURCES.has(item.source) || isNotMedia(item))) return false;
    if (activeSourceFilter === "thumbnail" && FULLSIZE_SOURCES.has(item.source)) return false;
    return true;
  });

  if (filtered.length === 0) {
    // "No media found on this page" was shown for a filter that hides everything
    // too - the page does have media, the FILTER is the reason. Say which one.
    emptyDiv.querySelector("p").textContent = mediaItems.length === 0
      ? "No media found on this page."
      : "No items match the filters — pick another source or domain above.";
    resultsDiv.classList.add("hidden");
    emptyDiv.classList.remove("hidden");
    return;
  }

  emptyDiv.classList.add("hidden");
  resultsDiv.classList.remove("hidden");

  // F-07: rendering 1000+ rows froze the popup (every row builds a preview
  // <img> that the browser then tries to load). Show the first 300 and say how
  // many are left, so the list is usable instead of merely complete — the
  // filters and "select all" still cover what is on screen.
  const { shown, hidden } = capForRender(filtered);

  shown.forEach((item) => {
    const globalIndex = mediaItems.indexOf(item);
    const div = document.createElement("div");
    div.className = "media-item";

    // Checkbox — named for screen readers; the row's own text is the URL.
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.index = globalIndex;
    checkbox.setAttribute("aria-label", `Select ${item.url}`);
    checkbox.addEventListener("change", updateCount);

    // Preview: a real <button> that opens the item in a new tab. It used to be
    // an image/div with `cursor: pointer` and no handler - a click that did
    // nothing - and, being neither focusable nor a control, unreachable by
    // keyboard.
    const thumb = document.createElement("button");
    thumb.type = "button";
    thumb.className = "thumb";
    thumb.title = "Open in a background tab next to this page";
    thumb.setAttribute("aria-label", `Open ${item.url} in a new tab`);
    thumb.addEventListener("click", async () => {
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        chrome.tabs.create({
          url: item.url,
          index: tab ? tab.index + 1 : undefined,
          active: false,
        });
      } catch (e) {
        chrome.tabs.create({ url: item.url, active: false });
      }
    });

    const glyph = document.createElement("span");
    glyph.className = "thumb-glyph";
    glyph.textContent = item.type === "video" ? "\u25B6" : "\uD83D\uDCF7";
    thumb.appendChild(glyph);

    // Lazy-load thumbnail for images
    if (item.type === "image" && item.url) {
      const img = new Image();
      // F-07: only the rows near the viewport are fetched at all.
      img.loading = "lazy";
      img.decoding = "async";
      img.src = item.url;
      img.className = "thumb-img";
      img.alt = "";
      img.style.display = "none";
      img.onload = () => {
        glyph.style.display = "none";
        img.style.display = "block";
      };
      img.onerror = () => {};
      thumb.appendChild(img);
    }
    div.appendChild(thumb);

    // Info
    const info = document.createElement("div");
    info.className = "info";

    const urlDiv = document.createElement("div");
    urlDiv.className = "url";
    urlDiv.textContent = item.url;
    urlDiv.title = `Click to copy link:\n${item.url}`;
    urlDiv.setAttribute("role", "button");
    urlDiv.setAttribute("tabindex", "0");
    urlDiv.setAttribute("aria-label", `Copy link ${item.url}`);

    const copyUrl = async () => {
      // Don't copy if user was selecting/dragging text with mouse
      const selection = window.getSelection() ? window.getSelection().toString() : "";
      if (selection.length > 0) return;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(item.url);
        } else {
          const ta = document.createElement("textarea");
          ta.value = item.url;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
        }
        urlDiv.classList.add("copied");
        const originalText = item.url;
        urlDiv.textContent = "\u2713 Copied to clipboard!";
        setTimeout(() => {
          urlDiv.classList.remove("copied");
          urlDiv.textContent = originalText;
        }, 1200);
      } catch (err) {
        showError(`Failed to copy: ${err.message}`);
      }
    };

    urlDiv.addEventListener("click", copyUrl);
    urlDiv.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        copyUrl();
      }
    });

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

    // ERR-9: the probe could not answer (403/timeout/no content-type). The row
    // stays — it may well be real media behind hotlink protection — but it must
    // not look confirmed.
    if (item.mediaVerdict === "unknown") {
      const unknownSpan = document.createElement("span");
      unknownSpan.className = "verdict-unknown";
      unknownSpan.textContent = " \u00B7 ?";
      unknownSpan.title = "Not confirmed as media: the host did not answer the check";
      metaDiv.appendChild(unknownSpan);
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

  if (hidden > 0) {
    const more = document.createElement("div");
    more.className = "media-more";
    more.textContent = `\u2026 and ${hidden} more`
      + " \u2014 narrow the domain or source filter to reach them";
    mediaList.appendChild(more);
  }
}

// F-07: how many rows the popup paints at once, and whether it must say that
// more exist. Pure so the cap is testable without a DOM.
const RENDER_LIMIT = 300;

function capForRender(items, limit = RENDER_LIMIT) {
  const shown = items.slice(0, limit);
  return { shown, hidden: items.length - shown.length };
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
    if (activeSourceFilter === "fullsize" && (!FULLSIZE_SOURCES.has(item.source) || isNotMedia(item))) return false;
    if (activeSourceFilter === "thumbnail" && FULLSIZE_SOURCES.has(item.source)) return false;
    return true;
  }).length;

  // Both counters are re-queried: their spans live inside button labels that
  // other code rewrites via innerHTML, so the cached references go stale.
  const sc = document.getElementById("selected-count");
  if (sc) sc.textContent = checkedCount;
  const cc = document.getElementById("chrome-count");
  if (cc) cc.textContent = checkedCount;
  countSpan.textContent = `${checkedCount} / ${total} selected`;
  countSpan.title = `${checkedCount} of the ${total} listed items selected`;
  const noneSelected = checkedCount === 0;
  // A disabled control without a reason reads as a broken one: keep the button
  // in place and say what is missing on hover.
  downloadBtn.disabled = noneSelected || !isAppConnected;
  downloadBtn.title = !isAppConnected
    ? "Desktop app not running"
    : noneSelected
      ? "Select at least one item first"
      : "Download selected items in desktop app";
  chromeDownloadBtn.disabled = noneSelected;
  chromeDownloadBtn.title = noneSelected
    ? "Select at least one item first"
    : "Save selected files with Chrome's own downloader";
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
        // D-07: a name Chrome accepts — a path separator or a device name
        // ("..%2F..%2Fx.png", "CON.png") makes it refuse the whole download.
        const baseName = sanitizeFilename(item.url.split("/").pop().split("?")[0]);
        selected.push({
          url: item.url,
          referer: item.pageUrl || "",
          filename: baseName,
          // ERR-5: "link-direct" items were network-verified during the scan
          // (processLink fetched them and saw image/video) — the background
          // skips the resolve round-trip so downloads start immediately.
          // ERR-9: items the scan verified as media carry `verified: true` —
          // the background then skips its resolve round-trip entirely.
          verified: item.verified === true || item.source === "link-direct",
        });
      }
    });

  if (selected.length === 0) return;

  chromeDownloadBtn.disabled = true;
  downloadBtn.disabled = true;
  setSaveState("saving", `Saving ${selected.length} file(s)\u2026`);

  // Send to background for download. Live per-file progress arrives via
  // storage.onChanged (downloadProgress) — the popup is not blocked until
  // every file finishes, and a popup close mid-download loses nothing.
  try {
    await chrome.action.setBadgeText({ text: `${selected.length}` });
    await chrome.action.setBadgeBackgroundColor({ color: "#FFA000" });
    const resp = await chrome.runtime.sendMessage({ action: "chromeDownload", items: selected, concurrentLimit });
    const saved = resp && resp.saved ? resp.saved : 0;
    if (saved > 0) {
      setSaveState("done", `\u2713 Saved ${saved}`);
      try {
        await chrome.action.setBadgeText({ text: `${saved}` });
        await chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
      } catch (e) {}
      chrome.runtime.sendMessage({ action: "clearBadgeAfter", delay: 5000 });
      setTimeout(() => {
        setSaveState("idle");
        chromeDownloadBtn.disabled = false;
        downloadBtn.disabled = false;
        updateCount();
      }, 3000);
    } else {
      setSaveState("idle");
      chromeDownloadBtn.disabled = false;
      downloadBtn.disabled = false;
      updateCount();
    }
  } catch (e) {
    showError(`Download failed: ${e.message}`);
    setSaveState("idle");
    chromeDownloadBtn.disabled = false;
    downloadBtn.disabled = false;
    updateCount();
  }
});

// --- Deep Crawl handler ---

async function sendDeepCrawl(triggerBtn) {
  const btn = triggerBtn || deepCrawlBtn;
  if (!btn) return;
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "Sending...";
  await chrome.action.setBadgeText({ text: "..." });
  await chrome.action.setBadgeBackgroundColor({ color: "#2196F3" });

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const pageUrl = tab ? tab.url : "";
    let context = {};
    try { context = await chrome.runtime.sendMessage({ action: "getContext", tabId: tab?.id }); } catch (e) {}
    const resp = await chrome.runtime.sendMessage({
      action: "download",
      urls: [{ url: pageUrl }],
      one_shot: false,
      context
    });
    if (resp && resp.ok) {
      btn.innerHTML = "\u2713 Sent to app";
      await chrome.action.setBadgeText({ text: "\u2713" });
      await chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
      chrome.runtime.sendMessage({ action: "clearBadgeAfter", delay: 3000 });
    } else if (resp && resp.error) {
      showError(formatAppError(resp.error));
      await chrome.action.setBadgeText({ text: "!" });
      await chrome.action.setBadgeBackgroundColor({ color: "#F44336" });
      chrome.runtime.sendMessage({ action: "clearBadgeAfter", delay: 3000 });
    }
  } catch (e) {
    showError(`Failed to send: ${e.message}`);
  }

  setTimeout(() => {
    btn.innerHTML = originalHtml;
    btn.disabled = !isAppConnected;
  }, 1500);
  await checkConnection();
}

if (deepCrawlBtn) {
  deepCrawlBtn.addEventListener("click", () => sendDeepCrawl(deepCrawlBtn));
}

// --- Copy / Export Media Links ---

function getExportUrls() {
  const checkedBoxes = mediaList.querySelectorAll("input[type='checkbox']:checked");
  const urls = [];
  if (checkedBoxes.length > 0) {
    checkedBoxes.forEach((cb) => {
      const item = mediaItems[parseInt(cb.dataset.index)];
      if (item && item.url) urls.push(item.url);
    });
  } else {
    // If nothing checked, take all items currently matching visible filters
    mediaItems.forEach((item) => {
      if (activeDomainFilter && extractDomain(item.url) !== activeDomainFilter) return;
      if (activeSourceFilter === "fullsize" && (!FULLSIZE_SOURCES.has(item.source) || isNotMedia(item))) return;
      if (activeSourceFilter === "thumbnail" && FULLSIZE_SOURCES.has(item.source)) return;
      if (item.url) urls.push(item.url);
    });
  }
  return urls;
}

if (copyLinksBtn) {
  copyLinksBtn.addEventListener("click", async (e) => {
    const urls = getExportUrls();
    if (urls.length === 0) {
      showError("No media URLs to copy");
      return;
    }

    const text = urls.join("\n");

    // If Ctrl or Shift is held, save as .txt file via Chrome downloads
    if (e.ctrlKey || e.shiftKey) {
      try {
        const blob = new Blob([text], { type: "text/plain" });
        const blobUrl = URL.createObjectURL(blob);
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const host = tab?.url ? extractDomain(tab.url) : "media";
        chrome.downloads.download({
          url: blobUrl,
          filename: `${host}_links.txt`,
          saveAs: false,
        });
        const origText = copyLinksBtn.textContent;
        copyLinksBtn.textContent = `\u2713 Saved ${urls.length}`;
        setTimeout(() => { copyLinksBtn.textContent = origText; }, 1500);
        return;
      } catch (err) {}
    }

    // Default: copy directly to clipboard
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      const origText = copyLinksBtn.textContent;
      copyLinksBtn.textContent = `\u2713 Copied ${urls.length}`;
      setTimeout(() => { copyLinksBtn.textContent = origText; }, 1500);
    } catch (err) {
      showError(`Could not copy links: ${err.message}`);
    }
  });
}

// --- Download Selected Media to Desktop App ---

downloadBtn.addEventListener("click", async () => {
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
    showError(formatAppError(resp.error));
  } else if (resp && resp.ok) {
    downloadBtn.innerHTML = `\u2713 Added ${resp.added}`;
    setTimeout(() => {
      downloadBtn.innerHTML = 'Download in App <span id="selected-count">0</span>';
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
      infoEl.title = "Imagus Sieve rules: automatically upgrades thumbnail previews to original full-size images";
    } else {
      infoEl.textContent = "Sieve: no rules loaded";
      infoEl.title = "No sieve rules loaded. Click 'Update now' or 'Load sieve rules…'";
    }
  } catch (e) {
    infoEl.textContent = "Sieve: error reading rules";
  }
}

// The file dialog is opened by a focusable button, not by a click handler on
// static text: #sieve-btn and #sieve-update-btn were <span>s, so the keyboard
// could not reach either of these actions at all.
const sieveFileInput = document.getElementById("sieve-file");
document.getElementById("sieve-btn").addEventListener("click", () => sieveFileInput.click());

sieveFileInput.addEventListener("change", async (e) => {
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
  btn.textContent = "Update now";
  setTimeout(updateSieveInfo, 1500);
});
