// P4 gateway worker: simulate a click on a consent/age-gate button inside a
// real DOM (happy-dom with JavaScript evaluation ENABLED for this operation).
//
// Protocol: newline-delimited JSON over stdin/stdout, one request per line:
//   -> {"id":0,"html":"<page html>","pageUrl":"https://..",
//       "textPatterns":["i agree",..],"overlaySelectors":[".age-gate",..]}
//   <- {"id":0,"result":{"cookies":{"name":"value",..},
//       "html_after":"<mutated html>","redirect":null,"reload_requested":false}}
//   <- {"id":0,"result":null}              no clickable consent button found
//   <- {"id":0,"error":"..."}              JS threw / happy-dom load failed
//
// Sandbox: spawned by Python with NO --allow-* flags. Unlike dom_worker.js
// (enableJavaScriptEvaluation=false — sieve rules only), THIS worker enables
// JavaScript evaluation for the page because the whole point is to run the
// button's onclick handler. The Deno sandbox (no net/fs/env) is the only
// boundary: site scripts cannot touch the host, and the Python side kills the
// worker on timeout (bounded CPU). External <script src> do not load (no
// --allow-net), which is fine — consent handlers are almost always inline or
// in the onclick attribute itself.
//
// Navigation interception happens BEFORE the click: happy-dom's
// location.reload()/location.href= would otherwise re-navigate the virtual
// document and destroy the state we need to read back.

import { Window } from "npm:happy-dom@15.11.7";

const AVOID_KEYWORDS = [
  "login", "sign in", "sign-in", "signin", "register", "sign up", "sign-up",
  "log out", "logout", "account", "password", "email", "username",
  "checkout", "cart", "donate", "subscribe", "newsletter",
  "вход", "регистрация", "аккаунт", "пароль", "войти", "зарегистрироваться",
];

function isAvoided(text) {
  const t = String(text || "").toLowerCase();
  return AVOID_KEYWORDS.some((k) => t.includes(k));
}

function matchText(text, patterns) {
  // Strong match = the button text equals a pattern (len>5) or contains it.
  const t = String(text || "").toLowerCase();
  let score = 0;
  for (const p of patterns || []) {
    const pl = String(p || "").toLowerCase();
    if (!pl) continue;
    if (t === pl) score += pl.length > 5 ? 200 : 50;
    else if (t.includes(pl)) score += pl.length > 5 ? 100 : 20;
  }
  return score;
}

function findButton(doc, patterns, overlaySelectors) {
  let root = doc;
  for (const sel of overlaySelectors || []) {
    try {
      const el = doc.querySelector(sel);
      if (el) { root = el; break; }
    } catch (_) { /* bad selector */ }
  }
  const candidates = root.querySelectorAll(
    "a[onclick], a[onmousedown], button, input[type=submit], input[type=button], div[onclick], span[onclick]",
  );    let best = null;
    let bestScore = 0;
    for (const el of candidates) {
        const text =
            (el.textContent || "").trim() ||
            (el.getAttribute && el.getAttribute("value")) ||
            "";
        if (isAvoided(text)) continue;
        // Only click elements that have a JS handler or submit a form.
        // A plain form-less <button> without onclick would be a no-op click
        // (diffCookies={}, unchanged html) — a wasted worker round-trip.
        const handler =
            (el.getAttribute && (el.getAttribute("onclick") || el.getAttribute("onmousedown") || "")) || "";
        const score = matchText(text, patterns);
        if (score > 0 && (handler || el.closest("form"))) {
            if (score > bestScore) {
                bestScore = score;
                best = el;
            }
        }
    }
    return best;
}

function parseCookies(cookieHeader) {
  const out = {};
  for (const part of String(cookieHeader || "").split(";")) {
    const idx = part.indexOf("=");
    if (idx > 0) {
      const name = part.slice(0, idx).trim();
      const value = part.slice(idx + 1).trim();
      if (name) out[name] = value;
    }
  }
  return out;
}

function diffCookies(before, after) {
  const b = parseCookies(before);
  const a = parseCookies(after);
  const out = {};
  for (const [name, value] of Object.entries(a)) {
    if (b[name] !== value) out[name] = value; // new or changed
  }
  return out;
}

function handle(line) {
  let req;
  try {
    req = JSON.parse(line);
  } catch (_) {
    return JSON.stringify({ id: null, error: "bad json" });
  }
  const { id, html, pageUrl, textPatterns, overlaySelectors } = req;
  if (typeof html !== "string") {
    return JSON.stringify({ id, error: "missing html" });
  }
  try {
    const win = new Window({
      url: pageUrl || "https://local.invalid/",
      settings: { enableJavaScriptEvaluation: true },
    });
    const doc = win.document;
    try {
      doc.write(html);
      doc.close();
    } catch (_) {
      // minimal documents (no <body>) throw in happy-dom — inject instead
      doc.body.innerHTML = html;
    }
    // Intercept navigation BEFORE the click.
    let reloadRequested = false;
    let redirect = null;
    try {
      win.location.reload = () => { reloadRequested = true; };
    } catch (_) {}
    try {
      // Cache the original href FIRST: the getter below must never recurse
      // into win.location.href (that would re-enter itself → "Maximum call
      // stack size exceeded"). We shadow the property and serve the cached
      // value until a script assigns to it.
      const origHref = win.location.href;
      Object.defineProperty(win.location, "href", {
        get: () => (redirect !== null ? redirect : origHref),
        set: (v) => { redirect = String(v); },
        configurable: true,
      });
    } catch (_) {}

    const target = findButton(doc, textPatterns || [], overlaySelectors || []);
    if (!target) return JSON.stringify({ id, result: null });

    const beforeCookies = doc.cookie || "";
    try {
      target.click();
    } catch (_) {}
    // Let synchronous handler side effects land (cookie writes are sync).
    const afterCookies = doc.cookie || "";
    const cookies = diffCookies(beforeCookies, afterCookies);

    let htmlAfter = "";
    try {
      htmlAfter = doc.documentElement ? doc.documentElement.outerHTML : doc.body.outerHTML;
    } catch (_) {}

    return JSON.stringify({
      id,
      result: {
        cookies,
        html_after: htmlAfter,
        redirect,
        reload_requested: reloadRequested,
      },
    });
  } catch (e) {
    return JSON.stringify({ id, error: String((e && e.message) || e) });
  }
}

// Route console output to stderr — stdout is the JSON protocol channel.
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
