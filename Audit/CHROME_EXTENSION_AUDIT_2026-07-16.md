# Chrome Extension Audit — Web Media Parser

| | |
|---|---|
| **Date** | 2026-07-16 |
| **Scope** | `extension/**` + desktop bridge (`src/server/http_server.py`, `MainWindow._start_extension_server`) |
| **Method** | Full source trace of MV3 service worker, content script, popup, sieve pipeline, HTTP API. No speculative “maybe”. Runtime checks for sieve.json shape and dead-code references. |
| **Code changes** | **None** — document only |
| **Audience** | Junior developer: fix bugs without re-learning desktop crawler |

Related: full-app audit `Audit/FULL_AUDIT_2026-07-16.md` (queue/parser). This file is **extension-only**.

---

## 0. How to use this document

1. Read **§1** (architecture + data flows) once.
2. Fix **P0 → P1 → P2** in order; each bug has evidence, repro, patch sketch, regression checks.
3. Manual smoke matrix is in **§5**.
4. Do **not** rewrite the desktop parser while fixing the extension unless a bridge bug forces a tiny change (noted explicitly).

---

## 1. Architecture (as implemented)

### 1.1 Files

| File | Role |
|------|------|
| `extension/manifest.json` | MV3, permissions, commands, content script entry |
| `extension/background.js` | Service worker: sieve load, discover fullsize, Chrome downloads, desktop HTTP |
| `extension/content_script.js` | In-page DOM scan (`scanMedia`) |
| `extension/popup/popup.html` + `popup.js` + `popup.css` | UI: scan, filter, Download / Save / Parse |
| `extension/sieve.json` | ~849 Imagus-style rules (~900 KB) |
| `extension/sieve.js` | Parser for `img`/`to` transforms — **not loaded by any entrypoint** (dead) |
| `src/server/http_server.py` | `127.0.0.1:19876` REST for desktop |
| `MainWindow._start_extension_server` | Callbacks: add tasks, status |

### 1.2 Permissions (manifest)

```json
"permissions": ["activeTab", "storage", "downloads", "cookies"],
"host_permissions": ["<all_urls>"]
```

Missing (relevant to fixes): `scripting` (optional, if you inject scanners), no `tabs` permission (limited `tabs.query`/`sendMessage` still work for active tab in many cases via activeTab when user opens popup; **keyboard commands without popup may be restricted** — see BUG-E12).

### 1.3 Data flows

```
┌──────────────────── POPUP ────────────────────┐
│ Scan → tabs.sendMessage(scanMedia)            │
│      → background discoverFullsize(links)     │
│      → list + filters (domain / fullsize)     │
│                                               │
│ [Page only ✓] Download  → POST /api/tasks     │
│   one_shot:true + selected media URLs         │
│ [Page only ✗] Parse Page → POST /api/tasks    │
│   one_shot:false + [{url: pageUrl}]           │
│ Save (Chrome) → chrome.downloads via BG       │
└────────────────────┬──────────────────────────┘
                     │
┌────────────────────▼── background.js ─────────┐
│ loadSieveRules → chrome.storage.local         │
│ cachedSieveRes from rules with link+res       │
│ sendToDesktop → http://127.0.0.1:19876        │
│ chromeDownload / resolveUrl / discoverFullsize│
│ commands: Ctrl+Shift+S / Ctrl+Shift+D         │
└────────────────────┬──────────────────────────┘
                     │
┌────────────────────▼── Desktop ───────────────┐
│ ExtensionServer POST /api/tasks               │
│ one_shot → TaskItem + task._pending_downloads │
│ !one_shot → one queued URL task per page      │
│ ⚠ does NOT auto-start task (user must Start)  │
└───────────────────────────────────────────────┘
```

### 1.4 What sieve fields are actually used

| Sieve field | In sieve.json (sample stats) | Used by extension? |
|-------------|------------------------------|--------------------|
| `img` + `to` | ~515 `img`, ~158 JS `to` | **No** — only in dead `sieve.js` |
| `link` + `res` | ~677 `link`, ~596 `res` | **Yes** — `background.js` `cachedSieveRes` / `discoverFullsize` / `resolveUrl` |
| `url`, `loop`, `note` | present | **No** |

Stats from loaded `extension/sieve.json` (runtime): **849** rules; `img=515`, `res=596`, `link=677`, JS `to`≈158.

### 1.5 Modes (code vs README)

