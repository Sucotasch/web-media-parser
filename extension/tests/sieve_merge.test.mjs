// C-1 / R-E-8 / R-E-1 / R-E-5 / R-E-12 tests for sieve_updater.js.
//
// sieve_updater.js is a CLASSIC script (importScripts cannot load a module),
// so the whole file source is evaluated with `self`/`chrome`/`fetch` injected
// and its public API is read back from `self.WMPSieveUpdater`. Re-introducing
// `export` in that file makes this load fail loudly — the module→classic
// conversion is the actual fix for the dead online update, so it is asserted
// here rather than trusted.
//
// Run: node extension/tests/sieve_merge.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "sieve_updater.js"), "utf8");

// setTimeout is injected so the retry backoff does not make the suite wait.
const instantTimeout = (fn) => fn();

function loadUpdater({ fetchImpl, chromeImpl } = {}) {
  const self = {};
  const chrome = chromeImpl || { storage: { local: null } };
  const load = new Function(
    "self", "chrome", "fetch", "AbortSignal", "setTimeout",
    `${src}\nreturn self.WMPSieveUpdater;`
  );
  return load(self, chrome, fetchImpl || (async () => ({ status: 304, ok: false })), AbortSignal, instantTimeout);
}

let updater;
try {
  updater = loadUpdater();
} catch (e) {
  console.error("FAIL: sieve_updater.js is not loadable as a classic script:", e.message);
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

// --- the classic-script surface ------------------------------------------

check("exported api is complete",
  Object.keys(updater).sort(),
  ["COLD_START_MIN_INTERVAL_MS", "SIEVE_REPO_URL", "SIEVE_UPDATE_ALARM", "ensureWeeklyAlarm",
   "jsDelivrMirror", "mergeSieve", "parseStoredSieve", "shouldProbeLastUpdate", "updateSieve",
   "validRuleCount"].sort());

// jsDelivrMirror
check(
  "raw.githubusercontent converts to jsDelivr",
  updater.jsDelivrMirror("https://raw.githubusercontent.com/user/repo/master/data/sieve.json"),
  "https://cdn.jsdelivr.net/gh/user/repo@master/data/sieve.json"
);
check("non-github URL returns null", updater.jsDelivrMirror("https://example.com/x.json"), null);
check("empty URL returns null", updater.jsDelivrMirror(""), null);

// validRuleCount
check(
  "counts rules with link or img",
  updater.validRuleCount({ a: { link: "x" }, b: { img: "y" }, c: {}, d: { other: 1 } }),
  2
);
check("empty sieve counts 0", updater.validRuleCount({}), 0);

// mergeSieve — port of Mod's semantics
const oldSieve = {
  _userCustom: { link: "user-rule", to: "img" },   // user-defined → survives
  Keep: { link: "keep", to: "img", off: 1 },        // off flag carries over
  Gone: { link: "gone", to: "img" },                // removed upstream → off:1
  Changed: { link: "old", to: "img", off: 1 },      // upstream disabled it → stays off
};
const newSieve = {
  Keep: { link: "keep-new", to: "img" },
  Fresh: { link: "fresh", to: "img" },
  Changed: { link: "changed-new", to: "img" },
};
const merged = updater.mergeSieve(oldSieve, newSieve);

check("_-prefixed user rule survives", !!merged._userCustom, true);
check("off flag carried over to updated rule", merged.Keep.off, 1);
check("removed rule kept with off:1", merged.Gone && merged.Gone.off, 1);
check("upstream change accepted", merged.Changed.link, "changed-new");
check("upstream off preserved", merged.Changed.off, 1);
check("new upstream rule added", merged.Fresh.link, "fresh");
check("user rule not present upstream still there", merged._userCustom.link, "user-rule");

// --- R-E-1: stored sieve values ------------------------------------------

check("string value is parsed", updater.parseStoredSieve('{"A":{"img":"x"}}'), { A: { img: "x" } });
check("legacy object value is accepted as-is", updater.parseStoredSieve({ A: { img: "x" } }), { A: { img: "x" } });
check("garbage string is ignored", updater.parseStoredSieve("not json"), {});
check("array value is ignored", updater.parseStoredSieve("[1,2]"), {});
check("empty value is ignored", updater.parseStoredSieve(""), {});

// --- R-E-5: the weekly alarm is created only when missing ----------------

function makeAlarms(existing) {
  const calls = { get: 0, create: [] };
  return {
    calls,
    api: {
      async get(name) { calls.get++; return existing ? { name } : undefined; },
      async create(name, opts) { calls.create.push([name, opts]); },
    },
  };
}

const created = makeAlarms(false);
check("missing alarm is created", await updater.ensureWeeklyAlarm(created.api), true);
check("created with a 7-day period", created.calls.create, [["sieve-update-weekly", { periodInMinutes: 10080 }]]);

const kept = makeAlarms(true);
check("existing alarm is left alone", await updater.ensureWeeklyAlarm(kept.api), false);
check("existing alarm is never re-created", kept.calls.create, []);
check("no alarm api → no crash", await updater.ensureWeeklyAlarm(undefined), false);

// --- R-E-12: cold-start throttle -----------------------------------------

const now = Date.now();
check("first ever attempt is allowed", updater.shouldProbeLastUpdate(undefined, now), true);
check("attempt one hour ago is throttled", updater.shouldProbeLastUpdate(now - 3600_000, now), false);
check("attempt eight hours ago is allowed", updater.shouldProbeLastUpdate(now - 8 * 3600_000, now), true);
check("garbage timestamp is allowed", updater.shouldProbeLastUpdate("nope", now), true);

// --- updateSieve: storage contract + merge -------------------------------

function makeStorage(initial = {}) {
  const data = { ...initial };
  const writes = [];
  return {
    data, writes,
    api: {
      async get(keys) {
        const out = {};
        for (const k of [].concat(keys)) if (k in data) out[k] = data[k];
        return out;
      },
      async set(patch) { writes.push(patch); Object.assign(data, patch); },
    },
  };
}

function makeFetch({ status = 200, body = "{}" } = {}) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, headers: (opts && opts.headers) || {} });
    return {
      status,
      ok: status >= 200 && status < 300,
      async text() { return body; },
    };
  };
  fn.calls = calls;
  return fn;
}

