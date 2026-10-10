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
    // R-E-9: `off: 1` is how the sieve (and mergeSieve's ghosts, and the
    // desktop app via site_pattern_manager) disables a rule. Skipping it here
    // keeps one rule set valid for every consumer; the bundled sieve ships 8
    // of them, and they used to keep transforming URLs in the extension.
    if (rule.off) continue;
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
 * Count rules whose `to` is a JS expression (starts with ':').
 *
 * MV3 CSP forbids new Function() in the popup, so JS rules are silently
 * skipped there (EXT-7) — this count lets the UI tell the user exactly how
 * many of the loaded rules actually apply in the popup.
 * @param {Object} data — raw JSON from sieve file
 * @returns {number}
 */
function countJsRules(data) {
  let n = 0;
  for (const [name, rule] of Object.entries(data)) {
    if (!rule || typeof rule !== "object") continue;
    const to = rule.to || "";
    if (typeof to === "string" && to.startsWith(":")) n++;
  }
  return n;
}

/**
 * Apply sieve rules to a media URL.
 * Returns the transformed URL or null if no rule matched.
 *
 * Thin wrapper over applySieveRulesAll (R-E-2): one rule set, one contract —
 * keeping a second variant-selecting implementation here is what let the two
 * drift apart in the first place.
 *
 * @param {string} url — the media URL to transform
 * @param {string} pageUrl — the current page URL
 * @param {Array} rules — parsed sieve rules
 * @returns {string|null} — transformed URL or null
 */
function applySieveRules(url, pageUrl, rules) {
  const variants = applySieveRulesAll(url, pageUrl, rules);
  return variants.length > 0 ? variants[0] : null;
}

/**
 * Apply sieve rules and return EVERY URL variant produced by the first
 * matching rule (R-E-2).
 *
 * Imagus `to` patterns can expand to several extensions ("#jpg png#", up to
 * 133 rules in the bundled sieve); the old single-result form silently kept
 * only the first variant, and the callers' newline split could never see the
 * rest because nothing generated a newline.
 *
 * @param {string} url — the media URL to transform
 * @param {string} pageUrl — the current page URL
 * @param {Array} rules — parsed sieve rules
 * @returns {string[]} — variant URLs (empty when no rule matched)
 */
function applySieveRulesAll(url, pageUrl, rules) {
  for (const rule of rules) {
    // Check page URL filter
    if (rule.linkRegex && !rule.linkRegex.test(pageUrl)) continue;

    // Check image URL match
    const match = url.match(rule.imgRegex);
    if (!match) continue;

    if (rule.isJS) {
      // Execute JS expression in page context (MV3 CSP: returns null)
      const result = executeSieveJS(rule.toPattern, url, match, pageUrl);
      if (result && result !== url) return [result];
      continue; // a JS rule that cannot run must not mask later rules
    }

    // Simple regex substitution — only the FIRST matching rule applies, so
    // its variants are the complete result for this URL.
    const variants = applySubstitutionAll(url, match, rule.toPattern)
      .filter((v) => v && v !== url);
    if (variants.length > 0) return variants;
  }
  return [];
}

/**
 * Apply a simple regex substitution pattern.
 * Handles $1, $2, $3... and #ext# expansion.
 *
 * @returns {string} — the first variant (kept for existing callers/tests)
 */
function applySubstitution(url, match, pattern) {
  return applySubstitutionAll(url, match, pattern)[0];
}

/**
 * Full expansion of a substitution pattern: $1, $2, … plus one URL per
 * "#ext1 ext2#" alternative.
 *
 * @returns {string[]} — one entry for a plain pattern, N for an #ext# list
 */
function applySubstitutionAll(url, match, pattern) {
  // Expand $1, $2, etc.
  let result = pattern.replace(/\$(\d+)/g, (_, num) => {
    return match[parseInt(num)] || "";
  });

  // Handle #ext# expansion: "#jpg png#" → one URL per extension
  const extMatch = result.match(/#([^#]+)#/);
  if (extMatch) {
    const exts = extMatch[1].trim().split(/\s+/).filter(Boolean);
    if (exts.length > 0) {
      return exts.map((ext) => result.replace(extMatch[0], ext));
    }
  }

  return [result];
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

// R-E-11/R-E-2: `transformMedia` (a newline-splitting copy of the upgrade
// pipeline that guarded on `typeof … === "function"` and was never called)
// is gone — background.js's upgradeMediaList() is the single pipeline, and
// applySieveRulesAll() is the single variant contract it builds on.