| UI | Code behavior | README claim |
|----|---------------|--------------|
| **Page only ✓ + Download** | Sends selected media to desktop (`one_shot: true`) | Says “скачивает через Chrome” — **wrong** |
| **Page only ✗ + Parse Page** | Sends page URL to desktop (`one_shot: false`) | Correct (deep parse) |
| **Save (Chrome)** | `chrome.downloads` | Correct |
| **Ctrl+Shift+S** | Scan + fullsize filter + Chrome download | Correct |
| **Ctrl+Shift+D** | Scan (waste) then only page URL → desktop | README OK; scan is wasted |

---

## 2. Confirmed bugs

Legend: **P0** broken core promise · **P1** user-visible defect · **P2** robustness · **P3** docs/cleanup.

---

### BUG-E01 [P0] `sieve.js` never loaded — thumbnail→fullsize `img`/`to` transforms dead

**Where:** `extension/sieve.js` exists; **not** in `manifest.json` content_scripts/background, **not** in `popup.html` scripts.

**Evidence:**

```html
<!-- popup.html — only popup.js -->
<script src="popup.js"></script>
```

```json
// manifest.json background
"service_worker": "background.js"
// content_scripts: only "content_script.js"
```

Repo search: `parseSieve` / `applySieveRules` / `transformMedia` appear **only** in `sieve.js`. Popup never transforms media with Imagus `to` rules. Background only uses `link`+`res` HTML scrape.

**Impact:** Half of Imagus power (direct URL rewrite: thumb CDN → full CDN without fetching gallery HTML) is unused. Many sites only work via `img`/`to`, not `res`.

**Fix (recommended path — wire into popup after scan):**

1. Add to `popup.html` **before** `popup.js`:

```html
<script src="../sieve.js"></script>
```

2. After DOM scan, before/after discoverFullsize:

```javascript
// popup.js — after mediaItems.push(...response.media)
const stored = await chrome.storage.local.get("sieveRules");
if (stored.sieveRules) {
  const rules = parseSieve(JSON.parse(stored.sieveRules));
  const pageUrl = response.url;
  const transformed = transformMedia(mediaItems.slice(), pageUrl, rules);
  mediaItems.length = 0;
  mediaItems.push(...transformed);
}
```

3. Also run transform on items returned from `discoverFullsize` if needed.

4. **Do not** run `executeSieveJS` in the **service worker** (no `document`). JS rules (`to` starts with `:`) need page context:

```javascript
// Preferred for JS rules: run in content script
// content_script.js: import or inline executeSieveJS with real document
```

Safer architecture:

| Rule type | Where to run |
|-----------|----------------|
| String `to` with `$1` | popup or background (pure string) |
| `to` starting with `:` | content script only |

**Alternative minimal fix:** Document that only `res` works; remove dead `sieve.js` to reduce confusion — **functional loss** vs Imagus. Prefer wiring.

**Regression:**  
- Rules with invalid regex still skipped.  
- Transformed items show `transformed: true` badge (already in UI).  
- Desktop payload already has `original_url` / `transformed` fields.

---

### BUG-E02 [P0] Relative links dropped — fullsize discovery mostly blind on normal sites

**Where:** `content_script.js` → `addLink` / `addMedia`

**Evidence:**

```javascript
function addLink(url) {
  if (url.startsWith("//")) url = "https:" + url;
  if (!url.startsWith("http://") && !url.startsWith("https://")) return; // DROP
  ...
}
// parent <a href="/photo/123"> → dropped
// href="image.jpg" → dropped
```

Runtime check of intended absolute forms:

| href | Accepted today | Correct absolute (base `https://site.com/album/page`) |
|------|----------------|--------------------------------------------------------|
| `/gallery/1` | No | `https://site.com/gallery/1` |
| `photo/2` | No | `https://site.com/album/photo/2` |
| `//cdn.../a.jpg` | Yes | OK |
| `https://...` | Yes | OK |

Galleries almost always use relative `href` on thumbnails.

**Impact:** `discoverFullsize` often gets **0 usable links** → default filter “Full size” shows empty/near-empty list → users think extension is broken.

**Fix:**

