// Discovery: the host-tier queue, its failure classification, the cross-scan cache and the
// per-origin politeness rules in background.js.
//
// The real sources are extracted from background.js (loadDiscoverCache, saveDiscoverCache,
// discoverFullsize) and run against a stubbed fetch and a stubbed chrome.storage.session,
// the same way content_scan.test.mjs drives the real scanner. Time is compressed by one
// factor: page deadline 45s -> 280ms, probe timeout 8s -> 50ms, postponed tier 15s -> per
// scenario, backoff 2s -> 60ms, cache TTL 15min -> 200ms. The ratios are what the policy
// needs, and every timing assertion below is an inequality, never an exact duration.
//
// Run: node extension/tests/discover_queue.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
    console.error(`FAIL: ${name}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok: ${name}`);
  }
}

// --- the real sources, out of the real file --------------------------------
// background.js ships with CRLF; normalise before slicing source out of it.
const bgSrc = readFileSync(join(here, "..", "background.js"), "utf8")
  .split(String.fromCharCode(13, 10)).join(String.fromCharCode(10));
check("background.js no longer carries a content cap", bgSrc.includes("LINKS_CAP"), false);

const START = "async function loadDiscoverCache(";
const TAIL = "  return { media: discovered };\n}";
const start = bgSrc.indexOf(START);
const end = bgSrc.indexOf(TAIL, start);
if (start < 0 || end < 0) {
  console.error("FAIL: cannot extract the discovery sources from background.js");
  process.exit(1);
}
// Budgets are compressed, and the per-scenario values below only need to keep the two
// policy facts true: a hanging round costs far more than a responsive one, and all the
// responsive links fit inside the page deadline. The 60+120 scale is covered by the
// browser gate (.ext-dev-loop/cap180_steps.json), not here.
const PAGE_DEADLINE_MS = 300;
const HANG_MS = 50;
const BACKOFF_MS = 60;
const CACHE_TTL_MS = 200;
const CACHE_MAX = 5;
const PROBE_MS = 2; // a non-hanging probe still takes a moment, so overlap is observable
const PER_ORIGIN = 2;
const GLOBAL_CONCURRENCY = 5;
// The behaviour below runs with COMPRESSED time budgets, but the non-time constants are
// read from the shipped shared.js and asserted, so a drifted value (a raised per-origin
// limit, a zeroed backoff) fails here instead of passing unnoticed.
const sharedSrc = readFileSync(join(here, "..", "shared.js"), "utf8");
const shipped = (name) => {
  const line = sharedSrc.split("\n").find((l) => l.trim().startsWith(`const ${name} =`));
  return line ? Number(line.split("=")[1].replace(";", "").trim()) : NaN;
};
check("shipped per-origin limit is 2", shipped("DISCOVER_PER_ORIGIN"), PER_ORIGIN);
check("shipped backoff is a positive delay", shipped("DISCOVER_BACKOFF_MS") > 0, true);
check("shipped backoff cap is not below the base", shipped("DISCOVER_BACKOFF_MAX_MS") >= shipped("DISCOVER_BACKOFF_MS"), true);
check("shipped cache keeps a sane number of entries", shipped("DISCOVER_CACHE_MAX") >= 50, true);
check("shipped cache TTL is positive", shipped("DISCOVER_CACHE_TTL_MS") > 0, true);
check(
  "shipped postponed-tier budget is smaller than the page budget",
  shipped("DISCOVER_QUARANTINE_MS") > 0 && shipped("DISCOVER_QUARANTINE_MS") < 45000,
  true
);

// expose failedOrigins so the classification policy itself is asserted, then compress time
const srcTemplate = (bgSrc.slice(start, end) + TAIL)
  .replace(TAIL, `  globalThis.__failedOrigins = failedOrigins;\n${TAIL}`);

function header(quarantineMs) {
  return `
const DISCOVER_CONCURRENCY = ${GLOBAL_CONCURRENCY};
const DISCOVER_QUARANTINE_MS = ${quarantineMs};
const DISCOVER_PER_ORIGIN = ${PER_ORIGIN};
const DISCOVER_BACKOFF_MS = ${BACKOFF_MS};
const DISCOVER_BACKOFF_MAX_MS = 200;
const DISCOVER_CACHE_TTL_MS = ${CACHE_TTL_MS};
const DISCOVER_CACHE_MAX = ${CACHE_MAX};
const DISCOVER_CACHE_KEY = "discoverCache";
const cachedSieveRes = {};
const classifyPageImages = (n) => (n === 1 ? "linked-img" : "page-img");
const applyUrlTransform = () => null;
`;
}

function makeChrome() {
  const store = {};
  const api = {
    store,
    broken: false,
    storage: {
      session: {
        get: async (key) => {
          if (api.broken) throw new Error("storage unavailable");
          return key in store ? { [key]: store[key] } : {};
        },
        set: async (obj) => {
          if (api.broken) throw new Error("quota exceeded");
          Object.assign(store, obj);
        },
      },
    },
  };
  return api;
}

