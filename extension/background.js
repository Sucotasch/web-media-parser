/**
 * Background service worker for Web Media Parser extension.
 * Loads sieve rules into storage and communicates with desktop app.
 */

// EXT-9: shared constants (FULLSIZE_SOURCES, DISCOVER_QUARANTINE_MS) — single source.
// sieve.js: parseSieve/applySieveRules — used by handleStartScan (the scan
// moved from the popup into the SW so it survives popup close, ERR-4).
// sieve_updater.js: online sieve update (R-E-8) — a CLASSIC script on purpose;
// a classic worker refuses dynamic import() of a module, which is why the
// update flow used to be dead code.
// Firefox has no service-worker background: it runs these same files as plain
// background scripts (manifest "background.scripts"), loaded in order into ONE
// shared global, and there `importScripts` does not exist. Guarded, not removed:
// Chrome keeps loading them here, Firefox via the manifest. Either way the three
// files are evaluated before this one, so the globals below stay identical.
if (typeof importScripts === "function") importScripts("shared.js", "sieve.js", "sieve_updater.js");

const API_BASE = "http://127.0.0.1:19876";
const SIEVE_VERSION = "2026.09.15"; // Bump when shipping new sieve.json

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
    // R-E-13: a bundle bump used to OVERWRITE storage, silently dropping every
    // `_`-prefixed user rule and every `off: 1` written by an online merge —
    // disabled/removed rules came back to life. Merge with the same semantics
    // the updater promises ("user modifications are never dropped").
    const updater = self.WMPSieveUpdater;
    const merged = updater
      ? updater.mergeSieve(updater.parseStoredSieve(stored.sieveRules), data)
      : data;
    await chrome.storage.local.set({
      sieveRules: JSON.stringify(merged),
      sieveVersion: SIEVE_VERSION,
    });
    console.info(`Loaded ${Object.keys(merged).length} sieve rules into storage (v${SIEVE_VERSION})`);
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
// R-E-8: sieve_updater.js is a classic script loaded with importScripts above
// (a classic MV3 worker refuses import()), so its API is the global object.
// The null guard keeps the whole worker alive if that script ever fails to load.
const SIEVE_UPDATER = self.WMPSieveUpdater || null;
const SIEVE_ALARM_NAME = (SIEVE_UPDATER && SIEVE_UPDATER.SIEVE_UPDATE_ALARM)
  || "sieve-update-weekly";

/**
 * Run an online sieve update.
 * @param {boolean} force — skip the R-E-12 throttle (user pressed the button)
 */
async function runSieveUpdate(force = false) {
  const updater = SIEVE_UPDATER;
  if (!updater) {
    console.warn("Sieve updater unavailable");
    return null;
  }
  const stored = (await chrome.storage.local.get(["sieveRepository", "sieveUpdateAttempt"])) || {};
  // R-E-12: now that the updater actually runs, the cold-start probe is a real
  // network request on every worker start — throttle the automatic path.
  if (!force && !updater.shouldProbeLastUpdate(stored.sieveUpdateAttempt)) {
    return { status: "skipped", message: "Sieve check already performed recently" };
  }
  // D-01: the update can sit through two URLs × three retries with exponential
  // backoff — hand it the session keepalive so MV3 does not terminate the
  // worker in the middle of it (the third keepalive path the audit measured).
  const result = await updater.updateSieve(stored.sieveRepository, {
    version: SIEVE_VERSION,
    keepalive: { acquire: keepaliveAcquire, release: keepaliveRelease },
  });
  console.info("Sieve update:", result.status, "—", result.message);
  if (result.status === "updated") {
    // New rules in storage — refresh the in-memory regex cache.
    await loadSieveResPatterns();
  }
  return result;
}

// Weekly auto-update (MV3 alarms survive worker suspension)
// R-E-5: create it only when missing — re-creating on every cold start reset
// the 7-day period, so the alarm could never fire.
if (SIEVE_UPDATER) {
  Promise.resolve(SIEVE_UPDATER.ensureWeeklyAlarm(chrome.alarms)).catch(() => {});
}
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SIEVE_ALARM_NAME) runSieveUpdate();
});