```javascript
function toAbsolute(url, baseUrl) {
  if (!url) return null;
  try {
    return new URL(url, baseUrl).href;
  } catch (e) {
    return null;
  }
}

function addLink(url) {
  url = toAbsolute(url, baseUrl);
  if (!url || !url.startsWith("http")) return;
  if (url === baseUrl) return;
  if (linkSet.has(url)) return;
  linkSet.add(url);
  links.push(url);
}

function addMedia(url, type, attrs = {}) {
  url = toAbsolute(url, baseUrl);
  if (!url || seen.has(url)) return;
  if (isJunkUrl(url)) return;
  // size check unchanged
  seen.add(url);
  media.push({ url, type, pageUrl: baseUrl, ...attrs });
}
```

Apply same for `a[href]` loop and `data-src` (today requires `http` or `//` only).

**Why safe:** Standard URL resolution; no extra network. May increase link count — already capped at 50 in popup/background.

**Regression:**  
- `javascript:` / `#` still filtered before `toAbsolute`.  
- link budget: still `links.slice(0, 50)`.

---

### BUG-E03 [P0] Cookies never sent to desktop (`chrome.cookies.getAll({ tabId })` is invalid)

**Where:** `background.js` → `getPageContext`

**Evidence:**

```javascript
const cookies = await chrome.cookies.getAll({ tabId });
```

Chrome Cookies API `getAll` accepts: `url`, `domain`, `name`, `path`, `secure`, `session`, `storeId` — **not `tabId`**. Call fails or returns empty; `catch (e) {}` swallows error.

README promises: «Извлечение cookies … → передача в desktop».

**Impact:** Age-gates / session sites fail when desktop fetches without browser cookies.

**Fix:**

```javascript
async function getPageContext(tabId) {
  const context = {};
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab?.url && /^https?:/i.test(tab.url)) {
      const cookies = await chrome.cookies.getAll({ url: tab.url });
      if (cookies.length > 0) {
        context.cookies = cookies.map(c => `${c.name}=${c.value}`).join("; ");
      }
      // Optional: also get cookies for eTLD+1 if needed later
    }
  } catch (e) {
    console.warn("getPageContext cookies:", e);
  }
  // UA: see BUG-E04
  try {
    const response = await chrome.tabs.sendMessage(tabId, { action: "getUA" });
    if (response?.userAgent) context.user_agent = response.userAgent;
  } catch (e) {
    // fallback
    try {
      const tab = await chrome.tabs.get(tabId);
      // cannot read navigator without script; leave empty or use chrome.runtime.getPlatformInfo only
    } catch (_) {}
  }
  return context;
}
```

**Note:** Cookie header as a single string is what desktop `AsyncClientManager` already applies via `extension_cookies` → `Cookie` header. Domain scoping on desktop still imperfect (see full-app BUG-14); fixing getAll is still necessary and correct for first request.

**Regression:** Works only for `http(s)` tabs (not `chrome://`, `file://`).

---

### BUG-E04 [P0] User-Agent never collected — no `getUA` handler in content script

**Where:**  
- Sender: `background.js` `getPageContext` → `sendMessage({ action: "getUA" })`  
- Receiver: `content_script.js` only handles `scanMedia`

**Evidence:**

```javascript
// content_script.js
if (request.action === "scanMedia") { ... }
// no getUA branch
```

`scanMedia` response **does** include `userAgent: navigator.userAgent`, but popup **never stores it** for later Download; it calls `getContext` instead.

**Impact:** Desktop uses its own default UA; fingerprint mismatch with real browser session.

**Fix (minimal — content script):**

```javascript
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getUA") {
    sendResponse({ userAgent: navigator.userAgent });
    return;
  }
  if (request.action === "scanMedia") {
    // existing
  }
});
```

**Fix (popup — also stash UA from scan):**

```javascript
// after successful scan
window.__lastScanMeta = { url: response.url, userAgent: response.userAgent };
// when downloading:
context.user_agent = context.user_agent || window.__lastScanMeta?.userAgent;
```

**Regression:** None; pure addition.

---

### BUG-E05 [P0] Desktop only enqueues tasks — never auto-starts download

**Where:** `MainWindow._start_extension_server` → `add_tasks_from_extension`

**Evidence:**

```python
task = self.task_queue.add_task(...)
task._pending_downloads = items
# NO start_task / _launch_parser_for_task
return {"added": added}
```

User sees popup “✓ Added N” but files never download until they switch to desktop and press **Start**.

`_pending_downloads` is **not** in `TaskItem.to_dict()` — if app restarts before Start, one-shot selection is **lost**.

