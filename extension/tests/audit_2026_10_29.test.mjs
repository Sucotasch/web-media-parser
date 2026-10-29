// 2026-10-29 audit fixes on the extension side.
//
// E-10 — a manually loaded sieve file must be marked authoritative in the
//        background (sieveVersion), not written straight to storage.
// E-05 — `chrome.alarms.clear()` returns undefined on Chrome <111 / Firefox;
//        `.catch` on it threw a TypeError inside onAlarm.
// E-07 — the keyboard "Save in Chrome" path filters proven-not-media items and
//        keeps the scan's `verified` flag, exactly like the popup.
// E-08 — scan progress carries `ts`, so the popup can expire a dead scan.
//
// Run: node extension/tests/audit_2026_10_29.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const bg = readFileSync(join(here, "..", "background.js"), "utf8");
const popup = readFileSync(join(here, "..", "popup", "popup.js"), "utf8");

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

/** Extract `function name(...) { ... }` (brace-matched) from a source string. */
function extractFunction(source, marker, fromEnd = false) {
  const start = fromEnd ? source.lastIndexOf(marker) : source.indexOf(marker);
  if (start < 0) throw new Error(`not found: ${marker}`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced body: ${marker}`);
}

// --- E-10: setUserSieveRules marks the manual file authoritative ----------

{
  const fnSrc = extractFunction(bg, "async function setUserSieveRules(text) {");
  check("E-10: the background router exposes setSieveRules",
    bg.includes('request.action === "setSieveRules"'), true);

  const make = (stored) => {
    const chrome = {
      storage: { local: { set: async (obj) => { stored.push(obj); } } },
    };
    let reloads = 0;
    const fn = new Function(
      "chrome", "SIEVE_VERSION", "loadSieveResPatterns",
      `return (${fnSrc});`
    )(chrome, "2026.09.15", async () => { reloads++; });
    return { fn, stored, reloads: () => reloads };
  };

  {
    const stored = [];
    const t = make(stored);
    const result = await t.fn(JSON.stringify({ a: { img: "x", to: "y" }, b: {} }));
    check("E-10: valid manual set reports count", result, { ok: true, count: 2 });
    check("E-10: sieveRules stored as given", stored[0].sieveRules,
      JSON.stringify({ a: { img: "x", to: "y" }, b: {} }));
    check("E-10: sieveVersion is stamped (authoritative)", stored[0].sieveVersion, "2026.09.15");
    check("E-10: res patterns reloaded", t.reloads(), 1);
  }

  {
    const stored = [];
    const t = make(stored);
    check("E-10: invalid JSON is refused", await t.fn("{ nope"), { ok: false, error: "Invalid JSON" });
    check("E-10: empty object is refused", await t.fn("{}"), { ok: false, error: "File contains no rules" });
    check("E-10: an array is refused", (await t.fn("[]")).ok, false);
    check("E-10: nothing written on refusal", stored.length, 0);
  }
}

// --- E-05: the keepalive alarm listener survives a synchronous clear -------

{
  const marker = "chrome.alarms.onAlarm.addListener((alarm) => {";
  // The file has two onAlarm listeners (sieve update first, keepalive last).
  const fnSrc = extractFunction(bg, marker, true);
  const body = fnSrc.slice(fnSrc.indexOf("{"));
  check("E-05: the listener no longer calls .catch directly on clear()",
    /chrome\.alarms\.clear\(KEEPALIVE_ALARM\)\.catch/.test(bg), false);

  const build = (refs, clearReturn) => {
    let cleared = 0;
    const chrome = {
      alarms: { clear: () => { cleared++; return clearReturn; } },
    };
    const compiled = new Function(
      "chrome", "KEEPALIVE_ALARM", "refs", "alarm",
      `let _keepaliveRefs = refs; ${body}`
    );
    return {
      run: () => compiled(chrome, "keepalive", refs, { name: "keepalive" }),
      cleared: () => cleared,
    };
  };

  {
    // Chrome <111 / Firefox namespace: clear() returns undefined.
    const t = build(0, undefined);
    let threw = null;
    try { t.run(); } catch (e) { threw = e; }
    check("E-05: undefined clear() does not throw", threw, null);
    check("E-05: the stale alarm is still cleared", t.cleared(), 1);
  }
  {
    // A live session must not be cleared by the wake itself.
    const t = build(2, undefined);
    t.run();
    check("E-05: an active session keeps its alarm", t.cleared(), 0);
  }
}

// --- E-07: keyboard save mirrors the popup's bucket -----------------------

{
  const start = bg.indexOf("async function commandScanAndProcess(");
  const end = bg.indexOf("\n// FULLSIZE_SOURCES now comes from shared.js", start);
  const body = bg.slice(start, end);
  check("E-07: proven not-media items are filtered",
    body.includes('item.mediaVerdict !== "not-media"'), true);
  check("E-07: the scan's verified flag is kept",
    body.includes('verified: item.verified === true || item.source === "link-direct"'), true);
}

// --- E-08: progress entries carry a timestamp ------------------------------

{
  check("E-08: initial scan progress is stamped",
    /status: "scanning"[^}]*ts: Date\.now\(\)/s.test(bg), true);
  check("E-08: link progress is stamped", bg.includes("progress.ts = Date.now();"), true);
  check("E-08: the final done state is stamped",
    /status: "done"[^}]*ts: Date\.now\(\)/s.test(bg), true);
  check("E-08: the popup expires a dead scan after the budget",
    popup.includes("Date.now() - stored.scanProgress.ts > 90_000"), true);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
