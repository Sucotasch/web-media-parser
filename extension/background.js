/**
 * Background service worker for Web Media Parser extension.
 * Loads sieve rules into storage and communicates with desktop app.
 */

// EXT-9: shared constants (FULLSIZE_SOURCES, LINKS_CAP) — single source.
// sieve.js: parseSieve/applySieveRules — used by handleStartScan (the scan
// moved from the popup into the SW so it survives popup close, ERR-4).
importScripts("shared.js", "sieve.js");

const API_BASE = "http://127.0.0.1:19876";
const SIEVE_VERSION = "2026.07.15"; // Bump when shipping new sieve.json

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

// --- C-1: online sieve update (port of Mod's updateSieve) ---
// sieve_updater.js is an ES module (importScripts cannot load it), so its
// pure logic is mirrored via dynamic import when the worker supports it;
// the weekly alarm and merge semantics live there.
let _sieveUpdater = null;
async function getSieveUpdater() {
  if (!_sieveUpdater) {
    try {
      _sieveUpdater = await import(chrome.runtime.getURL("sieve_updater.js"));
    } catch (e) {
      console.warn("Sieve updater module unavailable:", e.message);
      return null;
    }
  }
  return _sieveUpdater;
}

async function runSieveUpdate() {
  const updater = await getSieveUpdater();
  if (!updater) return null;
  const stored = (await chrome.storage.local.get("sieveRepository")) || {};
  const result = await updater.updateSieve(stored.sieveRepository);
  console.info("Sieve update:", result.status, "—", result.message);
  if (result.status === "updated") {
    // New rules in storage — refresh the in-memory regex cache.
    await loadSieveResPatterns();
  }
  return result;
}

// Weekly auto-update (MV3 alarms survive worker suspension)
chrome.alarms.create("sieve-update-weekly", { periodInMinutes: 7 * 24 * 60 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "sieve-update-weekly") runSieveUpdate();
});

