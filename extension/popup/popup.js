/**
 * Popup script for Web Media Parser extension.
 * Scans the current page for media, shows previews, sends to desktop app.
 */

const mediaItems = [];
let activeDomainFilter = "";
let activeSourceFilter = "";

const FULLSIZE_SOURCES = new Set(["sieve-res", "linked-dom", "linked-html", "linked-img", "link-direct"]);

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

function updateOneShotMode() {
  const isDeepParse = !oneShotCheckbox.checked;
  deepParseWarning.classList.toggle("hidden", !isDeepParse);
  chromeDownloadBtn.disabled = isDeepParse || getVisibleCheckboxes().length === 0;
  downloadBtn.textContent = isDeepParse ? "Parse Page" : "Download";
  downloadBtn.disabled = false;
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

scanBtn.addEventListener("click", async () => {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  mediaItems.length = 0;
  activeDomainFilter = "";
  activeSourceFilter = "fullsize";
  domainFilter.value = "";
  sourceFilter.value = "fullsize";

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      showError("No active tab found");
      return;
    }

    let response;
    try {
      response = await chrome.tabs.sendMessage(tab.id, {
        action: "scanMedia",
      });
    } catch (e) {
      showError(`Content script not loaded on this page. Please refresh the page (F5) first. Error: ${e.message}`);
      return;
    }

    if (response && response.media) {
      mediaItems.push(...response.media);
      // Ask background to discover fullsize from linked pages (CORS bypass)
      if (response.links && response.links.length > 0) {
        const linked = await chrome.runtime.sendMessage({
          action: "discoverFullsize",
          links: response.links.slice(0, 15),
          pageUrl: response.url,
        });
        if (linked && linked.media) {
          mediaItems.push(...linked.media);
        }
      }
      pageInfoDiv.textContent = response.title || response.url;
      pageInfoDiv.classList.remove("hidden");
      populateDomainFilter();
      renderMediaList();
      updateCount();
    } else {
      showError("No media found on this page");
    }
  } catch (e) {
    showError(`Scan failed: ${e.message}`);
  }

  scanBtn.disabled = false;
  scanBtn.textContent = "Scan This Page";
});

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

    if (item.width && item.height) {
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

  const total = activeDomainFilter
    ? mediaItems.filter((item) => extractDomain(item.url) === activeDomainFilter).length
    : mediaItems.length;

  selectedCountSpan.textContent = checkedCount;
  chromeCountSpan.textContent = checkedCount;
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
        selected.push({ url: item.url, referer: item.pageUrl || "", filename: baseName });
      }
    });

  if (selected.length === 0) return;

  chromeDownloadBtn.disabled = true;
  chromeDownloadBtn.innerHTML = `Saving <span>${selected.length}</span>...`;

  // Send to background for download
  try {
    const resp = await chrome.runtime.sendMessage({ action: "chromeDownload", items: selected });
    const saved = resp && resp.saved ? resp.saved : 0;
    chromeDownloadBtn.innerHTML = `\u2713 Saved ${saved}`;
  } catch (e) {
    showError(`Download failed: ${e.message}`);
    chromeDownloadBtn.innerHTML = `Save (Chrome) <span>${selected.length}</span>`;
  }

  setTimeout(() => {
    chromeDownloadBtn.innerHTML = `Save (Chrome) <span>${selected.length}</span>`;
    chromeDownloadBtn.disabled = false;
  }, 1500);
});

// --- Download ---

downloadBtn.addEventListener("click", async () => {
  const oneShot = oneShotCheckbox.checked;

  if (!oneShot) {
    // Deep parse: just send the page URL to desktop, no scan needed
    downloadBtn.disabled = true;
    downloadBtn.innerHTML = "Sending...";
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const pageUrl = tab ? tab.url : "";
    // Get browser context (cookies + UA) for the desktop app
    let context = {};
    try { context = await chrome.runtime.sendMessage({ action: "getContext", tabId: tab?.id }); } catch (e) {}
    const resp = await chrome.runtime.sendMessage({ action: "download", urls: [{ url: pageUrl }], one_shot: false, context });
    if (resp && resp.ok) {
      downloadBtn.innerHTML = "\u2713 Sent to app";
    } else if (resp && resp.error) {
      showError(resp.error);
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
      source: item.pageUrl || "",
      type: item.type,
      original_url: item.original || null,
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
      downloadBtn.innerHTML = `Download <span>${selected.length}</span>`;
      downloadBtn.disabled = false;
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

// --- Sieve rules management ---

async function updateSieveInfo() {
  const infoEl = document.getElementById("sieve-info");
  try {
    const stored = await chrome.storage.local.get("sieveRules");
    if (stored.sieveRules) {
      const data = JSON.parse(stored.sieveRules);
      const count = Object.keys(data).length;
      infoEl.textContent = `Sieve: ${count} rules loaded`;
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
    document.getElementById("sieve-info").textContent = `Sieve: ${count} rules loaded`;
    showError(`Loaded ${count} sieve rules from ${file.name}`);
    setTimeout(() => document.getElementById("error").classList.add("hidden"), 3000);
  } catch (e) {
    showError(`Failed to load: ${e.message}`);
  }
  e.target.value = "";
});
