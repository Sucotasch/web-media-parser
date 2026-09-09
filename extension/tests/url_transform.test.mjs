// P2-1 + P3-2 (extension twin) tests for applyUrlTransform / data: rejection.
// Run: node extension/tests/url_transform.test.mjs
// background.js is a service worker script (no exports), so we extract the
// function source and evaluate it in isolation — acceptable for pure functions.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");

// Extract the applyUrlTransform function source from background.js
const fnMatch = src.match(/function applyUrlTransform\([^)]*\) \{[\s\S]*?\n\}/);
if (!fnMatch) {
  console.error("FAIL: applyUrlTransform not found in background.js");
  process.exit(1);
}
const applyUrlTransform = new Function(`return (${fnMatch[0]})`)();

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

// P2-1: $10 must be substituted BEFORE $1 (descending order)
const groups10 = ["full", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j"];
check(
  "$10 substituted intact",
  applyUrlTransform("$10-$1", groups10),
  "j-a"
);
check(
  "$2 unaffected by descending order",
  applyUrlTransform("$2/$1", ["full", "x", "y"]),
  "y/x"
);
check(
  "$& full match still works",
  applyUrlTransform("https://cdn.example/$&", ["abc123"]),
  "https://cdn.example/abc123"
);
check(
  "undefined group placeholder left as-is",
  applyUrlTransform("$1-$3", ["full", "a"]),
  "a-$3"
);
check(
  "null template returns null",
  applyUrlTransform(null, ["x"]),
  null
);

// P3-2 (extension twin): data: results must be detectable so the caller can
// skip the fetch. The caller guards with startsWith("data:") — verify the
// canonical data-template form produces exactly that prefix.
const dataResult = applyUrlTransform("data:,$&", ["abc123"]);
check(
  "data: template produces data: result (caller rejects it)",
  typeof dataResult === "string" && dataResult.startsWith("data:"),
  true
);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
