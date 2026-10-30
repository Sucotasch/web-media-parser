// Behavioural test for the popup against a minimal DOM (F-07 follow-up).
//
// Unit helpers prove the arithmetic; this suite proves the ACTUAL complaint is
// gone: a 1439-item result set is fully reachable by scrolling, every item is
// selected by default, and "Send to App" / "Save in Chrome" receive all 1439 —
// not the 300 rows that happened to be painted.
//
// popup.js is evaluated whole (shared.js first) against stub globals, then the
// real functions are called. A syntax error anywhere in either file fails here.
//
// Run: node extension/tests/popup_render_dom.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(here, "..", ...p), "utf8");
const popupSrc = read("popup", "popup.js");
const sharedSrc = read("shared.js");

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

// --- Minimal DOM -----------------------------------------------------------

class El {
  constructor(tag) {
    this.tagName = tag;
    this.id = "";
    this._className = "";
    this.children = [];
    this.parentNode = null;
    this.textContent = "";
    this.value = "";
    this.title = "";
    this.disabled = false;
    this.checked = false;
    this.indeterminate = false;
    this.type = "";
    this.style = {};
    this.dataset = {};
    this.attrs = {};
    this._classes = new Set();
    this._listeners = {};
    // className and classList describe the same thing, as in a real element.
    Object.defineProperty(this, "className", {
      get: () => this._className,
      set: (v) => {
        this._className = v;
        this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
      },
    });
    this.classList = {
      add: (...c) => c.forEach((x) => this._classes.add(x)),
      remove: (...c) => c.forEach((x) => this._classes.delete(x)),
      contains: (c) => this._classes.has(c),
    };
    // The list container reports a viewport so the paint loop can decide
    // whether another chunk is needed without a real layout engine.
    this.clientHeight = 600;
  }

  get scrollHeight() {
    return this.children.length * 78;
  }

  set innerHTML(v) {
    this.children = [];
    // The only markup the popup writes through innerHTML is a <select>'s
    // option list ("All formats (5)"); a real DOM would build those nodes,
    // so the stub has to as well. Anything else just clears the container.
    for (const m of String(v).matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)) {
      const opt = new El("option");
      opt.value = m[1];
      opt.textContent = m[2];
      this.appendChild(opt);
    }
  }

  appendChild(node) {
    if (node && node.isFragment) {
      node.children.forEach((c) => this.appendChild(c));
      node.children = [];
      return node;
    }
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  insertBefore(node, ref) {
    const at = ref ? this.children.indexOf(ref) : this.children.length;
    const nodes = node && node.isFragment ? node.children.slice() : [node];
    if (node && node.isFragment) node.children = [];
    nodes.forEach((n) => { n.parentNode = this; });
    this.children.splice(at < 0 ? this.children.length : at, 0, ...nodes);
    return node;
  }

  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  dispatch(type) { (this._listeners[type] || []).forEach((fn) => fn({ type, preventDefault() {} })); }

  _all() {
    const out = [];
    const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); });
    walk(this);
    return out;
  }

  querySelector(tag) {
    let found = this.children.find((c) => c.tagName === tag);
    if (!found) {
      found = new El(tag);
      this.appendChild(found);
    }
    return found;
  }

  querySelectorAll(selector) {
    const m = /^input\[type='checkbox'\](:checked)?$/.exec(selector);
    if (!m) throw new Error(`unsupported selector in the stub: ${selector}`);
    return this._all().filter((n) => n.tagName === "input" && n.type === "checkbox"
      && (!m[1] || n.checked));
  }
}

const elements = new Map();
const document = {
  createElement: (tag) => new El(tag),
  createDocumentFragment: () => {
    const frag = new El("#fragment");
    frag.isFragment = true;
    return frag;
  },
  getElementById(id) {
    if (!elements.has(id)) {
      const el = new El("div");
      el.id = id;
      elements.set(id, el);
    }
    return elements.get(id);
  },
  body: new El("body"),
};

class ImageEl extends El {
  constructor() { super("img"); }
}
const ImageCtor = function () { return new ImageEl(); };

