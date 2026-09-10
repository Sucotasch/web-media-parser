// ERR-7 / Errors.txt #1: native [Resize] sieve-rule port.
// The sieve's [Resize] rule is a JS `to` — MV3 CSP blocks new Function in
// the SW, so JS rules silently never ran and generic CDN resize params
// (Shopify &width=500, Cloudflare ?w=, ?h=, ?resize=, ...) were never
// stripped (colorsuper.com case). applyResizeUpgrade is the pure-string
// equivalent. Run: node extension/tests/resize_upgrade.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "background.js"), "utf8");

const fnMatch = src.match(/function applyResizeUpgrade\(url\) \{[^]*?\n\}/);
if (!fnMatch) {
  console.error("FAIL: applyResizeUpgrade not found in background.js");
  process.exit(1);
}
const applyResizeUpgrade = new Function(`return (${fnMatch[0]})`)();

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

// colorsuper.com (Errors.txt #1): &width=500 → original URL (trailing &
// preserved, same as Imagus/Mod MD output)
check(
  "colorsuper &width=500 stripped",
  applyResizeUpgrade("https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&width=500"),
  "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&"
);
check(
  "colorsuper &width=2048 stripped",
  applyResizeUpgrade("https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&width=2048"),
  "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&"
);
// no resize param → untouched
check(
  "no width param returns null",
  applyResizeUpgrade("https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149"),
  null
);
// excluded hosts never stripped (same negative lookahead as the sieve)
check(
  "reddit excluded",
  applyResizeUpgrade("https://i.redd.it/abc123.jpg?width=500"),
  null
);
// non-resize CDN path untouched
check(
  "plain image path untouched",
  applyResizeUpgrade("https://img10.reactor.cc/pics/post/full/abc.jpg"),
  null
);
// multiple params stripped (w/h/quality)
check(
  "w/h/q all stripped",
  applyResizeUpgrade("https://cdn.example.com/img.jpg?w=100&h=200&q=80"),
  "https://cdn.example.com/img.jpg?"
);
// resize= (imgproxy style) stripped
check(
  "resize param stripped",
  applyResizeUpgrade("https://imgproxy.example.com/plain/img.jpg?resize=100x100"),
  "https://imgproxy.example.com/plain/img.jpg?"
);
// null / empty input
check(
  "null input returns null",
  applyResizeUpgrade(null),
  null
);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");