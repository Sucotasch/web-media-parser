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
    const fn = new Function("$", "location", "document", "window", "URL",
      '"use strict";\n' + code);
    const result = fn($, location, document, window, URL);
    return JSON.stringify(typeof result === "string" ? { id, result } : { id, result: null });
  } catch (e) {
    return JSON.stringify({ id, error: String((e && e.message) || e) });
  }
}

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
