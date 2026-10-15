// Stage-5 extension resilience (2026-10-15):
//   A-13 / NF-9 — keepaliveAcquire must not throw and must not leak a reference
//   D-09       — every message branch answers (a rejected promise used to hang
//                the popup on "Scanning..." forever)
//   D-05       — the WordPress/CDN size suffix is stripped on this side too
//   D-07       — a filename Chrome will actually accept
//   A-14       — one scan / one download batch at a time
//
// background.js is a classic service-worker script, so the functions under test
// are extracted by brace matching and evaluated with their collaborators
// injected. The A-14 half that cannot be driven without a full chrome mock is
// pinned at source level and says so.
//
// Run: node extension/tests/background_resilience.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const bgSrc = readFileSync(join(here, "..", "background.js"), "utf8");
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

/** Extract `function NAME(...) { ... }` (or `async function`) by brace match. */
function extractFunction(src, name) {
  const marker = new RegExp(`(?:async )?function ${name}\\(`);
  const m = marker.exec(src);
  if (!m) throw new Error(`${name} not found`);
  const start = m.index;
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`could not extract ${name}`);
}

/** Extract a contiguous source slice between two markers (inclusive). */
function extractBetween(src, from, to) {
  const start = src.indexOf(from);
  if (start < 0) throw new Error(`marker not found: ${from}`);
  const end = src.indexOf(to, start);
  if (end < 0) throw new Error(`end marker not found: ${to}`);
  return src.slice(start, end + to.length);
}

// --- A-13 / NF-9: the keepalive must not throw and must not leak ------------

const keepaliveBlock = extractBetween(
  bgSrc,
  "let _keepaliveRefs = 0;",
  extractFunction(bgSrc, "keepaliveRelease")
);

function loadKeepalive(alarms) {
  const chrome = { alarms };
  const consoleStub = { warn: () => {} };
  const load = new Function(
    "chrome", "console",
    `${keepaliveBlock}\nreturn { keepaliveAcquire, keepaliveRelease, refs: () => _keepaliveRefs };`
  );
  return load(chrome, consoleStub);
}

// Chrome < 111: alarms.create() returns undefined (no .catch on it)
{
  const calls = { created: 0, cleared: 0 };
  const api = loadKeepalive({
    create: () => { calls.created++; return undefined; },
    clear: () => { calls.cleared++; return undefined; },
  });
  let threw = null;
  try { api.keepaliveAcquire(); } catch (e) { threw = e; }
  check("NF-9: acquire does not throw when create returns undefined", threw, null);
  check("NF-9: the reference is counted exactly once", api.refs(), 1);
  check("alarm armed once", calls.created, 1);
  api.keepaliveRelease();
  check("release drops to zero", api.refs(), 0);
  check("alarm cleared at zero", calls.cleared, 1);
}

// A failing alarms API: no throw, and NO leaked reference
{
  const api = loadKeepalive({
    create: () => { throw new Error("alarms unavailable"); },
    clear: () => { throw new Error("alarms unavailable"); },
  });
  let threw = null;
  let acquired = null;
  try { acquired = api.keepaliveAcquire(); } catch (e) { threw = e; }
  check("NF-9: a failing create does not throw", threw, null);
  check("NF-9: acquire reports failure", acquired, false);
  check("NF-9: the refcount stays at zero (never inflated)", api.refs(), 0);
  let releaseThrew = null;
  try { api.keepaliveRelease(); } catch (e) { releaseThrew = e; }
  check("NF-9: release tolerates a broken clear()", releaseThrew, null);
  check("NF-9: the refcount never goes negative", api.refs(), 0);
}

// --- D-09: a rejected promise still answers --------------------------------

const replyWith = new Function(`return (${extractFunction(bgSrc, "replyWith")})`)();

{
  const answers = [];
  replyWith(Promise.reject(new Error("network down")), (r) => answers.push(r));
  await new Promise((r) => setTimeout(r, 10));
  check("D-09: a rejected promise answers with an error", answers, [{ ok: false, error: "network down" }]);
}
{
  const answers = [];
  replyWith(Promise.resolve({ ok: true }), (r) => answers.push(r));
  await new Promise((r) => setTimeout(r, 10));
  check("D-09: a resolved promise passes its value through", answers, [{ ok: true }]);
}