// Check once shortly after every service-worker cold start (throttled to one
// attempt per COLD_START_MIN_INTERVAL_MS and cheap on the happy path: 304).
// Note: MV3 may suspend the SW before the timer fires; that is acceptable —
// the weekly alarm above is the authoritative update trigger, and the popup's
// "Update sieve now" button covers on-demand checks.
setTimeout(() => runSieveUpdate(false), 15000);

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
      // R-E-9: respect `off: 1` (8 rules in the bundled sieve, plus the
      // ghosts mergeSieve writes) — mirror of parseSieve's skip.
      if (rule.off) continue;
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

// Apply string url transform: replace $1, $2 with regex match groups.
//
// A-10/A-12/NF-2: this used to be a third substitution dialect (descending
// split/join, its own `$&` handling, no `$$`/`$nn` rules). It now delegates to
// the ONE spec implementation in sieve.js, shared with the desktop app
// (`site_pattern_manager.get_substitution`) and driven by the shared vectors in
// extension/tests/vectors/substitution.json.
function applyUrlTransform(template, matchGroups) {
  if (!template || !matchGroups) return null;
  try {
    const input = typeof matchGroups.input === "string" ? matchGroups.input : undefined;
    const result = getSubstitution(template, matchGroups, input);
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

/**
 * Errors.txt (e-hentai.gallery): what a probed page's <img> dump means.
 *
 * A DETAIL page (the classic "thumbnail links to a bigger version") presents
 * ONE image — that is the full-size URL, source "linked-img" (a FULLSIZE
 * source since R-E-4). A GRID/index page presents many: its images are the
 * page's own thumbnails, so calling them full size is a lie the popup then
 * shows in its "Full size" view (measured: e-hentai's front page / /popular /
 * tag pages contributed 285 thumbnails with source linked-img). They are kept
 * — the URL is real media — but demoted to "page-img", which is NOT in
 * FULLSIZE_SOURCES, so they land in the popup's "Thumbnails" bucket instead.
 *
 * @param {number} count — candidate image count on the probed page
 * @returns {"linked-img"|"page-img"}
 */
function classifyPageImages(count) {
  return count === 1 ? "linked-img" : "page-img";
}

/**
 * D-05: strip a WordPress/CDN size suffix (`photo-300x200.jpg` → `photo.jpg`).
 *
 * The desktop twin has done this since PAT-3 (site_pattern_manager, the
 * `-\d{2,4}x\d{2,4}` strip) — the extension had no equivalent, so a gallery of
 * `-1024x768` variants never produced its original here (measured: null).
 * Only the FIRST suffix is removed, and only directly before the extension.
 */
function stripSizeSuffix(url) {
  if (typeof url !== "string" || !url) return url;
  return url.replace(/(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp|avif))(?=$|\?)/i, "$2");
}

// Discovery step 2: cross-scan cache of probe results. A broken, empty or unavailable
// cache must never break a scan, so every storage call is guarded and caching degrades to
// "probe the network" (the pre-step-2 behaviour).
async function loadDiscoverCache() {
  try {
    const st = await chrome.storage.session.get(DISCOVER_CACHE_KEY);
    const raw = st && st[DISCOVER_CACHE_KEY];
    if (!raw || typeof raw !== "object") return {};
    const now = Date.now();
    const fresh = {};
    for (const [url, entry] of Object.entries(raw)) {
      if (entry && Array.isArray(entry.items) && now - entry.ts < DISCOVER_CACHE_TTL_MS) fresh[url] = entry;
    }
    return fresh;
  } catch (e) {
    return {};
  }
}

async function saveDiscoverCache(cache) {
  try {
    const newest = Object.entries(cache)
      .sort((a, b) => b[1].ts - a[1].ts)
      .slice(0, DISCOVER_CACHE_MAX);
    await chrome.storage.session.set({ [DISCOVER_CACHE_KEY]: Object.fromEntries(newest) });
  } catch (e) {
    // quota reached or session storage unavailable: an optimisation is never a requirement
  }
}

async function discoverFullsize(links, pageUrl, onProgress) {
  const discovered = [];
  const seen = new Set();

  // Host state for THIS scan. Keyed by origin (scheme+host+port), not by hostname:
  // `new URL(...).hostname` drops the port, so a hanging `host:8080` would demote a healthy
  // `host:443` — real sites do advertise ports (mirror CDNs).
  const originOf = (u) => { try { return new URL(u).origin; } catch (e) { return "?"; } };
  const failedOrigins = new Set(); // demoted to the postponed tier
  const backoffUntil = new Map();  // origin -> earliest timestamp for its next probe
  const strikes = new Map();       // origin -> consecutive host failures (exponential backoff)

  // Both tiers honour DISCOVER_PER_ORIGIN; the global DISCOVER_CONCURRENCY stays the cap.
  const inChunk = (chunk, origin) => chunk.filter((p) => p.origin === origin).length;
  const eligible = (p, chunk, now) => !failedOrigins.has(p.origin)
    && (backoffUntil.get(p.origin) || 0) <= now
    && inChunk(chunk, p.origin) < DISCOVER_PER_ORIGIN;

  async function processLink(linkUrl, report) {
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
      const originalLinkUrl = linkUrl;

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
              // NF-1/A-06: one normalizer for every produced URL. The old inline
              // version kept the link's protocol for a scheme-less template
              // (often wrong) and forced https:// on a protocol-relative one —
              // hardcoding https broke http pages.
              linkUrl = normalizeVariant(transformed, originalLinkUrl);
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
      if (!resp.ok) {
        // A host-level failure (throttled / broken origin) postpones the origin's other
        // links. A 404 or a page without media says nothing about the origin and must not
        // demote it — e-hentai alone is full of media-less pages (forums, ?act=expunge,
        // tag lists) served by hosts that also hold good detail pages.
        if (resp.status === 429 || resp.status >= 500) {
          report.hostFailed = true;
          if (resp.status === 429) report.throttled = true;
        }
        return [];
      }
      report.cacheable = true; // a 200 is knowledge worth reusing; failures are not
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("image/") || ct.includes("video/")) {
        return [{ url: linkUrl, type: ct.includes("video/") ? "video" : "image", pageUrl, source: "link-direct" }];
      }
      if (!ct.includes("text/html")) return [];

      const html = await resp.text();
      const results = [];
      let ruleMatched = false;

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
                ruleMatched = true;
              }
            }
          }
        } catch (e) {}
        if (foundAny) break;
      }

      // A rule matched: the site's own knowledge picked the image. The <img>
      // dump below is a blind regex over the whole document and would only add
      // noise (page furniture, thumbnails) next to it.
      if (ruleMatched) return results;

      // 2. Fallback: scan <img> src from HTML
      const SRC_RE = /<img[^>]+src=["']([^"']+)["']/gi;
      const candidates = [];
      let m;
      while ((m = SRC_RE.exec(html)) !== null) {
        let url = m[1];
        if (url.startsWith("//")) url = "https:" + url;
        if (url.startsWith("http") && !seen.has(url) && url !== linkUrl
            && /\.(jpe?g|jfif|jif|webp|avif|heic|bmp|tiff?)$/i.test(url)) {
          seen.add(url);
          candidates.push(url);
        }
      }
      const source = classifyPageImages(candidates.length);
      for (const url of candidates) {
        results.push({ url, type: "image", pageUrl, source });
      }
      return results;
    } catch (e) {
      // Network error / AbortSignal.timeout: the origin did not answer in time.
      report.hostFailed = true;
      return [];
    }
  }

  // B-4: shared page-level deadline (mirrors desktop FULLSIZE_DISCOVER_TIME_BUDGET).
  // A page full of dead hosts must not stall the popup for minutes.
  const deadline = Date.now() + 45000;
  // Inside a fixed deadline the ORDER decides the result set, so links are worked in ONE
  // pass with a host-tier queue: a link whose origin has not failed yet is probed first,
  // and an origin that fails is POSTPONED (not blocked — a slow host keeps its second
  // chance) to the tail, which is drained last under DISCOVER_QUARANTINE_MS. Measured on
  // 60 hanging + 120 live links: 0/120 results in 48s before this, 120/120 in 8.7s after,
  // with the scan over at 16.8s instead of held to the full 45s.
  const quarantineDeadline = Date.now() + DISCOVER_QUARANTINE_MS;
  const cache = await loadDiscoverCache();
  let cacheDirty = false;
  const queue = [];
  let scanned = 0;

  // Cache pass first: a cached link is free — no fetch, no politeness slot.
  for (const url of links) {
    const hit = cache[url];
    if (hit) {
      for (const item of hit.items) {
        if (!seen.has(item.url)) {
          seen.add(item.url);
          discovered.push(item);
        }
      }
      scanned += 1;
      continue;
    }
    queue.push({ url, origin: originOf(url) });
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  while (queue.length > 0) {
    if (Date.now() >= deadline) {
      console.info("discoverFullsize: 45s budget exhausted; remaining links skipped");
      break;
    }
    const now = Date.now();
    const chunk = [];
    for (let i = 0; i < queue.length && chunk.length < DISCOVER_CONCURRENCY; i++) {
      if (!eligible(queue[i], chunk, now)) continue;
      chunk.push(queue.splice(i, 1)[0]);
      i--;
    }
    if (chunk.length === 0) {
      // Nothing responsive is left. Either the postponed tier (origins that failed) gets its
      // bounded slice, or every remaining link is on an origin we owe a pause — waiting out
      // the shortest backoff is the point of the backoff, so wait instead of hammering.
      if (Date.now() < quarantineDeadline) {
        for (let i = 0; i < queue.length && chunk.length < DISCOVER_CONCURRENCY; i++) {
          if (!failedOrigins.has(queue[i].origin)) continue;
          if (!eligible(queue[i], chunk, now)) continue;
          chunk.push(queue.splice(i, 1)[0]);
          i--;
        }
      }
      if (chunk.length === 0) {
        const pending = [...backoffUntil.values()].filter((t) => t > Date.now());
        if (pending.length > 0) {
          const wait = Math.min(Math.min(...pending) - Date.now(), deadline - Date.now());
          if (wait > 0) {
            await sleep(wait);
            continue;
          }
        }
        console.info(`discoverFullsize: ${queue.length} link(s) left unprobed (postponed origins, budget)`);
        break;
      }
    }
    const reports = chunk.map(() => ({}));
    const results = await Promise.all(chunk.map((p, i) => processLink(p.url, reports[i])));
    for (let i = 0; i < chunk.length; i++) {
      const origin = chunk[i].origin;
      if (reports[i].hostFailed) {
        failedOrigins.add(origin);
        const n = (strikes.get(origin) || 0) + 1;
        strikes.set(origin, n);
        backoffUntil.set(origin, Date.now() + Math.min(DISCOVER_BACKOFF_MS * Math.pow(2, n - 1), DISCOVER_BACKOFF_MAX_MS));
        console.info(`discoverFullsize: ${origin} postponed (${reports[i].throttled ? "throttled" : "failed"})`);
      } else {
        strikes.delete(origin);
      }
      if (reports[i].cacheable) {
        cache[chunk[i].url] = { ts: Date.now(), items: results[i] };
        cacheDirty = true;
      }
      // Push unconditionally: processLink's own scan already dedupes against `seen` (it
      // adds every candidate it accepts before returning it), so filtering here drops
      // every result that page produced.
      for (const r of results[i]) discovered.push(r);
    }
    scanned += chunk.length;
    if (onProgress) onProgress(scanned, links.length, discovered.length);
  }

  if (cacheDirty) await saveDiscoverCache(cache);

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
/**
 * A-13 / NF-9: acquire the keepalive WITHOUT throwing.
 *
 * `chrome.alarms.create` returns a Promise on Chrome 111+ and `undefined`
 * before that, so the old `.catch(...)` on its result threw
 * ("Cannot read properties of undefined (reading 'catch')") — and because the
 * refcount was incremented BEFORE the call, the failure left it inflated at 1
 * forever: the alarm could never be cleared again.
 * The count is bumped only after the alarm request actually went through.
 * @returns {boolean} true when this call holds a new reference
 */
function keepaliveAcquire() {
  try {
    const created = chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.5 });
    if (created && typeof created.catch === "function") created.catch(() => {});
  } catch (e) {
    console.warn("keepalive: could not arm the session alarm:", e);
    return false;
  }
  _keepaliveRefs++;
  return true;
}
function keepaliveRelease() {
  _keepaliveRefs = Math.max(0, _keepaliveRefs - 1);
  if (_keepaliveRefs === 0) {
    try {
      const cleared = chrome.alarms.clear(KEEPALIVE_ALARM);
      if (cleared && typeof cleared.catch === "function") cleared.catch(() => {});
    } catch (e) {}
  }
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
/**
 * R-E-6: the ONE thumbnail→fullsize upgrade pipeline for scanned media.
 * handleStartScan (popup scan) and commandScanAndProcess (keyboard commands)
 * both call it; the command path used to run applyResizeUpgrade alone, so
 * string sieve rules silently never applied there.
 *
 * R-E-2: a rule whose `to` expands to several extensions (#jpg png#) yields
 * one media item per variant instead of only the first URL.
 *
 * @param {Array} media — scanned media items
 * @param {string} pageUrl — page the items came from (sieve `link` filter)
 * @returns {Promise<Array>} — new list (items are not mutated in place)
 */
async function upgradeMediaList(media, pageUrl) {
  let rules = null;
  try {
    const stored = await chrome.storage.local.get("sieveRules");
    if (stored.sieveRules && typeof parseSieve === "function") {
      rules = parseSieve(JSON.parse(stored.sieveRules));
    }
  } catch (e) {
    // A bad stored rule set must not kill the scan — the native [Resize]
    // fallback below still runs.
    console.warn("Sieve rules unavailable:", e);
  }

  const out = [];
  for (const item of media) {
    if (!item || !item.url) continue;
    if (item.source === "sieve-res" || item.source === "sieve-to") {
      out.push(item);
      continue;
    }

    // JS rules need page DOM and throw under MV3 CSP (executeSieveJS catches
    // that and returns null), so a JS rule never masks the fallback.
    const variants = rules ? applySieveRulesAll(item.url, pageUrl || "", rules) : [];
    if (variants.length > 0) {
      for (const url of variants) {
        out.push(Object.assign({}, item, {
          original_url: item.url, url, transformed: true, source: "sieve-to",
        }));
      }
      continue;
    }

    // ERR-7: native [Resize] port — strips CDN resize params
    // (&width=500 / ?w= / ?h= / ?resize= …) to reach the original file.
    const upgraded = applyResizeUpgrade(item.url);
    if (upgraded) {
      out.push(Object.assign({}, item, {
        original_url: item.url,
        url: normalizeVariant(upgraded, pageUrl || item.pageUrl || ""),
        transformed: true, source: "sieve-to",
      }));
      continue;
    }

    // D-05: WordPress/CDN size suffix on the URL itself (`photo-300x200.jpg`)
    // — the sieve's own rule for it is a JS rule that cannot run here, and the
    // desktop twin does the same strip (site_pattern_manager step 4).
    const suffixStripped = stripSizeSuffix(item.url);
    if (suffixStripped !== item.url) {
      out.push(Object.assign({}, item, {
        original_url: item.url,
        url: normalizeVariant(suffixStripped, pageUrl || item.pageUrl || ""),
        transformed: true, source: "sieve-to",
      }));
    } else {
      out.push(item);
    }
  }
  return out;
}

async function handleStartScan(tabId) {
  // A-14: a keyboard command (or a second popup) may already be scanning.
  // Starting a second pipeline doubled the discovery and let the loser
  // overwrite scanProgress/scanResult. Answer the caller instead.
  if (_scanBusy) {
    return { ok: false, busy: true, error: "A scan is already running" };
  }
  _scanBusy = true;
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
      progress.total = response.links.length;
      try { await chrome.storage.local.set({ scanProgress: progress }); } catch (e) {}
      const linked = await discoverFullsize(
        response.links, pageUrl,
        (scanned, total, found) => {
          progress.scanned = scanned;
          progress.total = total;
          progress.found = media.length + found;
          chrome.storage.local.set({ scanProgress: { ...progress } }).catch(() => {});
        }
      );
      if (linked && linked.media) media = media.concat(linked.media);
    }

    // R-E-6: string sieve rules + the native [Resize] port, shared with the
    // keyboard command path below.
    media = await upgradeMediaList(media, pageUrl);

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

    // ERR-9: check the full-size bucket before it is stored, so the popup's
    // count and the Saved X of Y line describe the same set. Only the bucket
    // is probed (never the Thumbnails bucket) and the whole pass is bounded by
    // VERIFY_BUDGET_MS — measured at 1.15 s for a 9-candidate bucket with the
    // image Accept, versus 8.3 s when the probe still sent "text/html".
    progress.phase = "verify";
    try { await chrome.storage.local.set({ scanProgress: { ...progress } }); } catch (e) {}
    let verdicts = { verified: 0, notMedia: 0, unknown: 0 };
    try {
      verdicts = await verifyFullsizeBucket(media, pageUrl);
    } catch (e) {
      // A verification failure must never lose the scan: items simply keep
      // their "unknown" verdict and stay in the list.
      console.warn("Media verification failed:", e);
    }

    const result = { media, pageUrl, title: response.title || pageUrl, tabId, ts: Date.now(), verdicts };
    try {
      await chrome.storage.local.set({
        scanResult: result,
        scanProgress: { status: "done", phase: "done", scanned: 1, total: 1, found: media.length, tabId },
      });
    } catch (e) {
      // D-02: the popup paints from storage, so a failed FINAL write would
      // leave "Scanning..." on screen forever (the intermediate writes above
      // all fail the same way, silently). Fall back to the second channel.
      broadcastScanFinished({ ok: false, error: `Could not store the scan result: ${e.message}` });
      return { ok: false, error: `Could not store the scan result: ${e.message}` };
    }
    broadcastScanFinished({ ok: true, count: media.length });
    return { ok: true, count: media.length };
  } catch (e) {
    console.error("handleStartScan failed:", e);
    try {
      await chrome.storage.local.set({
        scanProgress: { status: "error", phase: "error", error: e.message, tabId },
      });
    } catch (e2) {
      // D-02: the error state could not be persisted either — say it out loud.
      broadcastScanFinished({ ok: false, error: e.message });
    }
    return { ok: false, error: e.message };
  } finally {
    _scanBusy = false;
    keepaliveRelease();
  }
}

