# Agent Development Guide — Media Discovery, Link Selection, Junk Filtering, Anti-Bot

| | |
|---|---|
| **Audience** | Agent / developer with **no prior project knowledge** |
| **Goal** | Improve fullsize media discovery, crawl-link selection, junk filtering, gateway/anti-bot handling — **without** changing product concept |
| **Rules** | No speculative architecture; verify against real code; max benefit / min complexity; no regressions |
| **Related** | `AGENTS.md`, `CONTEXT.md`, `Audit/FULL_AUDIT_2026-07-16.md`, `Audit/CHROME_EXTENSION_AUDIT_2026-07-16.md` |
| **Date** | 2026-07-16 |

This document is a **work plan + map of the real code**. Fix P0 bugs from audits first if they block testing (especially Pause/Resume `downloaded_files`, session paths, extension cookies). Then implement work packages below in order.

---

## 0. Product concept (do not abandon)

```
User URL (or extension)
  → fetch HTML (browser-like headers, optional proxy/cookies)
  → optional gateway/age/cookie consent bypass
  → extract media + links
  → thumbnail URL → fullsize via patterns/Imagus sieve
  → prioritize “content” links, deprioritize navigation
  → download media (Referer, shared session, domain limits)
```

**Not in scope for this track:** rewriting GUI, new cloud services, headless Chromium as default (too heavy — only as optional last resort). Prefer **static HTML + smart heuristics + rules + lightweight HTTP bypass**.

---

## 1. 15-minute onboarding

### 1.1 Run / test

```bash
# Windows, from repo root
.\venv\Scripts\activate
pip install -r requirements.txt
pip install pytest
python main.py
python -m pytest tests -q
```

### 1.2 Critical files (this track only)

| File | Responsibility |
|------|----------------|
| `src/parser/webpage_parser.py` | Fetch HTML, gateways, extract images/videos/links, visibility |
| `src/parser/site_pattern_manager.py` | Native patterns + Imagus sieve `img`/`to` → fullsize |
| `src/parser/priority_url_queue.py` | Which URLs are crawled next (priority heap) |
| `src/parser/parser_manager.py` | Workers, stop-words, domain blocklist, enqueue media |
| `src/parser/utils.py` | `is_media_url`, `is_trash_media`, `is_banner_or_ad`, `normalize_url` |
| `src/parser/shared_session.py` | aiohttp session headers / extension cookies |
| `src/constants.py` | Defaults, stop words, gateway texts, ignore patterns |
| `resources/patterns/site_patterns.json` | Built-in site rules |
| `extension/*` | Browser-side scan + sieve `res` (optional parallel track) |
| `tests/test_*.py` | Unit tests — **extend here for every change** |

### 1.3 Hard invariants (break these → regressions)

1. **One `ParserManager` + one new `QThread` per task** — do not reuse after stop.
2. **Asyncio primitives only on the event-loop thread** (`start_parsing` / `_main_task`).
3. **Domain concurrency ≤ `DOMAIN_CONCURRENCY_LIMIT` (2)** — do not raise casually.
4. **Downloader shared session: `Retry(total=0)`** — keeps Stop responsive.
5. **`one_shot=True`**: no link following (`_process_parser_results` early return).
6. **Constants/settings keys** live in `src/constants.py`; GUI keys must stay aligned.
7. Paths via `src/app_paths.py` for portable builds.

### 1.4 End-to-end algorithm (desktop crawl)

```
ParserManager._parser_worker
  → url_queue.get
  → WebpageParser.parse() / JSONWebpageParser
       _get_content (aiohttp → requests fallback, 429 backoff, JS redirect)
       _extract_images / _extract_videos
       _handle_gateways → re-parse with cookies
       _extract_links
       optional JS string scan (_handle_dynamic_content)
  → _process_parser_results
       media → download_queue (via _process_media_batch)
       links → url_queue (if not one_shot), filtered by blocklist / stay_in_domain / stop_words
  → PriorityURLQueue.put (priority 0 → dropped as “not related”)
  → _downloader_worker → MediaDownloader
```

Fullsize path for a single `<img>` (real code):

1. `_get_best_image_url` picks best candidate (`src`, `data-src`, `srcset`, hi-res attrs).
2. `pattern_manager.transform_image_url(url, page_url)` applies sieve/native rules.
3. If parent `<a href>` is a **webpage**, thumbnail may be **skipped** and the page is queued with high priority (`from_image`, priority 15).
4. If parent `<a>` is a **direct image URL**, it is added as media with `source=parent-link` / `fullsize-link`.

---

## 2. Current capabilities vs gaps (verified in code)

### 2.1 Fullsize discovery — what already works

