// Selection model test (F-07 follow-up).
//
// The popup used to decide what was selected by counting `:checked` checkboxes
// in the DOM, while painting only RENDER_LIMIT rows — so on a 1439-item page
// "Select all" selected 300 and the other 1139 could not be sent or saved at
// all. Selection is now model-based: every item matching the filters is
// selected unless the user unchecked its row, whether that row is painted yet
// or not. This suite pins that property and the shape of the code that relies
// on it (bulk actions must not read the DOM for the selection).
//
// Run: node extension/tests/selection_model.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "popup", "popup.js"), "utf8");

function extractFn(source, marker, label) {
  const start = source.indexOf(marker);
  if (start < 0) {
    console.error(`FAIL: ${label} not found`);
    process.exit(1);
  }
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  console.error(`FAIL: ${label} body could not be delimited`);
  process.exit(1);
}

// shared.js defines these two; they are injected rather than parsed so the test
// fails if the popup starts depending on anything else. "js-literal" is the
// inline-<script> source (JS-REL-EXT) — kept in sync with shared.js.
const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct", "sieve-to", "a-link", "linked-img", "js-literal"]);
const isNotMedia = (item) => !!item && item.mediaVerdict === "not-media";

const api = new Function(
  "FULLSIZE_SOURCES",
  "isNotMedia",
  `
  ${extractFn(src, "function extractDomain(", "extractDomain")}
  ${extractFn(src, "function mediaExtension(", "mediaExtension")}
  ${extractFn(src, "function matchesFilters(", "matchesFilters")}
  ${extractFn(src, "function filteredItems(", "filteredItems")}
  ${extractFn(src, "function selectedItems(", "selectedItems")}
  return { extractDomain, mediaExtension, matchesFilters, filteredItems, selectedItems };
`
)(FULLSIZE_SOURCES, isNotMedia);

const { filteredItems, selectedItems, mediaExtension } = api;

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
    console.log(`FAIL: ${name}\n  expected ${JSON.stringify(expected)}\n  actual   ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok: ${name}`);
  }
}

// The reported case: one page, one domain, one source, 1439 items.
const page = () => Array.from({ length: 1439 }, (_, i) => ({
  url: `https://gallery.example/full/${i}.jpg`,
  pageUrl: "https://gallery.example/post/1",
  source: "sieve-res",
  type: "image",
}));

const DOMAIN = "gallery.example";

{
  const items = page();
  const filtered = filteredItems(items, DOMAIN, "fullsize");
  check("all 1439 items survive the filters", filtered.length, 1439);
  check("nothing is filtered by a foreign domain", filtered.length,
    filteredItems(items, "other.example", "fullsize").length === 0 ? 1439 : -1);
}

{
  const items = page();
  const selected = selectedItems(items, DOMAIN, "fullsize", new Set());
  check("all 1439 are selected without a single row being painted", selected.length, 1439);
}

{
  const items = page();
  const deselected = new Set([items[7]]);
  const selected = selectedItems(items, DOMAIN, "fullsize", deselected);
  check("unchecking one row removes exactly one item", selected.length, 1438);
  check("the unchecked item is the one the user clicked", selected.includes(items[7]), false);
}

{
  const items = page();
  const deselected = new Set(items.filter((_, i) => i < 300));
  const selected = selectedItems(items, DOMAIN, "fullsize", deselected);
  check("unchecking the first 300 leaves the remaining 1139 selected", selected.length, 1139);
}

{
  const items = page();
  const deselected = new Set(items);
  check("deselecting everything selects nothing", selectedItems(items, DOMAIN, "fullsize", deselected).length, 0);
}

{
  const items = page();
  check("the thumbnail filter holds none of these fullsize items",
    filteredItems(items, DOMAIN, "thumbnail").length, 0);
}