// --- Chrome Downloads ---

async function resolveUrl(url, referer) {
  try {
    // ERR-1: same hotlink-protection bypass as processLink — the referer
    // (originating page) is required to resolve full-size URLs on hosts
    // that 302 referer-less requests to an HTML page.
    // ERR-9: a browser requests an image with an image Accept — this probe used
    // to send "text/html", and media CDNs answered with a tarpit / HTML shell.
    // Measured on reactor.cc (live SW, same URL): HEAD with text/html timed out
    // at 4 s (or returned 200 text/html), while the same HEAD with an image
    // Accept answered 200 image/jpeg in 96-633 ms. The GET fallback below keeps
    // text/html on purpose: its job is to fetch an HTML shell and pull the
    // full-size URL out of it via the sieve res patterns.
    const headers = { "Accept": VERIFY_ACCEPT };
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
    // The GET's job is to fetch an HTML shell and pull the full-size URL out of
    // it, so IT asks for HTML — the image Accept belongs to the HEAD media check
    // above only (a shell that is asked for an image may answer 406/nothing).
    const getHeaders = Object.assign({}, headers, { "Accept": "text/html" });
    const resp = await fetch(url, {
      headers: getHeaders,
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

// --- Media verification (ERR-9) -------------------------------------------------
//
// The popup's "Full size" bucket is built from HOW a URL was discovered
// (FULLSIZE_SOURCES), never from whether the URL actually serves a media file.
// Measured on reactor.cc/post/6382634: the bucket held 9 rows — 8 real
// full-size JPEGs plus i.li.ru/ext/.../disney_33.jpg, an image URL that answers
// 404 text/html — so the popup promised 9 files and Chrome saved 8. This probes
// the bucket once, before the result is stored, and records a verdict per item.
//
// THREE-VALUED on purpose: "not-media" is only claimed when a response really
// says so (text/* content-type on a 2xx, or 404/410). 403/405/429/5xx, a
// timeout and a network error stay "unknown" and are KEPT — those are exactly
// the answers hotlink-protected hosts (hath.network and friends) give for real
// images, and a two-valued gate would delete real media from the list.

/**
 * One probe: does this URL serve a media file?
 *
 * @param {string} url
 * @param {string} [referer] — the page the URL came from (hotlink protection)
 * @returns {Promise<{verdict: "media"|"not-media"|"unknown", finalUrl: string}>}
 */
async function verifyMediaUrl(url, referer) {
  const headers = { "Accept": VERIFY_ACCEPT };
  if (referer) headers["Referer"] = referer;
  try {
    const resp = await fetch(url, {
      method: "HEAD",
      headers,
      signal: AbortSignal.timeout(VERIFY_TIMEOUT_MS),
    });
    const finalUrl = resp.url || url;
    const ct = (resp.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
    if (resp.ok) {
      if (/^(image|video|audio)\//.test(ct)) return { verdict: "media", finalUrl };
      // A page, not a file. `text/*` covers the HTML shell the measured 404
      // case and the tarpit pages both return.
      if (ct.startsWith("text/")) return { verdict: "not-media", finalUrl };
      // No content-type, or an exotic one (octet-stream, application/json):
      // not enough to promise, not enough to drop.
      return { verdict: "unknown", finalUrl };
    }
    // 404/410: the URL is dead this run — the measured disney_33.jpg case.
    if (resp.status === 404 || resp.status === 410) return { verdict: "not-media", finalUrl };
    // Everything else (403/405/429/5xx) is inconclusive, never "not media".
    return { verdict: "unknown", finalUrl };
  } catch (e) {
    return { verdict: "unknown", finalUrl: url };
  }
}

/**
 * Verify the full-size bucket of a scan result in place.
 *
 * Only bucket items (FULLSIZE_SOURCES) are probed — the Thumbnails bucket is
 * never network-checked, so a page full of grid images costs nothing extra.
 * Bounded twice: VERIFY_CONCURRENCY parallel probes and a VERIFY_BUDGET_MS wall
 * so a slow host can never drag the scan. Items left when the budget runs out
 * keep the "unknown" verdict (kept, not dropped).
 *
 * @param {Array<object>} items — scan media items, annotated in place
 * @param {string} pageUrl
 * @returns {Promise<{verified: number, notMedia: number, unknown: number}>}
 */
async function verifyFullsizeBucket(items, pageUrl) {
  const bucket = (items || []).filter((i) => i && FULLSIZE_SOURCES.has(i.source));
  const summary = { verified: 0, notMedia: 0, unknown: 0 };
  if (bucket.length === 0) return summary;
  const deadline = Date.now() + VERIFY_BUDGET_MS;
  for (let i = 0; i < bucket.length; i += VERIFY_CONCURRENCY) {
    if (Date.now() >= deadline) break;
    const slice = bucket.slice(i, i + VERIFY_CONCURRENCY);
    const results = await Promise.all(
      slice.map((item) => verifyMediaUrl(item.url, pageUrl || item.pageUrl || ""))
    );
    results.forEach((res, k) => {
      const item = slice[k];
      item.mediaVerdict = res.verdict;
      if (res.verdict === "media") {
        // ERR-5 flag: chromeDownloadBatch skips resolveUrl for these, so a
        // verified item costs no extra round-trip when the user saves it.
        item.verified = true;
      }
    });
  }
  for (const item of bucket) {
    if (item.mediaVerdict === "media") summary.verified++;
    else if (item.mediaVerdict === "not-media") summary.notMedia++;
    else {
      // Budget ran out, or the probe was inconclusive: say so, keep the item.
      item.mediaVerdict = "unknown";
      summary.unknown++;
    }
  }
  return summary;
}

async function chromeDownload(items, concurrentLimit = 2) {
  // A-14: a second batch (double-clicked Save, or a command during a batch)
  // used to interleave its own progress writes with the running one.
  if (_batches.running) return { saved: 0, busy: true };
  _batches.running = true;
  try {
    return await chromeDownloadBatch(items, concurrentLimit);
  } finally {
    _batches.running = false;
  }
}

async function chromeDownloadBatch(items, concurrentLimit) {
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

// A-14: ONE media pipeline at a time. The keyboard command, the popup's scan
// request and "Scan again" all enter the same work; without this gate a
// command during an active scan started a SECOND full scan — two discovery
// passes over the same links, doubled network load, and whichever finished last
// overwrote scanProgress/scanResult (the popup then showed the loser's media).
let _scanBusy = false;

// A-14 (download half): a second batch must not start while one is running.
// A holder object rather than a bare `let` so the extracted-function test can
// inject and observe the same state (extension/tests/chrome_download_headers.test.mjs).
const _batches = { running: false };

/**
 * D-09: every message branch must ANSWER.
 *
 * A rejected promise used to leave the caller's `sendMessage` waiting forever
 * (the popup sat on "Scanning..." / "Saving..." with no way out).
 */
function replyWith(promise, sendResponse) {
  Promise.resolve(promise).then(sendResponse).catch((e) => {
    try {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    } catch (e2) {}
  });
}

/**
 * D-02: the scan's end must not depend on a SINGLE storage write.
 *
 * The popup renders from storage.onChanged, so a failed final write left
 * "Scanning..." on screen forever. This second channel reports the end even
 * when that write could not land.
 */
function broadcastScanFinished(payload) {
  try {
    const sent = chrome.runtime.sendMessage({ action: "scanFinished", ...payload });
    if (sent && typeof sent.catch === "function") sent.catch(() => {});
  } catch (e) {}
}

async function setBadge(text, color) {
  try {
    await chrome.action.setBadgeText({ text });
    await chrome.action.setBadgeBackgroundColor({ color });
  } catch (e) {}
}

async function commandScanAndProcess(action) {
  if (_scanBusy) return;   // A-14
  _scanBusy = true;
  // D-01: the command path does its own long work (discovery + a whole
  // download batch) and used to be the one path WITHOUT a keepalive — a
  // command started on an idle service worker could be terminated mid-flight.
  keepaliveAcquire();
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
    const linked = await discoverFullsize(response.links, response.url);
    if (linked && linked.media) {
      media = media.concat(linked.media);
    }
  }

  // R-E-6: the keyboard path now runs the SAME pipeline as the popup scan
  // (string sieve rules + the native [Resize] port) instead of only the latter.
  media = await upgradeMediaList(media, response.url);

  const fullsize = media.filter(m => FULLSIZE_SOURCES.has(m.source));
  const items = fullsize.length > 0 ? fullsize : media;
  // ERR-9: same verification as the popup path, so the badge count this command
  // announces matches what chrome.downloads will actually save.
  try {
    await verifyFullsizeBucket(items, response.url);
  } catch (e) {
    console.warn("Media verification failed:", e);
  }

  if (action === "save-chrome") {
    await setBadge(`${items.length}`, "#4CAF50");
    const toDownload = items.map(item => ({
      url: item.url,
      // D-07: Chrome rejects the whole download for a name with a path
      // separator or a Windows device name ("..%2F..%2Fx.png", "CON.png").
      filename: sanitizeFilename(item.url.split("/").pop().split("?")[0]),
      referer: response.url || "",
      // ERR-5: link-direct items were already fetched (image/video) during
      // discovery — skip the resolve round-trip so downloads start instantly.
      verified: item.source === "link-direct",
    }));
    await chromeDownload(toDownload);
    setTimeout(() => setBadge(""), 5000);
  }
  } finally {
    keepaliveRelease();
    _scanBusy = false;
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
  // D-09: every branch goes through replyWith — a rejected promise must still
  // answer the caller (the popup used to hang on "Scanning..." forever).
  if (request.action === "download") {
    replyWith(sendToDesktop(request.urls, request.one_shot, request.context || {}), sendResponse);
    return true;
  }
  if (request.action === "discoverFullsize") {
    replyWith(discoverFullsize(request.links, request.pageUrl), sendResponse);
    return true;
  }
  if (request.action === "startScan") {
    replyWith(handleStartScan(request.tabId), sendResponse);
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
    replyWith(chromeDownload(request.items, limit), sendResponse);
    return true;
  }
  if (request.action === "getContext") {
    replyWith(getPageContext(request.tabId), sendResponse);
    return true;
  }
  if (request.action === "clearBadgeAfter") {
    // Clear badge from background (persists after popup closes)
    setTimeout(() => setBadge(""), request.delay || 5000);
    sendResponse({ ok: true });
    return false;
  }
  if (request.action === "getStatus") {
    replyWith(getStatus(), sendResponse);
    return true;
  }
  if (request.action === "updateSieve") {
    // The popup's button is an explicit user action — bypass the R-E-12 throttle.
    replyWith(runSieveUpdate(true).then((r) => r || { status: "error", message: "updater unavailable" }),
      sendResponse);
    return true;
  }
});