| Mechanism | Where | Notes |
|-----------|--------|------|
| data-* hi-res attrs | `webpage_parser._get_best_image_url` | `data-full`, `data-original`, … prioritized |
| srcset w / x | `_parse_srcset` | Density `x` → synthetic width |
| Parent link to image | `_extract_images` | Direct fullsize |
| Parent link to page | `_extract_images` | Queue crawl, skip thumb |
| Imagus `img`/`to` (+ some JS→Python) | `SitePatternManager.transform_image_url` | Domain-indexed sieve |
| Native JSON patterns | `site_patterns.json` via `image_transformations` | Regex replace |
| Interstitial HTML as media | downloader error → re-queue URL as page | `interstitial_retry` |
| Extension `res` scrape | `extension/background.js` | Separate path; not used by desktop crawler |

### 2.2 Fullsize — real gaps (cheap wins first)

| Gap | Evidence | Risk if ignored |
|-----|----------|-----------------|
| `SIGNIFICANT_MEDIA_IGNORE_PATTERNS` contains `"thumb"`, `"thumbnail"` | `constants.py` | **Drops real content** whose CDN path contains `/thumb/` even after sieve upgrade to fullsize if filter runs on original or transformed URL incorrectly — currently filter uses **final** URL; still blocks thumbs that never transform |
| Sieve JS needing DOM is skipped | `site_pattern_manager._needs_dom` | Fewer fullsizes on complex sites |
| No generic URL rewrite heuristics (strip `_s`, `/s/`, `?w=`) beyond global patterns | `_apply_global_transformations` only if configured | Miss simple CDN conventions |
| Videos: almost no significance filter | `_extract_videos` appends without `_is_significant_media` | Ad embeds / tiny players downloaded |
| No HEAD probe to confirm transformed URL is image | — | False fullsize → waste download |
| Extension fullsize not shared with desktop crawl | Two stacks | Duplicate effort |

### 2.3 Link selection — what already works

| Mechanism | Where |
|-----------|--------|
| Priority heap + media boost | `priority_url_queue._calculate_url_priority` |
| “Downward / related” path check | `_is_downward_url` (priority 0 → skip) |
| Homepage deprioritize | `is_homepage` × 0.005 |
| Content path boost | `_is_likely_content_page` |
| `from_image` × 20 | context from parent `<a>` |
| stop_words | `parser_manager` + Settings |
| domain blocklist file | `ParserManager._load_domain_blocklist` |
| stay_in_domain | settings |

### 2.4 Link selection — gaps

| Gap | Evidence |
|-----|----------|
| `DEFAULT_STOP_WORDS` too sparse | `login`, `register`, … missing `privacy`, `terms`, `advert`, `/tag/`, `sitemap` as **path segments** |
| Settings dialog has richer stop list than `DEFAULT_STOP_WORDS` | Divergence — new installs differ |
| Stop words use **substring** match | `"ad"` would kill `admin` / `media` if added carelessly — use segment-aware matching |
| `_extract_links` adds **all** visible anchors | No ad/legal filter at extract time — only later stop_words |
| Link **text** available in context but unused for scoring | `links[url] = { text: ... }` |
| Priority 0 hard-drop of “unrelated” siblings | May miss same-gallery pages on some URL shapes |

### 2.5 Junk filtering — what already works

| Mechanism | Where |
|-----------|--------|
| `is_trash_media` (.gif/.ico/.svg/.cur) | `utils.py` |
| `is_banner_or_ad` | keywords + aspect ratio + 1×1 |
| `SIGNIFICANT_MEDIA_IGNORE_PATTERNS` | icons, social, trackers |
| Hidden / honeypot elements | `_is_element_visible` |
| Min image dimensions | settings + `SIGNIFICANT_MEDIA_MIN_DIMENSION` |
| Min file size KB | downloader HEAD |

### 2.6 Junk — gaps / footguns

| Issue | Detail |
|-------|--------|
| `"thumb"` in ignore list | Blocks legitimate paths; prefer dimension/size over substring `thumb` |
| `"ad-"` substring | Can false-positive rare paths; prefer path-segment rules |
| All GIFs trash | Content GIFs lost — make setting |
| Favicon / apple-touch still sometimes kept | `_extract_images` link rel icon branch |
| No filter on **outbound crawl links** for `/privacy`, `/legal` | Only weak stop_words |

### 2.7 Gateway / anti-bot — what already works

| Mechanism | Where |
|-----------|--------|
| Pre-set consent + age cookies on request | `_get_content` |
| 429 + Retry-After | aiohttp loop |
| aiohttp fail → `requests` fallback (`verify=False`) | TLS fingerprint blocks |
| JS redirect detection (limited patterns) | `_extract_js_redirect` |
| Gateway button/form heuristic | `_handle_gateways` + `_execute_bypass` |
| LiveJournal adult cookies | `_get_sync_session` |
| Extension cookies header | `shared_session` + settings `extension_cookies` |
| Chrome-like Client Hints headers | `AsyncClientManager._get_default_headers` |

