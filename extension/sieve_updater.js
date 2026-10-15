// C-1 (extension): sieve online update — port of Mod's updateSieve
// (src-mv3-overlay/background/service.js:74-200) adapted to this extension's
// storage layout (chrome.storage.local keys: sieveRules, sieveVersion).
// MV3's service-worker sandbox makes automatic updates safe here, unlike the
// desktop app where sieve JS runs with full Deno/exec privileges.
//
// R-E-8/R-E-1: this file is loaded with importScripts() from background.js.
// It used to be an ES module imported with dynamic import(), which a classic
// service worker REFUSES ("import() is disallowed on ServiceWorkerGlobalScope")
// — so the updater was unreachable and every online update silently no-oped.
// It is a classic script now (same shape as shared.js / sieve.js) and exposes
// its API on the worker/page global as `self.WMPSieveUpdater`.
//
// Storage contract (all readers JSON.parse `sieveRules`):
//   sieveRules        — STRING (JSON) of the merged rule set
//   sieveVersion      — bundle version the merge was applied on top of
//                       (loadSieveRules() skips a re-install for the same
//                       version, so an online merge survives worker restarts)
//   sieveUpdateLast   — last successful sync (used as If-Modified-Since)
//   sieveUpdateAttempt— last attempt of any outcome (R-E-12 cold-start throttle)

const SIEVE_UPDATE_ALARM = "sieve-update-weekly";
const SIEVE_REPO_URL = "https://raw.githubusercontent.com/kuzn123/Imagus-Sieve-RuBoard/master/update.txt";
const MAX_RETRIES = 3;
// R-E-12: once the updater actually ran (R-E-8), the SW cold-start probe was
// no longer the cheap early-exit it was designed as — it became a real
// conditional GET on every worker start. One attempt per window is enough.
const COLD_START_MIN_INTERVAL_MS = 6 * 60 * 60 * 1000;
// D-06: a sieve is ~1-2 MB. The response used to be read into a string and
// JSON.parse'd with no bound at all, so a hostile or broken endpoint could make
// the service worker allocate an arbitrary graph (measured: a 20 MiB body was
// parsed without complaint). 8 MiB is ~4x the real bundle.
const MAX_SIEVE_BYTES = 8 * 1024 * 1024;

/**
 * Convert a raw.githubusercontent.com URL into its jsDelivr CDN equivalent
 * (no GitHub rate limit). Returns null when not a raw-GitHub URL.
 * (Port of Mod's jsDelivrMirror, service.js:74-83.)
 */
function jsDelivrMirror(repoUrl) {
  const m = /^https:\/\/raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\/(.+)$/i.exec(repoUrl || "");
  if (!m) return null;
  return `https://cdn.jsdelivr.net/gh/${m[1]}/${m[2]}@${m[3]}/${m[4]}`;
}

/**
 * Read a stored `sieveRules` value as an object. The key must hold a JSON
 * STRING, but an object written by the pre-R-E-1 updater (or by a hand-edited
 * storage) is tolerated instead of being merged as garbage: iterating a string
 * as if it were an object produced one bogus rule per character.
 */
function parseStoredSieve(raw) {
  if (!raw) return {};
  if (typeof raw === "object") return Array.isArray(raw) ? {} : raw;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (e) {
    return {};
  }
}

/**
 * Validate a downloaded sieve object. Returns the count of usable rules
 * (a rule is usable when it has link or img) — port of Mod's validRuleCount.
 */
function validRuleCount(sieve) {
  let count = 0;
  for (const key in sieve) {
    if (sieve[key] && (sieve[key].link || sieve[key].img)) count++;
  }
  return count;
}

/**
 * Merge user customizations into a fresh sieve — port of Mod's merge
 * (service.js:149-166):
 *  - `_`-prefixed (user-defined) rules survive an update;
 *  - `off` flags carry over for rules present in both;
 *  - rules that disappeared from upstream keep running with off:1
 *    (user modifications are never silently dropped).
 */
function mergeSieve(oldSieve, newSieve) {
  const merged = {};
  for (const key in oldSieve) {
    if (key.startsWith("_")) merged[key] = oldSieve[key];
  }
  for (const key in newSieve) {
    merged[key] = newSieve[key];
  }
  for (const key in oldSieve) {
    if (merged[key]) {
      if (oldSieve[key] && oldSieve[key].off) merged[key].off = oldSieve[key].off;
    } else {
      const ghost = Object.assign({}, oldSieve[key]);
      ghost.off = 1;
      merged[key] = ghost;
    }
  }
  return merged;
}

/**
 * R-E-5: create the weekly alarm only when it does not exist yet. Re-creating
 * it on every cold start resets its period, so a worker that starts more often
 * than once a week never let the alarm fire at all.
 * @returns {Promise<boolean>} true when the alarm was created by this call
 */