// Check once shortly after every service-worker cold start (cheap: 304 or
// early-exit when the module is unavailable — no network storm). Note: MV3
// may suspend the SW before the timer fires; that is acceptable — the weekly
// alarm below is the authoritative update trigger, and the popup's
// "Update sieve now" button covers on-demand checks.
setTimeout(runSieveUpdate, 15000);

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
    // Replace $1, $2, etc. with captured groups (literal string replacement).
    // B-2: substitute in DESCENDING order — ascending order corrupts $10
    // (its $1 substring is replaced first, leaving "(g1)0"). Mirrors the
    // desktop twin in site_pattern_manager.apply_link_url_transform.
    for (let i = matchGroups.length - 1; i >= 1; i--) {
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

// --- ERR-7 / Errors.txt #1: native port of the [Resize] sieve rule ---
// The sieve's [Resize] rule strips CDN resize params (Shopify `&width=500`,
// Cloudflare `?w=`, `?h=`, `?resize=`, ...) to reach the original file.
// Its `to` is a JS expression — MV3 CSP blocks new Function in the SW, so
// JS sieve rules silently never ran and the generic rule was lost (that is
// why colorsuper.com full-size URLs were never found while Imagus Mod MD,
// which runs JS rules, resolves them). This is the pure-string equivalent:
// same `img` regex match + same param-strip, no JS evaluation needed.
// Regexes are declared INSIDE the function so the node tests can extract the
// function source standalone (the url_transform test pattern); module-level
// consts would be invisible to the isolated evaluation.
function applyResizeUpgrade(url) {
  try {
    const EXCLUDED_RE = /(?:^|\.)(?:reddit\.com|redd\.it|jtvnw\.net|cdn\.tv2\.no)\//i;
    const IMG_RE = /^((?:https?:\/\/)?[^/]{4,70}\/[^?]+)((?:\?(?:[^&]*&)*?)(?:w(?:idth)?|h(?:eight)?|(?:cro|stri)p|q(?:uality)?(?==[\d.]+(?:&|$))|auto|f(?:orma|i)t|resize|im)=[\w%.,]+(?:&|$).*)$/i;
    const STRIP_RE = /(?<=[?&])(?:w(?:idth)?|h(?:eight)?|(?:cro|stri)p|q(?:uality)?(?==[\d.]+(?:&|$))|auto|f(?:orma|i)t|resize|im)=[\w%.,]+(?:&|$)/gi;
    if (!url || EXCLUDED_RE.test(url)) return null;
    const m = url.match(IMG_RE);
    if (!m) return null;
    const stripped = m[2].replace(STRIP_RE, "");
    if (stripped === m[2]) return null;
    const result = m[1] + stripped;
    return result === url ? null : result;
  } catch (e) {
    return null;
  }
}

async function discoverFullsize(links, pageUrl, onProgress) {
  const discovered = [];
  const seen = new Set();

  async function processLink(linkUrl) {
    try {
      // Try to apply sieve url pattern (string transforms + optional POST data)
      let fetchOptions = {
        // ERR-1: send the page as Referer — many image hosts hotlink-protect
        // full-size paths (img10.reactor.cc /pics/post/full/ 302s to the post
        // page when fetched referer-less). Browsers natively send Referer on
        // <img> loads, which is why Imagus resolves these; the SW fetch must
        // mirror that or discovery silently degrades to thumbnails.
        headers: { "Accept": "text/html", "Referer": pageUrl },
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
            // C-4: data:-templates (e.g. "data:,$&") are no-fetch markers in
            // Imagus, not URLs — fetching them produced https://data:,... garbage.
            if (transformed && !transformed.startsWith("data:")) {
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

  // B-4: shared page-level deadline (mirrors desktop FULLSIZE_DISCOVER_TIME_BUDGET).
  // A page full of dead hosts must not stall the popup for minutes.
  const deadline = Date.now() + 45000;
  // Process links in chunks of DISCOVER_CONCURRENCY
  for (let i = 0; i < links.length; i += DISCOVER_CONCURRENCY) {
    if (Date.now() >= deadline) {
      console.info("discoverFullsize: 45s budget exhausted; remaining links skipped");
      break;
    }
    const chunk = links.slice(i, i + DISCOVER_CONCURRENCY);
    const results = await Promise.all(chunk.map(processLink));
    for (const r of results) discovered.push(...r);
    if (onProgress) {
      onProgress(Math.min(i + chunk.length, links.length), links.length, discovered.length);
    }
  }

  return { media: discovered };
}

// --- Session keepalive (ERR-6) ---
// MV3 idle-terminates the service worker after ~30s without events. A long
// download batch or a slow linked-page scan can sit in quiet windows between
// events, the SW dies mid-promise, and chromeDownload/startScan never write
// their final state. A period alarm wakes the SW on a fixed cadence — the
// same approach as Imagus Mod MD's session keepalive (periodInMinutes 0.5).
let _keepaliveRefs = 0;
const KEEPALIVE_ALARM = "wmp-session-keepalive";
function keepaliveAcquire() {
  _keepaliveRefs++;
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.5 }).catch(() => {});
}
function keepaliveRelease() {
  _keepaliveRefs = Math.max(0, _keepaliveRefs - 1);
  if (_keepaliveRefs === 0) chrome.alarms.clear(KEEPALIVE_ALARM).catch(() => {});
}
chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || alarm.name !== KEEPALIVE_ALARM) return;
  // With refs > 0 the wake itself resets the idle timer (no-op needed here);
  // with refs == 0 (SW restarted with a stale alarm) self-clear.
  if (_keepaliveRefs === 0) chrome.alarms.clear(KEEPALIVE_ALARM).catch(() => {});
});
// Re-arm after a SW restart if a session is mid-flight (state survives in
// storage; the alarm does not).
chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.get(["downloadProgress", "scanProgress"]).then((st) => {
    const active = (st.downloadProgress && st.downloadProgress.status === "active")
      || (st.scanProgress && st.scanProgress.status === "scanning");
    if (active) keepaliveAcquire();
  }).catch(() => {});
});

