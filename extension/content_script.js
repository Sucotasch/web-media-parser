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
      /\/l-stat\./i, /\/userpic/i, /\/avatar/i, /\/logo\./i,
      /\/favicon/i, /\/emoji/i, /\/gravatar/i, /\/icon[s]?\//i,
      /ljcounter/i, /\/blank\./i, /\/spacer\./i, /\/pixel\./i,
      /1x1\./i, /\/spinner/i, /\/loading/i, /\.svg$/i,
      /\/button[s]?\//i, /\/badge/i, /\/arrow/i,
      /\/(nav|menu|search|cart|share|social|widget|advert|tracker)\b/i,
      /\/(prev|next|close|expand|collapse|play|pause|mute|volume)\b/i,
      /\.(gif|png|ico)$/i,
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
      linkSet.add(url);
      links.push(url);
    }

    // <img> — direct images
    doc.querySelectorAll("img").forEach((img) => {
      const src = img.currentSrc || img.src;
      if (src) addMedia(src, "image", {
        width: img.naturalWidth || img.width || 0,
        height: img.naturalHeight || img.height || 0,
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
    let bestUrl = null, bestScore = -1;
    srcset.split(",").forEach((item) => {
      const parts = item.trim().split(/\s+/);
      if (!parts[0]) return;
      let score = 1;
      if (parts[1]) {
        if (parts[1].endsWith("w")) score = parseInt(parts[1], 10) || 1;
        else if (parts[1].endsWith("x")) score = (parseFloat(parts[1]) || 1) * 10000;
      }
      if (score > bestScore) { bestScore = score; bestUrl = parts[0]; }
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
