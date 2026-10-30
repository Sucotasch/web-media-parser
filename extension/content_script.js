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

    // Junk matches on TOKENS, never as a substring of an arbitrary filename.
    // Errors.txt (reactor.cc): the old /\/arrow/i tested the whole URL, so every
    // /pics/post/full/Arrow-SG-…jpeg — the fullsize targets the page itself
    // carries in its anchor hrefs — was dropped, and the popup showed only
    // unrelated linked-img hits from the link probe. Same class of bug for
    // /\/badge/i, /\/avatar/i, /\/loading/i … and for the \b patterns, which cut
    // Play-Station-5.jpg and Search-and-rescue.jpg.
    // Tokens follow the desktop classifier (src/parser/utils.py: split on
    // non-word characters, compare WHOLE tokens) rather than prefixes.
    const JUNK_TOKENS = new Set([
      // icons / chrome / placeholders
      "icon", "icons", "logo", "logos", "favicon", "favicons", "badge", "badges",
      "button", "buttons", "emoji", "emojis", "arrow", "arrows", "spinner",
      "spinners", "loading", "blank", "spacer", "spacers", "pixel", "pixels",
      "1x1", "2x2", "3x3",
      // avatars / user pictures
      "avatar", "avatars", "userpic", "userpics", "gravatar", "ljcounter",
      // site chrome segments
      "nav", "navigation", "menu", "search", "cart", "share", "social",
      "widget", "advert", "tracker",
      "prev", "next", "close", "expand", "collapse", "play", "pause",
      "mute", "volume",
    ]);

    // Host LABELS are matched whole too: "l-stat.livejournal.com" and
    // "l-userpic.livejournal.com" are counter/userpic hosts, while
    // "img10.reactor.cc" is not. (The old substring patterns flagged whole hosts
    // by accident, which is how a legit CDN could be dropped.)
    const JUNK_HOST_LABELS = new Set([
      "l-stat", "l-userpic", "userpic", "userpics", "avatar", "avatars",
      "ljcounter",
    ]);

    // E-09: desktop parity — the ad/tracker tables below are the same data as
    // junk_filter.AD_HOST_SUFFIXES and utils._AD_KEYWORDS (pinned by
    // tests/test_junk_parity.py). Without them "Save in Chrome" happily
    // downloaded ad creatives the app itself drops. Hosts match with a dot
    // boundary; tokens match whole (see isAdKeyword).
    const AD_HOST_SUFFIXES = [
      "doubleclick.net", "googlesyndication.com", "googleadservices.com",
      "googletagmanager.com", "google-analytics.com", "googletagservices.com",
      "adnxs.com", "appnexus.com", "taboola.com", "outbrain.com",
      "criteo.com", "criteo.net", "rubiconproject.com", "pubmatic.com",
      "openx.net", "moatads.com", "moat.com", "liadm.com", "serving-sys.com",
      "scorecardresearch.com", "quantserve.com", "casalemedia.com",
      "adsafeprotected.com", "advertising.com", "adtechus.com",
      "adform.net", "adform.com", "adservice.google.com", "2mdn.net",
      "everesttech.net", "media.net", "contextweb.com", "mathtag.com",
      "krxd.net", "sovrn.com", "yieldmo.com", "yieldlab.net", "smartadserver.com",
      "cpx.to", "clickbooth.com", "clickbank.net", "revcontent.com",
      "mgid.com", "teads.tv", "teads.com", "sekindo.com", "tidaltv.com",
      "spotxchange.com", "springserve.com", "freewheel.tv", "gfpkrw.net",
      "ttwnz.com", "exelator.com", "adglare.net", "adskeeper.com",
      "adsterra.com", "propellerads.com", "popads.net", "onclickads.net",
      "revenuehits.com", "juicyads.com", "exoclick.com", "ero-advertising.com",
      "trafficfactory.biz", "trafficjunky.net", "exosrv.com", "badoink.com",
    ];

    // Same tokens as utils._AD_KEYWORDS ("pixel" is deliberately absent:
    // pixel-art galleries are legitimate content).
    const AD_KEYWORDS = [
      "ads", "advert", "advertisement", "advertising", "adserver", "adservice",
      "banner", "banners", "promo", "promotion", "promotions",
      "sponsor", "sponsors", "sponsored",
      "tracking", "tracker", "trackers",
      "analytics", "marketing", "campaign", "campaigns",
      "popup", "popups", "popover", "popovers",
      "cta", "ctas", "calltoaction", "call-to-action",
      "adsense", "doubleclick", "googlesyndication", "googleadservices",
      "adnxs", "taboola", "outbrain", "adservice", "adroll", "criteo",
      "impression", "beacon", "affiliate", "popunder",
      "mgid", "revcontent", "juicyads", "exoclick", "propellerads", "popads",
    ];

    // A URL-wide substring test is safe for these two: neither can be a prefix
    // of a content filename.
    const JUNK_URL_PATTERNS = [
      /\$\{/i,             // un-interpolated template placeholder (products/${imgSrc})
      /\.svg([?#]|$)/i,    // SVG is vector chrome (icons/logos), never scraped media
      // EXT-8: no hard format drop here — GIF/PNG/ICO filtering belongs to the
      // desktop app's format allowlist (which keeps .png ON by default). A
      // gallery with PNG art was invisible to "Save (Chrome)" otherwise.
    ];

    // Unicode-aware split, like Python's \w: a Cyrillic filename must yield
    // tokens (and therefore be KEPT), not split into nothing. `_` is a word
    // character in Python, so it is one here too (E-09: `icon_arrow.png` is
    // one token, exactly as the desktop classifies it).
    const TOKEN_SPLIT = /[^\p{L}\p{N}_]+/u;

    function tokens(text) {
      return (text || "").toLowerCase().split(TOKEN_SPLIT).filter(Boolean);
    }

    function isJunkToken(token) {
      return JUNK_TOKENS.has(token);
    }

    // Two rules mirror utils._matches_ad_keyword exactly: the token equals a
    // keyword, or extends it by a trailing "s" (banners, popads). Hyphens are
    // ignored on both sides so "call-to-action" matches its own spelling.
    function isAdKeyword(token) {
      for (const kw of AD_KEYWORDS) {
        const k = kw.replace(/-/g, "");
        const t = token.replace(/-/g, "");
        if (t === k) return true;
        if (t.length === k.length + 1 && t.startsWith(k) && t.endsWith("s")) return true;
      }
      return false;
    }

    function isJunkUrl(url) {
      if (JUNK_URL_PATTERNS.some(p => p.test(url))) return true;
      let parsed;
      try { parsed = new URL(url); } catch (e) { return false; }

      // Host label: one junk label is enough.
      const hostLabels = (parsed.hostname || "").toLowerCase().split(".");
      if (hostLabels.some(l => JUNK_HOST_LABELS.has(l))) return true;

      // Decoded, so a non-Latin name yields real tokens instead of percent
      // escapes (a Cyrillic content name must be judged on its own words).
      let rawPath = parsed.pathname || "";
      try { rawPath = decodeURIComponent(rawPath); } catch (e) { /* keep it encoded */ }

      // E-09: ad/tracker host suffix (dot boundary) or an ad token anywhere in
      // host + path. Strong signals — they decide alone, like the desktop gate.
      const host = (parsed.hostname || "").toLowerCase();
      if (AD_HOST_SUFFIXES.some(s => host === s || host.endsWith("." + s))) return true;
      if (tokens(host + "/" + rawPath).some(isAdKeyword)) return true;

      // Directory segments: `.../icons/x.png`, `.../images/avatar/1.jpg`.
      const segments = rawPath.split("/").filter(Boolean);
      const file = segments.length > 0 ? segments[segments.length - 1] : "";
      const dirs = segments.slice(0, -1);
      if (dirs.some(seg => tokens(seg).some(isJunkToken))) return true;

      // Filename: junk only when the WHOLE name is chrome — "arrow.png",
      // "spacer-2x2.gif", "logo_2.png" — never a content name that merely
      // contains the word ("Arrow-SG-Arrow-Marzetti-SG-(TAM)-….jpeg",
      // "Play-Station-5.jpg"). Digits are neutral, so numbering can't rescue a
      // chrome name, but a purely numeric name is never junk on its own.
      const stem = file.replace(/\.[a-z0-9]+$/i, "");
      const fileTokens = tokens(stem);
      if (fileTokens.length === 0) return false;
      return fileTokens.some(isJunkToken)
        && fileTokens.every(t => isJunkToken(t) || /^\d+$/.test(t));
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

    const IMAGE_FILE_RE = /\.(jpe?g|jfif|jif|png|gif|webp|bmp|tiff?|avif|heic)(\?|$)/i;

    // D-03: parity with the desktop app's DEFAULT_MAX_LINKS_PER_PAGE (200), and
    // ONLY for navigation links. A page with 1000 nav anchors (tag lists, page
    // numbers, archives) used to flood discovery, which works the list in order
    // — the real thumbnail links sat behind all of it and the page budget ran
    // out before they were probed. Media-bearing links are never capped: they
    // ARE the result, and dropping them would lose media with no upside.
    const MAX_PLAIN_LINKS = 200;

    function addLink(url, presentsMedia = true) {
      url = toAbsolute(url);
      if (!url || linkSet.has(url)) return;
      if (url === baseUrl) return;
      // Filter out non-HTTP(S) schemes to prevent fetch errors in background
      if (!url.startsWith("http://") && !url.startsWith("https://")) return;
      if (!presentsMedia && linkRest.length >= MAX_PLAIN_LINKS) return;
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
          // Errors.txt (secondary): when the parent anchor IS the media file
          // (<a href="…/pics/post/full/….jpeg"><img src="…thumbnail….jpeg"></a>)
          // the a[href] pass below already collects it as media with
          // source "a-link" (a FULLSIZE_SOURCE). Adding it here as well made the
          // background probe the image URL as a PAGE, spending discovery budget
          // on a file that can never contain links.
          const absHref = toAbsolute(href);
          if (!absHref || !IMAGE_FILE_RE.test(absHref)) addLink(href);
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
      if (IMAGE_FILE_RE.test(fullHref)) {
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

    // --- JS-REL-EXT: media literals inside inline <script> data ---------------
    // Gallery pages that render their cards from a data array
    // (`const D=[{"thumb":"thumbs/x.webp","full":"png/x.png"}]`) put the full-size
    // path in the SCRIPT TEXT, not in any attribute, so a DOM-only scan reports
    // only the reduced rendition (Errors.txt, ai-costume-gallery.pages.dev).
    // The text is still in the DOM (script.textContent) — no page-world access
    // or network round-trip is needed.
    //
    // Only a quoted literal ending in a media extension matches, so page/script
    // extensions (.html/.json/.css) can never be collected; the scheme filter,
    // the junk table and the ${} template check still run in addMedia below.
    const JS_MEDIA_LITERAL_RE = /["']([^"'\s]+\.(?:jpe?g|jfif|jif|png|gif|webp|bmp|tiff?|avif|heic|mp4|webm|ogv|ogg|mov|m4v|mkv|avi|flv|wmv|ts|mpeg|mpg|m3u8|mpd)(?:[?#][^"'\s]*)?)["']/gi;
    const JS_LITERAL_VIDEO_RE = /\.(?:mp4|webm|ogv|ogg|mov|m4v|mkv|avi|flv|wmv|ts|mpeg|mpg|m3u8|mpd)(?:[?#]|$)/i;
    // A single page can inline megabytes of JSON; scan but do not chew on a
    // pathological document.
    const JS_SCAN_MAX_CHARS = 2000000;
    // Soft hints, same data as the desktop's K.THUMBNAIL_URL_HINTS. They only
    // ever classify an item that a same-stem fullsize sibling also covers —
    // never a standalone drop rule (see dropShadowThumbnails).
    const THUMB_HINTS = ["/thumb", "/thumbs/", "/thumbnail", "_thumb", "-thumb",
                         "/small/", "/s/", "/preview/", "/lqip/", "w=150", "w=200"];

    function isThumbHintUrl(url) {
      const lower = (url || "").toLowerCase();
      return THUMB_HINTS.some((h) => lower.includes(h));
    }

    function fileStem(url) {
      try {
        const path = new URL(url, baseUrl).pathname;
        const base = path.slice(path.lastIndexOf("/") + 1);
        const dot = base.lastIndexOf(".");
        return (dot > 0 ? base.slice(0, dot) : base).toLowerCase();
      } catch (e) {
        return "";
      }
    }

    (doc.querySelectorAll("script") || []).forEach((tag) => {
      // External scripts are fetched, not inlined — their text is empty anyway,
      // but a stub (or a CDN tag with body text) must not be scanned.
      if (tag.hasAttribute && tag.hasAttribute("src")) return;
      const text = tag.textContent || "";
      if (!text || text.length > JS_SCAN_MAX_CHARS) return;
      JS_MEDIA_LITERAL_RE.lastIndex = 0;
      let m;
      while ((m = JS_MEDIA_LITERAL_RE.exec(text)) !== null) {
        // An un-interpolated `${id}` is a TEMPLATE PIECE, not a path: URL
        // serialization percent-encodes the braces, so the junk table's
        // /\$\{/ check (which runs on the resolved URL) can no longer see it.
        if (m[1].includes("${")) continue;
        const abs = toAbsolute(m[1]);
        if (!abs) continue;
        addMedia(abs, JS_LITERAL_VIDEO_RE.test(abs) ? "video" : "image", {
          // A literal that looks like a rendition stays in the Thumbnails
          // bucket; the plain one is a full-size candidate (FULLSIZE_SOURCES).
          source: isThumbHintUrl(abs) ? "js-thumb" : "js-literal",
        });
      }
    });

    // --- WP-2.4 mirror: drop a thumbnail only when its fullsize twin is here --
    // A gallery that ships both renditions under the SAME file name
    // (thumbs/x.webp + img/x.webp + png/x.png) has a true duplicate: keeping the
    // reduced one only pads the list and outbids the original for the on-disk
    // name. A thumbnail WITHOUT a same-stem, non-thumbnail sibling — the classic
    // thumb -> viewer page shape, where the original is one link away — is kept,
    // and `links` is never touched by this rule.
    function dropShadowThumbnails(items) {
      if (items.length < 2) return items;
      const fullsizeStems = new Set();
      for (const it of items) {
        if (isThumbHintUrl(it.url)) continue;
        const stem = fileStem(it.url);
        if (stem) fullsizeStems.add(stem);
      }
      if (!fullsizeStems.size) return items;
      return items.filter((it) => {
        const stem = fileStem(it.url);
        return !(stem && fullsizeStems.has(stem) && isThumbHintUrl(it.url));
      });
    }

    // Media-bearing links first: discovery works the list in order, so a link that shows
    // a thumbnail must not sit behind a "Watched"/"Faq" link.
    return { media: dropShadowThumbnails(media), links: links.concat(linkRest) };
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