**Impact:** Core extension→desktop promise feels broken; README implies action completes.

**Fix options (choose product intent):**

| Option | Behavior | Pros |
|--------|----------|------|
| **A (recommended)** | After add, if no active task → auto-start newest; if busy → leave queued | Matches user expectation |
| **B** | Always queue only; popup text “Queued — press Start in app” | Honest, no surprise concurrent tasks |
| **C** | Persist `pending_downloads` into task_queue.json or a side file | Survives restart |

**Option A sketch (desktop, GUI thread — see also BUG-E13 thread affinity):**

```python
# After building task / items:
task = self.task_queue.add_task(...)
task._pending_downloads = items
if self.task_queue.active_task is None:
    self.task_queue.start_task(task.id)
    self._launch_parser_for_task(task)
    self.update_ui_state(True)
```

Must run on **GUI thread** (QueuedConnection).

**Option C sketch:**

```python
# TaskItem.to_dict / from_dict optional key pending_downloads: list[dict]
# Or write items JSON next to download_path
```

**Why safe (A):** Same as user pressing Start on selected row; reuses existing launch path. If a task is already running, leave new one queued (do not force pause unless product wants that).

**Regression:**  
- Deep parse multi-URL: currently one task per URL in loop — auto-start only first or last; prefer auto-start only when `one_shot` and single task.  
- Do not double-start.

---

### BUG-E06 [P1] First matching sieve `link` rule always `break`s even if `res` finds nothing

**Where:** `background.js` → `discoverFullsize` and `resolveUrl`

**Evidence:**

```javascript
for (const [name, { linkRegex, resPattern }] of Object.entries(cachedSieveRes)) {
  if (!linkRegex.test(...)) continue;
  try {
    // exec res patterns...
  } catch (e) {}
  break; // ALWAYS exit after first link match
}
```

Object key order is arbitrary. First rule whose `link` matches wins even with zero captures.

**Impact:** Wrong/empty fullsize URL for multi-rule domains.

**Fix:**

```javascript
let foundAny = false;
for (const [name, { linkRegex, resPattern }] of Object.entries(cachedSieveRes)) {
  if (!linkRegex.test(linkUrl.replace(/^https?:\/\//, "")) && !linkRegex.test(linkUrl)) continue;
  try {
    const patterns = Array.isArray(resPattern) ? resPattern : [resPattern];
    for (const pat of patterns) {
      pat.lastIndex = 0;
      const match = pat.exec(html);
      if (match && match[1]) {
        // push / return imgUrl
        foundAny = true;
        // for resolveUrl: return imgUrl immediately
        // for discover: continue collecting; optionally break outer if one is enough
      }
    }
  } catch (e) {}
  if (foundAny) break; // only stop after success
}
```

**Why safe:** More rules tried → better recall; may add a few more images (deduped via `seen`).

---

### BUG-E07 [P1] Default filter “Full size” hides almost all DOM media

**Where:** `popup.js` — `FULLSIZE_SOURCES = {"sieve-res","link-direct"}`; default `source-filter` = `fullsize`.

**Evidence:** DOM sources are `img`, `srcset`, `data-src`, `a-link`, `video`, `meta`, `css` — **none** are “fullsize”. Only background discovery tags count.

Combined with BUG-E02 (no relative links), default view is often empty after scan.

**Impact:** UX: “No media” / empty list despite thumbnails on page.

**Fix (product — pick one):**

1. **Default filter to “All sources”** after scan if fullsize count is 0.  
2. Expand FULLSIZE set:

```javascript
const FULLSIZE_SOURCES = new Set([
  "sieve-res", "link-direct", "a-link", "srcset", "data-src", "meta"
]);
// keep "img" as thumbnail-ish if dimensions small
```

3. Mark sieve `img`/`to` transforms as fullsize (`source: "sieve-to"`) after BUG-E01.

**Recommended:** (1) + after E01/E02, default fullsize becomes useful.

```javascript
// end of successful scan
const fs = mediaItems.filter(m => FULLSIZE_SOURCES.has(m.source)).length;
if (fs === 0) {
  sourceFilter.value = "";
  activeSourceFilter = "";
}
renderMediaList();
```

---

### BUG-E08 [P1] Chrome downloads ignore Referer; hotlink protection fails

**Where:** `popup.js` / `commandScanAndProcess` pass `referer`; `chromeDownload` never uses it.

**Evidence:**