### 2.8 Anti-bot — gaps (stay cheap)

| Gap | Note |
|-----|------|
| Origin Referer bug | `Referer = get_domain(url)` → invalid; fix first (audit BUG-08) |
| Cookies from bypass not always domain-scoped on downloader | `cookies.set(name, value)` without domain |
| No challenge solver (Cloudflare Turnstile etc.) | **Out of scope** for cheap path — document “use extension cookies / open in browser” |
| No request pacing beyond domain semaphore | Optional jitter |
| Gateway runs **after** image extract on first pass | Order: images first, then gateway — OK but media_count for suspicion uses first-pass images |
| Aggressive gateway on pages with `<5` images | False clicks on legal pages |

**Do not implement:** full browser automation as default, residential proxy marketplace, captcha farms. Prefer: correct headers, cookies from extension, better gateways, optional delay.

---

## 3. Design principles for this track

1. **Surgical** — change one concern per PR/commit; match existing style.
2. **Data-driven** — prefer expanding `constants.py` lists / JSON patterns over new frameworks.
3. **Segment-aware URL filters** — never raw `if "ad" in url`.
4. **Preserve recall for content** — when unsure, **deprioritize** rather than hard-drop (except legal/auth).
5. **Measure** — unit tests for pure functions; manual one site for integration.
6. **Performance budget**:
   - No extra full-page fetch per image by default.
   - Optional HEAD on transformed URL: only if setting on; timeout ≤ 5s; domain semaphore already limits concurrency.
   - No O(n²) over all links without cap.

---

## 4. Work packages (ordered by ROI)

Each package: goal, touch points, concrete code, tests, anti-regression.

---

### WP-0 — Prerequisites (do before features)

From audits — fixes that make improvement work measurable:

| ID | Fix | File |
|----|-----|------|
| A1 | Resume drops pending downloads | `parser_manager.load_state` |
| A2 | Origin Referer scheme | `webpage_parser._get_content` |
| A3 | Cookie set with domain for downloader | `parser_manager._invoke_parser` |
| E3/E4 | Extension cookies + UA | extension + desktop |

Without A2/A3/E3, anti-bot work looks “broken” when it is not.

---

### WP-1 — Link junk filter (max benefit / min code)

**Goal:** Stop crawling ads, legal, account, help, sitemaps — without killing galleries.

**Primary files:** `src/constants.py`, `src/parser/utils.py`, `src/parser/parser_manager.py`

#### 1.1 Add segment-aware URL classifier

```python
# src/parser/utils.py  (NEW helpers)

from urllib.parse import urlparse
import re

# Path segments that almost never hold scrapeable media content
DEFAULT_LINK_SKIP_SEGMENTS = frozenset({
    # account / commerce
    "login", "signin", "signup", "register", "logout", "account", "profile",
    "cart", "checkout", "payment", "subscribe", "billing", "password", "auth",
    # legal / corporate
    "privacy", "privacy-policy", "terms", "tos", "legal", "copyright",
    "dmca", "cookies", "cookie-policy", "gdpr", "imprint", "impressum",
    "about", "about-us", "contact", "careers", "jobs", "press", "help",
    "support", "faq", "feedback", "sitemap", "robots.txt",
    # noise
    "advert", "advertising", "ads", "adserver", "sponsor", "promo",
    "newsletter", "unsubscribe", "widget", "embed", "share", "redirect",
    "go", "out", "external", "tracking", "pixel", "analytics",
    "tag", "tags", "category", "categories",  # optional: may remove if galleries use /tag/
    "search", "login.php", "wp-admin", "wp-login",
})

# Substrings only safe as full path segment or query key — NOT free substring
_SKIP_QUERY_KEYS = frozenset({"utm_source", "utm_medium", "fbclid", "gclid"})


def path_segments(url: str) -> list[str]:
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return []
    return [s for s in path.split("/") if s]


def should_skip_crawl_url(url: str, extra_stop_words: list | None = None) -> bool:
    """
    Return True if URL should not be queued for HTML parsing.
    Prefer hard-skip for legal/auth; do not use bare substring 'ad'.
    """
    if not url:
        return True
    try:
        p = urlparse(url)
    except Exception:
        return True

    host = (p.netloc or "").lower()
    path = (p.path or "").lower()
    segs = path_segments(url)

    # Explicit file junk
    if path.endswith((".css", ".js", ".map", ".xml", ".json", ".txt", ".ico", ".woff", ".woff2", ".ttf")):
        return True

    # Segment match (safe)
    skip = set(DEFAULT_LINK_SKIP_SEGMENTS)
    if extra_stop_words:
        # Only treat multi-char tokens as segments if they look like path words
        for w in extra_stop_words:
            w = (w or "").strip().lower().strip("/")
            if len(w) >= 3:
                skip.add(w)

    if any(s in skip for s in segs):
        return True

    # Filename-like last segment
    if segs and segs[-1] in skip:
        return True

    # mailto etc. already filtered earlier; keep host-level ad networks
    ad_hosts = (
        "doubleclick.", "googlesyndication.", "googleadservices.",
        "facebook.com/tr", "adservice.", "adnxs.", "taboola.", "outbrain.",
    )
    full = url.lower()
    if any(h in full for h in ad_hosts):
        return True

    return False
```

