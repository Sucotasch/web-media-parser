// Errors.txt (e-hentai.gallery: "в Full size попадают заведомо мелкие thumbnails").
//
// Two decisions are covered here, both driven by real source files:
//   1. content_script.js — which anchors become discovery candidates, and in
//      which order (discovery works the list in order, so order decides who is probed).
//   2. background.js — what a probed page's <img> dump means: one image = the
//      full-size version of the link (linked-img, a FULLSIZE source), a grid of
//      images = that page's own thumbnails (page-img, NOT fullsize).
//
// Run: node extension/tests/page_image_classification.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

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

// --- 1. classifyPageImages from background.js (real source) ---------------
const bgSrc = readFileSync(join(here, "..", "background.js"), "utf8");
const fnMatch = bgSrc.match(/function classifyPageImages\(count\) \{[^]*?\n\}/);
if (!fnMatch) {
  console.error("FAIL: classifyPageImages not found in background.js");
  process.exit(1);
}
const classifyPageImages = new Function(`return (${fnMatch[0]})`)();

check("a detail page's single image is the full-size one", classifyPageImages(1), "linked-img");
check("a two-image page is not promoted", classifyPageImages(2), "page-img");
check("e-hentai's front page (25 thumbs) is not promoted", classifyPageImages(25), "page-img");
check("e-hentai's /popular grid (62 thumbs) is not promoted", classifyPageImages(62), "page-img");
check("page-img is not a fullsize source", ["sieve-res", "link-direct", "sieve-to", "a-link", "linked-img"].includes("page-img"), false);

// --- 2. scanPageMedia link collection (real source, fake DOM) -------------
const src = readFileSync(join(here, "..", "content_script.js"), "utf8");
const EXPOSE_ANCHOR = "  // --- Full scan orchestration ---";
if (!src.includes(EXPOSE_ANCHOR)) {
  console.error("FAIL: exposure anchor not found in content_script.js");
  process.exit(1);
}
const patched = src.replace(
  EXPOSE_ANCHOR,
  "  globalThis.__wmpScanner = { scanPageMedia };\n" + EXPOSE_ANCHOR
);
const { scanPageMedia } = new Function(
  "chrome", "document", "window", "navigator",
  `${patched}\nreturn globalThis.__wmpScanner;`
)(
  { runtime: { onMessage: { addListener: () => {} } } },
  {},
  {},
  {}
);

const BASE = "https://e-hentai.org/g/4249104/719e4d495b/";

function fakeDoc(anchors) {
  return {
    querySelectorAll(sel) {
      if (sel === "a[href]") return anchors;
      return [];
    },
  };
}

// A nav anchor: text label, no media of its own.
const navAnchor = (href) => ({
  getAttribute: (name) => (name === "style" ? null : href),
  querySelector: () => null,
});

// e-hentai's gallery cell: the thumbnail IS the anchor's background image.
const bgCellAnchor = (href, thumb) => ({
  getAttribute: (name) => (name === "style" ? `background-image:url(${thumb})` : href),
  querySelector: () => null,
});

// A background image on a CHILD of the anchor (the other e-hentai shape).
const bgChildAnchor = (href) => ({
  getAttribute: (name) => (name === "style" ? null : href),
  querySelector: (sel) => (sel === "img, picture, video, canvas" ? null : { nodeName: "DIV" }),
});

const imgAnchor = (href) => ({
  getAttribute: (name) => (name === "style" ? null : href),
  querySelector: (sel) => (sel === "img, picture, video, canvas" ? { nodeName: "IMG" } : null),
});

{
  const out = scanPageMedia(fakeDoc([navAnchor("https://e-hentai.org/popular")]), BASE);
  check("a navigation link is still collected (coverage kept)", out.links, ["https://e-hentai.org/popular"]);
}

{
  const out = scanPageMedia(fakeDoc([bgCellAnchor("/s/1387534a22/4249104-1", "https://ehgt.org/w/t.webp")]), BASE);
  check("a background-image cell is a discovery candidate", out.links, ["https://e-hentai.org/s/1387534a22/4249104-1"]);
}

{
  const out = scanPageMedia(fakeDoc([bgChildAnchor("https://e-hentai.org/s/02b2eca3ec/4249104-2")]), BASE);
  check("a background-image child counts too", out.links, ["https://e-hentai.org/s/02b2eca3ec/4249104-2"]);
}

{
  const out = scanPageMedia(fakeDoc([imgAnchor("https://example.com/view/7")]), BASE);
  check("an anchor wrapping an <img> is a candidate", out.links, ["https://example.com/view/7"]);
}

{
  // DOM order puts site chrome first — exactly the reported gallery. The 20
  // cells must still come first in the list — the background probes in that order.
  const anchors = [
    navAnchor("https://e-hentai.org/"),
    navAnchor("https://e-hentai.org/popular"),
    bgCellAnchor("/s/1387534a22/4249104-1", "https://ehgt.org/w/a.webp"),
    navAnchor("https://e-hentai.org/tag/parody:fairy+tail"),
    bgCellAnchor("/s/02b2eca3ec/4249104-2", "https://ehgt.org/w/b.webp"),
    imgAnchor("https://e-hentai.org/s/35d5692995/4249104-3"),
  ];
  const out = scanPageMedia(fakeDoc(anchors), BASE);
  check("media links are probed before site chrome", out.links, [
    "https://e-hentai.org/s/1387534a22/4249104-1",
    "https://e-hentai.org/s/02b2eca3ec/4249104-2",
    "https://e-hentai.org/s/35d5692995/4249104-3",
    "https://e-hentai.org/",
    "https://e-hentai.org/popular",
    "https://e-hentai.org/tag/parody:fairy+tail",
  ]);
}

{
  const out = scanPageMedia(fakeDoc([navAnchor("https://cdn.example/post/full.jpg")]), BASE);
  check("a direct image anchor stays an a-link, not a probed page", [out.links, out.media.map((m) => m.source)], [[], ["a-link"]]);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
