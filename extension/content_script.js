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
    const links = [];

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
      if (isJunkUrl(url)) return;
      if (attrs.width && attrs.height && attrs.width < 50 && attrs.height < 50) return;
      seen.add(url);
      media.push({ url, type, pageUrl: baseUrl, ...attrs });
    }

    function addLink(url) {
      url = toAbsolute(url);
      if (!url || linkSet.has(url)) return;
      if (url === baseUrl) return;
      // Filter out non-HTTP(S) schemes to prevent fetch errors in background
      if (!url.startsWith("http://") && !url.startsWith("https://")) return;
      linkSet.add(url);
      links.push(url);
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
        addLink(fullHref);
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

    return { media, links };
  }

  function parseSrcset(srcset) {
    // EXT-12: w- and x-descriptors are different scales (a 600w candidate is
    // NOT smaller than a 1.5x one) — compare only candidates of the same type.
    let bestUrl = null, bestScore = -1, bestType = null;
    srcset.split(",").forEach((item) => {
      const parts = item.trim().split(/\s+/);
      if (!parts[0]) return;
      let score = 1, type = "bare";
      if (parts[1]) {
        if (parts[1].endsWith("w")) { score = parseInt(parts[1], 10) || 1; type = "w"; }
        else if (parts[1].endsWith("x")) { score = parseFloat(parts[1]) || 1; type = "x"; }
      }
      if (bestType !== null && type !== bestType) return; // not comparable
      if (score > bestScore) { bestScore = score; bestUrl = parts[0]; bestType = type; }
    });
    return bestUrl;
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