#### 1.2 Wire into enqueue (replace fragile substring stop_words)

**Current code** (`parser_manager.py`):

```python
stop_words_list = self.settings.get(K.SETTING_STOP_WORDS, K.DEFAULT_STOP_WORDS)
if any(stop_word.lower() in abs_disc_url.lower() for stop_word in stop_words_list):
    continue
```

**Replace with:**

```python
from src.parser.utils import should_skip_crawl_url

extra = self.settings.get(K.SETTING_STOP_WORDS, K.DEFAULT_STOP_WORDS)
if should_skip_crawl_url(abs_disc_url, extra):
    logger.debug(f"Skipping non-content link: {abs_disc_url}")
    continue
```

**Keep** user-editable stop words in Settings as **additional segments**, not free substrings.

#### 1.3 Optional: score link text (cheap)

When building context in `_extract_links`, already stores `text`. In `PriorityURLQueue._calculate_url_priority`:

```python
# after base_priority init
link_text = (context.get("text") or "").lower()
noise_text = ("privacy", "terms", "cookie", "login", "sign in", "subscribe", "advertisement")
if any(t in link_text for t in noise_text):
    base_priority *= 0.05  # deprioritize, don't hard-drop (segment filter already hard-drops paths)
```

#### 1.4 Tests

```python
# tests/test_link_skip.py
from src.parser.utils import should_skip_crawl_url

def test_skips_privacy():
    assert should_skip_crawl_url("https://example.com/privacy/policy")

def test_keeps_gallery():
    assert not should_skip_crawl_url("https://example.com/gallery/photo/123")

def test_does_not_false_positive_media():
    # "ad" as substring of "media" or "upload" must NOT skip
    assert not should_skip_crawl_url("https://cdn.example.com/media/uploads/1.jpg")
```

#### 1.5 Consequences

| Risk | Mitigation |
|------|------------|
| `/tag/` galleries skipped | Make `tag`/`category` configurable or only deprioritize in queue, not skip |
| Non-English legal paths | Add `политика`, `оферта`, `контакты` as segments later |
| Perf | O(segments) per link — negligible |

**Do not** add `"ad"` as free substring stop word.

---

### WP-2 — Media junk filter cleanup (recall + precision)

**Goal:** Fewer icons/ads; more real thumbs that become fullsize.

#### 2.1 Soften `SIGNIFICANT_MEDIA_IGNORE_PATTERNS`

**Current** (`constants.py`):

```python
SIGNIFICANT_MEDIA_IGNORE_PATTERNS = [
    "icon", "logo", "avatar", "social-", "button-", "placeholder", "nav-",
    "banner-", "advert", "ad-", "pixel", "tracker", "facebook", "twitter",
    "instagram", "linkedin", "youtube", "telegram", "vk.com", "yandex",
    "thumb", "thumbnail",  # ← problematic for CDN /thumbs/ that still transform
]
```

**Recommended:**

```python
# Hard ignore (path/URL noise) — keep short list
SIGNIFICANT_MEDIA_IGNORE_PATTERNS = [
    "favicon", "sprite", "emoji", "gravatar", "userpic",
    "/logo.", "/icon/", "/icons/", "apple-touch-icon",
    "pixel.", "1x1.", "tracking", "analytics",
    "facebook.com", "twitter.com", "t.co/",
    # social share widgets — host based better than "vk.com" alone if content is on vk
]

# Paths that mean "this is likely a thumbnail" — do NOT drop; prefer transform / parent link
THUMBNAIL_URL_HINTS = [
    "/thumb", "/thumbs/", "/thumbnail", "_thumb", "-thumb",
    "/small/", "/s/", "/preview/", "/lqip/", "w=150", "w=200",
]
```

**In `_is_significant_media`:**

```python
def _is_significant_media(...):
    if is_trash_media(url):
        return False
    if is_banner_or_ad(url, attrs):
        return False
    if any(p in url_lower for p in K.SIGNIFICANT_MEDIA_IGNORE_PATTERNS):
        return False
    # dimensions check unchanged
    # Do NOT reject solely because of THUMBNAIL_URL_HINTS
    return True
```

Thumbnail hints can mark attrs for priority:

```python
# after best url chosen
if any(h in abs_url.lower() for h in K.THUMBNAIL_URL_HINTS):
    variant_attrs["likely_thumbnail"] = True
```

#### 2.2 GIF policy

