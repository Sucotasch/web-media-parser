/**
 * Shared constants for the extension (EXT-9).
 * Single source of truth for values that were duplicated between popup.js and
 * background.js with drifting semantics.
 */

// Sources whose URLs are already full-size (not thumbnails). "sieve-to" are
// popup sieve-transforms of thumbnails — also full-size results (EXT-1).
// "a-link" = an <a href> pointing directly at an image file (the classic
// thumbnail-wrapped-in-full-size-link pattern, e.g. reactor.cc posts) — the
// URL is the anchor's target, not the <img> src, so it is a full-size
// candidate. ERR-1: without it the popup's "fullsize" filter hid these
// real full-size URLs while discovery probed the network for them.
const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct", "sieve-to", "a-link"]);

// Cap on how many linked pages the popup/background probe for fullsize
// discovery. Discovery is bounded work; media lookups are not capped.
const LINKS_CAP = 50;