const observers = [];
class IntersectionObserverStub {
  constructor(cb, opts) {
    this.cb = cb;
    this.opts = opts;
    this.target = null;
    observers.push(this);
  }
  observe(el) { this.target = el; }
  disconnect() { this.target = null; }
  trigger() { this.cb([{ isIntersecting: true, target: this.target }]); }
}

// --- chrome stub -----------------------------------------------------------

const sentMessages = [];
const chromeStub = {
  runtime: {
    onMessage: { addListener() {} },
    sendMessage: async (msg) => {
      sentMessages.push(msg);
      return { ok: true, added: msg.urls ? msg.urls.length : 0, saved: msg.items ? msg.items.length : 0 };
    },
  },
  storage: {
    onChanged: { addListener() {} },
    local: {
      get(_keys, cb) {
        const p = Promise.resolve({});
        if (typeof cb === "function") { p.then(cb); return undefined; }
        return p;
      },
      set: () => Promise.resolve(),
      remove: () => Promise.resolve(),
    },
  },
  tabs: {
    query: async () => [{ id: 1, url: "https://gallery.example/post/1", index: 0 }],
    create() {},
    sendMessage: async () => ({}),
  },
  action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
  downloads: { download() {} },
};

const api = new Function(
  "chrome", "document", "window", "navigator", "Image", "IntersectionObserver",
  "requestAnimationFrame", "setTimeout", "clearTimeout", "console", "URL", "location",
  `${sharedSrc}\n${popupSrc}\nreturn {
    applyScanResult, updateCount, renderMediaList, mediaItems, deselected,
    mediaList: document.getElementById("media-list"),
    selectAll: document.getElementById("select-all"),
    chromeDownloadBtn: document.getElementById("chrome-download-btn"),
    downloadBtn: document.getElementById("download-btn"),
    countSpan: document.getElementById("count"),
    renderState: () => ({ painted: renderedCount, queued: renderQueue.length }),
  };`
)(
  chromeStub,
  document,
  { getSelection: () => null },
  {},
  ImageCtor,
  IntersectionObserverStub,
  (cb) => cb(),
  (cb) => cb(),
  () => {},
  console,
  URL,
  { href: "chrome-extension://test/popup/popup.html" }
);

const tick = () => new Promise((r) => setTimeout(r, 0));

const rows = () => api.mediaList.children.filter((c) => c.className === "media-item");
const sentinel = () => api.mediaList.children.find((c) => c._classes.has("media-more"));
const paintedCount = () => api.renderState().painted;

// The reported page: 1439 images, one domain, one source.
const scanResult = {
  tabId: 1,
  pageUrl: "https://gallery.example/post/1",
  title: "Gallery",
  media: Array.from({ length: 1439 }, (_, i) => ({
    url: `https://gallery.example/full/${i}.jpg`,
    pageUrl: "https://gallery.example/post/1",
    source: "sieve-res",
    type: "image",
    width: 1200,
    height: 1600,
  })),
};

api.applyScanResult(scanResult);

// --- 1. The list paints progressively, and nothing is cut -------------------

check("the model holds all 1439 items", api.mediaItems.length, 1439);
check("the paint queue holds all 1439 items", api.renderState().queued, 1439);
check("the first pass paints one chunk", paintedCount(), 100);
check("the first pass builds exactly that many rows", rows().length, 100);
check("the sentinel reports real progress, not a cap",
  sentinel().textContent, "showing 100 of 1439 \u2014 scroll for more");

{
  let guard = 0;
  while (paintedCount() < 1439 && guard++ < 100) {
    const io = observers.filter((o) => o.target).pop();
    if (io) io.trigger();
    else break;
  }
  check("scrolling reaches every one of the 1439 rows", paintedCount(), 1439);
  check("every row exists in the DOM", rows().length, 1439);
  check("nothing is left withheld", sentinel()._classes.has("hidden"), true);
  check("the last item painted is the last item scanned", rows()[1438].children.length > 0, true);
}

// --- 2. Selection covers the whole set, painted or not ---------------------

check("all 1439 are selected by default", api.countSpan.textContent, "1439 / 1439 selected");
check("Select all shows as checked", api.selectAll.checked, true);
check("Select all is not indeterminate", api.selectAll.indeterminate, false);