```javascript
chrome.downloads.download({
  url: resolved,
  filename: item.filename || undefined,
  conflictAction: "uniquify"
  // no headers / referer support in this API
});
```

Chrome Downloads API **cannot** set arbitrary Referer. Field is dead.

**Impact:** CDN returns 403 for many image hosts.

**Fix options:**

| Approach | Notes |
|----------|--------|
| **A** Prefer desktop download (has Referer) for protected hosts | Already works if cookies/UA fixed |
| **B** `declarativeNetRequest` / `webRequest` (MV3 limited) to attach Referer for download URLs | Complex, extra permission |
| **C** Fetch blob in SW with `Referer` header then `downloads.download({ url: blobUrl })` | Works for many hosts; CORS may block SW fetch without host permission — you have `<all_urls>` so extension fetch often works |

**Option C sketch:**

```javascript
async function chromeDownload(items) {
  let saved = 0;
  for (const item of items) {
    const resolved = await resolveUrl(item.url);
    let downloadUrl = resolved;
    try {
      const resp = await fetch(resolved, {
        headers: item.referer ? { "Referer": item.referer, "Accept": "image/*,*/*" } : {},
        credentials: "include",
      });
      if (resp.ok) {
        const blob = await resp.blob();
        downloadUrl = URL.createObjectURL(blob);
      }
    } catch (e) { /* fall back to direct URL */ }

    const id = await new Promise((resolve) => {
      chrome.downloads.download({
        url: downloadUrl,
        filename: sanitizeFilename(item.filename),
        conflictAction: "uniquify",
      }, (did) => resolve(chrome.runtime.lastError ? null : did));
    });
    if (downloadUrl.startsWith("blob:")) URL.revokeObjectURL(downloadUrl);
    if (id) saved++;
  }
  return { saved };
}
```

Add `sanitizeFilename` (strip `?`, illegal Windows chars).

**Regression:** Memory for large videos — skip blob path for `type===video` or size > N MB; use direct URL.

---

### BUG-E09 [P1] Bundled sieve never updates after first install

**Where:** `background.js` → `loadSieveRules`

```javascript
if (stored.sieveRules) {
  console.info("Sieve rules already loaded");
  return;  // blocks update forever
}
```

On extension **update**, new `sieve.json` in package is ignored if storage already set. User must manually “Load sieve rules…”.

**Fix:**

```javascript
const SIEVE_VERSION = "2026.04.01"; // bump when shipping new sieve.json

async function loadSieveRules(force = false) {
  const stored = await chrome.storage.local.get(["sieveRules", "sieveVersion"]);
  if (!force && stored.sieveRules && stored.sieveVersion === SIEVE_VERSION) return;

  const resp = await fetch(chrome.runtime.getURL("sieve.json"));
  const data = await resp.json();
  await chrome.storage.local.set({
    sieveRules: JSON.stringify(data),
    sieveVersion: SIEVE_VERSION,
  });
}

chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === "install" || details.reason === "update") {
    await loadSieveRules(true);
  }
});
```

**Why safe:** Overwrites only when version changes; custom user upload can set `sieveVersion: "custom"` so force update does not clobber without asking — optional prompt on update if version is custom.

---

### BUG-E10 [P1] `parseSrcset` ignores density descriptors (`1x`, `2x`)

**Where:** `content_script.js` → `parseSrcset`

```javascript
const width = parseInt(parts[1]); // "2x" → 2, wrongly treated as width
// or NaN for some forms → bestUrl stays null
```

**Impact:** Wrong/missing largest candidate from `srcset`.

**Fix:**

```javascript
function parseSrcset(srcset) {
  let bestUrl = null, bestScore = -1;
  for (const item of srcset.split(",")) {
    const parts = item.trim().split(/\s+/);
    if (!parts[0]) continue;
    let score = 1;
    if (parts[1]) {
      if (parts[1].endsWith("w")) score = parseInt(parts[1], 10) || 1;
      else if (parts[1].endsWith("x")) score = (parseFloat(parts[1]) || 1) * 10000; // prefer higher density
    }
    if (score > bestScore) {
      bestScore = score;
      bestUrl = parts[0];
    }
  }
  return bestUrl;
}
```

---

### BUG-E11 [P1] Page-only Download enabled with zero selection

**Where:** `popup.js` → `updateOneShotMode`

```javascript
downloadBtn.disabled = false; // always
```