const BUNDLE = { Keep: { link: "keep-new", img: "x" }, Fresh: { link: "fresh", img: "y" } };
const priorRules = JSON.stringify({
  _userCustom: { link: "u", img: "z" },
  Keep: { link: "keep-old", img: "x", off: 1 },
  Gone: { link: "gone", img: "g" },
});

{
  const storage = makeStorage({ sieveRules: priorRules, sieveUpdateLast: 1700000000000 });
  const fetchImpl = makeFetch({ status: 200, body: JSON.stringify(BUNDLE) });
  const up = loadUpdater({ fetchImpl });
  const result = await up.updateSieve("https://raw.githubusercontent.com/u/r/m/sieve.json", {
    version: "2026.07.15", storage: storage.api,
  });

  check("commit status", result.status, "updated");
  check("attempt timestamp recorded", typeof storage.data.sieveUpdateAttempt, "number");

  const rulesPatch = storage.writes[storage.writes.length - 1];
  check("sieveRules written as a STRING", typeof rulesPatch.sieveRules, "string");
  check("bundle version recorded with the merge", rulesPatch.sieveVersion, "2026.07.15");
  check("sieveUpdateLast written", typeof rulesPatch.sieveUpdateLast, "number");

  const reparsed = JSON.parse(storage.data.sieveRules);   // every reader does this
  check("readers can JSON.parse the stored rules", typeof reparsed, "object");
  check("user rule survived the merge", !!reparsed._userCustom, true);
  check("off flag survived the merge", reparsed.Keep.off, 1);
  check("removed upstream rule kept as a ghost", reparsed.Gone.off, 1);
  check("upstream rule updated", reparsed.Keep.link, "keep-new");
  check("upstream addition merged in", reparsed.Fresh.link, "fresh");
  check("If-Modified-Since sent from the previous sync",
    fetchImpl.calls[0].headers["If-Modified-Since"], new Date(1700000000000).toUTCString());
}

// 304 → up-to-date, and NOTHING is written to sieveRules
{
  const storage = makeStorage({ sieveRules: priorRules, sieveUpdateLast: 1700000000000 });
  const up = loadUpdater({ fetchImpl: makeFetch({ status: 304 }) });
  const result = await up.updateSieve(undefined, { version: "2026.07.15", storage: storage.api });
  check("304 is up-to-date", result.status, "up-to-date");
  check("304 keeps the stored rules", storage.data.sieveRules, priorRules);
  check("304 still marks the attempt", typeof storage.data.sieveUpdateAttempt, "number");
}

// A legacy OBJECT value in storage must not be merged as garbage
{
  const storage = makeStorage({ sieveRules: { Old: { link: "o", img: "z" } } });
  const up = loadUpdater({ fetchImpl: makeFetch({ status: 200, body: JSON.stringify(BUNDLE) }) });
  await up.updateSieve(undefined, { version: "v", storage: storage.api });
  const reparsed = JSON.parse(storage.data.sieveRules);
  const keys = Object.keys(reparsed).sort();
  check("object-shaped legacy value merges sanely", keys, ["Fresh", "Keep", "Old"]);
}

// Failure → error status, rules untouched
{
  const storage = makeStorage({ sieveRules: priorRules });
  const failing = async () => { throw new Error("network down"); };
  const up = loadUpdater({ fetchImpl: failing });
  const result = await up.updateSieve("https://raw.githubusercontent.com/u/r/m/sieve.json", {
    version: "v", storage: storage.api,
  });
  check("failure reported", result.status, "error");
  check("failure keeps the stored rules", storage.data.sieveRules, priorRules);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
