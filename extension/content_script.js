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
          addLink(href.startsWith("//") ? "https:" + href : href);
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

    return { media, links };
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

  // --- Fetch linked pages to discover fullsize images ---

  async function discoverFullsizeFromLinks(links, currentUrl, maxLinks) {
    const discovered = [];
    const toCheck = links.slice(0, maxLinks || 20);

    for (const linkUrl of toCheck) {
      try {
        const resp = await fetch(linkUrl, {
          credentials: "include",
          headers: { "Accept": "text/html" },
          signal: AbortSignal.timeout(5000),
        });
        if (!resp.ok) continue;
        const contentType = resp.headers.get("content-type") || "";
        if (contentType.includes("image/") || contentType.includes("video/")) {
          // Direct media URL
          discovered.push({ url: linkUrl, type: contentType.includes("video/") ? "video" : "image", source: "link-direct" });
          continue;
        }
        if (!contentType.includes("text/html")) continue;

        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");

        const result = scanPageMedia(doc, linkUrl);
        // Only keep the largest/best images from linked pages
        result.media.forEach((item) => {
          if (item.width > 200 || item.height > 200 || item.source === "meta" || !item.width) {
            discovered.push({ ...item, source: "linked-page" });
          }
        });
      } catch (e) {
        // Skip failed fetches silently
      }
    }
    return discovered;
  }

  // --- Imagus Sieve Engine (inline) ---

  function parseSieve(data) {
    const rules = [];
    for (const [name, rule] of Object.entries(data)) {
      if (!rule || typeof rule !== "object") continue;
      const img = rule.img || "";
      const to = rule.to || "";
      const link = rule.link || "";
      if (!img || !to) continue;

      let imgRegex;
      try { imgRegex = new RegExp(img, "i"); } catch (e) { continue; }
      let linkRegex = null;
      if (link) { try { linkRegex = new RegExp(link, "i"); } catch (e) {} }

      const isJS = typeof to === "string" && to.startsWith(":");
      const toPattern = isJS ? to.slice(1).trim() : to;
      rules.push({ name, imgRegex, toPattern, isJS, linkRegex });
    }
    return rules;
  }

  function applySieveRules(url, pageUrl, rules) {
    for (const rule of rules) {
      if (rule.linkRegex && !rule.linkRegex.test(pageUrl)) continue;
      const match = url.match(rule.imgRegex);
      if (!match) continue;

      if (rule.isJS) {
        try {
          const fnBody = `"use strict"; const $ = ${JSON.stringify(match.slice())}; const document = window.document; const URL = window.URL; ${rule.toPattern}`;
          const result = new Function(fnBody)();
          if (typeof result === "string" && (result.startsWith("http") || result.startsWith("//"))) {
            return result.startsWith("//") ? "https:" + result : result;
          }
        } catch (e) {}
      } else {
        let result = rule.toPattern.replace(/\$(\d+)/g, (_, num) => match[parseInt(num)] || "");
        const extMatch = result.match(/#([^#]+)#/);
        if (extMatch) {
          const exts = extMatch[1].trim().split(/\s+/);
          if (exts.length > 0) result = result.replace(extMatch[0], exts[0]);
        }
        if (result !== url) return result;
      }
    }
    return null;
  }

  function applySieveTransformation(mediaList, pageUrl, rules) {
    if (!rules || rules.length === 0) return mediaList;
    const transformed = [];
    for (const item of mediaList) {
      const result = applySieveRules(item.url, pageUrl, rules);
      if (result) {
        transformed.push({ ...item, url: result, original: item.url, transformed: true });
      } else {
        transformed.push(item);
      }
    }
    return transformed;
  }

  // --- Full scan orchestration ---

  async function performFullScan(sieveRules) {
    // Step 1: Scan current page DOM
    const currentResult = scanPageMedia(document, window.location.href);
    let allMedia = currentResult.media;
    const linksToCheck = currentResult.links;

    // Step 2: Discover fullsize from linked pages
    if (linksToCheck.length > 0) {
      const linkedMedia = await discoverFullsizeFromLinks(linksToCheck, window.location.href, 15);
      allMedia = allMedia.concat(linkedMedia);
    }

    // Step 3: Apply sieve rules for URL transformation
    allMedia = applySieveTransformation(allMedia, window.location.href, sieveRules);

    return allMedia;
  }

  // --- Message handler ---

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "scanMedia") {
      (async () => {
        try {
          let sieveRules = [];
          try {
            const stored = await chrome.storage.local.get("sieveRules");
            if (stored.sieveRules) {
              const data = JSON.parse(stored.sieveRules);
              sieveRules = parseSieve(data);
            }
          } catch (e) {}

          const media = await performFullScan(sieveRules);
          sendResponse({ media, url: window.location.href, title: document.title });
        } catch (e) {
          sendResponse({ media: [], url: window.location.href, title: document.title, error: e.message });
        }
      })();
      return true;
    }
  });
})();
