// F-07 test for the popup's row cap (popup.js capForRender/RENDER_LIMIT).
//
// The render loop itself needs a real DOM, so the decision it depends on was
// extracted into a pure helper; this suite pins the helper AND the structural
// facts of the loop that the helper cannot cover (that the cap is actually
// used, that the "N more" line reports the hidden count, that previews are
// lazy-loaded).
//
// Run: node extension/tests/render_cap.test.mjs

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

const limitMatch = /const RENDER_LIMIT = (\d+);/.exec(src);
if (!limitMatch) {
  console.error("FAIL: RENDER_LIMIT not found in popup.js");
  process.exit(1);
}
const RENDER_LIMIT = Number(limitMatch[1]);

const capSrc = extractFn(src, "function capForRender(", "capForRender");
const capForRender = new Function(
  `const RENDER_LIMIT = ${RENDER_LIMIT};
   return (${capSrc});`
)();

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

const items = (n) => Array.from({ length: n }, (_, i) => ({ url: `https://cdn/${i}.jpg` }));

check("the cap is a real bound, not a placeholder", RENDER_LIMIT > 0 && RENDER_LIMIT <= 500, true);

{
  const out = capForRender([]);
  check("an empty list renders nothing and hides nothing", [out.shown.length, out.hidden], [0, 0]);
}

{
  const input = items(299);
  const out = capForRender(input);
  check("under the cap every row is shown", [out.shown.length, out.hidden], [299, 0]);
  check("under the cap the rows are the input rows", out.shown[0], input[0]);
}

{
  const input = items(RENDER_LIMIT);
  const out = capForRender(input);
  check("exactly at the cap nothing is hidden", [out.shown.length, out.hidden], [RENDER_LIMIT, 0]);
}

{
  const input = items(1000);
  const out = capForRender(input);
  check("over the cap only RENDER_LIMIT rows are built", out.shown.length, RENDER_LIMIT);
  check("the hidden count is reported", out.hidden, 1000 - RENDER_LIMIT);
  check("the first rows are the ones kept", out.shown[0], input[0]);
  check("the input list is not mutated", input.length, 1000);
}

{
  const out = capForRender(items(10), 4);
  check("the limit is injectable", [out.shown.length, out.hidden], [4, 6]);
}

// --- structural guards for the parts that need a DOM -----------------------

check("the render path uses the helper", /const \{ shown, hidden \} = capForRender\(filtered\);/.test(src), true);
check("the 'more' line reports the hidden count", /and \$\{hidden\} more/.test(src), true);
check("the old length-minus-limit arithmetic is gone", /filtered\.length - RENDER_LIMIT/.test(src), false);
check("previews are lazy-loaded", /img\.loading = "lazy";/.test(src), true);
check("previews decode asynchronously", /img\.decoding = "async";/.test(src), true);

if (failures) {
  console.log(`\n${failures} FAILURE(S)`);
  process.exit(1);
}
console.log("\nALL PASS");