```python
# constants
DEFAULT_SKIP_GIF = True  # current behavior via TRASH_MEDIA_EXTENSIONS

# settings key SETTING_SKIP_GIF
# is_trash_media:
def is_trash_media(url, settings=None):
    ...
    trash = list(K.TRASH_MEDIA_EXTENSIONS)
    if settings is not None and not settings.get(K.SETTING_SKIP_GIF, True):
        trash = [e for e in trash if e != ".gif"]
```

Default stays current → **no behavior change** until user opts in.

#### 2.3 Video significance (cheap)

```python
# end of each video append in _extract_videos:
if not self._is_significant_media("video", abs_url, attrs):
    continue
# For embeds: skip known ad/tracker platforms if any; keep youtube/vimeo as optional setting
```

Note: `_is_significant_media` currently ignores media_type for most checks — OK for first pass. Embeds often have no dimensions — they pass; optional later filter `platform in AD_PLATFORMS`.

#### 2.4 Tests

- URL with `/thumbnail/120/foo.jpg` still **allowed** by significance if not trash.
- `/pixel.gif` still rejected.
- Banner 728×90 rejected via `is_banner_or_ad`.

---

### WP-3 — Fullsize discovery improvements

**Goal:** More correct fullsize URLs with minimal network cost.

#### 3.1 Always try transform even when parent page link exists (subtle)

**Current behavior:** if parent `<a>` is webpage, thumbnail may not be added as media (only page queued). That is good when interstitial holds fullsize; **bad** when sieve can expand thumb URL without crawl.

**Better hybrid** (preserve concept):

```python
# Inside _extract_images, when has_parent_webpage_link:
# 1) Always queue the page (existing)
# 2) ALSO enqueue transformed fullsize if transform changed the URL

urls, attrs = self._get_best_image_url(img)  # already transforms inside
for url in urls:
    abs_url = urljoin(self.url, url)
    transformed = attrs.get("transformed") or (attrs.get("original_url") and abs_url != attrs.get("original_url"))
    if has_parent_webpage_link:
        self.links[link_abs_url] = {...}  # existing
        if transformed and self._is_significant_media("image", abs_url, variant_attrs):
            self.media_files.append(("image", abs_url, variant_attrs))
            found += 1
        # else: skip raw thumb (existing)
    else:
        # existing append
```

**Careful:** `_get_best_image_url` returns transformed list when transform differs; attrs may mark `transformed`. Verify with unit test on a known sieve rule.

#### 3.2 Generic CDN rewrites (only high-confidence, list-driven)

Add to `site_patterns.json` **global** or `SitePatternManager._apply_global_transformations` **only patterns that are reversible and common**:

```python
# Example high-confidence rewrites (apply once, stop on first success)
GENERIC_THUMB_TO_FULL = [
    # WordPress-style -300x200 before extension
    (re.compile(r"(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp))$", re.I), r"\2"),
    # query size
    (re.compile(r"([?&])(w|width|h|height)=\d+", re.I), None),  # strip — implement carefully
]
```

**Safer WordPress-only strip** (real code pattern):

```python
def try_strip_wp_size_suffix(url: str) -> str | None:
    """https://x.com/a-300x200.jpg → https://x.com/a.jpg"""
    new = re.sub(r"(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp|avif))($|\?)", r"\2\3", url, flags=re.I)
    return new if new != url else None
```

Call from `transform_image_url` **after** site-specific rules fail, before return:

```python
if not transformed:
    wp = try_strip_wp_size_suffix(url)
    if wp:
        results = [wp]
        transformed = True
```

**Do not** strip all query params globally — breaks signed CDN URLs.

#### 3.3 Optional HEAD validation (setting, default OFF)

```python
# settings: validate_transformed_urls: false
async def _validate_image_url(self, session, url: str) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=5, connect=3)
        async with session.head(url, timeout=timeout, allow_redirects=True) as resp:
            if resp.status >= 400:
                return False
            ct = (resp.headers.get("Content-Type") or "").lower()
            return ct.startswith("image/") or ct == ""  # some CDNs omit type on HEAD
    except Exception:
        return True  # fail open — do not lose URL
```

Use only for **transformed** candidates. Fail **open** (keep URL if HEAD fails) to avoid recall loss.

#### 3.4 Improve Imagus coverage without browser

- Ship/update `Imagus_sieve_*.json` next to app (already loaded by name prefix).
- Log count of `js_skipped` — already done; track sites that need DOM rules → document “use Chrome extension”.
- Do **not** implement full JS DOM sieve in Python.

#### 3.5 Video fullsize / direct file

```python
# In _extract_videos, also check:
# - data-src, data-video-src on video
# - source[src] already handled
# - JSON-LD VideoObject contentUrl (cheap, high value)
```

**JSON-LD extract (minimal):**

