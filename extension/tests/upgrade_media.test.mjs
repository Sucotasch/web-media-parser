// R-E-6 / R-E-4 / R-E-13 tests for background.js upgradeMediaList(),
// shared.js FULLSIZE_SOURCES and loadSieveRules().
//
// background.js is a service worker script (no exports): the two functions are
// extracted by brace counting and called with their dependencies injected.
//
// Run: node extension/tests/upgrade_media.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const bg = readFileSync(join(here, "..", "background.js"), "utf8");
const shared = readFileSync(join(here, "..", "shared.js"), "utf8");
const updaterSrc = readFileSync(join(here, "..", "sieve_updater.js"), "utf8");

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

// --- R-E-4: linked-img is a fullsize source ------------------------------

const FULLSIZE_SOURCES = new Function(`${shared}\nreturn FULLSIZE_SOURCES;`)();
check(
  "discovery fallback items count as fullsize",
  FULLSIZE_SOURCES.has("linked-img"),
  true
);
for (const source of ["sieve-res", "link-direct", "sieve-to", "a-link"]) {
  check(`kept fullsize source ${source}`, FULLSIZE_SOURCES.has(source), true);
}
check("a plain thumbnail source is not fullsize", FULLSIZE_SOURCES.has("img"), false);

// --- R-E-6: both media paths share one pipeline --------------------------

