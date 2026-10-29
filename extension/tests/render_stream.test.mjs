// Chunked-render test (F-07 follow-up).
//
// The popup used to cut the list at 300 rows because building 1000+ rows in one
// pass froze it, and the leftover items were then unreachable. The fix is to
// paint the list in chunks as it scrolls — all items stay in the queue and get
// a row eventually. This suite pins the chunking math (nothing lost, order
// kept, bounded per pass) and the structural facts the pure helper cannot see.
//
// Run: node extension/tests/render_stream.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "popup", "popup.js"), "utf8");

const chunkMatch = /const RENDER_CHUNK = (\d+);/.exec(src);
if (!chunkMatch) {
  console.error("FAIL: RENDER_CHUNK not found in popup.js");
  process.exit(1);
}
const RENDER_CHUNK = Number(chunkMatch[1]);

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

const nextRenderChunk = new Function(
  `const RENDER_CHUNK = ${RENDER_CHUNK};
   return (${extractFn(src, "function nextRenderChunk(", "nextRenderChunk")});`
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

const items = (n) => Array.from({ length: n }, (_, i) => ({ url: `https://gallery.example/full/${i}.jpg` }));

check("the chunk size is a sane paint batch", RENDER_CHUNK >= 10 && RENDER_CHUNK <= 300, true);

{
  const out = nextRenderChunk([], 0);
  check("an empty list has nothing to paint", out.length, 0);
}

{
  const input = items(1439);
  const painted = [];
  for (let from = 0; from < input.length; from += RENDER_CHUNK) {
    painted.push(...nextRenderChunk(input, from));
  }
  check("every one of the 1439 items gets a row", painted.length, 1439);
  check("nothing is painted twice", new Set(painted).size, 1439);
  check("the order is the model's order", painted[0], input[0]);
  check("the last item is reached", painted[painted.length - 1], input[1438]);
}

{
  const input = items(1439);
  const sizes = [];
  for (let from = 0; from < input.length; from += RENDER_CHUNK) {
    sizes.push(nextRenderChunk(input, from).length);
  }
  check("no pass builds more than one chunk", sizes.every((n) => n <= RENDER_CHUNK), true);
  check("the tail pass builds only what is left",
    sizes[sizes.length - 1], 1439 - RENDER_CHUNK * (sizes.length - 1));
  check("the input list is not mutated", input.length, 1439);
}

// --- structural guards for the parts that need a DOM -----------------------

check("the list is painted in chunks, not cut",
  /renderQueue = filtered;[\s\S]{0,200}renderNextChunk\(renderToken\);/.test(src), true);
check("the sentinel says how far the paint has got",
  /showing \$\{renderedCount\} of \$\{renderQueue\.length\}/.test(src), true);
check("the sentinel invites scrolling instead of narrowing filters",
  /scroll for more/.test(src), true);
check("a chunk is inserted as one fragment", /mediaList\.insertBefore\(frag, renderSentinel\);/.test(src), true);
check("the sentinel is the scroll observer's target",
  /new IntersectionObserver\([\s\S]{0,220}root: mediaList/.test(src), true);
check("a short chunk keeps painting instead of stranding the rest",
  /if \(!listOverflows\(\)\) \{\s*\n\s*requestAnimationFrame\(\(\) => renderNextChunk\(renderToken\)\);/.test(src), true);
check("without an observer the rest is painted in one pass",
  /while \(renderedCount < renderQueue\.length\) renderNextChunk\(renderToken\);/.test(src), true);
check("previews are lazy-loaded", /img\.loading = "lazy";/.test(src), true);
check("previews decode asynchronously", /img\.decoding = "async";/.test(src), true);

if (failures) {
  console.log(`\n${failures} FAILURE(S)`);
  process.exit(1);
}
console.log("\nALL PASS");