```python
def _extract_json_ld_media(self, soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        for obj in stack:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type", "")
            if t in ("ImageObject", "VideoObject") or "ImageObject" in str(t):
                for key in ("contentUrl", "url"):
                    u = obj.get(key)
                    if isinstance(u, str) and u.startswith("http"):
                        yield ("video" if "Video" in str(t) else "image", u, {"source": "json-ld"})
```

Call from `parse()` after soup built. Low cost, high precision.

---

### WP-4 — Link selection / crawl priority

**Goal:** Spend parser threads on galleries and image containers, not menus.

#### 4.1 Prefer soft signals already present

Context keys already used:

- `from_image`, `priority`, `interstitial_retry`, `start_url`, `thumbnail_url`

**Ensure** `_extract_links` does not overwrite high-priority `from_image` entries:

**Current** `_extract_links`:

```python
self.links[abs_url] = {'from_image': False, 'element': 'a', 'text': ...}
```

This **overwrites** earlier `from_image: True` from `_extract_images` if the same URL appears later!

**Confirmed bug + fix (high ROI):**

```python
async def _extract_links(self, soup):
    ...
    abs_url = urljoin(self.url, href)
    if not abs_url.startswith(("http://", "https://")):
        continue
    prev = self.links.get(abs_url)
    if prev and prev.get("from_image"):
        # merge text only
        if not prev.get("text"):
            prev["text"] = a_tag.get_text(strip=True, separator=" ")[:100]
        continue
    self.links[abs_url] = {
        "from_image": False,
        "element": "a",
        "text": a_tag.get_text(strip=True, separator=" ")[:100],
    }
```

**Order in `parse()`:** images before links (already true). Overwrite was destroying fullsize crawl boost.

#### 4.2 Cap low-value link fan-out

```python
# parser_manager._process_parser_results
MAX_LINKS_PER_PAGE = self.settings.get("max_links_per_page", 80)

# Sort: from_image first, then others
urls_to_queue.sort(key=lambda x: (0 if x[1].get("from_image") else 1, -float(x[1].get("priority") or 0)))
urls_to_queue = urls_to_queue[:MAX_LINKS_PER_PAGE]
```

Prevents menu-heavy pages from exploding the queue. **Default 80** is conservative; adjustable.

#### 4.3 Do not raise domain concurrency by default

`DOMAIN_CONCURRENCY_LIMIT = 2` is anti-ban. Document only.

#### 4.4 Priority tweaks (careful)

In `_calculate_url_priority`, homepage ×0.005 is strong — keep.  
Add boost if context has `thumbnail_url` or `potential_media_container`:

```python
if context.get("potential_media_container") or context.get("from_image"):
    base_priority *= 1.5  # may already be ×20 for from_image — avoid double-dipping
```

`from_image` already ×20 — **do not** multiply again. Only use sort order in WP-4.2.

---

### WP-5 — Gateway & anti-bot (cheap stack)

**Goal:** Higher pass rate on consent/age walls; better session continuity; no captcha solving.

#### 5.1 Fix Referer origin (required)

```python
# webpage_parser._get_content — WRONG today:
request_specific_headers["Referer"] = get_domain(self.url)

# CORRECT:
from urllib.parse import urlparse
pu = urlparse(self.url)
request_specific_headers["Referer"] = f"{pu.scheme}://{pu.netloc}/"
```

Align with downloader `_get_per_request_headers`.

#### 5.2 Domain-scoped cookie jar sync

```python
# parser_manager._invoke_parser
from urllib.parse import urlparse
host = urlparse(url).hostname
if cookies and self._shared_downloader_session and host:
    for name, value in cookies.items():
        self._shared_downloader_session.cookies.set(
            name, value, domain=host, path="/"
        )
```

#### 5.3 Gateway scoring: reduce false positives

**Current:** `is_suspicious = len(self.media_files) < 5 or any(kw in text for kw in overlay_keywords)` — the `or` keywords alone trigger on many pages.

**Safer:**

```python
has_overlay = any(soup.select_one(sel) for sel in K.GATEWAY_OVERLAY_SELECTORS)
has_age_phrase = any(kw in text_content for kw in (
    "confirm your age", "18 years", "over 18", "adult content",
    "мне есть 18", "старше 18", "вход только",
))
is_suspicious = has_overlay or has_age_phrase or (
    len(self.media_files) < 2 and any(kw in text_content for kw in ("i agree", "cookie", "согласен"))
)
```

Blacklist already skips legal TOS links in candidates — keep.

#### 5.4 Persist bypass cookies on aiohttp session

Today bypass uses **sync** `requests` session; re-parse may use aiohttp without those cookies.

**Minimal fix after successful `_execute_bypass`:**

```python
# After bypass success, before re-parse:
if self._sync_session:
    # Copy cookies into aiohttp session cookie jar
    for c in self._sync_session.cookies:
        self.session.cookie_jar.update_cookies({c.name: c.value}, response_url=URL(self.url))
```

