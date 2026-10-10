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

const api = new Function(`${src}
  return { parseSieve, applySieveRules, applySieveRulesAll, applySubstitution, applySubstitutionAll };
`)();

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
check("off:0 rule still transforms", api.applySieveRules("/zero/7", "https://a/", rules), "/one/7");

// Real bundled sieve: no rule flagged off:1 may survive parseSieve.
const bundled = JSON.parse(readFileSync(join(here, "..", "sieve.json"), "utf8"));
const offNames = Object.keys(bundled).filter((k) => bundled[k] && bundled[k].off);
const parsedNames = new Set(api.parseSieve(bundled).map((r) => r.name));
check("bundled sieve has disabled rules (fixture sanity)", offNames.length > 0, true);
check("no off:1 rule from sieve.json survives", offNames.filter((n) => parsedNames.has(n)), []);
check("bundled sieve still yields many rules", parsedNames.size > 100, true);

// --- R-E-2: every #ext# variant of the first matching rule -----------------

const variantRules = api.parseSieve({
  Variants: { img: "/v/(\\d+)\\.jpg", to: "/v/$1.#jpg png#" },
});

check(
  "two #ext# variants produce two URLs",
  api.applySieveRulesAll("https://a/v/12.jpg", "https://a/", variantRules),
  ["/v/12.jpg", "/v/12.png"]
);
check(
  "applySieveRules still returns the first variant",
  api.applySieveRules("https://a/v/12.jpg", "https://a/", variantRules),
  "/v/12.jpg"
);
check(
  "applySubstitutionAll expands one URL per extension",
  api.applySubstitutionAll("https://a/v/12.jpg", ["/v/12.jpg", "12"], "/v/$1.#jpg webp#"),
  ["/v/12.jpg", "/v/12.webp"]
);
check(
  "plain pattern yields a single variant",
  api.applySubstitutionAll("x", [], "https://cdn/$&"),
  ["https://cdn/$&"]
);

// First matching rule wins — later rules must not add variants.
const orderedRules = api.parseSieve({
  First: { img: "/o/(\\d+)", to: "/first/$1.#a b#" },
  Second: { img: "/o/(\\d+)", to: "/second/$1" },
});
check(
  "only the first matching rule contributes",
  api.applySieveRulesAll("https://a/o/5", "https://a/", orderedRules),
  ["/first/5.a", "/first/5.b"]
);

// --- link filter + no-match contract --------------------------------------

const linkRules = api.parseSieve({
  Scoped: { img: "/p/(\\d+)", to: "/full/$1", link: "example\\.com" },
});
check("link filter blocks other pages", api.applySieveRulesAll("https://a/p/1", "https://other/", linkRules), []);
check("link filter allows matching page", api.applySieveRulesAll("https://a/p/1", "https://example.com/", linkRules), ["/full/1"]);
check("no match returns empty array", api.applySieveRulesAll("https://a/nope.jpg", "https://a/", linkRules), []);
check("no match returns null (single form)", api.applySieveRules("https://a/nope.jpg", "https://a/", linkRules), null);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