{
  const items = page();
  items[0].mediaVerdict = "not-media";
  check("a proven non-media URL is not part of Full size",
    filteredItems(items, DOMAIN, "fullsize").length, 1438);
  items[1].mediaVerdict = "unknown";
  check("an unconfirmed URL stays (hotlink-protected media is real)",
    filteredItems(items, DOMAIN, "fullsize").length, 1438);
}

// --- FMT-1: file-type filter -----------------------------------------------
//
// A gallery that ships an original AND a web variant of every picture (1407
// png + 1407 webp on ai-costume-gallery.pages.dev) must be reducible to one
// type from the popup — without changing the model for anyone who leaves the
// filter on "All formats".
const MIXED = [
  { url: "https://gallery.example/png/a.png", source: "js-literal", type: "image" },
  { url: "https://gallery.example/img/a.webp?sig=1", source: "js-literal", type: "image" },
  { url: "https://gallery.example/img/b.PNG", source: "js-literal", type: "image" },
  { url: "https://gallery.example/media/noext", source: "js-literal", type: "image" },
];

{
  check("no format chosen = everything", filteredItems(MIXED, DOMAIN, "fullsize").length, 4);
  check("the empty format argument means the same thing",
    filteredItems(MIXED, DOMAIN, "fullsize", "").length, 4);
  check(".png keeps both the lower- and upper-case names",
    filteredItems(MIXED, DOMAIN, "fullsize", ".png").map((i) => i.url),
    ["https://gallery.example/png/a.png", "https://gallery.example/img/b.PNG"]);
  check(".webp keeps the one web variant (query stripped)",
    filteredItems(MIXED, DOMAIN, "fullsize", ".webp").map((i) => i.url),
    ["https://gallery.example/img/a.webp?sig=1"]);
  check("an extension-less URL is only listed under All formats",
    filteredItems(MIXED, DOMAIN, "fullsize", ".webp").some((i) => i.url.endsWith("noext")), false);
  check("the selection model honours the type too",
    selectedItems(MIXED, DOMAIN, "fullsize", new Set(), ".png").length, 2);
  check("mediaExtension ignores the query and lowercases",
    mediaExtension("https://gallery.example/img/X.WebP?v=2"), ".webp");
  check("mediaExtension reports no type for an extension-less path",
    mediaExtension("https://gallery.example/media/noext"), "");
}

// --- structural guards -----------------------------------------------------

check("the list is no longer truncated by a render cap",
  /RENDER_LIMIT|capForRender/.test(src), false);
check("the 'narrow the filter to reach them' dead end is gone",
  /narrow the domain or source filter to reach them/.test(src), false);
check("Select all updates the model", /filtered\.forEach\(\(item\) => \(select \? deselected\.delete\(item\) : deselected\.add\(item\)\)\);/.test(src), true);
check("Send to App builds its payload from the model",
  /const selected = selectedItems\(mediaItems, activeDomainFilter, activeSourceFilter, deselected, activeFormatFilter\)/.test(src), true);
check("Save in Chrome builds its payload from the model",
  /const selected = selectedItems\(mediaItems, activeDomainFilter, activeSourceFilter, deselected, activeFormatFilter\)\s*\n\s*\.map\(\(item\) => \(\{/.test(src), true);
check("Copy links exports the model, not the painted rows",
  /const selected = selectedItems\(mediaItems, activeDomainFilter, activeSourceFilter, deselected, activeFormatFilter\);/.test(src), true);
check("no bulk action reads the selection back from the DOM",
  /querySelectorAll\("input\[type='checkbox'\]:checked"\)/.test(src), false);
check("the counter counts the model",
  /const checkedCount = selectedItems\(mediaItems, activeDomainFilter, activeSourceFilter, deselected, activeFormatFilter\)\.length;/.test(src), true);
check("the type filter is threaded through every model call",
  (src.match(/activeFormatFilter/g) || []).length >= 10, true);

if (failures) {
  console.log(`\n${failures} FAILURE(S)`);
  process.exit(1);
}
console.log("\nALL PASS");
