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

// Find the real element the rule was invoked on, so `this.node` is a live
// DOM node (happy-dom) instead of null. The extension passes the hovered
// link anchor; we reconstruct it from the context: match an <a> whose href
// equals the probe link URL, else an <img> whose src equals one of the
// regex groups (the thumbnail URL). Deliberately NO blind "first element"
// fallback: in the discovery flow the fetched page (html) is the image-host
// wrapper, which rarely contains the probe link's own anchor — returning the
// first <a>/<img> would hand rules a logo/nav element and produce WRONG
// URLs instead of a clean fail-open. Returns null when nothing matches.
function findNode(doc, href, groups) {
  try {
    const norm = (u) => String(u || "").trim().replace(/\/$/, "");
    const want = norm(href);
    if (want) {
      for (const a of doc.querySelectorAll("a[href]")) {
        const h = norm(a.getAttribute("href"));
        if (h === want) return a;
        // relative href resolves against the document URL
        try {
          if (new URL(h, doc.baseURI).href === new URL(want, doc.baseURI).href) return a;
        } catch (_) {}
      }
    }
    const gs = Array.isArray(groups) ? groups.map(norm).filter(Boolean) : [];
    for (const g of gs) {
      for (const img of doc.querySelectorAll("img[src]")) {
        const s = norm(img.getAttribute("src"));
        if (s === g) return img;
        try {
          if (new URL(s, doc.baseURI).href === new URL(g, doc.baseURI).href) return img;
        } catch (_) {}
      }
    }
  } catch (_) {}
  return null;
}

// this.find({href|src, ...}) — the extension searches the document for an
// element matching a URL and returns it (or its resolved href/src). Rules
// use it as `this.find({href: u}) || u`; return the resolved URL so the
// fallback chain works.
function findInDoc(doc, opts) {
  try {
    if (!opts || typeof opts !== "object") return null;
    const norm = (u) => String(u || "").trim();
    if (opts.href) {
      const want = norm(opts.href);
      for (const a of doc.querySelectorAll("a[href]")) {
        const h = norm(a.getAttribute("href"));
        if (h === want) return want;
        try {
          if (new URL(h, doc.baseURI).href === new URL(want, doc.baseURI).href) return want;
        } catch (_) {}
      }
    }
    if (opts.src) {
      const want = norm(opts.src);
      for (const img of doc.querySelectorAll("img[src]")) {
        const s = norm(img.getAttribute("src"));
        if (s === want) return want;
        try {
          if (new URL(s, doc.baseURI).href === new URL(want, doc.baseURI).href) return want;
        } catch (_) {}
      }
    }
  } catch (_) {}
  return null;
}

function shimSelf(doc, href, groups) {
  // `this` for res rules. this.node/TRG is a Proxy over the matched element
  // that emulates the extension's hovered thumbnail semantics:
  //   - this.node.src            -> first <img> child's src (1.org-pp)
  //   - this.node.closest('a')   -> anchor of that img (Google_Images)
  //   - this.node.querySelector  -> container lookup (CNN-m-pp)
  // Unhandled properties fall through to the anchor/img element; writes
  // (this.node.IMGS_* = ...) land on the real element. Anything heavier
  // (this.set/prepare/getImages, IMGS_ext_data wiring) stays absent -> the
  // rule throws -> caught -> fail-open.
  const anchor = findNode(doc, href, groups);
  const img = anchor
    ? (anchor.tagName === "IMG" ? anchor
       : (anchor.querySelector && anchor.querySelector("img[src]")) || null)
    : null;
  // No element matched: fall back to the DOCUMENT, not to an arbitrary first
  // element. querySelector/querySelectorAll then work from a sane root
  // (CNN-m-pp finds the page image); src/closest/parentNode are guarded and
  // return null (clean fail-open) instead of garbage from a logo/nav node.
  const target = anchor || img || doc;
  const node = new Proxy(target, {
    get(t, prop) {
      if (typeof prop !== "string") return t[prop];
      if (prop === "src") {
        return img
          ? (img.src || img.getAttribute("src") || null)
          : (anchor ? anchor.getAttribute("src") || null : null);
      }
      if (prop === "href") {
        if (anchor) return anchor.href || anchor.getAttribute("href") || "";
        return "";
      }
      if (prop === "querySelector") {
        return typeof t.querySelector === "function" ? (sel) => t.querySelector(sel) : null;
      }
      if (prop === "querySelectorAll") {
        return typeof t.querySelectorAll === "function" ? (sel) => t.querySelectorAll(sel) : null;
      }
      // Element-only methods (closest/matches/getAttribute): when the target
      // is the Document fallback they are absent — return a stub that yields
      // null so `this.node.closest('a')?.href` chains resolve cleanly instead
      // of throwing TypeError and relying on the catch-all fail-open.
      if (prop === "closest") {
        return typeof t.closest === "function" ? (sel) => t.closest(sel) : () => null;
      }
      if (prop === "matches") {
        return typeof t.matches === "function" ? (sel) => t.matches(sel) : () => null;
      }
      if (prop === "tagName") return t.tagName || "A";
      if (prop === "parentNode") return t.parentNode || null;
      if (prop === "previousElementSibling") return t.previousElementSibling || null;
      if (prop === "getAttribute") {
        return typeof t.getAttribute === "function" ? (name) => t.getAttribute(name) : () => null;
      }
      const v = t[prop];
      return typeof v === "function" ? v.bind(t) : v;
    },
    set(t, prop, val) {
      t[prop] = val;
      return true;
    },
    has(t, prop) {
      return prop in t;
    },
  });
  return {
    href: href || node.href || "",
    node: node,
    TRG: node,
    tagName: node.tagName,
    getAttribute: (name) => node.getAttribute(name),
    querySelector: (sel) => node.querySelector(sel),
    querySelectorAll: (sel) => node.querySelectorAll(sel),
    closest: (sel) => node.closest(sel),
    matches: (sel) => node.matches(sel),
    parentNode: node.parentNode,
    previousElementSibling: node.previousElementSibling,
    src: node.src,
    find: (opts) => findInDoc(doc, opts),
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
    const self = shimSelf(doc, href, $);
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
