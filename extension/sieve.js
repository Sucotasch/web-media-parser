/**
 * Imagus sieve rules engine for the browser extension.
 * Parses sieve JSON and applies transformations to media URLs.
 *
 * Supported rule fields:
 *   img  — regex to match thumbnail URL
 *   to   — substitution pattern ($1, $2...) or JS expression (starts with :)
 *   link — regex to match page URL (for domain filtering)
 *   dc   — domain code
 */

/**
 * Parse a sieve JSON file and return processed rules.
 * @param {Object} data — raw JSON from sieve file
 * @returns {Array} — array of {name, imgRegex, toPattern, isJS, toJS, linkRegex}
 */
function parseSieve(data) {
  const rules = [];
  for (const [name, rule] of Object.entries(data)) {
    if (!rule || typeof rule !== "object") continue;
    const img = rule.img || "";
    const to = rule.to || "";
    const link = rule.link || "";

    if (!img || !to) continue;

    let imgRegex;
    try {
      imgRegex = new RegExp(img, "i");
    } catch (e) {
      continue; // Invalid regex
    }

    let linkRegex = null;
    if (link) {
      try {
        linkRegex = new RegExp(link, "i");
      } catch (e) {
        // Invalid link regex — skip
      }
    }

    const isJS = typeof to === "string" && to.startsWith(":");
    const toPattern = isJS ? to.slice(1).trim() : to;

    rules.push({ name, imgRegex, toPattern, isJS, linkRegex });
  }
  return rules;
}

/**
 * Apply sieve rules to a media URL.
 * Returns the transformed URL or null if no rule matched.
 *
 * @param {string} url — the media URL to transform
 * @param {string} pageUrl — the current page URL
 * @param {Array} rules — parsed sieve rules
 * @returns {string|null} — transformed URL or null
 */
function applySieveRules(url, pageUrl, rules) {
  for (const rule of rules) {
    // Check page URL filter
    if (rule.linkRegex && !rule.linkRegex.test(pageUrl)) continue;

    // Check image URL match
    const match = url.match(rule.imgRegex);
    if (!match) continue;

    if (rule.isJS) {
      // Execute JS expression in page context
      const result = executeSieveJS(rule.toPattern, url, match, pageUrl);
      if (result && result !== url) return result;
    } else {
      // Simple regex substitution
      const result = applySubstitution(url, match, rule.toPattern);
      if (result && result !== url) return result;
    }
  }
  return null;
}

/**
 * Apply a simple regex substitution pattern.
 * Handles $1, $2, $3... and #ext# expansion.
 */
function applySubstitution(url, match, pattern) {
  // Expand $1, $2, etc.
  let result = pattern.replace(/\$(\d+)/g, (_, num) => {
    return match[parseInt(num)] || "";
  });

  // Handle #ext# expansion: "#jpg png#" → try each extension
  const extMatch = result.match(/#([^#]+)#/);
  if (extMatch) {
    const exts = extMatch[1].trim().split(/\s+/);
    if (exts.length > 0) {
      result = result.replace(extMatch[0], exts[0]);
      // Return first variant (full variant expansion would need the caller to handle)
    }
  }

  return result;
}

/**
 * Execute a JS expression from an Imagus sieve rule in the page context.
 * Provides a minimal Imagus-compatible API shim.
 *
 * @param {string} jsCode — the JS expression (after removing leading :)
 * @param {string} url — the matched thumbnail URL
 * @param {Array} match — regex match result
 * @param {string} pageUrl — current page URL
 * @returns {string|null} — transformed URL or null
 */
function executeSieveJS(jsCode, url, match, pageUrl) {
  try {
    // Build Imagus-compatible context
    const context = {
      node: document.querySelector("img[src='" + url + "']") || document.querySelector("[data-src='" + url + "']"),
      URL: pageUrl,
    };

    // Build $[0], $[1], $[2]... references
    const $ = match.slice();

    // Build the function body
    const fnBody = `
      "use strict";
      const $ = ${JSON.stringify(match.slice())};
      const document = window.document;
      const URL = window.URL;
      ${jsCode}
    `;

    // Execute in page context via script injection
    const result = new Function(fnBody)();
    if (typeof result === "string" && result.startsWith("http")) {
      return result;
    }
    return null;
  } catch (e) {
    // JS execution failed — rule is not compatible
    return null;
  }
}

/**
 * Scan a page for media and apply sieve transformations.
 * @param {Array} mediaList — scanned media from content_script
 * @param {string} pageUrl — current page URL
 * @param {Array} rules — parsed sieve rules
 * @returns {Array} — transformed media list
 */
function transformMedia(mediaList, pageUrl, rules) {
  const transformed = [];

  for (const item of mediaList) {
    const result = applySieveRules(item.url, pageUrl, rules);
    if (result) {
      // Handle newline-separated variants
      const variants = result.split("\n").filter((v) => v.trim());
      for (const variant of variants) {
        transformed.push({ ...item, url: variant.trim(), original: item.url, transformed: true });
      }
    } else {
      transformed.push(item);
    }
  }

  return transformed;
}
