// Errors.txt follow-up: the popup must show dimensions only when they
// describe the FILE at item.url. Recorded dims come from the DOM element —
// for transformed URLs (sieve / [Resize] upgrade) and non-img sources
// (a-link / link-direct / sieve-res) they are the THUMBNAIL's dims (192×240
// preview vs real 1110×1375 file) and must not be displayed. The content
// script records natural dimensions only (0 = unknown, never the layout
// fallback). Run: node extension/tests/display_dims.test.mjs

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

// --- 1. shouldShowDims from popup.js (real source) ---
const popupSrc = readFileSync(join(here, "..", "popup", "popup.js"), "utf8");
const fnMatch = popupSrc.match(/function shouldShowDims\(item\) \{[^]*?\n\}/);
if (!fnMatch) {
  console.error("FAIL: shouldShowDims not found in popup.js");
  process.exit(1);
}
const shouldShowDims = new Function(`return (${fnMatch[0]})`)();

// --- 2. scanPageMedia + parseSrcset from content_script.js (real source) ---
// Brace counting must skip braces inside strings/regexes/comments — the junk
// filter contains regex literals with braces (e.g. /\$\{/i), which naive
// counting would misread. Mini-lexer keeps it exact.
const csSrc = readFileSync(join(here, "..", "content_script.js"), "utf8");
function extractFn(name, src) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} not found`);
  const REGEX_PREV = "=(:,[!&|?{};+-";
  let depth = 0, i = start, end = src.length;
  let mode = "code", strCh = "", esc = false; // code | line | block | str | regex
  while (i < src.length) {
    const ch = src[i], nx = src[i + 1];
    if (mode === "line") { if (ch === "\n") mode = "code"; i++; continue; }
    if (mode === "block") { if (ch === "*" && nx === "/") { mode = "code"; i += 2; } else i++; continue; }
    if (mode === "str") {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === strCh) mode = "code";
      i++; continue;
    }
    if (mode === "regex") {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === "/") mode = "code";
      i++; continue;
    }
    if (ch === "/" && nx === "/") { mode = "line"; i += 2; continue; }
    if (ch === "/" && nx === "*") { mode = "block"; i += 2; continue; }
    if (ch === '"' || ch === "'" || ch === "`") { mode = "str"; strCh = ch; i++; continue; }
    if (ch === "/") {
      let j = i - 1;
      while (j >= start && /\s/.test(src[j])) j--;
      if (j >= start && REGEX_PREV.includes(src[j])) { mode = "regex"; i++; continue; }
      i++; continue;
    }
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
    i++;
  }
  if (end <= start) throw new Error(`${name} body not extracted`);
  return src.slice(start, end);
}
const parseSrcset = new Function(`return (${extractFn("parseSrcset", csSrc)})`)();
const scanPageMedia = new Function("parseSrcset", `return (${extractFn("scanPageMedia", csSrc)})`)(parseSrcset);

// --- Fake DOM (minimal, mirrors what scanPageMedia touches) ---
function fakeImg(o) {
  return {
    currentSrc: o.currentSrc, src: o.src, srcset: o.srcset || "",
    naturalWidth: o.naturalWidth || 0, naturalHeight: o.naturalHeight || 0,
    width: o.width || 0, height: o.height || 0, alt: o.alt || "",
    getAttribute: () => null, closest: () => null,
  };
}
function fakeDoc(imgs) {
  return {
    querySelectorAll(sel) {
      if (sel === "img") return imgs;
      return []; // anchors / video / picture / css / meta
    },
  };
}
const BASE = "https://colorsuper.com/products/palette-triple-pink-brazilian-bikini";

// A. Loaded full-size img: natural dims recorded, dims shown
{
  const res = scanPageMedia(fakeDoc([fakeImg({
    src: "https://cdn.shopify.com/s/files/1/0597/0042/files/real.jpg?v=1",
    naturalWidth: 1110, naturalHeight: 1375,
  })]), BASE);
  const item = res.media[0];
  check("loaded img: natural width recorded", item && item.width, 1110);
  check("loaded img: natural height recorded", item && item.height, 1375);
  check("loaded img: dims shown", shouldShowDims(item), true);
}

// B. Loaded 192×240 preview (width=192 param): dims match THAT url — but the
// scan pipeline later [Resize]-upgrades it (transformed=true) which hides dims.
{
  const res = scanPageMedia(fakeDoc([fakeImg({
    src: "https://cdn.shopify.com/s/files/1/0597/0042/products/thing.jpg?v=1&width=192",
    naturalWidth: 192, naturalHeight: 240,
  })]), BASE);
  const item = res.media[0];
  check("preview img: natural width recorded", item && item.width, 192);
  check("preview img (untransformed): dims shown", shouldShowDims(item), true);
  const upgraded = { ...item, url: item.url.replace("&width=192", "&"), transformed: true, source: "sieve-to" };
  check("preview after [Resize] upgrade: dims hidden", shouldShowDims(upgraded), false);
}

// C. Unloaded img (naturalWidth 0, layout 192): recorded 0 → dims NOT shown
{
  const res = scanPageMedia(fakeDoc([fakeImg({
    src: "https://cdn.shopify.com/s/files/1/0597/0042/products/lazy.jpg?v=1",
    width: 192, height: 240, // layout only
  })]), BASE);
  const item = res.media[0];
  check("unloaded img: no layout fallback in width", item && item.width, 0);
  check("unloaded img: dims hidden", shouldShowDims(item), false);
}

// D. Tiny unloaded layout element (16×16 icon/spacer): dropped entirely
{
  const res = scanPageMedia(fakeDoc([fakeImg({
    src: "https://cdn.shopify.com/s/files/1/0597/0042/files/icon.png?v=1",
    width: 16, height: 16, naturalWidth: 0, naturalHeight: 0,
  })]), BASE);
  check("tiny unloaded layout element dropped", res.media.length, 0);
}

// E. Tiny LOADED icon (natural 16×16): dropped by layout backstop
{
  const res = scanPageMedia(fakeDoc([fakeImg({
    src: "https://cdn.shopify.com/s/files/1/0597/0042/files/icon.png?v=1",
    naturalWidth: 16, naturalHeight: 16,
  })]), BASE);
  check("tiny loaded icon dropped", res.media.length, 0);
}

// F. Real file displayed small: naturalWidth 1110 in a 32px <img> — kept
{
  const res = scanPageMedia(fakeDoc([fakeImg({
    src: "https://cdn.shopify.com/s/files/1/0597/0042/products/big.jpg?v=1",
    naturalWidth: 1110, naturalHeight: 1375, width: 32, height: 39,
  })]), BASE);
  check("real file displayed small kept", res.media.length, 1);
  check("real file displayed small: dims shown", shouldShowDims(res.media[0]), true);
}

// G. Non-img sources never show dims, even with recorded dims
check("a-link dims hidden", shouldShowDims({ source: "a-link", width: 192, height: 240 }), false);
check("link-direct dims hidden", shouldShowDims({ source: "link-direct", width: 192, height: 240 }), false);
check("sieve-res dims hidden", shouldShowDims({ source: "sieve-res", width: 192, height: 240 }), false);
check("sieve-to dims hidden", shouldShowDims({ source: "sieve-to", width: 192, height: 240 }), false);
check("transformed img dims hidden", shouldShowDims({ source: "img", transformed: true, width: 1110, height: 1375 }), false);
check("zero-width dims hidden", shouldShowDims({ source: "img", width: 0, height: 0 }), false);
check("null item hidden", shouldShowDims(null), false);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");