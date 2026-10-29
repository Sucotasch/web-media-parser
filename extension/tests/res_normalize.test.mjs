// E-04 (extension twin) tests for normalizeResMatch.
// Run: node extension/tests/res_normalize.test.mjs
//
// background.js is a service worker script (no exports), so the function is
// extracted and evaluated in isolation — same approach as url_transform.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");

const fnMatch = src.match(/function normalizeResMatch\([^)]*\) \{[\s\S]*?\n\}/);
if (!fnMatch) {
  console.error("FAIL: normalizeResMatch not found in background.js");
  process.exit(1);
}
const normalizeResMatch = new Function(`return (${fnMatch[0]})`)();

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

// The defect: `https:` + relative produced an opaque, unfetchable URL.
check(
  "relative match resolves against the fetched page",
  normalizeResMatch("images/big.jpg", "https://host.example/gallery/1"),
  "https://host.example/gallery/images/big.jpg"
);
check(
  "root-relative match resolves to the host",
  normalizeResMatch("/images/big.jpg", "https://host.example/gallery/1"),
  "https://host.example/images/big.jpg"
);
check(
  "protocol-relative match keeps https (desktop parity)",
  normalizeResMatch("//cdn.example/big.jpg", "http://host.example/"),
  "https://cdn.example/big.jpg"
);
check(
  "absolute match untouched",
  normalizeResMatch("http://cdn.example/big.jpg", "https://host.example/"),
  "http://cdn.example/big.jpg"
);
check("whitespace is trimmed", normalizeResMatch("  https://cdn/x.jpg  ", ""), "https://cdn/x.jpg");
check("empty match yields empty string", normalizeResMatch("", "https://host/"), "");

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
