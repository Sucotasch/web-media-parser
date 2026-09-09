// C-1 (extension): sieve online update — port of Mod's updateSieve
// (src-mv3-overlay/background/service.js:74-200) adapted to this extension's
// storage layout (chrome.storage.local keys: sieveRules, sieveVersion).
// MV3's service-worker sandbox makes automatic updates safe here, unlike the
// desktop app where sieve JS runs with full Deno/exec privileges.

export const SIEVE_UPDATE_ALARM = "sieve-update-weekly";
const SIEVE_REPO_URL = "https://raw.githubusercontent.com/kuzn123/Imagus-Sieve-RuBoard/master/update.txt";
const MAX_RETRIES = 3;

/**
 * Convert a raw.githubusercontent.com URL into its jsDelivr CDN equivalent
 * (no GitHub rate limit). Returns null when not a raw-GitHub URL.
 * (Port of Mod's jsDelivrMirror, service.js:74-83.)
 */
export function jsDelivrMirror(repoUrl) {
  const m = /^https:\/\/raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\/(.+)$/i.exec(repoUrl || "");
  if (!m) return null;
  return `https://cdn.jsdelivr.net/gh/${m[1]}/${m[2]}@${m[3]}/${m[4]}`;
}

/**
 * Validate a downloaded sieve object. Returns the count of usable rules
 * (a rule is usable when it has link or img) — port of Mod's validRuleCount.
 */
export function validRuleCount(sieve) {
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
export function mergeSieve(oldSieve, newSieve) {
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
  const text = await resp.text();
  const data = JSON.parse(text);
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new Error("Invalid sieve format: must be an object");
  }
  return { data };
}

/**
 * Full update flow: primary URL → jsDelivr mirror → retries with backoff.
 * Returns { status: "updated"|"up-to-date"|"error", message }.
 */
export async function updateSieve(repositoryUrl, storage = chrome.storage.local) {
  const repoUrl = repositoryUrl || SIEVE_REPO_URL;
  const mirrorUrl = jsDelivrMirror(repoUrl);
  const stored = await storage.get(["sieveRules", "sieveUpdateLast"]);
  const lastModified = stored.sieveUpdateLast
    ? new Date(Number(stored.sieveUpdateLast)).toUTCString()
    : null;

  let lastError = null;
  const urls = [repoUrl, mirrorUrl].filter(Boolean);
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
        const merged = mergeSieve(stored.sieveRules || {}, result.data);
        await storage.set({
          sieveRules: merged,
          sieveUpdateLast: Date.now(),
        });
        return { status: "updated", message: "Sieve updated successfully" };
      } catch (e) {
        lastError = e;
        if (attempt < MAX_RETRIES - 1) {
          await new Promise(r => setTimeout(r, Math.pow(2, attempt) * 1000));
        }
      }
    }
  }
  return { status: "error", message: lastError ? lastError.message : "unknown error" };
}