**Impact:** Click does nothing (`selected.length === 0 return`) or confuses users.

**Fix:**

```javascript
function updateOneShotMode() {
  const isDeepParse = !oneShotCheckbox.checked;
  deepParseWarning.classList.toggle("hidden", !isDeepParse);
  if (isDeepParse) {
    downloadBtn.textContent = "Parse Page";
    downloadBtn.disabled = false; // only needs page URL
  } else {
    downloadBtn.textContent = "Download";
    updateCount(); // sets disabled from checkboxes
  }
  chromeDownloadBtn.disabled = isDeepParse || getVisibleCheckboxes().length === 0;
}
```

---

### BUG-E12 [P1] Keyboard commands may fail without content script / tab permission

**Where:** `commandScanAndProcess` → `tabs.sendMessage` without ensuring injection.

**Evidence:** On `chrome://`, PDF viewer, Web Store, or tabs opened before extension install without refresh, `sendMessage` fails → badge `!`.

Also `commands` fire without user gesture on the page; `activeTab` may **not** grant host access the same way as popup click.

**Fix:**

1. Add `"scripting"` permission.  
2. Before scan:

```javascript
async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { action: "getUA" });
    return true;
  } catch {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content_script.js"],
    });
    return true;
  }
}
```

3. Guard non-http(s) URLs early with clear badge.

---

### BUG-E13 [P1] Extension HTTP callback mutates Qt queue off the GUI thread

**Where:** Desktop `add_tasks_from_extension` runs in aiohttp/server thread (see full-app BUG-12).

**Impact:** Rare list corruption / crashes when adding from extension while UI updates.

**Fix:** Documented in full audit — `QTimer.singleShot(0, ...)` / `QMetaObject.invokeMethod` for all `task_queue` mutations. Extension side needs no change once desktop fixed; optional: popup shows “Queued” after 200ms status poll.

---

### BUG-E14 [P2] `Ctrl+Shift+D` scans media then discards it

**Where:** `commandScanAndProcess` for `send-desktop`:

```javascript
// builds media + discoverFullsize...
await sendToDesktop([{ url: response.url }], false, context);
```

**Impact:** Wasted network/time (up to 50×8s); battery; looks like hang.

**Fix:**

```javascript
if (action === "send-desktop") {
  const context = await getPageContext(tab.id);
  await sendToDesktop([{ url: tab.url }], false, context);
  // no scan needed
}
```

---

### BUG-E15 [P2] Sequential `discoverFullsize` — no concurrency, long popup freeze

**Where:** `for (const linkUrl of links)` await fetch one-by-one, timeout 8s each → worst **400s**.

Popup waits on `sendMessage` for whole discovery.

**Fix:**

```javascript
const CONCURRENCY = 5;
async function mapPool(items, limit, fn) {
  const ret = [];
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      ret[idx] = await fn(items[idx]);
    }
  }
  await Promise.all(Array.from({ length: limit }, () => worker()));
  return ret;
}
// merge discovered arrays
```

Also send progress messages optional. Cap links at 50 remains.

**Why safe:** Slightly higher parallel load on target site — 5 is reasonable; domain politeness optional later.

---

### BUG-E16 [P2] Junk filter drops all `.png` / `.gif` by path

**Where:** `content_script.js` `JUNK_PATTERNS`:

```javascript
/\.(gif|png|ico)$/i,
```

**Impact:** Direct PNG fullsize links via `a[href$=.png]` never become media; many real images classified junk if path ends with `.png`.

**Fix:** Remove blanket `png` (and maybe `gif`) from junk; keep `ico`, spacers, avatar path patterns. Size filter already drops tiny images.

```javascript
// remove: /\.(gif|png|ico)$/i
// keep:  /\.ico$/i  and path-based avatar/logo rules
```

---

### BUG-E17 [P2] Filename for Chrome download unsafe / empty

**Where:** `item.url.split("/").pop().split("?")[0]`

CDN paths like `.../abc123` or `.../media/` produce bad names; Windows illegal chars not stripped.

**Fix:**

```javascript
function filenameFromUrl(url, fallback = "image.jpg") {
  try {
    let name = new URL(url).pathname.split("/").filter(Boolean).pop() || fallback;
    name = decodeURIComponent(name).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_");
    if (!/\.[a-z0-9]{2,5}$/i.test(name)) name += ".jpg";
    return name.slice(0, 180);
  } catch {
    return fallback;
  }
}
```