async function ensureWeeklyAlarm(alarmsApi) {
  if (!alarmsApi || typeof alarmsApi.get !== "function" || typeof alarmsApi.create !== "function") {
    return false;
  }
  let existing = null;
  try {
    existing = await alarmsApi.get(SIEVE_UPDATE_ALARM);
  } catch (e) {
    return false;
  }
  if (existing) return false;
  await alarmsApi.create(SIEVE_UPDATE_ALARM, { periodInMinutes: 7 * 24 * 60 });
  return true;
}

/** R-E-12: may a throttled (non-forced) update attempt run now? */
function shouldProbeLastUpdate(lastAttemptTs, now = Date.now()) {
  if (!lastAttemptTs) return true;
  const last = Number(lastAttemptTs);
  if (!Number.isFinite(last)) return true;
  return now - last >= COLD_START_MIN_INTERVAL_MS;
}

/**
 * Fetch a sieve URL with timeout; returns a parsed JSON object or throws.
 */
async function fetchSieve(url, lastModified) {
  const headers = {};
  if (lastModified) headers["If-Modified-Since"] = lastModified;
  const resp = await fetch(url, {
    headers,
    signal: AbortSignal.timeout(10000),
  });
  if (resp.status === 304) return { notModified: true };
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  // D-06: refuse before reading the body, when the server declares its size…
  const declared = Number(resp.headers && resp.headers.get && resp.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_SIEVE_BYTES) {
    throw new Error(`Sieve response too large (${declared} bytes)`);
  }
  const text = await resp.text();
  // …and again for a body without (or with a lying) Content-Length.
  if (text.length > MAX_SIEVE_BYTES) {
    throw new Error(`Sieve response too large (${text.length} bytes)`);
  }
  const data = JSON.parse(text);
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new Error("Invalid sieve format: must be an object");
  }
  return { data };
}

/**
 * Full update flow: primary URL → jsDelivr mirror → retries with backoff.
 * Returns { status: "updated"|"up-to-date"|"error", message }.
 *
 * @param {string} [repositoryUrl] sieve source URL (defaults to the RuBoard repo)
 * @param {Object} [options]
 * @param {string} [options.version] current bundle version (SIEVE_VERSION) —
 *   written as `sieveVersion` so loadSieveRules() keeps the merged set instead
 *   of overwriting it on the next worker start.
 * @param {Object} [options.storage] storage area (injectable for tests)
 */
async function updateSieve(repositoryUrl, options = {}) {
  const storage = options.storage || chrome.storage.local;
  const version = options.version || null;
  const repoUrl = repositoryUrl || SIEVE_REPO_URL;
  // D-01: this flow can sit through MAX_RETRIES * two URLs with exponential
  // backoff, far past MV3's ~30 s idle window — the caller passes its own
  // keepalive in (background.js) so the worker is not terminated mid-update.
  const keepalive = options.keepalive || null;
  if (keepalive && typeof keepalive.acquire === "function") keepalive.acquire();
  const mirrorUrl = jsDelivrMirror(repoUrl);
  const stored = await storage.get(["sieveRules", "sieveVersion", "sieveUpdateLast"]);
  const lastModified = stored.sieveUpdateLast
    ? new Date(Number(stored.sieveUpdateLast)).toUTCString()
    : null;

  // R-E-12: record the attempt itself (success, 304 or failure) so the
  // cold-start probe can be throttled — otherwise every worker start retries.
  await storage.set({ sieveUpdateAttempt: Date.now() });

  let lastError = null;
  const urls = [repoUrl, mirrorUrl].filter(Boolean);
  try {
    for (const url of urls) {
      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        try {
          const result = await fetchSieve(url, lastModified);
          if (result.notModified) {
            return { status: "up-to-date", message: "Sieve is up to date (HTTP 304)" };
          }
          if (validRuleCount(result.data) === 0) {
            throw new Error("Sieve contains no valid rules");
          }
          const merged = mergeSieve(parseStoredSieve(stored.sieveRules), result.data);
          const patch = {
            // R-E-1: readers JSON.parse this key — it must stay a string.
            sieveRules: JSON.stringify(merged),
            sieveUpdateLast: Date.now(),
          };
          if (version) patch.sieveVersion = version;
          await storage.set(patch);
          return { status: "updated", message: "Sieve updated successfully" };
        } catch (e) {
          lastError = e;
          if (attempt < MAX_RETRIES - 1) {
            await new Promise(r => setTimeout(r, Math.pow(2, attempt) * 1000));
          }
        }
      }
    }
  } finally {
    if (keepalive && typeof keepalive.release === "function") keepalive.release();
  }
  return { status: "error", message: lastError ? lastError.message : "unknown error" };
}

// R-E-8: expose the API on the worker/page global — importScripts() cannot
// load a module, so this is the single entry point background.js uses.
self.WMPSieveUpdater = {
  SIEVE_UPDATE_ALARM,
  SIEVE_REPO_URL,
  COLD_START_MIN_INTERVAL_MS,
  MAX_SIEVE_BYTES,
  jsDelivrMirror,
  validRuleCount,
  mergeSieve,
  parseStoredSieve,
  ensureWeeklyAlarm,
  shouldProbeLastUpdate,
  updateSieve,
};
