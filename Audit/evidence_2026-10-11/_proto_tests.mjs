// Prototype for R-T-1 (+R-E-2/R-E-3/R-E-9 gates). Extracts the pure functions
// from the real sources (same pattern the other extension tests use) and runs
// the DESIRED behaviour, so a red case = a real defect still present.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const MUTATE = process.env.MUTATE || "";

/** Extract a function source by name using brace matching. */
function extract(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found`);
  let i = src.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

function loadSieve() {
  let src = readFileSync(join(here, "extension", "sieve.js"), "utf8");
  if (MUTATE === "offguard") {
    // apply the *fix* — the off:1 test must flip from FAIL to PASS
    src = src.replace("    if (!img || !to) continue;",
                      "    if (rule.off) continue;\n    if (!img || !to) continue;");
  }
  if (MUTATE === "digit") {
    // break a PASSING case: $10 handling
    src = src.replace("/\\$(\\d+)/g", "/\\$(\\d)/g");
  }
  const body = [extract(src, "applySubstitution"), extract(src, "applySieveRules")]
    .join("\n").replace(/function executeSieveJS[\s\S]*?\n\}/, "");
  const parse = extract(src, "parseSieve");  // must use the (possibly mutated) src
  const fn = new Function(`${parse}\nconst executeSieveJS = () => null;\n${body}\nreturn { parseSieve, applySieveRules, applySubstitution };`);
  return fn();
}

function loadParseSrcset() {
  let src = readFileSync(join(here, "extension", "content_script.js"), "utf8");
  if (MUTATE === "srcset") {
    src = src.replace("if (bestType !== null && type !== bestType) return;", "");
  }
  const fn = new Function(`${extract(src, "parseSrcset")}\nreturn parseSrcset;`);
  return fn();
}

const { parseSieve, applySieveRules, applySubstitution } = loadSieve();
const parseSrcset = loadParseSrcset();

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

// --- applySubstitution / applySieveRules -----------------------------------
check("$10 before $1",
      applyUrl("$10-$1", ["full", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]), "j-a");
function applyUrl(pattern, groups) {
  return applySubstitution("https://x/y.jpg", groups, pattern);
}

const RULES = parseSieve({
  Site: { link: "example\\.com", img: "/thumb/(\\d+)\\.jpg", to: "https://cdn.example.com/full/$1.jpg" },
  Dead: { link: "example\\.com", img: "/thumb/(\\d+)\\.jpg", to: "https://cdn.example.com/off/$1.jpg", off: 1 },
});
check("string rule transforms",
      applySieveRules("https://example.com/thumb/42.jpg", "https://example.com/gallery", RULES),
      "https://cdn.example.com/full/42.jpg");
check("off:1 rule is skipped (R-E-9)",
      applySieveRules("https://example.com/thumb/42.jpg", "https://example.com/gallery",
                      parseSieve({ Dead: { img: "/thumb/(\\d+)\\.jpg", to: "https://evil/$1.jpg", off: 1 } })),
      null);
check("off:0 rule still applies",
      applySieveRules("https://example.com/thumb/7.jpg", "https://example.com/g",
                      parseSieve({ On: { img: "/thumb/(\\d+)\\.jpg", to: "https://cdn/$1.jpg", off: 0 } })),
      "https://cdn/7.jpg");

// --- #ext# variants (R-E-2) ------------------------------------------------
check("#ext# expands to every variant",
      applyVariants("https://cdn.example/#jpg png#"), 
      ["https://cdn.example/jpg", "https://cdn.example/png"]);
function applyVariants(url) {
  const out = [];
  // desired API: a list-returning transform; today only one variant is returned
  const single = applySieveRules(url, "https://example.com/p", parseSieve({
    V: { img: "^https://cdn\\.example/", to: "#jpg png#" },
  }));
  if (typeof single === "string") out.push(single);
  return out;
}

// --- parseSrcset (R-E-3) ---------------------------------------------------
check("bare first candidate must not hide 600w/1.5x",
      parseSrcset("c.jpg, a.jpg 600w, b.jpg 1.5x"), "a.jpg");
check("largest w wins", parseSrcset("a.jpg 300w, b.jpg 1200w, c.jpg 600w"), "b.jpg");
check("largest x wins", parseSrcset("a.jpg 1x, b.jpg 3x, c.jpg 2x"), "b.jpg");
check("bare only", parseSrcset("only.jpg"), "only.jpg");

if (failures > 0) {
  console.error(`${failures} failure(s)${MUTATE ? " (mutation " + MUTATE + ")" : " (expected red before the fix)"}`);
  process.exit(1);
}
console.log("ALL PASS" + (MUTATE ? ` (mutation ${MUTATE} still passes = TEST NOT TOOTHY)` : ""));
