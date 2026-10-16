// ERR-9 regression guard: the "Full size" bucket must be verified before the
// popup promises it.
//
// Measured on reactor.cc/post/6382634: the bucket held 9 rows — 8 real
// full-size JPEGs plus i.li.ru/ext/.../disney_33.jpg, which answers
// 404 text/html — so the popup promised 9 files and Chrome saved 8.
//
// The safety property this file exists for: a two-valued gate would DELETE real
// media (hotlink-protected hosts answer 403/timeout for genuine images), so
// anything inconclusive must stay in the list as "unknown".
// Run: node extension/tests/verify_media.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");
const sharedSrc = readFileSync(join(here, "..", "shared.js"), "utf8");

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

function extract(marker) {
  const start = src.indexOf(marker);
  if (start < 0) {
    console.error(`FAIL: ${marker} not found in background.js`);
    process.exit(1);
  }
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  console.error(`FAIL: could not extract ${marker}`);
  process.exit(1);
}

function sharedConst(name, type) {
  const re = new RegExp(`const ${name} = (${type});`);
  const m = re.exec(sharedSrc);
  if (!m) {
    console.error(`FAIL: ${name} not found in shared.js`);
    process.exit(1);
  }
  return JSON.parse(m[1]);
}

const VERIFY_ACCEPT = sharedConst("VERIFY_ACCEPT", '"[^"]*"');
const VERIFY_CONCURRENCY = sharedConst("VERIFY_CONCURRENCY", "\\d+");
const VERIFY_TIMEOUT_MS = sharedConst("VERIFY_TIMEOUT_MS", "\\d+");
const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct", "sieve-to", "a-link", "linked-img"]);

// Build verifyMediaUrl / verifyFullsizeBucket standalone, with the constants and a
// fake fetch injected (same technique as resolve_head.test.mjs).
function makeVerify(fetchImpl, opts = {}) {
  const mediaUrlBody = extract("async function verifyMediaUrl(url, referer) {");
  const bucketBody = extract("async function verifyFullsizeBucket(items, pageUrl) {");
  const budget = opts.budget === undefined
    ? sharedConst("VERIFY_BUDGET_MS", "\\d+")
    : opts.budget;
  // Both must share ONE scope: verifyFullsizeBucket calls verifyMediaUrl, so the
  // two extracted bodies go into a single IIFE instead of separate closures.
  const factory = new Function(
    "fetch", "AbortSignal", "FULLSIZE_SOURCES", "VERIFY_ACCEPT",
    "VERIFY_CONCURRENCY", "VERIFY_TIMEOUT_MS", "VERIFY_BUDGET_MS",
    `return (() => {
${mediaUrlBody}
${bucketBody}
return { verifyMediaUrl, verifyFullsizeBucket };
})();`
  );
  return factory(
    fetchImpl,
    { timeout: () => ({}) },
    FULLSIZE_SOURCES,
    VERIFY_ACCEPT,
    VERIFY_CONCURRENCY,
    VERIFY_TIMEOUT_MS,
    budget
  );
}

const PAGE = "https://reactor.cc/post/6382634";
const JPEG_URL = "https://img10.reactor.cc/pics/post/full/Arrow-9489969.jpeg";
const DEAD_URL = "http://i.li.ru/ext/tv.akado.ru/images/data/akadotv/channelimage/image/114226/disney_33.jpg";

function resp({ ok = true, status = 200, ct = "", url = JPEG_URL }) {
  return {
    ok,
    status,
    url,
    headers: new Map(ct ? [["content-type", ct]] : []),
  };
}

// 1. A real image file: media.
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ct: "image/jpeg; charset=binary" }));
  check("200 image/jpeg -> media", (await verifyMediaUrl(JPEG_URL, PAGE)).verdict, "media");
}

// 2. The measured dead row: 404 with an HTML body -> not-media.
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ok: false, status: 404, ct: "text/html; charset=windows-1251" }));
  check("404 text/html -> not-media", (await verifyMediaUrl(DEAD_URL, PAGE)).verdict, "not-media");
}

// 3. 410 Gone is dead too.
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ok: false, status: 410, ct: "text/html" }));
  check("410 -> not-media", (await verifyMediaUrl(DEAD_URL, PAGE)).verdict, "not-media");
}

// 4. A 2xx that is a page (HTML shell) -> not-media.
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ct: "text/html; charset=utf-8" }));
  check("200 text/html -> not-media", (await verifyMediaUrl(DEAD_URL, PAGE)).verdict, "not-media");
}

