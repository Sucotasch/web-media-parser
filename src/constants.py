#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Global constants for the Web Media Parser application.
"""

# Default Application Settings
DEFAULT_RETRY_COUNT = 3
DEFAULT_TIMEOUT = 30  # General network timeout for requests
DEFAULT_PAGE_TIMEOUT = 30  # Timeout for fetching and processing a single webpage or API
DEFAULT_CONNECT_TIMEOUT = 20 # Timeout for establishing a connection
DEFAULT_SOCK_READ_TIMEOUT = DEFAULT_PAGE_TIMEOUT # Timeout for reading from a socket

DEFAULT_SEARCH_DEPTH = 3
DEFAULT_PARSER_THREADS = 4
DEFAULT_DOWNLOADER_THREADS = 8
DEFAULT_THREADS_PER_FILE = 1 # For multi-threaded download of a single file

# Media Filtering
DEFAULT_MIN_IMAGE_WIDTH = 100
DEFAULT_MIN_IMAGE_HEIGHT = 100
DEFAULT_MIN_IMAGE_SIZE_KB = 40 # Minimum image file size in KB
DEFAULT_MIN_VIDEO_SIZE_KB = 1000 # Minimum video file size in KB

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
MAX_FILENAME_LENGTH = 200 # Max length for sanitized filenames (excluding extension)

# UI & Dialog Defaults
DEFAULT_SAVE_PATH = "downloads" # Default download directory base name

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
DEFAULT_STOP_WORDS = [
    "login", "register", "cart", "checkout", "about", "contact", "privacy", "terms", "careers"
]

# Crawl limits
DEFAULT_MAX_LINKS_PER_PAGE = 200  # Cap links per page to prevent queue explosion on menu-heavy pages

# Guards against pathological URL growth (e.g. urljoin of relative vBulletin
# "threads/..." links on a base URL without a trailing slash appends the same
# segment forever: threads/threads/threads/...). URLs beyond these bounds are
# dropped by PriorityURLQueue.put() before they can flood the queue.
MAX_URL_PATH_SEGMENTS = 50   # Max path segments a queued URL may have
MAX_URL_LENGTH = 2000        # Max total length of a queued URL

# Fullsize discovery via the sieve link->url->res chain (mirrors the
# extension's discoverFullsize). Applied to thumbnail-transition links
# (from_image) on every parsed page so external image hosts (imx.to, postimg,
# ...) resolve to fullsize originals instead of being dropped by domain checks.
# Media lookups are never content-capped (a 500-image gallery resolves all
# 500); the guards are RESOURCE-based only: per-probe timeout, concurrency,
# and a total time budget per source page so a dead host can't stall a worker.
FULLSIZE_DISCOVER_CONCURRENCY = 5   # Concurrent probes (extension uses 5)
FULLSIZE_DISCOVER_TIMEOUT = 8       # Seconds per probe (extension uses 8000ms)
FULLSIZE_DISCOVER_TIME_BUDGET = 45  # Max total probe time per source page

# Gateway & Visibility Filtering
DEFAULT_FILTER_HIDDEN_LINKS = True
DEFAULT_BYPASS_GATEWAYS = True
GATEWAY_MIN_MEDIA_THRESHOLD = 2

# CSS and attribute values that typically hide elements (bot-traps)
VISIBILITY_HIDDEN_KEYWORDS = [
    "display: none", "display:none", "visibility: hidden", "visibility:hidden", 
    "opacity: 0", "opacity:0", "left: -999", "top: -999", "width: 0", "height: 0"
]
VISIBILITY_HIDDEN_CLASSES = [
    "hidden", "sr-only", "visually-hidden", "bot-trap", "honeypot", "invisible", "d-none"
]

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
SETTING_SEARCH_DEPTH = "search_depth"
SETTING_PARSER_THREADS = "parser_threads"
SETTING_DOWNLOADER_THREADS = "downloader_threads"
SETTING_THREADS_PER_FILE = "threads_per_file"
SETTING_MIN_IMG_WIDTH = "min_image_width"
SETTING_MIN_IMG_HEIGHT = "min_image_height"
SETTING_MIN_IMG_SIZE = "min_image_size" # in KB
SETTING_MIN_VID_SIZE = "min_video_size" # in KB
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
SETTING_PROCESS_JS = "process_js"
SETTING_BYPASS_COOKIE_CONSENT = "bypass_cookie_consent"
SETTING_BYPASS_JS_REDIRECTS = "bypass_js_redirects"
SETTING_FILTER_HIDDEN_LINKS = "filter_hidden_links"
SETTING_FILTER_JUNK = "filter_junk"  # P2-lite ad/tracker/junk URL classifier
SETTING_STOP_WORDS = "stop_words"

# Filter settings keys
SETTING_MAX_DOWNLOAD_SPEED = "max_download_speed" # in KB/s, 0 for unlimited
SETTING_PAGE_TIMEOUT = "page_timeout" # Timeout for page loading/parsing
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
    SETTING_PROCESS_JS: DEFAULT_PROCESS_JS,
    # SETTING_PROCESS_DYNAMIC: DEFAULT_PROCESS_DYNAMIC, # Removed
    SETTING_BYPASS_COOKIE_CONSENT: DEFAULT_BYPASS_COOKIE_CONSENT,
    SETTING_BYPASS_JS_REDIRECTS: DEFAULT_BYPASS_JS_REDIRECTS,
    SETTING_FILTER_JUNK: True,  # P2-lite junk classifier (precision-first + allowlist)
    SETTING_STOP_WORDS: DEFAULT_STOP_WORDS,
    SETTING_MAX_DOWNLOAD_SPEED: 0, # KB/s
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

# Comprehensive File Extensions
IMAGE_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tiff", ".bmp", ".avif", ".ico"
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
    ".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff",
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
