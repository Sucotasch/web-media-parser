// Deno JS worker for Imagus sieve JS rules (P0: `to` rules without hard DOM).
//
// Protocol: newline-delimited JSON over stdin/stdout, one request per line:
//   -> {"id":0,"code":"<JS body after ':'>","groups":["full",..],"pageUrl":"https://.."}
//   <- {"id":0,"result":"https://.."}            success (string result)
//   <- {"id":0,"result":null}                    rule returned non-string
//   <- {"id":0,"error":"..."}                    JS threw / bad input
//
// Sandbox: no --allow-net / --allow-read / --allow-write are granted by the
// Python side. Rules are untrusted sieve code; DOM-ish globals (document,
// location, URL) are thin shims built from pageUrl. `fetch`, `this.node`,
// querySelector etc. are NOT available and will throw -> caught -> error.
//
// No external imports: stays dependency-free so the worker runs fully offline.

function shimLocation(pageUrl) {
  try {
    const u = new URL(pageUrl || "https://local.invalid/");
    return {
      href: u.href, protocol: u.protocol, host: u.host, hostname: u.hostname,
      port: u.port, pathname: u.pathname, search: u.search, hash: u.hash,
      origin: u.origin,
    };
  } catch (_) {
    return { href: "", protocol: "", host: "", hostname: "", port: "", pathname: "", search: "", hash: "", origin: "" };
  }
}

// `this` for url/to rules that reference this.node / this.find / this.TRG.
// The P0 worker has NO page HTML, so a real DOM element is impossible — but
// url rules often branch on groups first (`$[2] ? '//'+$[1]+... : this.node...`)
// or read this.node.src only as a guard. Without a shim every such rule threw
// "Cannot read properties of undefined (reading 'node')" and the WORKING
// branch never ran. This shim returns safe null-ish values instead, so lazy
// branches still evaluate and genuine DOM lookups fail open (no exception).
function shimSelf(groups) {
  const node = {
    src: null,
    href: null,
    textContent: null,
    className: "",
    tagName: "A",
    nodeType: 1,
    parentNode: null,
    previousElementSibling: null,
    closest: () => null,
    matches: () => false,
    querySelector: () => null,
    querySelectorAll: () => [],
    getAttribute: () => null,
    hasAttribute: () => false,
  };
  return {
    href: null,
    node: node,
    TRG: node,
    tagName: "A",
    src: null,
    getAttribute: () => null,
    closest: () => null,
    matches: () => false,
    querySelector: () => null,
    querySelectorAll: () => [],
    parentNode: null,
    previousElementSibling: null,
    find: () => null,
    set: null,
    prepare: null,
    getImages: null,
    $: groups || [],
  };
}

function handle(line) {
  let req;
  try {
    req = JSON.parse(line);
  } catch (_) {
    return JSON.stringify({ id: null, error: "bad json" });
  }
  const { id, code, groups, pageUrl } = req;
  if (typeof code !== "string" || !code) {
    return JSON.stringify({ id, error: "empty code" });
  }
  try {
    const $ = Array.isArray(groups) ? groups : [];
    const location = shimLocation(pageUrl);
    const document = {
      URL: pageUrl || "",
      location: location,
      domain: location.hostname || "",
      cookie: "",
    };
    // window shim: rules may read window.URL / window.location; anything
    // heavier (window.fetch, window.document.body...) is undefined -> throw.
    const window = { location, URL, document };
    // Many Imagus rules are wrapped as `(()=>{...})()` expressions. A bare
    // function body does NOT return the value of a trailing expression, so
    // `(()=>{return 42})()` as the last statement yields undefined. Try
    // compiling the body as `return (body)` (works only when the body is a
    // pure expression — statements like `;` or a top-level `return` make the
    // wrapper a SyntaxError and it falls back to the raw body, which carries
    // its own return). This is robust for both styles without a fragile
    // prefix heuristic.
    const body = code.trim();
    // Cache the wrap decision per rule body: the probe Function compile is
    // cheap but double-compiles per call; rule bodies are a small finite set.
    let wrapped = wrapCache.get(body);
    if (wrapped === undefined) {
      const exprBody = body.replace(/;\s*$/, "");
      wrapped = body;
      try {
        new Function('"use strict";\nreturn (' + exprBody + ')');
        wrapped = "return (" + exprBody + ")";
      } catch (_) {
        // statement-style body — keep as-is
      }
      wrapCache.set(body, wrapped);
    }
    const fn = new Function("$", "location", "document", "window", "URL",
      '"use strict";\n' + wrapped);
    const self = shimSelf($);
    const result = fn.call(self, $, location, document, window, URL);
    return JSON.stringify(typeof result === "string" ? { id, result } : { id, result: null });
  } catch (e) {
    return JSON.stringify({ id, error: String((e && e.message) || e) });
  }
}

// Wrap-decision cache keyed by rule body (see handle()). Bounded: one entry
// per distinct sieve JS rule body.
const wrapCache = new Map();

// Route rule console output to stderr, NEVER stdout: stdout is the JSON
// protocol channel (one response line per request). A rule calling
// console.log() would otherwise corrupt the protocol and desync responses.
const enc = new TextEncoder();
const toStderr = (...a) => {
  try { Deno.stderr.writeSync(enc.encode(a.map(String).join(" ") + "\n")); } catch (_) {}
};
console.log = console.info = console.warn = console.error = toStderr;

const buf = new Uint8Array(1 << 16);
let acc = "";
const decoder = new TextDecoder();
const encoder = new TextEncoder();
for (;;) {
  const n = Deno.stdin.readSync(buf);
  if (n === null) break; // EOF
  acc += decoder.decode(buf.subarray(0, n), { stream: true });
  let idx;
  while ((idx = acc.indexOf("\n")) >= 0) {
    const line = acc.slice(0, idx).trim();
    acc = acc.slice(idx + 1);
    if (!line) continue;
    Deno.stdout.writeSync(encoder.encode(handle(line) + "\n"));
  }
}