// 5. Hotlink protection: 403 says NOTHING about the file. Must stay unknown.
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ok: false, status: 403, ct: "text/html" }));
  check("403 -> unknown (kept)", (await verifyMediaUrl(JPEG_URL, PAGE)).verdict, "unknown");
}

// 6. HEAD not allowed: unknown, so the item is not lost.
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ok: false, status: 405, ct: "" }));
  check("405 -> unknown (kept)", (await verifyMediaUrl(JPEG_URL, PAGE)).verdict, "unknown");
}

// 7. Timeout / network error: unknown, never a throw.
{
  const { verifyMediaUrl } = makeVerify(async () => { throw new Error("signal timed out"); });
  check("timeout -> unknown (kept)", (await verifyMediaUrl(JPEG_URL, PAGE)).verdict, "unknown");
}

// 8. No content-type at all: unknown (cannot promise, must not drop).
{
  const { verifyMediaUrl } = makeVerify(async () => resp({ ct: "" }));
  check("no content-type -> unknown (kept)", (await verifyMediaUrl(JPEG_URL, PAGE)).verdict, "unknown");
}

// 9. The probe asks for an image, not for HTML.
{
  let seenAccept = null;
  const { verifyMediaUrl } = makeVerify(async (url, opts) => {
    seenAccept = opts.headers && opts.headers["Accept"];
    return resp({ ct: "image/png" });
  });
  await verifyMediaUrl(JPEG_URL, PAGE);
  check("probe sends the image Accept", seenAccept, VERIFY_ACCEPT);
}

// 10. Bucket pass: annotates items, marks verified only for real media, and never
//     touches the Thumbnails bucket.
{
  const items = [
    { url: JPEG_URL, source: "a-link" },
    { url: DEAD_URL, source: "linked-img" },
    { url: JPEG_URL + "?x", source: "sieve-res" },
    { url: "https://img10.reactor.cc/pics/post/thumb-1.jpeg", source: "page-img" },
  ];
  let calls = 0;
  const { verifyFullsizeBucket } = makeVerify(async (url) => {
    calls++;
    if (url.startsWith("http://i.li.ru")) return resp({ ok: false, status: 404, ct: "text/html", url });
    return resp({ ct: "image/jpeg", url });
  });
  const summary = await verifyFullsizeBucket(items, PAGE);
  check("summary counts", summary, { verified: 2, notMedia: 1, unknown: 0 });
  check("only bucket items probed (3 of 4)", calls, 3);
  check("thumbnail item untouched", items[3].mediaVerdict, undefined);
  check("media item verdict", items[0].mediaVerdict, "media");
  check("media item carries verified flag", items[0].verified, true);
  check("not-media item verdict", items[1].mediaVerdict, "not-media");
  check("not-media item is NOT verified", items[1].verified, undefined);
}

// 11. A probe that cannot answer keeps the item in the list (the whole point).
{
  const items = [{ url: JPEG_URL, source: "a-link" }];
  const { verifyFullsizeBucket } = makeVerify(async () => { throw new Error("network down"); });
  const summary = await verifyFullsizeBucket(items, PAGE);
  check("failed probe -> unknown, item kept", summary, { verified: 0, notMedia: 0, unknown: 1 });
  check("item still has its url", items[0].url, JPEG_URL);
  check("item verdict recorded", items[0].mediaVerdict, "unknown");
}

// 12. Budget exhausted: nothing is probed and nothing is dropped.
{
  const items = [{ url: JPEG_URL, source: "a-link" }, { url: JPEG_URL + "2", source: "a-link" }];
  let calls = 0;
  const { verifyFullsizeBucket } = makeVerify(async (url) => { calls++; return resp({ ct: "image/jpeg", url }); }, { budget: 0 });
  const summary = await verifyFullsizeBucket(items, PAGE);
  check("budget 0 -> nothing probed", calls, 0);
  check("budget 0 -> items kept as unknown", summary, { verified: 0, notMedia: 0, unknown: 2 });
}

// 13. An empty list is not an error and issues no probes.
{
  let calls = 0;
  const { verifyFullsizeBucket } = makeVerify(async () => { calls++; return resp({}); });
  check("empty bucket", await verifyFullsizeBucket([], PAGE), { verified: 0, notMedia: 0, unknown: 0 });
  check("empty bucket -> no probes", calls, 0);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
