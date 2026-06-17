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
    const mediaLinks = [];

    const JUNK_PATTERNS = [
      /\/l-stat\./i, /\/userpic/i, /\/avatar/i, /\/logo\./i,
      /\/favicon/i, /\/emoji/i, /\/gravatar/i, /\/icon[s]?\//i,
      /ljcounter/i, /\/blank\./i, /\/spacer\./i, /\/pixel\./i,
      /1x1\./i, /\/spinner/i, /\/loading/i, /\.svg$/i,
      /\/button[s]?\//i, /\/badge/i, /\/arrow/i,
      /l-files\.livejournal\.net\/userhead/i,
      /l-userpic\.livejournal\.com/i,
      /xc3\.services\.livejournal\.com\/ljcounter/i,
    ];

    function isJunkUrl(url) {
      return JUNK_PATTERNS.some(p => p.test(url));
    }

    function addMedia(url, type, attrs = {}) {
      if (!url || seen.has(url)) return;
      if (url.startsWith("//")) url = "https:" + url;
      if (!url.startsWith("http://") && !url.startsWith("https://")) return;
      if (seen.has(url)) return;
      if (isJunkUrl(url)) return;
      if (attrs.width && attrs.height && attrs.width < 50 && attrs.height < 50) return;
      seen.add(url);
      media.push({ url, type, pageUrl: baseUrl, ...attrs });
    }

    function addLink(url) {
      if (!url || linkSet.has(url)) return;
      if (url.startsWith("//")) url = "https:" + url;
      if (!url.startsWith("http://") && !url.startsWith("https://")) return;
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
          const fullHref = href.startsWith("//") ? "https:" + href : href;
          if (!mediaLinks.includes(fullHref)) mediaLinks.push(fullHref);
        }
      }
    });

    // <a> tags wrapping nothing but linking to images
    doc.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      const fullHref = href.startsWith("//") ? "https:" + href : href;
      if (fullHref === baseUrl) return;
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
      if (content && (content.startsWith("http") || content.startsWith("//"))) {
        addMedia(content, "image", { source: "meta" });
      }
    });

    return { media, links, mediaLinks };
  }

  function parseSrcset(srcset) {
    let bestUrl = null, maxWidth = 0;
    srcset.split(",").forEach((item) => {
      const parts = item.trim().split(/\s+/);
      if (parts.length >= 2) {
        const width = parseInt(parts[1]);
        if (!isNaN(width) && width > maxWidth) { maxWidth = width; bestUrl = parts[0]; }
      }
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
    if (request.action === "scanMedia") {
      (async () => {
        try {
          const result = await performFullScan();
          sendResponse({ media: result.media, links: result.links, mediaLinks: result.mediaLinks, url: window.location.href, title: document.title, userAgent: navigator.userAgent });
        } catch (e) {
          sendResponse({ media: [], url: window.location.href, title: document.title, error: e.message });
        }
      })();
      return true;
    }
  });
})();