// ERR-8 / Errors.txt follow-up: a cached scanResult belongs to a specific
// PAGE. When that tab navigates (page reload, link click), the cached result
// is stale forever — restoreScanState would re-show the OLD page's media on
// the NEW page and a fresh scan would be drowned by the restored one. Drop
// the cached result for the navigating tab so the popup starts clean.
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo || changeInfo.status !== "loading") return;
  chrome.storage.local.get(["scanResult", "scanProgress"]).then((st) => {
    const changes = {};
    if (st.scanResult && st.scanResult.tabId === tabId) changes.scanResult = null;
    if (st.scanProgress && st.scanProgress.tabId === tabId) changes.scanProgress = null;
    if (Object.keys(changes).length) {
      chrome.storage.local.set(changes).catch(() => {});
    }
  }).catch(() => {});
});

// --- Background scan (ERR-4: survives popup close) ---
// The popup used to run the whole pipeline (scanMedia + discoverFullsize +
// sieve transforms) and kept mediaItems in its own memory. Action popups are
// closed by Chrome on blur — moving the mouse away mid-scan killed everything
// and the user had to start over. Now the scan runs in the SW: progress goes
// to chrome.storage.local (scanProgress), the result to scanResult; the popup
// listens to storage.onChanged and re-renders live, and re-opens restore the
// last result for the current tab instead of forcing a re-scan.

async function handleStartScan(tabId) {
  // tabId is stored in EVERY progress state so stale entries can be matched
  // and cleared per-tab (tabs.onUpdated navigation clear, restoreScanState
  // staleness check). Without it a crashed/abandoned scan left
  // "Scanning..." in the popup forever and survived page navigations.
  const progress = {
    status: "scanning", phase: "page", scanned: 0, total: 0, found: 0, tabId,
  };
  keepaliveAcquire();
  try {
    await chrome.storage.local.set({ scanProgress: progress, scanResult: null });
  } catch (e) {}
  try {
    const response = await chrome.tabs.sendMessage(tabId, { action: "scanMedia" });
    if (!response || !response.media) {
      throw new Error("Content script returned no media");
    }
    let media = response.media || [];
    const pageUrl = response.url || "";

    if (response.links && response.links.length > 0) {
      progress.phase = "links";
      progress.total = Math.min(response.links.length, LINKS_CAP);
      try { await chrome.storage.local.set({ scanProgress: progress }); } catch (e) {}
      const linked = await discoverFullsize(
        response.links.slice(0, LINKS_CAP), pageUrl,
        (scanned, total, found) => {
          progress.scanned = scanned;
          progress.total = total;
          progress.found = media.length + found;
          chrome.storage.local.set({ scanProgress: { ...progress } }).catch(() => {});
        }
      );
      if (linked && linked.media) media = media.concat(linked.media);
    }

    // Apply sieve transforms (string rules only — same as the popup did;
    // JS rules need page DOM and stay skipped in MV3). ERR-7: the generic
    // [Resize] rule is JS, so it never ran here — applyResizeUpgrade is the
    // native port that strips CDN resize params (colorsuper.com case).
    try {
      const stored = await chrome.storage.local.get("sieveRules");
      if (stored.sieveRules && typeof parseSieve === "function") {
        const rules = parseSieve(JSON.parse(stored.sieveRules));
        for (const item of media) {
          if (item.source === "sieve-res" || item.source === "sieve-to") continue;
          const transformed = applySieveRules(item.url, pageUrl, rules);
          if (transformed && transformed !== item.url) {
            item.original_url = item.url;
            item.url = transformed;
            item.transformed = true;
            item.source = "sieve-to";
          } else {
            // ERR-7: fall back to the native [Resize] port — strips
            // &width=500 / ?w= / ?h= / ?resize= … to reach the original
            // file (Shopify & other CDNs). Applies to any image URL, so
            // page thumbnails (img/srcset/meta sources) upgrade too.
            const upgraded = applyResizeUpgrade(item.url);
            if (upgraded) {
              item.original_url = item.url;
              item.url = upgraded;
              item.transformed = true;
              item.source = "sieve-to";
            }
          }
        }
      }
    } catch (e) {
      console.warn("Sieve transform failed:", e);
    }

    // ERR-7: several &width= variants (1100/1000/2048 …) collapse to the
    // same stripped URL — dedup by final URL. Keep the variant with the
    // LARGEST rendered dimensions, not the first in DOM order: the gallery
    // strip (192×240) usually comes first, so "keep first" displayed thumb
    // metadata while the file itself was full-size. Area comparison prefers
    // the main image / og:image variant of the collapsed set.
    {
      const bestByUrl = new Map();
      for (const m of media) {
        if (!m) continue;
        const prev = bestByUrl.get(m.url);
        if (!prev) { bestByUrl.set(m.url, m); continue; }
        const prevArea = (prev.width || 0) * (prev.height || 0);
        const curArea = (m.width || 0) * (m.height || 0);
        if (curArea > prevArea) bestByUrl.set(m.url, m);
      }
      media = [...bestByUrl.values()];
    }

    const result = { media, pageUrl, title: response.title || pageUrl, tabId, ts: Date.now() };
    try {
      await chrome.storage.local.set({
        scanResult: result,
        scanProgress: { status: "done", phase: "done", scanned: 1, total: 1, found: media.length, tabId },
      });
    } catch (e) {}
    return { ok: true, count: media.length };
  } catch (e) {
    console.error("handleStartScan failed:", e);
    try {
      await chrome.storage.local.set({
        scanProgress: { status: "error", phase: "error", error: e.message, tabId },
      });
    } catch (e2) {}
    return { ok: false, error: e.message };
  } finally {
    keepaliveRelease();
  }
}