Use `yarl.URL` if available (aiohttp dependency). If complex, **re-fetch in re-parse using sync session only** when bypass ran — simpler:

```python
# flag
self._prefer_sync_fetch = True
# in _get_content: if self._prefer_sync_fetch: skip aiohttp, use sync first
```

#### 5.5 Lightweight pacing (optional setting)

```python
# after successful page parse in _parser_worker
delay = float(self.settings.get("request_delay_seconds", 0))
if delay > 0:
    await asyncio.sleep(delay)
```

Default `0` → no behavior change.

#### 5.6 Explicitly out of scope (do not implement in this track)

- Cloudflare JS challenge solving  
- Residential proxy rotation services  
- Playwright/Chromium default fetch  
- TLS fingerprint impersonation libraries (unless product later mandates)

**User escape hatch (document in UI later):** “Send cookies from Chrome extension” — fix E03/E04 first.

#### 5.7 Extension synergy (optional parallel)

| Desktop | Extension |
|---------|-----------|
| Imagus `img`/`to` | Currently dead `sieve.js` — see extension audit |
| — | `res` HTML scrape for linked pages |

Unifying both is large; for crawl quality focus on **desktop** WP-1–5 first.

---

## 5. Recommended implementation order

```
WP-0  Referer + cookie domain + audit blockers that hide results
WP-1  should_skip_crawl_url + wire parser_manager          ← biggest crawl quality win
WP-4.1 Fix links overwrite of from_image                   ← fullsize crawl fix (bug)
WP-2  Soften thumb ignore patterns + tests
WP-3.1 Hybrid parent-link + transformed fullsize
WP-3.2 WP size suffix strip
WP-3.5 JSON-LD media
WP-4.2 Max links per page
WP-5  Gateway suspicion + cookie continuity + delay setting
WP-3.3 Optional HEAD validate (default off)
Extension audits (if product wants browser path)
```

---

## 6. Testing strategy

### 6.1 Unit (required for each WP)

```bash
python -m pytest tests -q
```

Add focused files:

| Test module | Covers |
|-------------|--------|
| `tests/test_link_skip.py` | WP-1 |
| `tests/test_significant_media.py` | WP-2 |
| `tests/test_transform_heuristics.py` | WP-3.2 |
| `tests/test_links_merge.py` | WP-4.1 merge from_image |
| Existing `test_pattern_manager.py` | sieve still works |
| Existing `test_url_detection.py` | media URL helpers |

### 6.2 Integration smoke (manual)

1. **Junk crawl:** site with fat footer (Privacy, Login) — queue should not grow with those paths.  
2. **Gallery:** thumbs with parent links — fullsize downloaded or linked pages processed.  
3. **WP blog:** `-300x200.jpg` thumbs → full images if strip enabled.  
4. **Age gate fixture HTML** (local file server) — bypass clicks once, media appears.  
5. **one_shot** still only one page.  
6. **Stop** still responsive (no new retries on shared session).

### 6.3 Perf sanity

- Same site, depth 2: wall time should not grow >15% after WP-1/2/4.  
- WP-3.3 HEAD only if enabled.  
- WP-4.2 should **reduce** pages processed on menu-heavy sites.

---

## 7. Code map — exact hooks for agents

### 7.1 Enqueue filter

`src/parser/parser_manager.py` → `_process_parser_results` ~lines 476–497  
→ insert `should_skip_crawl_url`

### 7.2 Image fullsize

`src/parser/webpage_parser.py` → `_get_best_image_url` ~365–422  
`_extract_images` ~467–591  
`SitePatternManager.transform_image_url` ~547+

### 7.3 Link priority

`src/parser/priority_url_queue.py` → `_calculate_url_priority` ~170–364  
`put` drops `priority <= 0`

### 7.4 Significance / ads

`webpage_parser._is_significant_media` ~682  
`utils.is_banner_or_ad` ~350  
`utils.is_trash_media` ~68  
`constants.SIGNIFICANT_MEDIA_*`, `DEFAULT_STOP_WORDS`, `GATEWAY_*`

### 7.5 Fetch / bypass

`webpage_parser._get_content` ~110  
`_handle_gateways` ~710  
`_execute_bypass` ~317  
`shared_session.AsyncClientManager` headers/cookies

### 7.6 Settings surface (only if new knobs needed)

`src/gui/settings_dialog.py` + `constants.DEFAULT_SETTINGS_VALUES`  
New keys: `request_delay_seconds`, `max_links_per_page`, `skip_gif`, `validate_transformed_urls`  
**Default to current behavior** when unset.

---

## 8. Ready-to-paste micro-patches (highest ROI)

### 8.1 from_image overwrite fix (WP-4.1)

**File:** `webpage_parser.py` method `_extract_links`