async function run({ list, route, quarantineMs = 150, deadlineMs = PAGE_DEADLINE_MS, chrome = makeChrome() }) {
  const probes = [];
  let inFlight = 0;
  let maxInFlight = 0;
  const perOriginMax = new Map();

  globalThis.chrome = chrome;
  globalThis.AbortSignal = { timeout: () => undefined };
  globalThis.fetch = async (url) => {
    const origin = url.split("/").slice(0, 3).join("/");
    const started = Date.now();
    probes.push({ url, started, ended: 0 });
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    perOriginMax.set(origin, Math.max(perOriginMax.get(origin) || 0, inFlight));
    const finish = () => {
      inFlight -= 1;
      probes[probes.length - 1].ended = Date.now();
    };
    const kind = route(url);
    // models AbortSignal.timeout(8000) for a hanging host; every probe takes a moment so
    // the in-flight counters below actually see the overlap
    await new Promise((r) => setTimeout(r, kind === "hang" ? HANG_MS : PROBE_MS));
    finish();
    if (kind === "hang") throw new Error("aborted");
    if (typeof kind === "number") {
      return { ok: false, status: kind, headers: { get: () => "text/html" }, text: async () => "" };
    }
    if (kind === "empty") {
      return { ok: true, status: 200, headers: { get: () => "text/html" }, text: async () => "<html><body>nothing here</body></html>" };
    }
    const name = url.split("/").pop().split(".")[0];
    return {
      ok: true,
      status: 200,
      headers: { get: () => "text/html" },
      text: async () => `<html><img src="http://live.example/media/${name}.jpg"></html>`,
    };
  };

  const src = srcTemplate.split("+ 45000").join(`+ ${deadlineMs}`);
  const discoverFullsize = new Function(header(quarantineMs) + src + "\nreturn discoverFullsize;")();
  const out = await discoverFullsize(list, "http://page.example/", null);
  return {
    urls: out.media.map((m) => m.url),
    probes,
    failed: [...(globalThis.__failedOrigins || [])],
    maxInFlight,
    perOriginMax,
    chrome,
  };
}

// --- 1/2/3: hanging links ahead of responsive ones (the measured worst case) ---
const N_HANG = 20;
const N_LIVE = 40;
{
  const HANG = "http://127.0.0.1:9828";
  const LIVEHOST = "http://127.0.0.1:9829";
  const list = [
    ...Array.from({ length: N_HANG }, (_, i) => `${HANG}/view/${i + 1}.html`),
    ...Array.from({ length: N_LIVE }, (_, i) => `${LIVEHOST}/view/${i + 1}.html`),
  ];
  // A longer page budget so the 20 responsive rounds fit with room to spare, and a short
  // postponed budget so a diagnosed origin is never retried while responsive links remain.
  const res = await run({
    list,
    route: (u) => (u.includes(":9828") ? "hang" : "html"),
    deadlineMs: 600,
    quarantineMs: 30,
  });

  check("every responsive original is collected (no content cap)", res.urls.length, N_LIVE);
  check("the hanging origin is diagnosed as failed", res.failed, [HANG]);
  check("the port is part of the key, so the live port is never demoted", res.failed.includes(LIVEHOST), false);
  check("the live links start right after the first hanging round", res.probes.findIndex((p) => p.url.includes(":9829")), PER_ORIGIN);
  // Exactly the one unavoidable round: once an origin is diagnosed, its remaining links wait
  // in the postponed tier instead of competing with the responsive ones. Without the
  // healthy-first rule (only the backoff left) they keep coming back into the main tier.
  const hangProbes = res.probes.filter((p) => p.url.includes(":9828")).length;
  check("a diagnosed origin is not retried while responsive links remain", hangProbes, PER_ORIGIN);
}

// --- 4: 404 is not a host failure (a wrong policy loses a real image) ------
{
  const A = "http://127.0.0.1:9831"; // 404s and one good detail page
  const C = "http://127.0.0.1:9828"; // hangs, and burns the postponed-tier budget first
  const list = [
    ...Array.from({ length: 5 }, (_, i) => `${C}/view/${i + 1}.html`),
    ...Array.from({ length: 5 }, (_, i) => `${A}/miss/${i + 1}.html`),
    `${A}/view/6.html`,
  ];
  // 30ms of postponed budget: once the hanging round is over, a demoted origin is cut
  const res = await run({
    list,
    route: (u) => (u.startsWith(C) ? "hang" : u.includes("/miss/") ? 404 : "html"),
    quarantineMs: 30,
  });
  check("a 404 does not demote its origin", res.failed, [C]);
  check("the 404 host still serves its good page", res.urls, ["http://live.example/media/6.jpg"]);
}

// --- 5: 429 and 5xx ARE host failures, and postponement is not a drop ------
for (const status of [429, 500]) {
  const B = "http://127.0.0.1:9833";
  const list = [`${B}/throttled/1.html`, `${B}/view/2.html`];
  const res = await run({ list, route: (u) => (u.includes("/throttled/") ? status : "html") });
  check(`a ${status} demotes its origin`, res.failed, [B]);
  check(`the ${status} origin's link is postponed, not dropped`, res.urls, ["http://live.example/media/2.jpg"]);
}

