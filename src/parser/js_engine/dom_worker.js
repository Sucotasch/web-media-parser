// Deno DOM worker (P1: sieve JS `url`/`res` rules that need a real DOM).
//
// Protocol: newline-delimited JSON over stdin/stdout, one request per line:
//   -> {"id":0,"html":"<page html>","pageUrl":"https://..",
//       "code":"<JS body after ':'>","groups":["full",..],"href":"https://.."}
//   <- {"id":0,"result":["https://..", ..]}   success (array of URL strings)
//   <- {"id":0,"result":null}                 rule returned nothing usable
//   <- {"id":0,"error":"..."}                 JS threw / bad input / happy-dom load failed
//
// Sandbox: spawned by Python with NO --allow-* flags (happy-dom@15.11.7 needs
// none). Site scripts are NOT executed (enableJavaScriptEvaluation=false) —
// only the user's own sieve rule runs, against the parsed document, exactly
// like the P0 sieve worker but with a real DOM. Escapes to the Deno global
// are useless without permissions; the Python side kills the worker on
// timeout. No external imports beyond happy-dom (resolved offline from the
// bundled DENO_DIR cache).

import { Window } from "npm:happy-dom@15.11.7";

function shimSelf(href, groups) {
  // Minimal `this` for res/url rules: the matched link anchor. Fields the
  // extension provides (this.node etc.) are absent -> rules using them throw
  // -> caught -> error -> fail-open (same as extension page-context limits).
  return {
    href: href || "",
    node: null,
    TRG: null,
    tagName: "A",
    getAttribute: () => null,
    querySelector: () => null,
    find: null,
    set: null,
    prepare: null,
    getImages: null,
    $: groups || [],
  };
}

function normalize(result) {
  // Imagus res rules may return: a URL string, a flat array of URLs, or
  // nested arrays (e.g. [[['#url1','#url2']]] for multi-image results).
  // Flatten recursively; strip the leading '#' fullsize marker that the
  // extension uses to flag "already fullsize" URLs.
  const out = [];
  (function walk(v) {
    if (v == null) return;
    if (Array.isArray(v)) { v.forEach(walk); return; }
    if (typeof v === "string") {
      const s = v.trim().replace(/^#/, "");
      if (s) out.push(s);
    }
  })(result);
  return out.length ? out : null;
}

function handle(line) {
  let req;
  try {
    req = JSON.parse(line);
  } catch (_) {
    return JSON.stringify({ id: null, error: "bad json" });
  }
  const { id, html, pageUrl, code, groups, href } = req;
  if (typeof code !== "string" || !code) {
    return JSON.stringify({ id, error: "empty code" });
  }
  try {
    const win = new Window({
      url: pageUrl || "https://local.invalid/",
      settings: { enableJavaScriptEvaluation: false },
    });
    const htmlStr = typeof html === "string" ? html : "";
    try {
      win.document.write(htmlStr);
      win.document.close();
    } catch (_) {
      // happy-dom throws 'insertBefore' for minimal documents (no <body>);
      // inject into the body instead — real pages are full documents and
      // take the first path.
      win.document.body.innerHTML = htmlStr;
    }
    const doc = win.document;
    // Imagus convention: `$` is the match array AND `$._` carries the raw
    // fetched page text for res rules (used heavily, e.g.
    // `$._.match(/window.__INIT_DATA=([^\n]+)/)`). Without it every such rule
    // threw `Cannot read properties of undefined (reading 'match')` and the
    // P1 fullsize discovery produced zero results on real sites.
    const $ = Array.isArray(groups) ? groups.slice() : [];
    $._ = htmlStr;
    const fn = new Function(
      "document", "window", "URL", "location", "$",
      '"use strict";\n' + code,
    );
    const self = shimSelf(href, $);
    const result = normalize(fn.call(self, doc, win, win.URL, win.location, $));
    return JSON.stringify({ id, result });
  } catch (e) {
    return JSON.stringify({ id, error: String((e && e.message) || e) });
  }
}

// Route rule console output to stderr (stdout is the JSON protocol channel).
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