```python
async def _extract_links(self, soup: BeautifulSoup) -> None:
    found = 0
    filter_hidden = self.settings.get(K.SETTING_FILTER_HIDDEN_LINKS, K.DEFAULT_FILTER_HIDDEN_LINKS)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        if filter_hidden and not self._is_element_visible(a_tag):
            continue
        abs_url = urljoin(self.url, href)
        if not abs_url.startswith(("http://", "https://")):
            continue

        text = a_tag.get_text(strip=True, separator=" ")[:100]
        existing = self.links.get(abs_url)
        if existing is not None:
            if existing.get("from_image"):
                if text and not existing.get("text"):
                    existing["text"] = text
                continue
            # keep stronger priority if any
            if existing.get("priority", 0) >= 10:
                if text and not existing.get("text"):
                    existing["text"] = text
                continue

        self.links[abs_url] = {
            "from_image": False,
            "element": "a",
            "text": text,
        }
        found += 1
    # canonical link handling unchanged...
```

### 8.2 Referer origin fix (WP-5.1)

```python
elif referrer_policy == "origin":
    parsed = urlparse(self.url)
    if parsed.scheme and parsed.netloc:
        request_specific_headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
```

### 8.3 WordPress size strip (WP-3.2)

```python
# site_pattern_manager.py — end of transform_image_url before return
if not transformed:
    stripped = re.sub(
        r"(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp|avif))(?=$|\?)",
        r"\2",
        results[0],
        count=1,
        flags=re.I,
    )
    if stripped != results[0]:
        results = [stripped]
```

---

## 9. Anti-patterns (do not do)

| Anti-pattern | Why |
|--------------|-----|
| Substring stop word `"ad"` | Breaks `media`, `upload`, `adobe`, `pad` |
| Dropping all URLs with `thumb` | Loses CDN content before transform |
| Raising domain concurrency to “go faster” | Ban / 429 storms |
| Enabling HEAD validate by default | Latency × N images |
| Puppeteer as default fetch | Complexity, packaging, flaky CI |
| Rewriting PriorityURLQueue from scratch | High regression risk; tune constants first |
| Free-form AI-generated regex per site in core | Put in `site_patterns.json` / sieve instead |
| Silent change of `one_shot` semantics | Breaks extension contract |

---

## 10. Success metrics (definition of done)

For a **fixed set of 5 representative sites** (owner should pick; keep a private list):

| Metric | Target |
|--------|--------|
| Useful fullsize images / total downloaded | ↑ vs baseline |
| Pages crawled that are legal/login/help | ↓ ≥ 50% |
| False gateway clicks on normal pages | ≈ 0 |
| `pytest` | all green |
| Wall time depth=2 same start URL | within +15% or faster |

Log-based measurement:

```
pages_processed, files_downloaded, files_skipped
```

from `ParserManager.get_stats()` — already in UI.

---

## 11. Agent checklist before opening a PR

- [ ] Read this guide §1–§3 and `AGENTS.md`  
- [ ] Change only files needed for one WP  
- [ ] Defaults preserve old behavior unless bugfix  
- [ ] Unit tests for pure functions  
- [ ] Manual smoke for one real page  
- [ ] No new heavy dependency without discussion  
- [ ] Document new setting keys in `constants.py`  
- [ ] If touching pause/state/queue — re-read full audit P0  

---

## 12. Quick reference — “where do I change X?”

| I want to… | Go to… |
|------------|--------|
| Block more legal/ad URLs from crawl | WP-1 `should_skip_crawl_url` |
| Stop ignoring good thumbnails | WP-2 `SIGNIFICANT_MEDIA_IGNORE_PATTERNS` |
| More fullsize without new fetches | WP-3 sieve + WP strip + hybrid parent link |
| Prefer gallery links over menu | WP-4.1 merge + WP-4.2 cap + priority |
| Better age/cookie walls | WP-5 + extension cookies |
| Site-specific rule without code | `resources/patterns/site_patterns.json` or Imagus JSON |
| Browser-only discovery | `extension/` + extension audit |

---

## 13. Summary for the incoming agent

You are improving a **mature desktop crawler** that already has:

- pattern/sieve fullsize transforms  
- priority queue  
- gateway heuristics  
- junk heuristics  

The highest leverage is **not** a new engine. It is:

1. **Correctness bugs** that destroy `from_image` priority and weak stop-words.  
2. **Segment-aware** non-content URL rejection.  
3. **Smarter media filters** that don’t delete thumbs before transform.  
4. **Cheap fullsize heuristics** (WP size strip, JSON-LD).  
5. **Header/cookie continuity** for walls — not captcha AI.

Stay surgical. Prefer lists and pure functions with tests. Preserve one-shot, domain limits, and Stop behavior.

When in doubt: **deprioritize, don’t delete**; **fail open on optional validation**; **measure with pytest + one real site**.