// --- 6: a page without media is not a host failure -------------------------
{
  const E = "http://127.0.0.1:9835";
  const list = [`${E}/empty/1.html`, `${E}/view/1.html`];
  const res = await run({ list, route: (u) => (u.includes("/empty/") ? "empty" : "html") });
  check("a media-less page is not a host failure", res.failed, []);
  check("its origin's next link is probed normally", res.urls, ["http://live.example/media/1.jpg"]);
}

// --- 7: per-origin politeness ---------------------------------------------
{
  const ONE = "http://127.0.0.1:9840";
  const list = Array.from({ length: 20 }, (_, i) => `${ONE}/view/${i + 1}.html`);
  const res = await run({ list, route: () => "html" });
  check("no more than DISCOVER_PER_ORIGIN probes to one origin at a time", res.perOriginMax.get(ONE) <= PER_ORIGIN, true);
  check("the per-origin limit really is used (not accidentally 1)", res.perOriginMax.get(ONE), PER_ORIGIN);
  check("all 20 links still resolve", res.urls.length, 20);
}

{
  const origins = ["http://127.0.0.1:9841", "http://127.0.0.1:9842", "http://127.0.0.1:9843"];
  const list = origins.flatMap((o) => Array.from({ length: 4 }, (_, i) => `${o}/view/${i + 1}.html`));
  const res = await run({ list, route: () => "html" });
  check("across origins the global concurrency cap still applies", res.maxInFlight, GLOBAL_CONCURRENCY);
}

// --- 8: a throttled origin is not retried before its backoff ---------------
{
  const B = "http://127.0.0.1:9833";
  const list = [`${B}/throttled/1.html`, `${B}/view/2.html`, `${B}/view/3.html`];
  const res = await run({ list, route: (u) => (u.includes("/throttled/") ? 429 : "html") });
  const throttled = res.probes.find((p) => p.url.includes("/throttled/"));
  const after = res.probes.filter((p) => p.url.includes("/view/") && p.started >= throttled.ended);
  check("the origin is retried after the 429, not dropped", after.length > 0, true);
  check(
    `the retry waits at least DISCOVER_BACKOFF_MS (${BACKOFF_MS}ms)`,
    Math.min(...after.map((p) => p.started - throttled.ended)) >= BACKOFF_MS - 5,
    true
  );
}

// --- 9: the cross-scan cache ----------------------------------------------
{
  const A = "http://127.0.0.1:9850";
  const list = Array.from({ length: 3 }, (_, i) => `${A}/view/${i + 1}.html`);
  const chrome = makeChrome();
  const first = await run({ list, route: () => "html", chrome });
  const second = await run({ list, route: () => "html", chrome });
  check("first scan probes the network", first.probes.length, 3);
  check("a second scan of the same page costs no network", second.probes.length, 0);
  check("and returns the same media", second.urls, first.urls);
}

{
  const A = "http://127.0.0.1:9851";
  const list = Array.from({ length: 3 }, (_, i) => `${A}/view/${i + 1}.html`);
  const chrome = makeChrome();
  await run({ list, route: () => "html", chrome });
  // make the cached entries expire without waiting the TTL in real time
  for (const entry of Object.values(chrome.store.discoverCache)) entry.ts = Date.now() - CACHE_TTL_MS - 1;
  const again = await run({ list, route: () => "html", chrome });
  check("an expired entry is probed again", again.probes.length, 3);
}

{
  const B = "http://127.0.0.1:9852";
  const C = "http://127.0.0.1:9853";
  const list = [`${B}/view/1.html`, `${C}/view/2.html`];
  const chrome = makeChrome();
  await run({ list, route: (u) => (u.startsWith(B) ? "hang" : "html"), chrome });
  const again = await run({ list, route: (u) => (u.startsWith(B) ? "hang" : "html"), chrome });
  check("a failure is never cached as 'no media'", again.probes.some((p) => p.url.startsWith(B)), true);
  check("the healthy link was cached", again.probes.some((p) => p.url.startsWith(C)), false);
}

{
  const A = "http://127.0.0.1:9854";
  const list = Array.from({ length: 8 }, (_, i) => `${A}/view/${i + 1}.html`);
  const chrome = makeChrome();
  await run({ list, route: () => "html", chrome });
  check("the cache is capped to the newest entries", Object.keys(chrome.store.discoverCache).length, CACHE_MAX);
}

{
  const A = "http://127.0.0.1:9855";
  const list = Array.from({ length: 2 }, (_, i) => `${A}/view/${i + 1}.html`);
  const chrome = makeChrome();
  chrome.broken = true;
  const res = await run({ list, route: () => "html", chrome });
  check("a broken cache degrades to probing, it does not break the scan", res.urls.length, 2);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
