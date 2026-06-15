/**
 * Popup script for Web Media Parser extension.
 * Scans the current page for media, shows previews, sends to desktop app.
 */

const mediaItems = [];

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

// --- Scan page ---

scanBtn.addEventListener("click", async () => {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  mediaItems.length = 0;

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
      pageInfoDiv.textContent = response.title || response.url;
      pageInfoDiv.classList.remove("hidden");
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

// --- Render media list ---

function renderMediaList() {
  mediaList.innerHTML = "";

  if (mediaItems.length === 0) {
    resultsDiv.classList.add("hidden");
    emptyDiv.classList.remove("hidden");
    return;
  }

  emptyDiv.classList.add("hidden");
  resultsDiv.classList.remove("hidden");
  updateCount();
  downloadBtn.disabled = false;

  mediaItems.forEach((item, index) => {
    const div = document.createElement("div");
    div.className = "media-item";

    // Checkbox
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.index = index;
    checkbox.addEventListener("change", updateCount);

    // Thumbnail or placeholder
    const thumb = document.createElement("div");
    thumb.className = "thumb placeholder";
    thumb.textContent = item.type === "video" ? "▶" : "📷";

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
      img.onerror = () => {}; // Keep placeholder
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
      sizeSpan.textContent = ` · ${item.width}×${item.height}`;
      metaDiv.appendChild(sizeSpan);
    }
    if (item.source) {
      const sourceSpan = document.createElement("span");
      sourceSpan.textContent = ` · ${item.source}`;
      metaDiv.appendChild(sourceSpan);
    }
    if (item.transformed) {
      const tSpan = document.createElement("span");
      tSpan.className = "transformed";
      tSpan.textContent = " ✦ transformed";
      metaDiv.appendChild(tSpan);
    }

    info.appendChild(urlDiv);
    info.appendChild(metaDiv);
    div.appendChild(checkbox);
    div.appendChild(info);
    mediaList.appendChild(div);
  });
}

function updateCount() {
  const checked = mediaList.querySelectorAll("input[type='checkbox']:checked");
  selectedCountSpan.textContent = checked.length;
  countSpan.textContent = `${checked.length} / ${mediaItems.length}`;
  downloadBtn.disabled = checked.length === 0;
  selectAllCheckbox.checked = checked.length === mediaItems.length && mediaItems.length > 0;
}

// --- Select all ---

selectAllCheckbox.addEventListener("change", () => {
  mediaList.querySelectorAll("input[type='checkbox']").forEach((cb) => {
    cb.checked = selectAllCheckbox.checked;
  });
  updateCount();
});

// --- Download ---

downloadBtn.addEventListener("click", async () => {
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

  const oneShot = oneShotCheckbox.checked;
  const resp = await chrome.runtime.sendMessage({ action: "download", urls: selected, one_shot: oneShot });

  if (resp && resp.error) {
    showError(resp.error);
  } else if (resp && resp.ok) {
    downloadBtn.innerHTML = `✓ Added ${resp.added}`;
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
