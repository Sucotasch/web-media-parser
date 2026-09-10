// ERR-1: discoverFullsize processLink must fetch linked full-size URLs with
// the page as Referer — hosts like img10.reactor.cc 302 referer-less
// requests to an HTML page (hotlink protection), which silently degrades
// full-size discovery to thumbnails. Browsers natively send Referer on
// <img> loads (that is why Imagus resolves these URLs); the SW must mirror it.
// Run: node extension/tests/discover_referer.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");

// Extract `async function processLink(linkUrl) { ... }` by brace counting
// (its body contains nested braces, so the simple non-greedy regex used for
// applyUrlTransform cannot be reused).
const startMarker = "async function processLink(linkUrl) {";
const start = src.indexOf(startMarker);
if (start < 0) {
  console.error("FAIL: processLink not found in background.js");
  process.exit(1);
}
let depth = 0;
let end = start;
for (let i = start; i < src.length; i++) {
  const ch = src[i];
  if (ch === "{") depth++;
  else if (ch === "}") {
    depth--;
    if (depth === 0) {
      end = i + 1;
      break;
    }
  }
}
if (end <= start) {
  console.error("FAIL: could not extract processLink body");
  process.exit(1);
}

// Inject the closure dependencies as explicit parameters (new Function bodies
// run in global scope — .call() alone would not resolve them).
function makeProcessLink(fetchStub) {
  const cachedSieveRes = {};
  function applyUrlTransform() { return null; }
  const seen = new Set();
  const pageUrl = "https://pr.reactor.cc/post/6064303";
  return new Function(
    "cachedSieveRes", "applyUrlTransform", "seen", "pageUrl", "fetch",
    `return (${src.slice(start, end)})`
  )(cachedSieveRes, applyUrlTransform, seen, pageUrl, fetchStub);
}

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

const THUMB = "https://img10.reactor.cc/pics/post/full/Candyball-Goddess-of-victory-Nikke-8862774.jpeg";
const PAGE_URL = "https://pr.reactor.cc/post/6064303";

// 1. Referer header must be sent on the fetch
{
  let capturedHeaders = null;
  const processLink = makeProcessLink(async (url, opts) => {
    capturedHeaders = opts.headers;
    return { ok: true, headers: { get: () => "image/jpeg" } };
  });
  await processLink(THUMB);
  check("fetch sent Referer = pageUrl", capturedHeaders && capturedHeaders.Referer, PAGE_URL);
  check("Accept header preserved", capturedHeaders && capturedHeaders.Accept, "text/html");
}

// 2. An image/jpeg response yields a link-direct fullsize item
{
  const processLink = makeProcessLink(async () => ({
    ok: true,
    headers: { get: () => "image/jpeg" },
  }));
  const items = await processLink(THUMB);
  check("image response -> link-direct item", items.length === 1 && items[0].source, "link-direct");
  check("image response -> same url", items[0].url, THUMB);
}

// 3. Non-ok responses still return nothing (no regression)
{
  const processLink = makeProcessLink(async () => ({ ok: false }));
  const items = await processLink(THUMB);
  check("non-ok -> empty", items.length, 0);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");