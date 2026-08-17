/**
 * Shared constants for the extension (EXT-9).
 * Single source of truth for values that were duplicated between popup.js and
 * background.js with drifting semantics.
 */

// Sources whose URLs are already full-size (not thumbnails). "sieve-to" are
// popup sieve-transforms of thumbnails — also full-size results (EXT-1).
const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct", "sieve-to"]);

// Cap on how many linked pages the popup/background probe for fullsize
// discovery. Discovery is bounded work; media lookups are not capped.
const LINKS_CAP = 50;
