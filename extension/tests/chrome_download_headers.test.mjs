// ERR-2/ERR-3 regression guard: chrome.downloads.download() must NEVER carry
// a Referer in its `headers` option. Chrome rejects both shapes —
//   ERR-2: object map  -> "Error at property 'headers': Invalid type: expected array, found object"
//   ERR-3: {name,value} array with "Referer" -> "Unsafe request header name"
// — and every download fails to start ("nothing is saved"). The Referer fix
// belongs in OUR OWN fetches (discoverFullsize/resolveUrl) and in the desktop
// app's downloader, not in the Chrome downloads API.
// This test extracts the real chromeDownload from background.js and asserts
// the DownloadOptions it builds contain no headers option.
// Run: node extension/tests/chrome_download_headers.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");

const startMarker = "async function chromeDownload(items, concurrentLimit = 2) {";
const start = src.indexOf(startMarker);
if (start < 0) {
  console.error("FAIL: chromeDownload not found in background.js");
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
  console.error("FAIL: could not extract chromeDownload body");
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

function run(items, resolveFn) {
  let capturedOpts = null;
  let listener = null;
  const chrome = {
    storage: { local: { set: async () => {} } },
    downloads: {
      download: (opts, cb) => { capturedOpts = opts; cb(1); },
      onChanged: {
        addListener: (l) => { listener = l; },
        removeListener: () => {},
      },
    },
    runtime: { lastError: null },
  };
  // keepaliveAcquire/Release are defined outside chromeDownload in the real
  // file — inject no-op stand-ins so the extracted body runs standalone.
  const keepaliveAcquire = () => {};
  const keepaliveRelease = () => {};
  const chromeDownload = new Function(
    "resolveUrl", "chrome", "keepaliveAcquire", "keepaliveRelease",
    `return (${src.slice(start, end)})`
  )(resolveFn || (async (u) => u), chrome, keepaliveAcquire, keepaliveRelease);
  const promise = chromeDownload(items, 2);
  return { promise, getOpts: () => capturedOpts, getListener: () => listener };
}

// 1. Even WITH a referer present, no headers option may reach downloads.download
{
  const t = run([{ url: FULL_URL, filename: "x.jpeg", referer: PAGE }]);
  await new Promise((r) => setTimeout(r, 20));
  if (t.getListener()) t.getListener()({ id: 1, state: { current: "complete" } });
  const result = await t.promise;
  const opts = t.getOpts();
  check("no headers option when referer present", "headers" in (opts || {}), false);
  check("url passed to download()", opts && opts.url, FULL_URL);
  check("conflictAction preserved", opts && opts.conflictAction, "uniquify");
  check("download completed and counted", result && result.saved, 1);
}

// 2. resolveUrl still receives the referer (our own fetch may use it)
{
  let seenReferer = null;
  const resolveFn = async (u, ref) => { seenReferer = ref; return u; };
  const t = run([{ url: FULL_URL, filename: "y.jpeg", referer: PAGE }], resolveFn);
  await new Promise((r) => setTimeout(r, 20));
  if (t.getListener()) t.getListener()({ id: 1, state: { current: "complete" } });
  await t.promise;
  check("resolveUrl called with referer", seenReferer, PAGE);
}

// 3. ERR-5: a verified item (network-checked during scan) skips resolveUrl
// entirely — the download starts on the next tick without a resolve probe.
{
  let resolveCalls = 0;
  const resolveFn = async (u, ref) => { resolveCalls++; return u; };
  const t = run(
    [{ url: FULL_URL, filename: "v.jpeg", referer: PAGE, verified: true }],
    resolveFn
  );
  await new Promise((r) => setTimeout(r, 20));
  if (t.getListener()) t.getListener()({ id: 1, state: { current: "complete" } });
  const result = await t.promise;
  check("verified item skips resolveUrl", resolveCalls, 0);
  check("verified download still saved", result && result.saved, 1);
}

// 4. ERR-5: progress is published to storage (popup renders it live instead
// of freezing on sendMessage until all files finish).
{
  const progressWrites = [];
  const chrome2 = {
    storage: { local: { set: async (obj) => { progressWrites.push(obj.downloadProgress); } } },
    downloads: {
      download: (opts, cb) => cb(2),
      onChanged: {
        addListener: (l) => { listener2 = l; },
        removeListener: () => {},
      },
    },
    runtime: { lastError: null },
  };
  let listener2 = null;
  const chromeDownload2 = new Function(
    "resolveUrl", "chrome", "keepaliveAcquire", "keepaliveRelease",
    `return (${src.slice(start, end)})`
  )(async (u) => u, chrome2, () => {}, () => {});
  const p2 = chromeDownload2([{ url: FULL_URL, filename: "z.jpeg" }], 2);
  await new Promise((r) => setTimeout(r, 20));
  if (listener2) listener2({ id: 2, state: { current: "complete" } });
  await p2;
  check("progress written at least once (active)",
    progressWrites.some(w => w && w.status === "active" && w.total === 1), true);
  check("final progress written as done",
    progressWrites.some(w => w && w.status === "done" && w.saved === 1), true);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");