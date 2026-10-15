// A-10 / A-12 / NF-2 / NF-7: the shared substitution vectors, run against
// sieve.js. `extension/tests/vectors/substitution.json` is the same file the
// pytest suite feeds to site_pattern_manager.get_substitution, so the two
// implementations cannot drift apart silently again.
//
// Every expected value is re-derived here with String.prototype.replace (V8 =
// the ECMAScript spec) BEFORE the implementation is checked: a hand-edited or
// stale fixture fails loudly instead of blessing a wrong implementation.
//
// Run: node extension/tests/substitution.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const sieveSrc = readFileSync(join(here, "..", "sieve.js"), "utf8");
const vectors = JSON.parse(
  readFileSync(join(here, "vectors", "substitution.json"), "utf8")
);

const api = new Function(
  "console",
  `${sieveSrc}
  return { getSubstitution, expandVariants, applySubstitutionAll, normalizeVariant, matchRuleUrl };
`
)(console);

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

// --- the fixture is anchored to the spec ----------------------------------

for (const c of vectors.substitution) {
  const re = new RegExp(c.regex, c.flags || "");
  const m = c.url.match(re);
  if (!m) {
    failures++;
    console.error(`FAIL: fixture regex does not match its own url: ${c.name}`);
    continue;
  }
  // GetSubstitution returns the REPLACEMENT text; String.replace splices it
  // into the match span. Rebuilding the native result that way anchors
  // `expected` to V8 even for vectors whose match is only part of the subject.
  const native = c.url.replace(re, c.template);
  const rebuilt = c.url.slice(0, m.index) + c.expected
    + c.url.slice(m.index + m[0].length);
  check(`spec anchor (native replace): ${c.name}`, rebuilt, native);
}

// --- getSubstitution matches the spec -------------------------------------

for (const c of vectors.substitution) {
  const re = new RegExp(c.regex, c.flags || "");
  const match = c.url.match(re);
  check(`getSubstitution: ${c.name}`, api.getSubstitution(c.template, match, c.url), c.expected);
}

// --- expansion ------------------------------------------------------------

for (const c of vectors.expansion) {
  check(`applySubstitutionAll: ${c.name}`, api.applySubstitutionAll("", c.match, c.template), c.expected);
}

// --- the two bugs the vectors exist for -----------------------------------

// NF-7: `$11200x1200` with 2 groups used to collapse to "x1200" (the digits
// were read greedily, the group lookup failed and `|| ""` erased it).
check(
  "NF-7 regression: long digit run keeps its literal tail",
  api.getSubstitution("$11200x1200", "12".match(/^(\d)(\d)$/), "12"),
  "11200x1200"
);

// A-12: `$&` used to be left in the output verbatim (only $N was handled).
check(
  "A-12 regression: $& is the whole match",
  api.getSubstitution("https://cdn/$&", "abc123".match(/(\w+)/), "abc123"),
  "https://cdn/abc123"
);

// --- matching both URL forms and normalizing the result (A-09 / NF-1) -----

check(
  "A-09: a ^-anchored rule matches the scheme-stripped form",
  api.matchRuleUrl(/^\d+\.cdn\/img\/(\d+)\.jpg$/i, "https://42.cdn/img/7.jpg")?.[1],
  "7"
);
check(
  "A-09: the full URL still matches first",
  api.matchRuleUrl(/^https:\/\/a\/(\d+)$/i, "https://a/9")?.[1],
  "9"
);
check(
  "A-09: no match returns null",
  api.matchRuleUrl(/^nope$/i, "https://a/9"),
  null
);
check(
  "NF-1: protocol-relative takes the page's scheme",
  api.normalizeVariant("//cdn.example/x.jpg", "http://page.example/post"),
  "http://cdn.example/x.jpg"
);
check(
  "NF-1: a root-relative variant is resolved against the page",
  api.normalizeVariant("/x.jpg", "https://page.example/p/1"),
  "https://page.example/x.jpg"
);
check(
  "NF-1: an absolute variant is untouched",
  api.normalizeVariant("https://cdn/x.jpg", "https://page.example/"),
  "https://cdn/x.jpg"
);
check(
  "NF-1: a data: result is not turned into a URL",
  api.normalizeVariant("data:,x", "https://page.example/"),
  "data:,x"
);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
