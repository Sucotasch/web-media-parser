// ERR-5 regression guard: resolveUrl must probe URLs with HEAD first (no body
// transfer — the old GET-first path downloaded the ENTIRE media file into the
// SW before chrome.downloads downloaded it again: double transfer per file,
// and the first download visibly stalled). GET is only the fallback for hosts
// that mishandle HEAD (405 / no content-type / HTML shell).
// Run: node extension/tests/resolve_head.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");

const startMarker = "async function resolveUrl(url, referer) {";
const start = src.indexOf(startMarker);
if (start < 0) {
  console.error("FAIL: resolveUrl not found in background.js");
  process.exit(1);
}
let depth = 0;
let end = start;
for (let i = start; i < src.length; i++) {
  const ch = src[i];
  if (ch === "{") depth++;
  else if (ch === "}") {
    depth--;
    if (depth === 0) { end = i + 1; break; }
  }
}
if (end <= start) {
  console.error("FAIL: could not extract resolveUrl body");
  process.exit(1);
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

const PAGE = "https://pr.reactor.cc/post/6064303";
const FULL_URL = "https://img10.reactor.cc/pics/post/full/Candyball-8862774.jpeg";

// ERR-9: the HEAD probe now sends the browser image Accept, which lives in
// shared.js (single source) — read the real constant instead of duplicating the
// literal here, or this guard would pass against a string nobody ships.
const sharedSrc = readFileSync(join(here, "..", "shared.js"), "utf8");
const acceptMatch = /const VERIFY_ACCEPT = ("[^"]*");/.exec(sharedSrc);
if (!acceptMatch) {
  console.error("FAIL: VERIFY_ACCEPT not found in shared.js");
  process.exit(1);
}
const VERIFY_ACCEPT = JSON.parse(acceptMatch[1]);
if (/text\/html/.test(VERIFY_ACCEPT)) {
  console.error("FAIL: VERIFY_ACCEPT must not be an HTML Accept");
  process.exit(1);
}

function makeResolveUrl(fetchImpl) {
  return new Function(
    "fetch", "cachedSieveRes", "AbortSignal", "VERIFY_ACCEPT",
    `return (${src.slice(start, end)})`
  )(
    fetchImpl,
    {}, // empty sieve — no res patterns to hit
    { timeout: () => ({}) }, // fake AbortSignal for standalone execution
    VERIFY_ACCEPT
  );
}

// 1. HEAD returns image/* -> URL returned as-is, NO GET body transfer
{
  const methods = [];
  const fetchImpl = async (url, opts) => {
    methods.push(opts && opts.method ? opts.method : "GET");
    if ((opts && opts.method) === "HEAD") {
      return { ok: true, headers: new Map([["content-type", "image/jpeg"]]) };
    }
    return { ok: true, headers: new Map([["content-type", "image/jpeg"]]), text: async () => "body" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  const result = await resolveUrl(FULL_URL, PAGE);
  check("HEAD-first: only HEAD issued", methods, ["HEAD"]);
  check("image content-type returns url as-is", result, FULL_URL);
}

// 2. HEAD returns text/html -> GET fallback + sieve res extraction attempted
{
  const methods = [];
  const fetchImpl = async (url, opts) => {
    methods.push(opts && opts.method ? opts.method : "GET");
    if ((opts && opts.method) === "HEAD") {
      return { ok: true, headers: new Map([["content-type", "text/html"]]) };
    }
    return { ok: true, headers: new Map([["content-type", "text/html"]]), text: async () => "<html>shell</html>" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  const result = await resolveUrl(FULL_URL, PAGE);
  check("html HEAD -> GET fallback issued", methods, ["HEAD", "GET"]);
  check("no res pattern -> original url", result, FULL_URL);
}

// 3. HEAD 405 -> GET fallback (host mishandles HEAD)
{
  const methods = [];
  const fetchImpl = async (url, opts) => {
    methods.push(opts && opts.method ? opts.method : "GET");
    if ((opts && opts.method) === "HEAD") {
      return { ok: false, status: 405, headers: new Map() };
    }
    return { ok: true, headers: new Map([["content-type", "image/png"]]), text: async () => "" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  const result = await resolveUrl(FULL_URL, PAGE);
  check("405 HEAD -> GET fallback", methods, ["HEAD", "GET"]);
  check("png fallback returns url as-is", result, FULL_URL);
}

// 4. HEAD network error -> GET fallback (no throw)
{
  const methods = [];
  const fetchImpl = async (url, opts) => {
    methods.push(opts && opts.method ? opts.method : "GET");
    if ((opts && opts.method) === "HEAD") throw new Error("net down");
    return { ok: true, headers: new Map([["content-type", "video/mp4"]]), text: async () => "" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  const result = await resolveUrl(FULL_URL, PAGE);
  check("HEAD throw -> GET fallback", methods, ["HEAD", "GET"]);
  check("video fallback returns url as-is", result, FULL_URL);
}

// 5. HEAD returns image -> Referer header must be present on the HEAD probe
{
  let seenHeaders = null;
  const fetchImpl = async (url, opts) => {
    seenHeaders = opts && opts.headers ? opts.headers : null;
    if ((opts && opts.method) === "HEAD") {
      return { ok: true, headers: new Map([["content-type", "image/jpeg"]]) };
    }
    return { ok: true, headers: new Map([["content-type", "image/jpeg"]]), text: async () => "" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  await resolveUrl(FULL_URL, PAGE);
  check("HEAD probe carries Referer", seenHeaders && seenHeaders["Referer"], PAGE);
}

// 6. ERR-9: the HEAD probe must ask for an IMAGE, not for HTML. Measured live:
//    with "text/html" these hosts tarpit the probe (HEAD timed out at 4 s, or
//    answered 200 text/html); the same HEAD with an image Accept answered
//    200 image/jpeg in 96-633 ms.
{
  let headAccept = null;
  const fetchImpl = async (url, opts) => {
    if ((opts && opts.method) === "HEAD") {
      headAccept = opts.headers && opts.headers["Accept"];
      return { ok: true, headers: new Map([["content-type", "image/jpeg"]]) };
    }
    return { ok: true, headers: new Map([["content-type", "image/jpeg"]]), text: async () => "" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  await resolveUrl(FULL_URL, PAGE);
  check("HEAD probe sends the image Accept", headAccept, VERIFY_ACCEPT);
}

// 7. ERR-9: the GET fallback keeps text/html on purpose — its job is to fetch an
//    HTML shell and pull the full-size URL out of it.
{
  let getAccept = null;
  const fetchImpl = async (url, opts) => {
    if ((opts && opts.method) === "HEAD") {
      return { ok: true, headers: new Map([["content-type", "text/html"]]) };
    }
    getAccept = opts.headers && opts.headers["Accept"];
    return { ok: true, headers: new Map([["content-type", "text/html"]]), text: async () => "<html></html>" };
  };
  const resolveUrl = makeResolveUrl(fetchImpl);
  await resolveUrl(FULL_URL, PAGE);
  check("GET (shell) fallback keeps text/html", getAccept, "text/html");
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");