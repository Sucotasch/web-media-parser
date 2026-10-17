#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Global constants for the Web Media Parser application.
"""

# Application version (single source of truth — used by main.py and setup.py)
APP_VERSION = "1.4.1"

# Localhost port for the browser-extension HTTP API (ExtensionServer).
# Mirrored in extension/background.js API_BASE (JS cannot import Python).
EXTENSION_API_PORT = 19876

# Extension ID trusted by the HTTP API (EXT-11). Until the extension ships in
# the Web Store its ID is unstable, so an empty value keeps the permissive
# "any chrome-extension:// origin" behavior as a documented compromise.
EXTENSION_ID = ""

# Default Application Settings
DEFAULT_RETRY_COUNT = 3
DEFAULT_TIMEOUT = 30  # General network timeout for requests
DEFAULT_PAGE_TIMEOUT = 30  # Timeout for fetching and processing a single webpage or API
DEFAULT_CONNECT_TIMEOUT = 20 # Timeout for establishing a connection
DEFAULT_SOCK_READ_TIMEOUT = DEFAULT_PAGE_TIMEOUT # Timeout for reading from a socket

DEFAULT_SEARCH_DEPTH = 3
UNLIMITED_SEARCH_DEPTH = -1
DEFAULT_PARSER_THREADS = 4
DEFAULT_DOWNLOADER_THREADS = 8
DEFAULT_THREADS_PER_FILE = 1 # For multi-threaded download of a single file

# Media Filtering
DEFAULT_MIN_IMAGE_WIDTH = 100
DEFAULT_MIN_IMAGE_HEIGHT = 100
DEFAULT_MIN_IMAGE_SIZE_KB = 40 # Minimum image file size in KB
DEFAULT_MIN_VIDEO_SIZE_KB = 1000 # Minimum video file size in KB

# A-07: hard ceiling for a single file. The old code read Content-Length only to
# enforce a *minimum* and to size progress bars, so a host that answered a
# plausible length and then streamed without end filled the disk. 0 = no limit
# (the downloader's fallback, so hand-built settings dicts keep working); the
# shipped default settings carry 2 GiB as a backstop (product decision §4.5).
DEFAULT_MAX_FILE_SIZE = 0 # in KB, 0 = unlimited
DEFAULT_REQUIRE_VERIFIED_LENGTH = False # B-12 strict mode: reject bodies with no verifiable length

# Domain Health & Quarantine
DEFAULT_QUARANTINE_FAILURE_THRESHOLD = 3
DEFAULT_DOMAIN_PROBATION_TIMEOUT = 5  # Timeout for downloads from domains on "probation"
DEFAULT_DOMAIN_PROBATION_RETRIES = 0 # Retries for downloads from domains on "probation"


# HTTP Headers
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9"
DEFAULT_ACCEPT_HEADER = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"
DEFAULT_ACCEPT_IMAGE_HEADER = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
DEFAULT_ACCEPT_VIDEO_HEADER = "video/mp4,video/webm,video/*,*/*;q=0.8"
DEFAULT_ACCEPT_JSON_HEADER = "application/json, text/javascript, */*; q=0.01"

# File Operations
WRITE_BUFFER_SIZE = 1024 * 1024  # 1MB buffer for disk writes
# B-11: how many leading bytes of a GET body are inspected before the first
# write. Enough for an HTML/XML/JSON prolog, small enough to be free.
SNIFF_PREFIX_BYTES = 512
MAX_FILENAME_LENGTH = 200 # Max length for sanitized filenames (excluding extension)

# UI & Dialog Defaults
DEFAULT_SAVE_PATH = "downloads" # Default download directory base name

# GUI log view: how many log lines the widget keeps. It matches the handler's
# message_history window so the view and the history cannot disagree. The
# document MUST be capped: at GUI DEBUG level the parsers feed thousands of
# records per minute and an uncapped QTextEdit document grows for the whole
# session (measured on Windows: +118 MB RSS per 200 000 appended records, with a
# flat Python heap — the document lives in Qt's C++ heap, so tracemalloc shows
# nothing). See Errors.txt bug 2.
GUI_LOG_MAX_BLOCKS = 5000

# Parser Behavior
DEFAULT_STAY_IN_DOMAIN = True
DEFAULT_PROCESS_JS = True       # Whether to attempt static JS analysis and other advanced content extraction
# DEFAULT_PROCESS_DYNAMIC = True  # Removed, consolidated into PROCESS_JS
DEFAULT_BYPASS_COOKIE_CONSENT = True
DEFAULT_BYPASS_JS_REDIRECTS = True
DEFAULT_USE_PATTERNS = True     # For SitePatternManager

# Stop words for URL filtering — matched against PATH SEGMENTS (segment-aware),
# never as free substrings (so "ad" can't kill "media"/"admin"). Plain words
# only: underscored variants (about_us, privacy_policy) never match real segments.
# F-01: single source for the crawl stop-word list. The settings dialog used to
# carry its own 25-word copy (with a duplicate "login") while this list had 9, so
# a missing/invalid `stop_words` value silently narrowed the filter by 2.7x. This
# is the deduplicated union of both lists — the wider behaviour users already got
# from the dialog stays, and the narrower fallbacks are gone.
DEFAULT_STOP_WORDS = [
    "login", "signin", "signup", "register", "password", "account", "payment",
    "checkout", "subscribe", "join", "admin", "analytics", "pixel", "tracking",
    "advertisement", "banner", "popup", "faq", "help", "support", "contact",
    "about", "terms", "privacy", "cart", "careers",
]

# Crawl limits
DEFAULT_MAX_LINKS_PER_PAGE = 200  # Cap links per page to prevent queue explosion on menu-heavy pages

# REMOVED 2026-10-16 (rolled back): a `max_pending_urls` ceiling that REJECTED
# excavation links once the frontier was full. It was the wrong instrument twice
# over — it capped a queue that was never the bottleneck (measured: the URL
# frontier sat at ~300-1000 entries while the download backlog grew 113 -> 936 and
# 57 -> 2303), and it lost links irreversibly for the run, because the source page
# is already processed and is not re-expanded (at a cap of 50 the crawl refused
# 15 278 links in 60 s on the fixture). A crawler must be able to run long without
# dropping discovered work; load is controlled by the backlog gate below instead.

# P4 / Errors.txt (2) — the actual load control: synchronise "excavating new"
# with "what we can download", in chunks. Measured reason (my own 2026-10-16 runs):
#   reactor.cc, 67 s   : found +6.0/s  vs downloaded +2.8/s  -> download backlog +181
#   reactor.cc, 64 s   : found +15.9/s vs downloaded +2.6/s  -> backlog 113 -> 936 (+12/s)
#   fixture,    149 s  : found +22.6/s vs downloaded +7.5/s  -> backlog 57 -> 2303 (+15/s)
# The backlog grew monotonically in every run: page excavation (which also triggers
# every link/media discovery) ran at full speed while the downloader could only
# consume a fraction of it, so requests, queue entries and RSS all accumulated.
# Rule: while the download backlog is >= CRAWL_BACKLOG_HWM, the parser stops
# STARTING new pages; it resumes once the backlog has drained to CRAWL_BACKLOG_LWM.
# The gate is on the DOWNLOAD queue (drained by downloader workers on their own),
# never on the URL frontier — pending URLs only shrink when a parser worker pops
# one, so a watermark there would freeze the crawl with nothing left to drain it.
# 0 disables the gate (old behaviour, for users who want maximum crawl speed).
DEFAULT_CRAWL_BACKLOG_HWM = 1000   # pause excavating at this many queued downloads
DEFAULT_CRAWL_BACKLOG_LWM = 200     # resume once it has drained to this many
# Safety valve: a jammed/hung downloader must not freeze the crawl forever. If the
# backlog does not move for this long while gated, excavation resumes with a warning.
CRAWL_BACKLOG_STALL_TIMEOUT = 60    # seconds without any change in backlog depth
CRAWL_BACKLOG_POLL_SECONDS = 0.5    # gate poll interval

# Guards against pathological URL growth (e.g. urljoin of relative vBulletin
# "threads/..." links on a base URL without a trailing slash appends the same
# segment forever: threads/threads/threads/...). URLs beyond these bounds are
# dropped by PriorityURLQueue.put() before they can flood the queue.
MAX_URL_PATH_SEGMENTS = 50   # Max path segments a queued URL may have
MAX_URL_LENGTH = 2000        # Max total length of a queued URL

# F-12: bound on PriorityURLQueue's dedup memory. `_seen` holds every URL the
# frontier has ever considered (including rejected ones) for the whole task run;
# a long crawl on a big host grew it without limit. Beyond this many entries the
# oldest are forgotten — the cost is only that a re-discovered ancient URL is
# queued a second time (the crawler's own processed_urls set still stops a
# re-parse), while the memory ceiling becomes predictable.
MAX_SEEN_URLS = 100000

# Fullsize discovery via the sieve link->url->res chain (mirrors the
# extension's discoverFullsize). Applied to thumbnail-transition links
# (from_image) on every parsed page so external image hosts (imx.to, postimg,
# ...) resolve to fullsize originals instead of being dropped by domain checks.
# Media lookups are never content-capped (a 500-image gallery resolves all
# 500); the guards are RESOURCE-based only: per-probe timeout, concurrency,
# and a total time budget per source page so a dead host can't stall a worker.
# B-17 (2026-10-15): a Deno worker is retired after this many calls, so the
# parsed documents it holds cannot accumulate for a whole task run.
JS_WORKER_MAX_REQUESTS = 200

# Errors.txt (2), measured 2026-10-16: a call COUNT cannot bound memory. The
# happy-dom DOM worker retains one parsed document per call — 1.4 MB/call on a
# 4 KB page and 13 MB/call on a 40 KB one (real pages run 50-200 KB), so the
# 200-call window above allowed a deno.exe child of 1.4 GB (measured with a
# 40 KB document: 237 MB after 10 calls, 1.4 GB after 100). The worker is now
# also retired as soon as its resident memory reaches this budget, which keeps
# the peak at the budget plus one call's growth instead of the count times the
# page size. worker.js (no DOM) sits at a steady ~32 MB and never hits it.
JS_WORKER_MAX_RSS_MB = 256

# A-16/B-02 (2026-10-15): worker threads for the dedicated JS-rule pool (sieve
# `to`/`res` rules). Downloads and HTML parsing have their own pools sized by
# the user's thread settings.
JS_ENGINE_POOL_WORKERS = 2

FULLSIZE_DISCOVER_CONCURRENCY = 5   # Concurrent probes (extension uses 5)
FULLSIZE_DISCOVER_TIMEOUT = 8       # Seconds per probe (extension uses 8000ms)
FULLSIZE_DISCOVER_TIME_BUDGET = 45  # Max total probe time per source page
SIEVE_LOOP_MAX_HOPS = 5             # C-2: max recursive re-resolutions for loop-rules (Mod cap)

# Gateway & Visibility Filtering
DEFAULT_FILTER_HIDDEN_LINKS = True
DEFAULT_BYPASS_GATEWAYS = True
GATEWAY_MIN_MEDIA_THRESHOLD = 2

# CSS and attribute values that typically hide elements (bot-traps)
# R-C-2: only unambiguous, value-independent forms stay here. Zero-valued
# opacity/width/height are decided by parsing the declaration in
# WebpageParser._is_element_visible — as bare substrings "opacity: 0" matched
# "opacity: 0.9" and "height: 0.5em", silently dropping real media.
VISIBILITY_HIDDEN_KEYWORDS = [
    "display: none", "display:none", "visibility: hidden", "visibility:hidden",
    "left: -999", "top: -999"
]
VISIBILITY_HIDDEN_CLASSES = [
    "hidden", "sr-only", "visually-hidden", "bot-trap", "honeypot", "invisible", "d-none"
]

# P4: keywords that mark a candidate as a NON-gateway (login/sign-up/account
# technical sections). Applied to gateway button text BEFORE scoring so a
# login modal or account link is never clicked as a "consent" action.
GATEWAY_AVOID_KEYWORDS = [
    "login", "sign in", "sign-in", "signin", "register", "sign up", "sign-up",
    "log out", "logout", "log-out", "account", "password", "email", "username",
    "checkout", "cart", "donate", "subscribe", "newsletter", "forgot", "support",
    "help", "faq", "contact", "регистрация", "аккаунт", "пароль",
    "зарегистрироваться", "корзина", "подписаться",
]
# NOTE: deliberately NOT in GATEWAY_AVOID_KEYWORDS: "войти"/"вход" — RU
# age-gates commonly confirm with «Войти»/«Вход на сайт», which ARE positive
# matches in GATEWAY_TEXT_PATTERNS. An avoid keyword would silently block them
# (false negative), so login-vs-consent is disambiguated by the surrounding
# page (overlay scoping) and the password-form skip instead.

# Common texts on Gateway (Age Verification / Consent) buttons and links
GATEWAY_TEXT_PATTERNS = [
    # English
    "i agree", "agree", "confirm", "continue", "yes", "i am 18", "enter", "accept", 
    "i am over 18", "enter site", "verify", "continue to", "i confirm", "agree and enter",
    "enter the site", "yes, enter", "yes, i am",
    # Russian
    "согласен", "подтверждаю", "да", "да, мне есть 18", "старше 18", "старше 18 лет",
    "продолжить", "войти", "принимаю", "войти на сайт", "да, я старше 18 лет",
    "подтверждаю возраст", "принимаю условия"
]
GATEWAY_OVERLAY_SELECTORS = [
    ".age-gate", ".age-warning", "#consent-modal", ".overlay-consent",
    ".popup-wrapper", "#agreement", ".agreement-overlay", ".overlay-wrapper"
]
# Generic modal/footer selectors that exist on many ordinary sites — only
# treated as gateway evidence when combined with consent/age text (WP-5.3).
GATEWAY_GENERIC_OVERLAY_SELECTORS = [
    ".modal-content", "#disclaimer"
]

# Patterns for 'noise' media that shouldn't be counted as main content
# NOTE: "thumb"/"thumbnail" removed — they drop legitimate CDN content before sieve upgrade
SIGNIFICANT_MEDIA_IGNORE_PATTERNS = [
    "favicon", "sprite", "emoji", "gravatar", "userpic",
    "/logo.", "/icon/", "/icons/", "apple-touch-icon",
    "pixel.", "1x1.", "tracking", "analytics",
    "facebook.com", "twitter.com", "t.co/",
    "icon", "avatar", "social-", "button-", "placeholder", "nav-",
    "banner-", "advert", "ad-", "tracker",
    "instagram", "linkedin", "youtube", "telegram", "vk.com", "yandex",
]

# Soft hints for thumbnail URLs — used for priority, NOT for hard filtering
THUMBNAIL_URL_HINTS = [
    "/thumb", "/thumbs/", "/thumbnail", "_thumb", "-thumb",
    "/small/", "/s/", "/preview/", "/lqip/", "w=150", "w=200",
]
SIGNIFICANT_MEDIA_MIN_DIMENSION = 100 # Minimum width/height if specified in HTML

# R-C-7: an aspect-ratio test only marks a banner when the creative is
# physically small — otherwise 3:1+ panoramas (3000x1000, 4096x1024) were
# dropped as ads while a 970x250 billboard was missed.
BANNER_MAX_AREA = 1_000_000

# Minimum dimension (px) for a <link rel=icon/apple-touch-icon> to be kept;
# smaller sizes (57..152 family that vBulletin emits as a dozen <link> tags)
# are dropped at parse time instead of reaching the download queue.
APPLE_TOUCH_ICON_MIN_DIM = 180

# Session state filename
SESSION_STATE_FILENAME = "last_session.pkl"
SESSION_STATE_SUBDIR = "sessions"

# Blocklist filename
DOMAIN_BLOCKLIST_FILENAME = "domain_blocklist.txt"

# Logging
LOG_CLEAR_HISTORY_ON_START = False # If true, clears log window every time app starts.

# Max items to process from quarantine queue in one go
QUARANTINE_BATCH_PROCESS_SIZE = 10

# Maximum number of times a single item can cycle through quarantine before being dropped
QUARANTINE_MAX_ITEM_RETRIES = 1

# Seconds of full queue inactivity before auto-completing the parsing task
IDLE_COMPLETION_TIMEOUT_SECONDS = 5

# Max concurrent requests per domain (parser + downloader combined)
DOMAIN_CONCURRENCY_LIMIT = 2

# Max threads for multi-threaded download of a single file (hard cap)
MAX_THREADS_PER_FILE_CAP = 8

# Minimum chunk size for considering multi-threaded download (per thread)
MIN_CHUNK_SIZE_PER_THREAD_MT = 1024 * 256 # 256KB

# Fallback extension for images and videos if undetermined
DEFAULT_IMAGE_EXTENSION = ".jpg"
DEFAULT_VIDEO_EXTENSION = ".mp4"

# Hash length for filenames generated from URLs without clear names
DEFAULT_FILENAME_HASH_LENGTH = 10

# Max path components from source URL to use for subdirectory creation
MAX_PATH_COMPONENTS_FOR_SUBDIR = 2

# Max length for a single path component when creating subdirectories
MAX_SUBDIR_COMPONENT_LENGTH = 50

# Default settings keys (matching SettingsDialog) - useful for consistency
SETTING_SEARCH_DEPTH = "search_depth"  # Link depth for parsing (0 = start page only, -1 = unlimited)
SETTING_PARSER_THREADS = "parser_threads"
SETTING_DOWNLOADER_THREADS = "downloader_threads"
SETTING_THREADS_PER_FILE = "threads_per_file"
SETTING_MIN_IMG_WIDTH = "min_image_width"
SETTING_MIN_IMG_HEIGHT = "min_image_height"
SETTING_MIN_IMG_SIZE = "min_image_size" # in KB
SETTING_MIN_VID_SIZE = "min_video_size" # in KB
SETTING_MAX_FILE_SIZE = "max_file_size" # A-07: in KB, 0 = unlimited
SETTING_REQUIRE_VERIFIED_LENGTH = "require_verified_length" # B-12 strict mode
SETTING_TIMEOUT = "timeout"
SETTING_RETRY_COUNT = "retry_count"
SETTING_USER_AGENT = "user_agent"
SETTING_ACCEPT_LANGUAGE = "accept_language"
SETTING_REFERRER_POLICY = "referrer" # "auto", "origin", "none"
SETTING_PROXY = "proxy" # "host:port"
DEFAULT_PROXY = ""
SETTING_STAY_IN_DOMAIN = "stay_in_domain"
SETTING_USE_PATTERNS = "use_patterns"
SETTING_CUSTOM_PATTERN_PATH = "custom_pattern_path"
SETTING_IMAGUS_SIEVE_PATH = "imagus_sieve_path"
# JS engine for Imagus sieve JS rules ("static" = Python converter only,
# "deno" = execute JS rules via a Deno subprocess worker). Default stays
# "static" so behaviour is unchanged until the user opts in.
SETTING_JS_ENGINE = "js_engine"
# HTTP engine for the SYNC paths (parser fallback + gateway bypass + media
# downloader). "aiohttp" keeps the historical requests/urllib3 stack;
# "curl_cffi" impersonates a real browser TLS fingerprint (JA3/JA4/HTTP2)
# via libcurl-impersonate — the async primary page fetch always stays aiohttp.
# Default stays "aiohttp" so behaviour is unchanged until the user opts in.
SETTING_HTTP_ENGINE = "http_engine"
DEFAULT_HTTP_ENGINE = "aiohttp"
# Impersonation profile for curl_cffi ("chrome" auto-tracks the latest).
SETTING_HTTP_IMPERSONATE = "http_impersonate"
DEFAULT_HTTP_IMPERSONATE = "chrome"
# Auto-escalation (P3): when a request is explicitly blocked (HTTP 403/5xx)
# on the regular stack, retry ONCE through a curl_cffi browser-TLS session.
# Bounded by construction (single attempt, no retry loop) so it is invisible
# to the domain-health/quarantine counters. Default on when curl_cffi is
# installed; 429 is never escalated (rate-limit backoff already exists).
SETTING_HTTP_ESCALATE = "http_escalate"
DEFAULT_HTTP_ESCALATE = True
# A-6: TLS verification for sync fallback paths. Default False preserves the
# historical behavior (some users run self-signed proxies); the checkbox in
# Settings → HTTP turns verification on. A one-shot warning is logged when a
# request goes out unverified (see http_engine.warn_if_unverified).
SETTING_VERIFY_TLS = "verify_tls"
DEFAULT_VERIFY_TLS = False
SETTING_PROCESS_JS = "process_js"
SETTING_BYPASS_COOKIE_CONSENT = "bypass_cookie_consent"
SETTING_BYPASS_JS_REDIRECTS = "bypass_js_redirects"
SETTING_FILTER_HIDDEN_LINKS = "filter_hidden_links"
SETTING_FILTER_JUNK = "filter_junk"  # P2-lite ad/tracker/junk URL classifier
SETTING_STOP_WORDS = "stop_words"

# Filter settings keys
SETTING_MAX_DOWNLOAD_SPEED = "max_download_speed" # in KB/s, 0 for unlimited
SETTING_PAGE_TIMEOUT = "page_timeout" # Timeout for page loading/parsing
SETTING_PAGE_LIMIT = "page_limit" # Stop after N pages with downloads (0 = unlimited)
# P4 backlog gate: pause page excavation at HWM, resume at LWM (0 = gate off).
SETTING_CRAWL_BACKLOG_HWM = "crawl_backlog_hwm"
SETTING_CRAWL_BACKLOG_LWM = "crawl_backlog_lwm"
SETTING_ENABLED_IMAGE_FORMATS = "enabled_image_formats"
SETTING_ENABLED_VIDEO_FORMATS = "enabled_video_formats"
SETTING_ENABLED_AUDIO_FORMATS = "enabled_audio_formats"

# Default settings dictionary structure (used by SettingsDialog to save/load)
DEFAULT_SETTINGS_VALUES = {
    SETTING_SEARCH_DEPTH: DEFAULT_SEARCH_DEPTH,
    SETTING_PARSER_THREADS: DEFAULT_PARSER_THREADS,
    SETTING_DOWNLOADER_THREADS: DEFAULT_DOWNLOADER_THREADS,
    SETTING_THREADS_PER_FILE: DEFAULT_THREADS_PER_FILE,
    SETTING_MIN_IMG_WIDTH: DEFAULT_MIN_IMAGE_WIDTH,
    SETTING_MIN_IMG_HEIGHT: DEFAULT_MIN_IMAGE_HEIGHT,
    SETTING_MIN_IMG_SIZE: DEFAULT_MIN_IMAGE_SIZE_KB,
    SETTING_MIN_VID_SIZE: DEFAULT_MIN_VIDEO_SIZE_KB,
    # A-07: 2 GiB backstop for a fresh profile / Reset (the code fallback stays
    # 0 = unlimited, so a settings dict built by hand is never capped).
    SETTING_MAX_FILE_SIZE: 2 * 1024 * 1024,
    SETTING_REQUIRE_VERIFIED_LENGTH: DEFAULT_REQUIRE_VERIFIED_LENGTH,
    SETTING_TIMEOUT: DEFAULT_TIMEOUT,
    SETTING_RETRY_COUNT: DEFAULT_RETRY_COUNT,
    SETTING_USER_AGENT: DEFAULT_USER_AGENT,
    SETTING_ACCEPT_LANGUAGE: DEFAULT_ACCEPT_LANGUAGE,
    SETTING_REFERRER_POLICY: "auto",
    SETTING_STAY_IN_DOMAIN: DEFAULT_STAY_IN_DOMAIN,
    SETTING_USE_PATTERNS: DEFAULT_USE_PATTERNS,
    SETTING_CUSTOM_PATTERN_PATH: "",
    SETTING_IMAGUS_SIEVE_PATH: "",
    SETTING_JS_ENGINE: "static",
    SETTING_HTTP_ENGINE: DEFAULT_HTTP_ENGINE,
    SETTING_HTTP_IMPERSONATE: DEFAULT_HTTP_IMPERSONATE,
    SETTING_HTTP_ESCALATE: DEFAULT_HTTP_ESCALATE,
    SETTING_VERIFY_TLS: DEFAULT_VERIFY_TLS,
    SETTING_PROCESS_JS: DEFAULT_PROCESS_JS,
    # SETTING_PROCESS_DYNAMIC: DEFAULT_PROCESS_DYNAMIC, # Removed
    SETTING_BYPASS_COOKIE_CONSENT: DEFAULT_BYPASS_COOKIE_CONSENT,
    SETTING_BYPASS_JS_REDIRECTS: DEFAULT_BYPASS_JS_REDIRECTS,
    SETTING_FILTER_JUNK: True,  # P2-lite junk classifier (precision-first + allowlist)
    SETTING_FILTER_HIDDEN_LINKS: DEFAULT_FILTER_HIDDEN_LINKS,
    SETTING_PAGE_LIMIT: 1000,  # 0 = unlimited (matches the dialog tooltip)
    SETTING_CRAWL_BACKLOG_HWM: DEFAULT_CRAWL_BACKLOG_HWM,
    SETTING_CRAWL_BACKLOG_LWM: DEFAULT_CRAWL_BACKLOG_LWM,
    SETTING_STOP_WORDS: DEFAULT_STOP_WORDS,
    SETTING_MAX_DOWNLOAD_SPEED: 0, # KB/s
    SETTING_PROXY: "",  # F-01: was dialog-only
    SETTING_PAGE_TIMEOUT: DEFAULT_PAGE_TIMEOUT,
    # NOTE: SETTING_ENABLED_*_FORMATS defaults are injected below via
    # DEFAULT_SETTINGS_VALUES.update() because the format lists are defined
    # after this dict in the file.
}

# Parser Error Statuses
PARSER_SUCCESS = "SUCCESS"
PARSER_NETWORK_ERROR = "NETWORK_ERROR"  # General network issue, e.g., DNS failure, connection refused
PARSER_TIMEOUT_ERROR = "TIMEOUT_ERROR"  # Request timed out
PARSER_HTTP_ERROR_4XX = "HTTP_ERROR_4XX" # Client errors (400-499)
PARSER_HTTP_ERROR_5XX = "HTTP_ERROR_5XX" # Server errors (500-599)
PARSER_INVALID_CONTENT_TYPE = "INVALID_CONTENT_TYPE" # e.g. expected HTML, got application/pdf
PARSER_CONTENT_DECODE_ERROR = "CONTENT_DECODE_ERROR" # Failed to decode content with specified/detected encoding
PARSER_JS_REDIRECT_MAX_EXCEEDED = "JS_REDIRECT_MAX_EXCEEDED" # Too many JS redirects
PARSER_UNKNOWN_ERROR = "UNKNOWN_ERROR"    # Other miscellaneous errors during parsing itself

# Maximum number of JS redirects to follow
MAX_JS_REDIRECTS = 5

# --- Appended Constants for JSON Parser and File Types ---

# JSON Specific Key Patterns
JSON_MEDIA_KEY_PATTERNS = [
    "url", "src", "source", "media", "image", "img", "photo", "picture", 
    "thumbnail", "thumb", "icon", "avatar", "video", "file", "path", 
    "href", "link", "content", "data", "original", "high_res", "hd", 
    "asset", "resource", "fileurl", "downloadurl", "mediaurl"
]
JSON_LINK_KEY_PATTERNS = [
    "next", "next_page", "nextpage", "pagination", "paging", "links", 
    "href", "url", "link", "related", "canonical", "alternate", "viewMoreUrl"
]
# R-C-4: the subset that is safe to follow as a crawl link. JSON_LINK_KEY_PATTERNS
# itself must NOT be used for crawling — its generic url/href/link entries would
# turn every URL in a payload into a crawl target.
JSON_PAGINATION_KEY_PATTERNS = [
    "next", "next_page", "nextpage", "pagination", "paging", "links",
]
# R-C-5 / R-C-6: machine-readable failure kinds. The downloader tags a failure
# result with one of these and ParserManager decides domain-health penalties from
# the kind instead of matching human-readable error substrings (the old substring
# list both punished local faults and listed messages nothing ever produced).
# Fail-open: a result with NO kind is never counted as a domain fault.
FAILURE_NETWORK = "network"
FAILURE_HTTP_5XX = "http_5xx"
FAILURE_HTTP_429 = "http_429"
FAILURE_HTTP_4XX = "http_4xx"
FAILURE_CONTENT_PAGE = "content_page"   # HTML/script/JSON body instead of media
FAILURE_UNEXPECTED = "unexpected"
# Only these justify quarantining a host; everything else is our own or content-side.
FAILURE_PENALISING = (FAILURE_NETWORK, FAILURE_HTTP_5XX, FAILURE_HTTP_429, FAILURE_UNEXPECTED)

# Bounds for the JSON pagination scan (R-C-4): a pathological payload must not
# cost unbounded time, and the walk is iterative so depth is only a sanity limit.
JSON_LINK_SCAN_MAX_NODES = 100_000
JSON_LINK_SCAN_MAX_DEPTH = 32

# Comprehensive File Extensions
IMAGE_EXTENSIONS = [
    ".jpg", ".jpeg", ".jfif", ".jif", ".png", ".gif", ".webp", ".svg", ".tiff", ".bmp", ".avif", ".ico"
]
VIDEO_EXTENSIONS = [
    ".mp4", ".webm", ".ogg", ".mov", ".avi", ".wmv", ".flv", ".mkv", 
    ".m4v", ".ts", ".mpeg", ".mpg"
]
AUDIO_EXTENSIONS = [
    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".m4a"
]

# Trash Media Extensions - Should be skipped for downloading but followed as triggers/links
TRASH_MEDIA_EXTENSIONS = [".gif", ".ico", ".svg", ".cur"]

# Enabled-format allowlists (Settings -> Filters). Users may enable formats that
# are disabled by default (GIF/SVG/ICO/CUR are almost always decorative junk).
# Defaults preserve historical behavior: those formats stay disabled.
DEFAULT_ENABLED_IMAGE_FORMATS = [
    ".jpg", ".jpeg", ".jfif", ".jif", ".png", ".webp", ".avif", ".bmp", ".tiff",
]
DEFAULT_ENABLED_VIDEO_FORMATS = list(VIDEO_EXTENSIONS) + [".m3u8", ".mpd"]  # + streaming manifests
DEFAULT_ENABLED_AUDIO_FORMATS = list(AUDIO_EXTENSIONS)

# Inject format-allowlist defaults now that the lists are defined.
DEFAULT_SETTINGS_VALUES[SETTING_ENABLED_IMAGE_FORMATS] = DEFAULT_ENABLED_IMAGE_FORMATS
DEFAULT_SETTINGS_VALUES[SETTING_ENABLED_VIDEO_FORMATS] = DEFAULT_ENABLED_VIDEO_FORMATS
DEFAULT_SETTINGS_VALUES[SETTING_ENABLED_AUDIO_FORMATS] = DEFAULT_ENABLED_AUDIO_FORMATS

KNOWN_FILE_EXTENSIONS = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS + AUDIO_EXTENSIONS + [
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", 
    ".zip", ".rar", ".tar.gz", ".7z",
    ".txt", ".csv", ".xml", ".json", 
    ".exe", ".msi", ".dmg", ".apk", ".deb", ".rpm"
]

# Video Platform Indicators (Domains/Substrings for quick checks)
VIDEO_PLATFORM_INDICATORS = [
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "vimeo.com", "player.vimeo.com",
    "dailymotion.com", "dai.ly",
    "twitch.tv",
    "streamable.com",
    "redgifs.com", "gifdeliverynetwork.com",
    "gfycat.com",
    "bilibili.com", "b23.tv",
    "tiktok.com"
]

# CDN domains that typically serve media files (used by is_media_url)
CDN_MEDIA_DOMAINS = [
    "cloudfront.net",
    "akamaihd.net",
    "googleapis.com",
    "cloudflare.com",
    "cdninstagram.com",
    "twimg.com",
    "imgur.com",
    "staticflickr.com",
    "ytimg.com",
    "fbcdn.net",
    "ssl-images-amazon.com",
    "pinimg.com",
    "wp.com",
    "media-amazon.com",
    "media.tumblr.com",
]

# URL path segments that indicate media content (used by is_media_url)
MEDIA_URL_PATHS = [
    "/images/", "/img/", "/photos/", "/photo/", "/pictures/", "/pics/",
    "/thumbnails/", "/thumb/", "/avatars/", "/uploads/", "/media/",
    "/static/", "/assets/", "/files/", "/download/", "/gallery/",
    "/videos/", "/video/", "/movie/", "/movies/", "/clip/", "/clips/",
]

# Query parameter names that indicate media content (used by is_media_url)
MEDIA_URL_PARAMS = [
    "image", "img", "photo", "pic", "video", "media", "file", "download",
    "media_url", "source", "src", "thumb", "preview", "original",
]