// --- Chrome Downloads ---

async function resolveUrl(url, referer) {
  try {
    // ERR-1: same hotlink-protection bypass as processLink — the referer
    // (originating page) is required to resolve full-size URLs on hosts
    // that 302 referer-less requests to an HTML page.
    const headers = { "Accept": "text/html" };
    if (referer) headers["Referer"] = referer;

    // ERR-5: HEAD-first. The old code did a full GET here — which downloaded
    // the ENTIRE media file into the SW before chrome.downloads downloaded it
    // AGAIN (double transfer per file, and the first download visibly stalled
    // while the resolve GET streamed megabytes). A HEAD round-trip answers the
    // one question we need (is this a real media file or an HTML shell?) in
    // milliseconds. GET is only the fallback for hosts that mishandle HEAD.
    let headCt = "";
    let headOk = false;
    try {
      const headResp = await fetch(url, {
        method: "HEAD",
        headers,
        signal: AbortSignal.timeout(4000),
      });
      headOk = headResp.ok;
      headCt = headResp.headers.get("content-type") || "";
    } catch (e) {}
    // Direct media (or anything not HTML) — no body needed, return as-is.
    if (headOk && headCt && !headCt.includes("text/html")) return url;

    // HEAD inconclusive (HTML shell / no content-type / 405 / network error):
    // fall back to GET and sieve res extraction (original behavior).
    const resp = await fetch(url, {
      headers,
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

    // ERR-5: live progress in storage — the popup renders it via
    // storage.onChanged instead of blocking on sendMessage until ALL files
    // finish (which previously left "Saving…" frozen for minutes with no
    // feedback, and hid that the first file hadn't even started yet).
    const emitProgress = () => {
      const state = {
        status: completed >= total ? "done" : "active",
        started, saved, failed: completed - saved, total,
      };
      chrome.storage.local.set({ downloadProgress: state }).catch(() => {});
    };
    keepaliveAcquire();
    emitProgress();

    function startNext() {
      // If all items processed, check if we're done
      if (started >= total) {
        if (completed >= total) {
          emitProgress();
          keepaliveRelease();
          resolve({ saved });
        }
        return;
      }
      // If at limit, wait (will be called again when one completes)
      if (activeCount >= concurrentLimit) return;

      const item = items[started++];
      activeCount++; // Increment immediately to reserve the slot

      // ERR-5: items already verified as direct media during the scan
      // (source "link-direct" — processLink fetched them and saw
      // image/video content-type) skip the resolve round-trip entirely:
      // the download starts on the next tick instead of after a network
      // probe per file. Other sources (a-link / sieve results) still
      // resolve — those URLs were never network-checked.
      // NB: named `probe` (not `resolve`) — a local `const resolve` would
      // shadow the Promise executor's `resolve` in TDZ and crash.
      const probe = item.verified
        ? Promise.resolve(item.url)
        : resolveUrl(item.url, item.referer);

      probe.then(resolved => {
        const url = resolved || item.url;
        emitProgress();
        // ERR-3 (regression guard): NEVER pass a Referer via the downloads API
        // headers option — Chrome rejects it with "Unsafe request header name"
        // and the download fails to start (ERR-2: object form was also invalid,
        // "expected array, found object"). The Chrome-download path cannot set
        // Referer; hotlink-protected hosts there keep their pre-ERR-1 behavior.
        // The desktop-app download path DOES send Referer (its own HTTP client).
        chrome.downloads.download({
          url: url,
          filename: item.filename || undefined,
          conflictAction: "uniquify"
        }, (downloadId) => {
          if (chrome.runtime.lastError || !downloadId) {
            console.error(`Download failed to start: ${item.url} — ${chrome.runtime.lastError?.message}`);
            activeCount--; // Release the slot
            completed++;
            emitProgress();
            startNext();
            return;
          }

          const listener = (delta) => {
            if (delta.id === downloadId && delta.state?.current) {
              const state = delta.state.current;
              if (state === 'complete' || state === 'interrupted') {
                // B-3: guard — watchdog is assigned by the setTimeout below,
                // after this listener is registered. A synchronous event
                // dispatch before assignment would hit the TDZ ReferenceError.
                if (watchdog) clearTimeout(watchdog);
                chrome.downloads.onChanged.removeListener(listener);
                activeCount--; // Release the slot
                if (state === 'complete') saved++;
                completed++;
                emitProgress();
                startNext(); // Start next download now that we have a free slot
              }
            }
          };
          chrome.downloads.onChanged.addListener(listener);
          // EXT-5: safety net — if onChanged never fires (download cancelled
          // from Chrome's shelf, extension reloaded), don't hang the promise
          // (and the popup's "Saving…") forever.
          // B-3: `let` (not `const`) so reassignment in the timeout callback
          // keeps the listener's guard simple and the TDZ window minimal.
          let watchdog = setTimeout(() => {
            watchdog = null;
            chrome.downloads.onChanged.removeListener(listener);
            activeCount--; // Release the slot
            completed++;
            emitProgress();
            startNext();
          }, 10 * 60 * 1000);
          setTimeout(startNext, 0); // Try to start next (async to avoid stack overflow)
        });
      }).catch(e => {
        console.error(`Resolve URL failed: ${item.url} — ${e.message}`);
        activeCount--; // Release the slot
        completed++;
        emitProgress();
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

  // ERR-7: same native [Resize] port as the popup scan — the keyboard
  // command path had no sieve transforms at all, so CDN resize params
  // (Shopify &width=500 …) were never stripped here either.
  for (const item of media) {
    if (item && item.source !== "sieve-to" && item.source !== "sieve-res") {
      const upgraded = applyResizeUpgrade(item.url);
      if (upgraded) {
        item.original_url = item.url;
        item.url = upgraded;
        item.transformed = true;
        item.source = "sieve-to";
      }
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
      // ERR-5: link-direct items were already fetched (image/video) during
      // discovery — skip the resolve round-trip so downloads start instantly.
      verified: item.source === "link-direct",
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
  if (request.action === "startScan") {
    handleStartScan(request.tabId).then(sendResponse);
    return true;
  }
  if (request.action === "resetScan") {
    // Called by the popup when the user starts a fresh scan or navigates away.
    chrome.storage.local.remove(["scanResult", "scanProgress"]).catch(() => {});
    sendResponse({ ok: true });
    return false;
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
  if (request.action === "updateSieve") {
    runSieveUpdate().then((r) => sendResponse(r || { status: "error", message: "updater unavailable" }));
    return true;
  }
});
