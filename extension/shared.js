/**
 * Shared constants for the extension (EXT-9).
 * Single source of truth for values that were duplicated between popup.js and
 * background.js with drifting semantics.
 */

// Sources whose URLs are already full-size (not thumbnails). "sieve-to" are
// popup sieve-transforms of thumbnails — also full-size results (EXT-1).
// "a-link" = an <a href> pointing directly at an image file (the classic
// thumbnail-wrapped-in-full-size-link pattern, e.g. reactor.cc posts) — the
// URL is the anchor's target, not the <img> src, so it is a full-size
// candidate. ERR-1: without it the popup's "fullsize" filter hid these
// real full-size URLs while discovery probed the network for them.
// "linked-img" = an <img src> found while probing a linked page (fullsize
// discovery fallback, background.js) — the URL points straight at an image
// file, so it belongs in the same bucket. R-E-4: without it the popup's
// default "fullsize" source filter hid everything discovery found.
// NOT here on purpose: "page-img" — an image from a probed page that turned out
// to be a GRID of images (the page's own thumbnails). Errors.txt (e-hentai):
// promoting those made "Full size" list 285 thumbnails next to the real
// originals; they stay in the "Thumbnails" bucket (see classifyPageImages).
const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct", "sieve-to", "a-link", "linked-img"]);
// (D-07's sanitizeFilename lives below, next to the other shared helpers.)

/**
 * D-07: a filename Chrome's downloader will accept.
 *
 * Both the popup and the background passed the URL's last path segment through
 * untouched, and Chrome rejects the DOWNLOAD when the name carries a path
 * separator, a Windows device name, a trailing dot/space or a control
 * character (measured: `..%2F..%2Fx.png` and `CON.png` both failed to start).
 * A percent-encoded separator is the interesting case — the name is decoded
 * before it is scrubbed, so `%2F` cannot smuggle one through.
 *
 * @param {string} name — the candidate name (URL path segment, usually)
 * @param {string} [fallback] — used when nothing usable is left
 * @returns {string}
 */
function sanitizeFilename(name, fallback) {
  let base = String(name == null ? "" : name);
  try {
    base = decodeURIComponent(base);
  } catch (e) {
    // a malformed escape sequence: scrub the raw form instead
  }
  base = base.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").replace(/\s+/g, " ").trim();
  base = base.replace(/^\.+/, "").replace(/[. ]+$/, "");
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)/i.test(base)) base = `_${base}`;
  if (base.length > 150) {
    const dot = base.lastIndexOf(".");
    const ext = dot > 0 && base.length - dot <= 6 ? base.slice(dot) : "";
    base = base.slice(0, 150 - ext.length) + ext;
  }
  return base || fallback || "media";
}

// Time budget (ms) for the POSTPONED tier of fullsize discovery: links whose origin
// already failed during this scan (timeout, 429, 5xx) and that were therefore moved
// behind the responsive ones. Discovery itself is NOT content-capped — every candidate
// link is probed while the page budget lasts, which mirrors the desktop twin
// (src/constants.py: "media lookups are never content-capped … the guards are
// RESOURCE-based only"). Measured on 60 hanging + 120 live links: without the queue
// 0/120 results were collected inside the 45s page budget; with it all 120 arrived in
// 8.7s, and this sub-budget ended the scan at 16.8s instead of holding it to 45s.
const DISCOVER_QUARANTINE_MS = 15000;

// Discovery is polite on purpose: at most this many probes may be in flight to ONE origin
// at a time (DISCOVER_CONCURRENCY stays the global cap). Uncapped discovery otherwise
// means 5 parallel requests to the same host, which is exactly what earns a 429 — and a
// 429 costs every remaining result from that host.
const DISCOVER_PER_ORIGIN = 2;

// After an origin times out or throttles us, it is not retried before this delay, doubling
// with consecutive failures up to the maximum. The scan waits out the shortest backoff
// instead of hammering (see discoverFullsize) — a host that asked for a pause gets one.
const DISCOVER_BACKOFF_MS = 2000;
const DISCOVER_BACKOFF_MAX_MS = 30000;

// Probe results are reused across scans within one browser session (chrome.storage.session
// survives service-worker restarts, not a browser restart). A second scan of the same page
// — Scan again, or the keyboard command right after the popup scan — then costs no network.
// Only HTTP 200 answers are cached; a timeout or a 429 must never be remembered as "this
// page has no media". Entries expire after the TTL and the cache keeps the newest N.
const DISCOVER_CACHE_TTL_MS = 900000;
const DISCOVER_CACHE_MAX = 300;
const DISCOVER_CACHE_KEY = "discoverCache";
