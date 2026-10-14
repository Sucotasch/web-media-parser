/**
 * Content script for Web Media Parser extension.
 * Full page scanner: DOM images + linked pages for fullsize discovery.
 * Desktop receives only selected URLs for download (no parsing).
 */

(() => {
  "use strict";

  // --- DOM Scanner ---

  function scanPageMedia(doc, baseUrl) {
    const media = [];
    const seen = new Set();
    const linkSet = new Set();
    const links = [];      // anchors that PRESENT media - real discovery candidates
    const linkRest = [];   // other page links, probed only with budget left over

    const JUNK_PATTERNS = [
      /\/l-stat\./i, /\/userpic/i, /\/avatar/i, /\/logo[._-]|logo\./i,
      /\/favicon/i, /\/emoji/i, /\/gravatar/i, /\/icon[s]?\//i,
      /ljcounter/i, /\/blank\./i, /\/spacer\./i, /\/pixel\./i,
      /1x1\./i, /\/spinner/i, /\/loading/i, /\.svg([?#]|$)/i,
      /\/button[s]?\//i, /\/badge/i, /\/arrow/i,
      /\/(nav|menu|search|cart|share|social|widget|advert|tracker)\b/i,
      /\/(prev|next|close|expand|collapse|play|pause|mute|volume)\b/i,
      /\$\{/i, // un-interpolated template placeholder (e.g. products/${imgSrc})
      // EXT-8: no hard format drop here — GIF/PNG/ICO filtering belongs to the
      // desktop app's format allowlist (which keeps .png ON by default). A
      // gallery with PNG art was invisible to "Save (Chrome)" otherwise.
    ];

    function isJunkUrl(url) {
      return JUNK_PATTERNS.some(p => p.test(url));
    }

    function toAbsolute(url) {
      if (!url) return null;
      try { return new URL(url, baseUrl).href; } catch (e) { return null; }
    }

    function addMedia(url, type, attrs = {}) {
      url = toAbsolute(url);
      if (!url || seen.has(url)) return;
      // R-E-7 (NEW-1): only http(s) is downloadable media. A `data:` URI is a
      // LAZY-LOAD PLACEHOLDER (src="data:image/gif;base64,…") — it reached the
      // media list and the desktop queue, where it can only fail, and
      // "Save (Chrome)" saved it under the generated name download.png.
      // `blob:`/`javascript:` are not media either.
      if (!/^https?:/i.test(url)) return;
      if (isJunkUrl(url)) return;
      if (attrs.width && attrs.height && attrs.width < 50 && attrs.height < 50) return;
      seen.add(url);
      media.push({ url, type, pageUrl: baseUrl, ...attrs });
    }

    function addLink(url, presentsMedia = true) {
      url = toAbsolute(url);
      if (!url || linkSet.has(url)) return;
      if (url === baseUrl) return;
      // Filter out non-HTTP(S) schemes to prevent fetch errors in background
      if (!url.startsWith("http://") && !url.startsWith("https://")) return;
      linkSet.add(url);
      (presentsMedia ? links : linkRest).push(url);
    }

    // Errors.txt (e-hentai.gallery): every anchor on the page used to be collected as a
    // discovery candidate, and discovery works the list in order — so site navigation
    // (Front Page / Popular / toplist / tag search / pagination) took the front of the
    // list, the 20 real thumbnail links sat behind it, and those navigation pages (grids
    // of thumbnails) fed the background's <img> dump instead of the gallery. An anchor
    // only counts as a discovery candidate when it PRESENTS media (an <img>/<picture>/
    // <video> child, or a CSS background image — e-hentai's gallery cells are
    // background-image divs, which the <img> branch above cannot see).
    function presentsMedia(a) {
      if (a.querySelector("img, picture, video, canvas")) return true;
      const style = a.getAttribute("style") || "";
      if (/background(?:-image)?\s*:[^;]*url\(/i.test(style)) return true;
      return !!a.querySelector('[style*="url("]');
    }

    // <img> — direct images
    doc.querySelectorAll("img").forEach((img) => {
      const src = img.currentSrc || img.src;
      // Tiny-layout backstop: a not-yet-loaded image has naturalWidth 0, so
      // the element's laid-out size is the only size signal — drop layout-tiny
      // elements (icons/spacers) up front. A REAL file displayed small
      // (1110×1375 in a 192px <img>) has naturalWidth > 0 and is NOT dropped.
      const laidW = img.naturalWidth || img.width || 0;
      const laidH = img.naturalHeight || img.height || 0;
      if (laidW > 0 && laidH > 0 && laidW < 50 && laidH < 50) return;
      if (src) addMedia(src, "image", {
        // Record ONLY natural dimensions (the file's real size). Falling back
        // to layout/attribute size would make the popup show THUMBNAIL dims
        // for a full-size file — e.g. 192×240 preview vs real 1110×1375.
        // Natural is 0 until the image loads, which the popup treats as
        // "dimensions unknown" (not shown) — unknown beats wrong.
        width: img.naturalWidth || 0,
        height: img.naturalHeight || 0,
        alt: img.alt || "",
        source: "img"
      });
      if (img.srcset) {
        const largest = parseSrcset(img.srcset);
        if (largest) addMedia(largest, "image", { source: "srcset" });
      }
      const dataSrc = img.getAttribute("data-src") || img.getAttribute("data-original") || img.getAttribute("data-full");
      if (dataSrc && (dataSrc.startsWith("http") || dataSrc.startsWith("//"))) {
        addMedia(dataSrc, "image", { source: "data-src" });
      }

      // Check parent <a> for fullsize link
      const parentA = img.closest("a[href]");
      if (parentA) {
        const href = parentA.getAttribute("href");
        if (href && href !== "#" && !href.startsWith("javascript:")) {
          addLink(href);
        }
      }
    });

    // <a> tags wrapping nothing but linking to images
    doc.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      const fullHref = toAbsolute(href);
      if (!fullHref || fullHref === baseUrl) return;
      // Check if link looks like it points to an image file
      if (/\.(jpe?g|png|gif|webp|bmp|tiff?|avif|heic)(\?|$)/i.test(fullHref)) {
        addMedia(fullHref, "image", { source: "a-link" });
      } else {
        addLink(fullHref, presentsMedia(a));
      }
    });

    // <video>
    doc.querySelectorAll("video").forEach((video) => {
      const src = video.src || video.currentSrc;
      if (src) addMedia(src, "video", { source: "video" });
      video.querySelectorAll("source").forEach((s) => {
        if (s.src) addMedia(s.src, "video", { source: "source" });
      });
    });

    // <picture>
    doc.querySelectorAll("picture source").forEach((source) => {
      if (source.srcset) {
        const largest = parseSrcset(source.srcset);
        if (largest) addMedia(largest, "image", { source: "picture" });
      }
    });

    // CSS background
    doc.querySelectorAll("[style*='background-image']").forEach((el) => {
      const style = el.getAttribute("style") || "";
      const match = style.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/);
      if (match) addMedia(match[1], "image", { source: "css" });
    });

    // <meta og:image>
    doc.querySelectorAll('meta[property="og:image"], meta[name="twitter:image"]').forEach((meta) => {
      const content = meta.getAttribute("content");
      if (content) addMedia(content, "image", { source: "meta" });
    });

    // Media-bearing links first: discovery works the list in order, so a link that shows
    // a thumbnail must not sit behind a "Watched"/"Faq" link.
    return { media, links: links.concat(linkRest) };
  }

  // R-E-7 (NEW-2): split a srcset into { url, descriptor } candidates. A
  // `data:` URI is read as ONE candidate — its own commas are data separators,
  // not candidate separators. Splitting the raw string on "," turned
  // `data:image/png;base64,AAAA 1x` into `data:image/png;base64` PLUS a second
  // "candidate" whose base64 tail looked like a relative URL (`iVBOR…`).
  // (The HTML grammar would require %2C in a URL, but pages ship raw data:
  // URIs in srcset and browsers tolerate them.)
  function splitSrcsetCandidates(srcset) {
    const out = [];
    const raw = srcset || "";
    const isSpace = (c) => c === " " || c === "\n" || c === "\t" || c === "\r" || c === "\f";
    let i = 0;
    while (i < raw.length) {
      while (i < raw.length && (raw[i] === "," || isSpace(raw[i]))) i++;
      if (i >= raw.length) break;
      const isData = raw.slice(i, i + 5).toLowerCase() === "data:";
      let url = "";
      if (isData) {
        // commas belong to the data URI — only whitespace ends it
        while (i < raw.length && !isSpace(raw[i])) url += raw[i++];
      } else {
        while (i < raw.length && raw[i] !== "," && !isSpace(raw[i])) url += raw[i++];
      }
      while (i < raw.length && isSpace(raw[i])) i++;
      let descriptor = "";
      while (i < raw.length && raw[i] !== "," && !isSpace(raw[i])) descriptor += raw[i++];
      if (url) out.push({ url, descriptor });
    }
    return out;
  }

  function parseSrcset(srcset) {
    // EXT-12: w- and x- descriptors are different scales (a 600w candidate is
    // NOT smaller than a 1.5x one) — scores are only comparable inside one
    // type, so each type keeps its own best candidate.
    // R-E-3: the old code locked onto the type of the FIRST candidate and
    // returned early for every other one, so a leading bare candidate
    // ("c.jpg, a.jpg 600w, b.jpg 1.5x") discarded the whole rest of the list.
    // Type priority: w (absolute widths) → x (device ratios) → bare.
    const bestUrl = { w: null, x: null, bare: null };
    const bestScore = { w: -1, x: -1, bare: -1 };
    splitSrcsetCandidates(srcset).forEach(({ url, descriptor }) => {
      // A `data:` candidate is a lazy-load placeholder, never a media file: it
      // must not win the comparison and hide a real candidate (R-E-7).
      if (/^data:/i.test(url)) return;
      let score = 1, type = "bare";
      if (descriptor) {
        if (descriptor.endsWith("w")) { score = parseInt(descriptor, 10) || 1; type = "w"; }
        else if (descriptor.endsWith("x")) { score = parseFloat(descriptor) || 1; type = "x"; }
      }
      if (score > bestScore[type]) { bestScore[type] = score; bestUrl[type] = url; }
    });
    return bestUrl.w || bestUrl.x || bestUrl.bare;
  }

  // --- Full scan orchestration ---

  async function performFullScan() {
    const currentResult = scanPageMedia(document, window.location.href);
    return { media: currentResult.media, links: currentResult.links };
  }

  // --- Message handler ---

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "getUA") {
      sendResponse({ userAgent: navigator.userAgent });
      return;
    }
    if (request.action === "scanMedia") {
      (async () => {
        try {
          const result = await performFullScan();
          sendResponse({ media: result.media, links: result.links, url: window.location.href, title: document.title, userAgent: navigator.userAgent });
        } catch (e) {
          sendResponse({ media: [], url: window.location.href, title: document.title, error: e.message });
        }
      })();
      return true;
    }
  });
})();