api.selectAll.checked = false;
api.selectAll.dispatch("change");
check("unchecking Select all clears the whole model", api.deselected.size, 1439);
check("the counter follows the model", api.countSpan.textContent, "0 / 1439 selected");

api.selectAll.checked = true;
api.selectAll.dispatch("change");
check("re-checking Select all restores all 1439", api.deselected.size, 0);

// Uncheck one row: 1438 remain selected (the other 1339 are not even painted).
{
  const box = api.mediaList.querySelectorAll("input[type='checkbox']")[5];
  box.checked = false;
  box.dispatch("change");
  check("unchecking a row removes exactly one item", api.countSpan.textContent, "1438 / 1439 selected");
  box.checked = true;
  box.dispatch("change");
  check("re-checking the row restores it", api.countSpan.textContent, "1439 / 1439 selected");
}

// --- 3. Bulk actions carry all 1439, not the painted rows ------------------

sentMessages.length = 0;
api.chromeDownloadBtn.dispatch("click");
await tick();
await tick();
{
  const msg = sentMessages.find((m) => m.action === "chromeDownload");
  check("Save in Chrome asks for all 1439 files", msg ? msg.items.length : -1, 1439);
  check("file names are still sanitized", msg.items[0].filename, "0.jpg");
}

sentMessages.length = 0;
api.downloadBtn.dispatch("click");
await tick();
await tick();
{
  const msg = sentMessages.find((m) => m.action === "download");
  check("Send to App sends all 1439 items", msg ? msg.urls.length : -1, 1439);
  check("Send to App stays a one-shot task", msg.one_shot, true);
  check("the page travels as the referer", msg.urls[0].referer, "https://gallery.example/post/1");
}

// --- 4. A filter change repaints from the top without losing the set ------

{
  const sourceFilter = document.getElementById("source-filter");
  sourceFilter.value = "thumbnail";
  sourceFilter.dispatch("change");
  check("a filter that matches nothing shows the filter explanation",
    document.getElementById("empty").querySelector("p").textContent,
    "No items match the filters \u2014 pick another source, format or domain above.");
  sourceFilter.value = "fullsize";
  sourceFilter.dispatch("change");
  check("switching back repaints the full set", api.renderState().queued, 1439);
  check("and the selection still covers all of it", api.countSpan.textContent, "1439 / 1439 selected");
}

// --- 5. FMT-1: the file-type filter drives the whole model ------------------
//
// A page that ships an original AND a web variant of every picture must be
// reducible to one type from the popup — list, counter, selection and the
// Save payload all at once.

{
  const fmt = document.getElementById("format-filter");
  const mk = (n, ext) => ({
    url: `https://gallery.example/art/${n}.${ext}`,
    pageUrl: "https://gallery.example/post/1",
    source: "js-literal",
    type: "image",
  });
  api.applyScanResult({
    tabId: 1,
    pageUrl: "https://gallery.example/post/1",
    title: "Mixed gallery",
    media: [mk(1, "png"), mk(2, "png"), mk(3, "png"), mk(4, "webp"), mk(5, "webp")],
  });

  check("the type filter lists every type found",
    fmt.children.map((o) => o.textContent),
    ["All formats (5)", "PNG (3)", "WEBP (2)"]);
  check("everything is selected while the filter is open",
    api.countSpan.textContent, "5 / 5 selected");

  fmt.value = ".png";
  fmt.dispatch("change");
  check("choosing PNG narrows the counter", api.countSpan.textContent, "3 / 3 selected");
  check("and the paint queue with it", api.renderState().queued, 3);

  sentMessages.length = 0;
  api.chromeDownloadBtn.dispatch("click");
  await tick();
  await tick();
  {
    const msg = sentMessages.find((m) => m.action === "chromeDownload");
    check("Save in Chrome saves only the chosen type",
      msg ? msg.items.map((i) => i.url) : -1,
      ["https://gallery.example/art/1.png",
       "https://gallery.example/art/2.png",
       "https://gallery.example/art/3.png"]);
  }

  fmt.value = "";
  fmt.dispatch("change");
  check("back to All formats restores the whole set",
    api.countSpan.textContent, "5 / 5 selected");
}

if (failures) {
  console.log(`\n${failures} FAILURE(S)`);
  process.exit(1);
}
console.log("\nALL PASS");
