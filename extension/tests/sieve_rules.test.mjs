// R-E-9 / R-E-2 / R-E-11 tests for the sieve rules engine (sieve.js).
//
// sieve.js is a classic script loaded with importScripts() (no exports), so
// the whole file source is evaluated in isolation and its functions are
// returned — if anyone re-adds module syntax there, this loads loudly instead
// of failing silently in the worker.
//
// Run: node extension/tests/sieve_rules.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "sieve.js"), "utf8");

// D-08: parseSieve warns about rules it had to drop, so the suite passes its
// own console in and can observe that warning.
const consoleCalls = [];
const fakeConsole = {
  warn: (...a) => consoleCalls.push(["warn", a.join(" ")]),
  debug: (...a) => consoleCalls.push(["debug", a.join(" ")]),
  error: console.error,
  log: console.log,
};

const api = new Function("console", `${src}
  return { parseSieve, applySieveRules, applySieveRulesAll, applySubstitution, applySubstitutionAll };
`)(fakeConsole);

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

// --- R-E-9: `off: 1` disables a rule ---------------------------------------

const rules = api.parseSieve({
  Disabled: { img: "/thumb/(\\d+)", to: "/full/$1", off: 1 },
  Enabled: { img: "/small/(\\d+)", to: "/big/$1" },
  ZeroIsOn: { img: "/zero/(\\d+)", to: "/one/$1", off: 0 },
  NoPattern: { img: "/x/(\\d+)" },           // no `to` → unusable
  BadRegex: { img: "([", to: "/y/$1" },      // invalid regex → skipped
});

check("parsed rule names", rules.map((r) => r.name), ["Enabled", "ZeroIsOn"]);
check("off:1 rule does not transform", api.applySieveRulesAll("/thumb/1", "https://a/", rules), []);
check("off:0 rule still transforms", api.applySieveRules("/zero/7", "https://a/", rules), "https://a/one/7");

// Real bundled sieve: no rule flagged off:1 may survive parseSieve.
const bundled = JSON.parse(readFileSync(join(here, "..", "sieve.json"), "utf8"));
const offNames = Object.keys(bundled).filter((k) => bundled[k] && bundled[k].off);
const parsedNames = new Set(api.parseSieve(bundled).map((r) => r.name));
check("bundled sieve has disabled rules (fixture sanity)", offNames.length > 0, true);
check("no off:1 rule from sieve.json survives", offNames.filter((n) => parsedNames.has(n)), []);
check("bundled sieve still yields many rules", parsedNames.size > 100, true);

// --- R-E-2: every #ext# variant of the first matching rule -----------------

const variantRules = api.parseSieve({
  Variants: { img: "/v/(\\d+)\\.jpg", to: "/full/$1.#jpg png#" },
});

// NF-1: variants leave the pipeline as ABSOLUTE URLs — protocol-relative or
// root-relative results used to reach Chrome as `file://…` / `MissingSchema`.
check(
  "two #ext# variants produce two absolute URLs",
  api.applySieveRulesAll("https://a/v/12.jpg", "https://a/", variantRules),
  ["https://a/full/12.jpg", "https://a/full/12.png"]
);
check(
  "applySieveRules still returns the first variant",
  api.applySieveRules("https://a/v/12.jpg", "https://a/", variantRules),
  "https://a/full/12.jpg"
);
check(
  "applySubstitutionAll expands one URL per extension (raw, unnormalized)",
  api.applySubstitutionAll("https://a/v/12.jpg", ["/v/12.jpg", "12"], "/v/$1.#jpg webp#"),
  ["/v/12.jpg", "/v/12.webp"]
);
check(
  "plain pattern yields a single variant",
  api.applySubstitutionAll("abc123", ["abc123"], "https://cdn/$&"),
  ["https://cdn/abc123"]
);
check(
  "$& with no match data yields the empty substitution (A-12 spec)",
  api.applySubstitutionAll("x", [], "https://cdn/$&"),
  ["https://cdn/"]
);

// First matching rule wins — later rules must not add variants.
const orderedRules = api.parseSieve({
  First: { img: "/o/(\\d+)", to: "/first/$1.#a b#" },
  Second: { img: "/o/(\\d+)", to: "/second/$1" },
});
check(
  "only the first matching rule contributes",
  api.applySieveRulesAll("https://a/o/5", "https://a/", orderedRules),
  ["https://a/first/5.a", "https://a/first/5.b"]
);

// --- link filter + no-match contract --------------------------------------

const linkRules = api.parseSieve({
  Scoped: { img: "/p/(\\d+)", to: "/full/$1", link: "example\\.com" },
});
check("link filter blocks other pages", api.applySieveRulesAll("https://a/p/1", "https://other/", linkRules), []);
check("link filter allows matching page", api.applySieveRulesAll("https://a/p/1", "https://example.com/", linkRules), ["https://example.com/full/1"]);
check("no match returns empty array", api.applySieveRulesAll("https://a/nope.jpg", "https://a/", linkRules), []);
check("no match returns null (single form)", api.applySieveRules("https://a/nope.jpg", "https://a/", linkRules), null);

// --- A-09: rules anchored on a scheme-stripped URL fire again ---------------
// The bundled sieve carries hundreds of `^(https?:\/\/)?…` / `^host\/` rules;
// matching only the full URL left every `^host/`-anchored one dead.
const anchoredRules = api.parseSieve({
  Anchored: { img: "^42\\.cdn/img/(\\d+)\\.jpg", to: "https://42.cdn/full/$1.jpg" },
});
check(
  "a ^-anchored rule matches the scheme-stripped form",
  api.applySieveRulesAll("https://42.cdn/img/7.jpg", "https://page.example/", anchoredRules),
  ["https://42.cdn/full/7.jpg"]
);
check(
  "the full URL is still tried first",
  api.applySieveRulesAll("https://42.cdn/img/7.jpg", "https://page.example/", api.parseSieve({
    Full: { img: "^https://42\\.cdn/img/(\\d+)\\.jpg", to: "https://42.cdn/full/$1.jpg" },
  })),
  ["https://42.cdn/full/7.jpg"]
);

// --- D-08: a broken rule is reported, not silently dropped ----------------
consoleCalls.length = 0;
const noisy = api.parseSieve({
  Fine: { img: "/ok/(\\d+)", to: "/ok/$1" },
  Broken: { img: "([", to: "/bad/$1" },
});
check("D-08: the broken rule is dropped", noisy.map((r) => r.name), ["Fine"]);
check(
  "D-08: the drop is logged per rule and counted once",
  consoleCalls.map((c) => c[0]),
  ["debug", "warn"]
);
check(
  "D-08: the aggregate warning names the count",
  /1 rule\(s\) could not be parsed/.test(consoleCalls[1][1]),
  true
);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