check(
  "upgradeMediaList is called by exactly the two media paths",
  (bg.match(/= await upgradeMediaList\(/g) || []).length,
  2
);
check(
  "no path left with an inline applyResizeUpgrade loop",
  /for \(const item of media\) \{\s*if \(item && item\.source !==/.test(bg),
  false
);

// --- upgradeMediaList behaviour ------------------------------------------

const upgradeSrc = extractFn(bg, "async function upgradeMediaList(media, pageUrl) {", "upgradeMediaList");

function makeUpgrade({ rules = null, stored = rules ? JSON.stringify(rules) : null, resize = () => null } = {}) {
  const warnings = [];
  const chrome = {
    storage: { local: { get: async () => (stored === null ? {} : { sieveRules: stored }) } },
  };
  const parseSieve = (data) => (
    Object.entries(data).map(([name, r]) => ({ name, imgRegex: new RegExp(r.img, "i"), toPattern: r.to, isJS: false, linkRegex: null }))
  );
  const applySieveRulesAll = (url, pageUrl, parsed) => {
    for (const rule of parsed) {
      const match = url.match(rule.imgRegex);
      if (!match) continue;
      const paths = rule.toPattern.match(/#([^#]+)#/);
      if (paths) return paths[1].trim().split(/\s+/).map((ext) => rule.toPattern.replace(/#[^#]+#/, ext).replace(/\$1/g, match[1]));
      return [rule.toPattern.replace(/\$1/g, match[1])];
    }
    return [];
  };
  const fn = new Function(
    "chrome", "parseSieve", "applySieveRulesAll", "applyResizeUpgrade", "console",
    `return (${upgradeSrc})`
  )(chrome, parseSieve, applySieveRulesAll, resize, { warn: (...a) => warnings.push(a.join(" ")) });
  return { fn, warnings };
}

{
  const { fn, warnings } = makeUpgrade();
  const input = [{ url: "https://cdn/thumb/5.jpg", type: "image", source: "img" }];
  const out = await fn(input, "https://page/");
  check("untouched item in, same item out", out, input);
  check("same object identity when nothing applied", out[0], input[0]);
  check("no warnings", warnings, []);
}

{
  const { fn } = makeUpgrade({ rules: { R: { img: "/v/(\\d+)\\.jpg", to: "/v/$1.#jpg png#" } } });
  const out = await fn([{ url: "https://a/v/12.jpg", width: 100, source: "img" }], "https://a/");
  check("both variants become media items", out.map((m) => m.url), ["/v/12.jpg", "/v/12.png"]);
  check("variants keep the original url", out.map((m) => m.original_url), ["https://a/v/12.jpg", "https://a/v/12.jpg"]);
  check("variants are marked as sieve transforms", [...new Set(out.map((m) => m.source))], ["sieve-to"]);
  check("variant metadata is preserved", out[0].width, 100);
  check("input list is not mutated", out[0] === out[1], false);
}

{
  const { fn } = makeUpgrade({ rules: { R: { img: "/nomatch/", to: "/x/$1" } } });
  const out = await fn(
    [{ url: "https://cdn/shop/a.jpg?v=1&width=500", source: "img" }],
    "https://a/"
  );
  check("single item stays single without a resize match", out.length, 1);
  check("fallback path leaves the url alone", out[0].url, "https://cdn/shop/a.jpg?v=1&width=500");
}

{
  const { fn } = makeUpgrade({
    rules: { R: { img: "/nomatch/", to: "/x/$1" } },
    resize: (url) => (url.includes("width=500") ? url.replace("&width=500", "&") : null),
  });
  const out = await fn([{ url: "https://cdn/shop/a.jpg?v=1&width=500", source: "img" }], "https://a/");
  check("native resize fallback still strips params", out[0].url, "https://cdn/shop/a.jpg?v=1&");
  check("fallback items are marked transformed", [out[0].transformed, out[0].source], [true, "sieve-to"]);
}

{
  const { fn } = makeUpgrade({ stored: "{ this is not json" });
  const out = await fn([{ url: "https://cdn/a.jpg", source: "img" }], "https://a/");
  check("a corrupted stored rule set does not break the scan", out.length, 1);
}

{
  const { fn } = makeUpgrade({ rules: { R: { img: "/x/", to: "/y/$1" } } });
  const input = [
    { url: "https://a/sieve-res/1.jpg", source: "sieve-res" },
    { url: "https://a/sieve-to/2.jpg", source: "sieve-to" },
    { url: "", source: "img" },
    null,
  ];
  const out = await fn(input, "https://a/");
  check("already-upgraded and invalid entries pass through", out.length, 2);
  check("sieve items are not re-transformed", out[0] === input[0] && out[1] === input[1], true);
}

// --- R-E-13: a bundle bump must merge, not overwrite ---------------------

const loadSrc = extractFn(bg, "async function loadSieveRules(force = false) {", "loadSieveRules");
const updater = new Function("self", "chrome", "fetch", "AbortSignal", "setTimeout",
  `${updaterSrc}\nreturn self.WMPSieveUpdater;`
)({}, { storage: { local: null } }, async () => ({ status: 304 }), AbortSignal, (fn) => fn());

async function runLoadSieveRules(stored) {
  const writes = [];
  const chrome = {
    runtime: { getURL: (p) => `chrome-extension://id/${p}` },
    storage: {
      local: {
        get: async () => stored,
        set: async (patch) => { writes.push(patch); },
      },
    },
  };
  const bundle = { Fresh: { link: "f", img: "y" }, Keep: { link: "new", img: "x" } };
  const fetchImpl = async () => ({ status: 200, ok: true, json: async () => bundle });
  // SIEVE_VERSION is a module-level const in background.js — injected.
  const fn = new Function(
    "chrome", "fetch", "self", "console", "SIEVE_VERSION",
    `return (${loadSrc})`
  )(chrome, fetchImpl, { WMPSieveUpdater: updater }, console, "2026.07.15");
  await fn(true);
  return writes[0];
}

{
  const patch = await runLoadSieveRules({
    sieveRules: JSON.stringify({
      _userRule: { link: "u", img: "z" },
      Keep: { link: "old", img: "x", off: 1 },
      Gone: { link: "g", img: "q" },
    }),
    sieveVersion: "old-version",
  });
  const rules = JSON.parse(patch.sieveRules);
  check("bundle bump keeps the user rule", rules._userRule.link, "u");
  check("bundle bump keeps the off flag", rules.Keep.off, 1);
  check("bundle bump keeps the removed-upstream ghost", rules.Gone.off, 1);
  check("bundle bump still installs the new bundle", rules.Fresh.link, "f");
  check("bundle bump updates the version", patch.sieveVersion, "2026.07.15");
  check("installed rules are written as a string", typeof patch.sieveRules, "string");
}

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
