// R-E-7 (NEW-1/NEW-2) + R-E-3 tests for the content script scanner.
//
// scanPageMedia/parseSrcset live inside content_script.js's IIFE, so they are
// extracted by brace counting and driven with a MINIMAL fake DOM: the point is
// the scanner's own decisions (scheme filter, srcset priority, size backstop),
// not the browser's parsing.
//
// Run: node extension/tests/content_scan.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "content_script.js"), "utf8");

// The scanner lives inside the content script's IIFE, and its source contains
// a regex with an unmatched brace (/\$\{/) — brace counting is unreliable
// there. Instead the file is evaluated WHOLE (with stub globals) after
// injecting one line that exposes the two functions; a syntax error anywhere
// in content_script.js therefore fails this suite loudly.
const EXPOSE_ANCHOR = "  // --- Full scan orchestration ---";
if (!src.includes(EXPOSE_ANCHOR)) {
  console.error("FAIL: exposure anchor not found in content_script.js");
  process.exit(1);
}
let listeners = 0;
const patched = src.replace(
  EXPOSE_ANCHOR,
  "  globalThis.__wmpScanner = { scanPageMedia, parseSrcset };\n" + EXPOSE_ANCHOR
);
const { scanPageMedia, parseSrcset } = new Function(
  "chrome", "document", "window", "navigator",
  `${patched}\nreturn globalThis.__wmpScanner;`
)(
  { runtime: { onMessage: { addListener: () => { listeners++; } } } },
  {},
  {},
  {}
);
if (typeof scanPageMedia !== "function" || listeners !== 1) {
  console.error("FAIL: content script did not load (scanner missing or no message listener)");
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

const BASE = "http://127.0.0.1:9811/index.html";
const DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAB4CAIAAAC2BqGFAAAAA";

function img(attrs) {
  return Object.assign({
    currentSrc: "", src: "", srcset: "", naturalWidth: 800, naturalHeight: 600,
    width: 300, height: 200, alt: "",
    getAttribute: () => null,
    closest: () => null,
  }, attrs);
}

function fakeDoc(imgs = [], anchors = [], sources = []) {
  return {
    querySelectorAll(sel) {
      if (sel === "img") return imgs;
      if (sel === "a[href]") return anchors;
      if (sel === "picture source") return sources;
      return [];
    },
  };
}

// An anchor stub with no media of its own: page_link() in content_script.js now
// asks an anchor whether it PRESENTS media (img child / background image), so the
// stub has to answer querySelector and a style lookup like a real element.
const anchor = (href) => ({
  getAttribute: (name) => (name === "style" ? null : href),
  querySelector: () => null,
});

// The reactor.cc markup (Errors.txt): an anchor that WRAPS the thumbnail image.
const imgAnchor = (href) => ({
  getAttribute: (name) => (name === "style" ? null : href),
  querySelector: (sel) => (sel === "img, picture, video, canvas" ? { tagName: "IMG" } : null),
});

// --- R-E-7 (NEW-1): a data: placeholder is not media ----------------------

{
  const out = scanPageMedia(fakeDoc([img({ src: DATA_URI })]), BASE);
  check("data: src is not collected", out.media.map((m) => m.url), []);
}

{
  const out = scanPageMedia(fakeDoc([img({ src: "http://127.0.0.1:9811/media/real.jpg" })]), BASE);
  check("http image is collected", out.media.map((m) => m.url), ["http://127.0.0.1:9811/media/real.jpg"]);
}

{
  const out = scanPageMedia(fakeDoc([], [anchor("data:text/html,<h1>x")]), BASE);
  check("data: links are not collected", out.links, []);
}

// --- R-E-7 (NEW-2) + R-E-3: srcset candidates -----------------------------

{
  const out = scanPageMedia(
    fakeDoc([img({ srcset: `${DATA_URI} 1x, http://127.0.0.1:9811/media/hi.jpg 600w` })]),
    BASE
  );
  check("truncated data: candidate never wins the srcset", out.media.map((m) => m.url), ["http://127.0.0.1:9811/media/hi.jpg"]);
}

{
  const out = scanPageMedia(fakeDoc([img({ srcset: `${DATA_URI} 1x` })]), BASE);
  check("a data:-only srcset yields nothing", out.media, []);
}

{
  const out = scanPageMedia(
    fakeDoc([img({ srcset: "http://127.0.0.1:9811/media/bare.jpg, http://127.0.0.1:9811/media/wide.jpg 600w" })]),
    BASE
  );
  check("bare-first srcset still picks the w candidate", out.media.map((m) => m.url), ["http://127.0.0.1:9811/media/wide.jpg"]);
}

// --- size backstop + relative links ---------------------------------------

{
  const out = scanPageMedia(fakeDoc([img({ src: "http://127.0.0.1:9811/media/tiny.jpg", naturalWidth: 1, naturalHeight: 1, width: 16, height: 16 })]), BASE);
  check("an icon-sized image is dropped", out.media, []);
}

{
  const out = scanPageMedia(fakeDoc([img({ src: "/media/relative.jpg" })]), BASE);
  check("relative src is resolved against the page", out.media.map((m) => m.url), ["http://127.0.0.1:9811/media/relative.jpg"]);
}

{
  const out = scanPageMedia(fakeDoc([], [anchor("/post.html"), anchor("javascript:void(0)")]), BASE);
  check("absolute http links only", out.links, ["http://127.0.0.1:9811/post.html"]);
}

// parseSrcset — the full matrix (ported from srcset_parse.test.mjs, which
// evaluated the function in isolation and could not see splitSrcsetCandidates).
check("rc: bare first candidate no longer wins", parseSrcset("c.jpg, a.jpg 600w, b.jpg 1.5x"), "a.jpg");
check("rc: w preferred over x", parseSrcset("b.jpg 1.5x, a.jpg 600w"), "a.jpg");
check("rc: largest w wins", parseSrcset("x.jpg 400w, y.jpg 800w"), "y.jpg");
check("rc: largest x wins", parseSrcset("b.jpg 1.5x, a.jpg 2x"), "a.jpg");
check("rc: descriptor order does not matter", parseSrcset("y.jpg 800w, x.jpg 400w"), "y.jpg");
check("rc: bare first candidate wins among bare", parseSrcset("c.jpg, a.jpg"), "c.jpg");
check("rc: single bare candidate", parseSrcset("only.jpg"), "only.jpg");
check("rc: single x candidate", parseSrcset("only.jpg 2x"), "only.jpg");
check("rc: x beats bare", parseSrcset("bare.jpg, small.jpg 1x"), "small.jpg");
check("rc: w beats x and bare", parseSrcset("bare.jpg, small.jpg 1x, big.jpg 1200w"), "big.jpg");
check("rc: empty srcset", parseSrcset(""), null);
check("rc: missing srcset", parseSrcset(undefined), null);
check("rc: whitespace and stray commas", parseSrcset("  , a.jpg 600w ,  "), "a.jpg");
check("parseSrcset: w beats x", parseSrcset("a.jpg 1.5x, b.jpg 600w"), "b.jpg");
check("parseSrcset: data: candidate skipped", parseSrcset("data:image/png;base64,iVBOR 1x, b.jpg 600w"), "b.jpg");
check("parseSrcset: all-data set is empty", parseSrcset("data:image/png;base64,iVBOR 1x"), null);
check("parseSrcset: data URI with commas stays one candidate", parseSrcset(`${DATA_URI} 1x, real.jpg 600w`), "real.jpg");

// --- Errors.txt (reactor.cc): junk patterns must not eat content filenames ---
//
// The page wraps each thumbnail in an anchor pointing straight at the fullsize
// file: <a href="//img10.reactor.cc/pics/post/full/Arrow-….jpeg"><img
// src="…/pics/post/Arrow-….jpeg"></a>. The old JUNK_PATTERNS held /\/arrow/i,
// tested against the WHOLE URL, so both URLs were dropped and the popup showed
// only unrelated linked-img hits from the link probe.
{
  const THUMB = "https://img10.reactor.cc/pics/post/Arrow-SG-Arrow-Marzetti-SG-%28TAM%29-"
    + "%D0%9A%D0%BB%D1%83%D0%B1-%D0%BB%D1%8E%D0%B1%D0%B8%D1%82%D0%B5%D0%BB%D0%B5%D0%B9-TAM-9489969.jpeg";
  const FULL = "https://img10.reactor.cc/pics/post/full/Arrow-SG-Arrow-Marzetti-SG-%28TAM%29-"
    + "%D0%9A%D0%BB%D1%83%D0%B1-%D0%BB%D1%8E%D0%B1%D0%B8%D1%82%D0%B5%D0%BB%D0%B5%D0%B9-TAM-9489969.jpeg";

  // The anchor href is protocol-relative in the real markup.
  const a = imgAnchor(FULL.replace("https:", "//"));
  const thumbnail = img({ src: THUMB, closest: (sel) => (sel === "a[href]" ? a : null) });
  const out = scanPageMedia(fakeDoc([thumbnail], [a]), "https://reactor.cc/post/6382634");

  check(
    "reactor.cc: the thumbnail AND the /full/ href are both collected",
    out.media.map((m) => [m.url, m.source]),
    [[THUMB, "img"], [FULL, "a-link"]]
  );
  check(
    "reactor.cc: the fullsize URL is not probed as a page",
    out.links,
    []
  );
}

// Real junk must still be dropped — the fix may not turn the filter off.
{
  const JUNK = [
    "http://127.0.0.1:9811/avatar/user1.png",
    "http://127.0.0.1:9811/logo.png",
    "http://127.0.0.1:9811/icons/arrow.png",
    "http://127.0.0.1:9811/media/arrow.png",
    "http://127.0.0.1:9811/img/spacer-2x2.gif",
    "http://127.0.0.1:9811/static/loading.gif",
    "http://127.0.0.1:9811/img/blank.png",
    "http://127.0.0.1:9811/ui/next.jpg",
    "http://127.0.0.1:9811/tools/1x1.png",
    "http://127.0.0.1:9811/badge.png",
    "http://127.0.0.1:9811/logo/2.png",
    "https://l-userpic.livejournal.com/123456/photo.jpg",
    "https://example.com/icons8-official-gmail.svg",
  ];
  for (const url of JUNK) {
    const out = scanPageMedia(fakeDoc([img({ src: url })]), BASE);
    check(`junk still dropped: ${url}`, out.media.map((m) => m.url), []);
  }
}

// Content filenames that merely CONTAIN a junk word must survive.
{
  const KEPT = [
    "http://127.0.0.1:9811/media/Play-Station-5.jpg",
    "http://127.0.0.1:9811/media/Search-and-rescue.jpg",
    "http://127.0.0.1:9811/gallery/photo-avatar-1.jpg",
    "http://127.0.0.1:9811/media/pixel-art-500.jpg",
    "http://127.0.0.1:9811/pics/post/%D0%BA%D0%BB%D1%83%D0%B1-%D0%BB%D1%8E%D0%B1%D0%B8%D1%82%D0%B5%D0%BB%D0%B5%D0%B9-9489969.jpeg",
    "http://127.0.0.1:9811/media/500.png",
  ];
  for (const url of KEPT) {
    const out = scanPageMedia(fakeDoc([img({ src: url })]), BASE);
    check(`content name kept: ${url}`, out.media.map((m) => m.url), [url]);
  }
}

// --- D-03: navigation links are capped, media links are not ---------------

{
  // 1000 navigation anchors (tag lists, pagination, archives) must not push a
  // real thumbnail link out of the discovery list. Cap = the desktop app's
  // DEFAULT_MAX_LINKS_PER_PAGE (200).
  const nav = [];
  for (let i = 0; i < 1000; i++) nav.push(anchor(`http://127.0.0.1:9811/tag/page/${i}`));
  const thumbAnchor = {
    getAttribute: (name) => (name === "style" ? null : "http://127.0.0.1:9811/gallery/999"),
    querySelector: (sel) => (sel === "img, picture, video, canvas" ? { tagName: "IMG" } : null),
  };
  const out = scanPageMedia(fakeDoc([], nav.concat([thumbAnchor])), BASE);
  check("D-03: navigation links are capped at 200", out.links.length, 201);
  check(
    "D-03: the media-bearing link from the END of the list survives — and leads",
    out.links[0],
    "http://127.0.0.1:9811/gallery/999"
  );
}

{
  // Control: media anchors are never capped, however many there are.
  const thumbs = [];
  for (let i = 0; i < 300; i++) {
    thumbs.push({
      getAttribute: (name) => (name === "style" ? null : `http://127.0.0.1:9811/g/${i}`),
      querySelector: (sel) => (sel === "img, picture, video, canvas" ? { tagName: "IMG" } : null),
    });
  }
  const out = scanPageMedia(fakeDoc([], thumbs), BASE);
  check("D-03 control: 300 media links are all kept", out.links.length, 300);
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