// --- D-05: the size suffix is stripped -------------------------------------

const stripSizeSuffix = new Function(`return (${extractFunction(bgSrc, "stripSizeSuffix")})`)();
check("D-05: photo-300x200.jpg -> photo.jpg", stripSizeSuffix("photo-300x200.jpg"), "photo.jpg");
check("D-05: suffix before a query string", stripSizeSuffix("https://x/p/a-1024x768.png?w=1"), "https://x/p/a.png?w=1");
check("D-05: a real name with digits is untouched", stripSizeSuffix("photo-2024.jpg"), "photo-2024.jpg");
// Only the suffix DIRECTLY before the extension is a size marker — the same
// anchor the desktop twin uses, so `a-100x100-200x200.jpg` keeps its first part.
check("D-05: only the extension-adjacent suffix goes", stripSizeSuffix("a-100x100-200x200.jpg"), "a-100x100.jpg");
check("D-05: non-strings pass through", stripSizeSuffix(null), null);

// --- D-07: sanitizeFilename -------------------------------------------------

const shared = new Function(
  "self", "console",
  `${sharedSrc}\nreturn { sanitizeFilename };`
)({}, console);

const name = shared.sanitizeFilename;
check("D-07: an encoded path traversal cannot survive", /[\\/]/.test(name("..%2F..%2Fx.png")), false);
check("D-07: the result is not empty", name("..%2F..%2Fx.png").length > 0, true);
check("D-07: a Windows device name is neutralised", name("CON.png"), "_CON.png");
check("D-07: a trailing dot is dropped", name("photo."), "photo");
check("D-07: separators become underscores", name("a/b\\c.jpg"), "a_b_c.jpg");
check("D-07: control characters go", name("a\u0000b.jpg"), "a_b.jpg");
check("D-07: a long name is capped", name("x".repeat(400) + ".jpg").length <= 150, true);
check("D-07: the extension survives the cap", name("x".repeat(400) + ".jpg").endsWith(".jpg"), true);
check("D-07: nothing usable falls back", name("...", "media"), "media");
check("D-07: an ordinary name is untouched", name("holiday-2024.jpg"), "holiday-2024.jpg");

// --- A-14: the busy guards (source level; behaviour of the download guard is
//     covered in chrome_download_headers.test.mjs) --------------------------

check("A-14: the scan guard exists and is shared",
  /let _scanBusy = false;/.test(bgSrc), true);
const handleSrc = extractFunction(bgSrc, "handleStartScan");
check("A-14: handleStartScan refuses while a scan runs",
  /if \(_scanBusy\) \{[\s\S]*?already running[\s\S]*?\}/.test(handleSrc), true);
check("A-14: handleStartScan releases the guard in finally",
  /finally \{[\s\S]*?_scanBusy = false;/.test(handleSrc), true);
const commandSrc = extractFunction(bgSrc, "commandScanAndProcess");
check("A-14: the command path refuses while a scan runs", /if \(_scanBusy\) return;/.test(commandSrc), true);
check("A-14: the command path releases the guard", /_scanBusy = false;/.test(commandSrc), true);
check("A-14: the old command-only flag is gone", /commandBusy/.test(bgSrc), false);
check("D-01: the command path holds a keepalive",
  /keepaliveAcquire\(\);/.test(commandSrc) && /keepaliveRelease\(\);/.test(commandSrc), true);
const onMessageSrc = bgSrc.slice(bgSrc.indexOf("chrome.runtime.onMessage.addListener"));
check("D-09: no branch calls .then(sendResponse) directly",
  /\.then\(sendResponse\)/.test(onMessageSrc), false);
// download, discoverFullsize, startScan, chromeDownload, getContext, getStatus,
// updateSieve — the audit counted six, the file has seven async branches.
check("D-09: every async branch goes through replyWith",
  (onMessageSrc.match(/replyWith\(/g) || []).length, 7);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
