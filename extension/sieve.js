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
  // D-08: a rule dropped for a broken regex used to vanish without a trace.
  // It is logged per rule at debug level and counted once at the end.
  let unparsed = 0;
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
      unparsed++;
      if (typeof console !== "undefined" && console.debug) {
        console.debug(`Sieve rule skipped (invalid img regex): ${name} — ${e.message}`);
      }
      continue;
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
  if (unparsed > 0 && typeof console !== "undefined" && console.warn) {
    console.warn(`Sieve: ${unparsed} rule(s) could not be parsed`);
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

    // A-09: try BOTH the full URL and its scheme-stripped twin. 307 of the
    // bundled rules anchor on `^` and expect a host-relative string, so
    // matching the full URL alone never fired for them.
    const match = matchRuleUrl(rule.imgRegex, url);
    if (!match) continue;

    if (rule.isJS) {
      // Execute JS expression in page context (MV3 CSP: returns null)
      const result = executeSieveJS(rule.toPattern, url, match, pageUrl);
      if (result && result !== url) return [normalizeVariant(result, pageUrl)];
      continue; // a JS rule that cannot run must not mask later rules
    }

    // Simple regex substitution — only the FIRST matching rule applies, so
    // its variants are the complete result for this URL.
    // NF-1: every variant leaves here as an absolute URL (protocol-relative
    // became `file://…` in Chrome and `MissingSchema` on the desktop).
    const variants = applySubstitutionAll(url, match, rule.toPattern)
      .map((v) => normalizeVariant(v, pageUrl))
      .filter((v) => v && v !== url);
    if (variants.length > 0) return variants;
  }
  return [];
}

/** The scheme-stripped form of a URL (`https://a/b` → `a/b`). */
function schemeStripped(url) {
  return typeof url === "string" ? url.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "") : "";
}

/**
 * A-09: all forms a sieve `img` regex is allowed to match against — the full
 * URL first, then the scheme-stripped one. Returns the match array (with
 * `.index`/`.input`, which `$`` and `$'` need) or null.
 */
function matchRuleUrl(imgRegex, url) {
  if (!imgRegex || typeof url !== "string") return null;
  const stripped = schemeStripped(url);
  const forms = stripped && stripped !== url ? [url, stripped] : [url];
  for (const form of forms) {
    imgRegex.lastIndex = 0;
    const match = form.match(imgRegex);
    if (match) return match;
  }
  return null;
}

/**
 * NF-1: make a produced variant absolute against the page it came from.
 *
 * `//cdn/x.jpg` takes the PAGE's scheme (hardcoding https broke http pages),
 * a scheme-less/relative result is resolved with the URL parser, and anything
 * that already carries a scheme (`http:`, `data:`, `blob:` …) is left alone.
 */
function normalizeVariant(variant, pageUrl) {
  if (typeof variant !== "string") return "";
  const v = variant.trim();
  if (!v) return "";
  if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return v;
  const base = pageUrl
    || (typeof location !== "undefined" && location && location.href)
    || "";
  if (!base) return v;
  try {
    return new URL(v, base).href;
  } catch (e) {
    return v;
  }
}

/**
 * ECMAScript GetSubstitution — the spec behind String.prototype.replace, and
 * the ONE substitution shared by the extension (here, background.js's url
 * templates) and the desktop app (`site_pattern_manager.get_substitution`).
 *
 * Implemented rather than delegated to `String.prototype.replace` because the
 * result must be expanded FURTHER (`#ext#` lists, several template lines) and
 * because two independent implementations is exactly what drifted before:
 * `$&` was never substituted here, and a two-digit reference beyond the
 * capture count collapsed to an empty string instead of the spec's fallback.
 *
 * $$      → a literal "$"
 * $&      → the whole match
 * $` $'   → the text before / after the match
 * $n $nn  → capture n; beyond the capture count a TWO-digit reference falls
 *           back to its first digit plus the second as literal text
 *           ("$11200" with 2 groups = group 1 then "1200"); anything else
 *           out of range stays literal ("$99")
 *
 * @param {string} template — the rule's `to`
 * @param {Array} match — result of `RegExp.exec`/`String.match` (0 = whole)
 * @param {string} [input] — the string the regex ran against
 * @returns {string}
 */
function getSubstitution(template, match, input) {
  if (typeof template !== "string") return "";
  const groups = match || [];
  const captureCount = Math.max(0, groups.length - 1);
  const whole = groups[0] === undefined ? "" : groups[0];
  const source = typeof input === "string"
    ? input
    : (typeof groups.input === "string" ? groups.input : "");
  const at = typeof groups.index === "number" ? groups.index : 0;
  const isDigit = (c) => c >= "0" && c <= "9";
  const capture = (n) => (groups[n] === undefined ? "" : groups[n]);

  let out = "";
  for (let i = 0; i < template.length; i++) {
    const ch = template[i];
    if (ch !== "$") {
      out += ch;
      continue;
    }
    const next = template[i + 1];
    if (next === undefined) {
      out += "$";
      continue;
    }
    if (next === "$") { out += "$"; i++; continue; }
    if (next === "&") { out += whole; i++; continue; }
    if (next === "`") { out += source.slice(0, at); i++; continue; }
    if (next === "'") { out += source.slice(at + whole.length); i++; continue; }
    if (isDigit(next)) {
      let digits = next;
      const third = template[i + 2];
      if (third !== undefined && isDigit(third)) digits += third;
      const num = parseInt(digits, 10);
      const first = parseInt(digits[0], 10);
      if (num >= 1 && num <= captureCount) {
        out += capture(num);
      } else if (digits.length === 2 && first >= 1 && first <= captureCount) {
        // Spec fallback: the first digit still names a real group.
        out += capture(first) + digits[1];
      } else {
        out += "$" + digits;
      }
      i += digits.length;
      continue;
    }
    out += "$";
  }
  return out;
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
 * Full expansion of a substitution pattern (A-10/A-12/NF-7): the spec
 * substitution, then EVERY `#ext1 ext2#` block and EVERY template line.
 *
 * @returns {string[]} — one entry for a plain pattern, N for an #ext# list
 */
function applySubstitutionAll(url, match, pattern) {
  return expandVariants(getSubstitution(pattern, match, match && match.input));
}

/**
 * Expand a substituted template into its variants: `\n`-separated lines, and
 * every `#a b#` block as one entry per alternative (nested blocks combine).
 *
 * @param {string} text
 * @returns {string[]}
 */
function expandVariants(text) {
  const out = [];
  if (typeof text !== "string" || !text) return out;

  const expandBlock = (line) => {
    const m = /#([^#]+)#/.exec(line);
    if (!m) {
      out.push(line);
      return;
    }
    const prefix = line.slice(0, m.index);
    const suffix = line.slice(m.index + m[0].length);
    for (const opt of m[1].trim().split(/\s+/).filter(Boolean)) {
      expandBlock(prefix + opt + suffix);
    }
  };

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (line) expandBlock(line);
  }
  return out;
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
function executeSieveJS(_jsCode, _url, _match, _pageUrl) {
  // MV3 CSP strictly forbids eval() and new Function(). Sieve rules that require
  // arbitrary JS execution are handled in the desktop app (via Deno engine);
  // in the browser extension they return null so static fallbacks apply.
  return null;
}

// R-E-11/R-E-2: `transformMedia` (a newline-splitting copy of the upgrade
// pipeline that guarded on `typeof … === "function"` and was never called)
// is gone — background.js's upgradeMediaList() is the single pipeline, and
// applySieveRulesAll() is the single variant contract it builds on.
