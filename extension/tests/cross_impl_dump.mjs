// Helper for tests/test_sieve_cross_impl.py (A-10/A-12/NF-2/NF-7).
//
// For every rule in the bundled sieve it applies sieve.js's spec substitution
// and its #ext#/multi-line expansion to a synthetic match, and prints the
// result as JSON. The pytest side builds the SAME synthetic match, runs
// site_pattern_manager.get_substitution + _expand_variants, and compares:
// two live implementations, one rule set, zero allowed divergences.
//
// Usage: node extension/tests/cross_impl_dump.mjs <sieve.json> <rule-count> [group-counts.json]
// The group counts are passed in from Python (it compiles the `img` regex to
// learn them; counting groups twice would invite a second dialect).

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const sieveSrc = readFileSync(join(here, "..", "sieve.js"), "utf8");
const api = new Function("console", `${sieveSrc}
  return { parseSieve, getSubstitution, expandVariants };
`)(console);

const sievePath = process.argv[2];
const countsPath = process.argv[3];
const groupCounts = JSON.parse(readFileSync(countsPath, "utf8"));

const raw = JSON.parse(readFileSync(sievePath, "utf8"));
const rules = api.parseSieve(raw);

const out = {};
for (const rule of rules) {
  if (rule.isJS) continue;                        // needs Deno/DOM, not a string transform
  const count = groupCounts[rule.name];
  if (typeof count !== "number") continue;        // Python could not compile the regex
  // Same synthetic match the Python side builds: whole match, then G1..Gn.
  const match = ["WHOLE"];
  for (let i = 1; i <= count; i++) match.push(`G${i}`);
  match.index = 0;
  match.input = "WHOLE";

  const substituted = api.getSubstitution(rule.toPattern, match, "WHOLE");
  out[rule.name] = {
    template: rule.toPattern,
    groups: count,
    substituted,
    variants: api.expandVariants(substituted),
  };
}

console.log(JSON.stringify(out));