---

### BUG-E18 [P2] `web_accessible_resources`: `sieve.json` exposed to all pages

**Where:** `manifest.json`

Any page can `fetch(chrome-extension://<id>/sieve.json)` if it learns the ID.

**Impact:** Low (rules are public Imagus-like), unnecessary surface.

**Fix:** Remove `web_accessible_resources` if only SW loads via `chrome.runtime.getURL` (SW does not need WAR). WAR is for page scripts. **Background fetch of extension resources does not require WAR.**

---

### BUG-E19 [P2] Deep-parse without validating `tab.url`

**Where:** popup Parse Page / send-desktop can send `chrome://` or `undefined` to desktop.

**Fix:**

```javascript
if (!pageUrl || !/^https?:\/\//i.test(pageUrl)) {
  showError("Open a normal http(s) page first");
  return;
}
```

---

### BUG-E20 [P2] API reports `"added": len(urls)` then overwrites — OK for one_shot; deep parse counts pages not media

Minor confusion in popup `Added ${resp.added}` for deep parse (1 page). Acceptable; document only.

---

### BUG-E21 [P3] README incorrect about Page only + Download

**Where:** `README.md` table lines 66–67.

Says Download with Page only uses Chrome; code uses desktop one-shot.

**Fix:** Update table to match code (or change product to match README — currently code is dual: Desktop Download + Chrome Save).

---

### BUG-E22 [P3] No automated tests for extension

No Jest/Playwright. After fixes, add at least pure-function tests for `toAbsolute`, `parseSrcset`, `parseSieve` (extract to testable module).

---

## 3. Checked and **not** bugs

| Claim | Verdict |
|-------|---------|
| MV3 service worker design | Correct for Chrome |
| `return true` on async `onMessage` | Correct |
| Desktop port 19876 + CORS `*` on localhost | OK for local tool |
| `host_permissions: <all_urls>` for discover fetch | Required for linked-page fetch |
| Page only checkbox default checked | Intentional |
| link cap 50 | Intentional throttle |
| `sieve-res` only counting as fullsize (design) | Intentional but harmful with E02 — product issue |

---

## 4. Fix order for junior developer

| Priority | Bugs | Effort | Verify |
|----------|------|--------|--------|
| Day 1 | **E02** absolute URLs, **E07** filter fallback | S | Scan gallery with relative links → list non-empty |
| Day 1 | **E03** cookies by URL, **E04** getUA | S | Context payload non-empty in desktop logs |
| Day 2 | **E05** auto-start or honest UX + optional persist pending | M | Download from popup actually saves files |
| Day 2 | **E01** wire sieve.js / content transform | M | Known Imagus thumb URL rewrites |
| Day 3 | **E06** break-only-on-success, **E09** sieve version | S | Multi-rule sites improve |
| Day 3 | **E08** blob+Referer downloads, **E17** filenames | M | Hotlink image saves via Chrome |
| Day 4 | **E10–E16, E18–E19**, README **E21** | S–M | Smoke matrix §5 |
| Day 5 | Desktop GUI-thread **E13**, light tests **E22** | M | Stress add while UI running |

---

## 5. Manual smoke matrix (after fixes)

| # | Steps | Expected |
|---|--------|----------|
| 1 | Desktop running, popup status | Online, `Q: n` |
| 2 | Desktop off, popup | Disconnected; Download shows error |
| 3 | Site with relative thumb links, Scan, Full size | ≥1 fullsize or auto switch to All |
| 4 | Page only Download selected | Files appear in desktop download dir without extra Start (if E05-A) |
| 5 | Uncheck Page only → Parse Page | One queue task with page URL; crawl starts (if auto) or queued |
| 6 | Save (Chrome) hotlink image | File in Chrome download folder, non-zero |
| 7 | Ctrl+Shift+S | Badge count, files downloading |
| 8 | Ctrl+Shift+D | Fast; page task only; no long discover |
| 9 | Age-gated site with cookies | Desktop request uses Cookie header |
| 10 | Load custom sieve JSON | Count updates; discover uses new `res` rules |
| 11 | Update extension with new sieve version | Rules refresh without manual load |
| 12 | `chrome://extensions` tab Scan | Clear error, no crash |

---

## 6. Patch sketches (high-value, minimal)

### 6.1 Absolute URLs (E02) — `content_script.js`

Replace start of `scanPageMedia` helpers with `toAbsolute` as in BUG-E02. Pass `baseUrl` into helpers (already available as parameter).

### 6.2 Cookies + UA (E03/E04)

**content_script.js** — add `getUA` branch.  
**background.js** — `getAll({ url: tab.url })`.

### 6.3 Stop breaking sieve rules early (E06) — `background.js`

Change `break` → `if (foundAny) break` after successful capture (both `discoverFullsize` and `resolveUrl`).

### 6.4 Auto-start one-shot (E05) — `main_window.py` only

Keep HTTP handler thin; schedule:

```python
def _apply_extension_add(...):
    # existing add logic
    if one_shot and task and self.task_queue.active_task is None:
        self.task_queue.start_task(task.id)
        self._launch_parser_for_task(task)
        self.update_ui_state(True)
```

Call via `QTimer.singleShot(0, lambda: _apply_extension_add(...))` from HTTP thread.

### 6.5 Wire pure sieve transforms (E01 minimal)

`popup.html`:

```html
<script src="../sieve.js"></script>
<script src="popup.js"></script>
```

Note: `executeSieveJS` uses `document` — in **popup** document is popup DOM, **wrong** for page-dependent JS rules. For first iteration:

```javascript
// In applySieveRules usage from popup:
// Only apply rules where !rule.isJS
// Leave JS rules for content_script port (phase 2)
```

Or move `transformMedia` call into content script after scan (best).

---

## 7. Performance notes

| Fix | Perf |
|-----|------|
| E02 more links | More discover fetches — already capped at 50 |
| E15 concurrency 5 | Faster discover; slight load increase |
| E14 no scan on Ctrl+D | Large win |
| E01 transform | CPU only, <100ms typical on 200 URLs |
| E08 blob download | Memory-bound; guard large files |

---

## 8. Security notes (extension-specific)

| Topic | Status |
|-------|--------|
| Desktop API no auth | Localhost only; OK for trusted machine |
| CORS `*` on API | Acceptable for 127.0.0.1 tool; do not bind `0.0.0.0` |
| `sieve.js` `new Function(jsCode)` | If ever enabled, treat rules as **trusted** only (user-loaded JSON = code exec). Sandbox or disable JS `to` in extension. |
| WAR sieve.json | Prefer remove (E18) |

---

## 9. Summary table

| ID | Sev | Title | Primary file(s) |
|----|-----|-------|-----------------|
| E01 | P0 | `sieve.js` dead — no `img`/`to` transforms | sieve.js, popup, content |
| E02 | P0 | Relative URLs dropped | content_script.js |
| E03 | P0 | Cookies API wrong (`tabId`) | background.js |
| E04 | P0 | No `getUA` handler | content_script.js |
| E05 | P0 | Desktop enqueue without start / pending not persisted | main_window.py |
| E06 | P1 | Sieve `break` after first link match | background.js |
| E07 | P1 | Full size filter hides DOM media | popup.js |
| E08 | P1 | Chrome download no Referer | background.js |
| E09 | P1 | Sieve not refreshed on extension update | background.js |
| E10 | P1 | srcset density parsing | content_script.js |
| E11 | P1 | Download enabled with 0 selection | popup.js |
| E12 | P1 | Commands without guaranteed content script | background.js, manifest |
| E13 | P1 | Queue mutation off GUI thread | main_window.py |
| E14 | P2 | Ctrl+D useless full scan | background.js |
| E15 | P2 | Sequential discover slow | background.js |
| E16 | P2 | Junk drops all PNG | content_script.js |
| E17 | P2 | Unsafe filenames | background.js / popup.js |
| E18 | P2 | Unnecessary WAR | manifest.json |
| E19 | P2 | Non-http page URL sent | popup.js |
| E20–E22 | P2–P3 | API wording, README, tests | docs / — |

---

## 10. Sign-off

- Traced: popup → content scan → background discover/download → desktop `/api/tasks`.  
- Confirmed dead code: **entire `sieve.js` pipeline unused**.  
- Confirmed API misuse: **cookies.getAll({tabId})**, **missing getUA**.  
- Confirmed product gap: **add without start**.  
- Confirmed crawl blindness: **relative href rejection**.  
- No extension or desktop code was modified in this audit.

**Implementer start order:** E02 → E03 → E04 → E05 → E07 → E01 → E06 → rest.
