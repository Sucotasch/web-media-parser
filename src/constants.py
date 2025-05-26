#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Global constants for the Web Media Parser application.
"""

# Default Application Settings
DEFAULT_RETRY_COUNT = 3
DEFAULT_TIMEOUT = 30  # General network timeout for requests
DEFAULT_PAGE_TIMEOUT = 60  # Timeout for fetching and processing a single webpage or API
DEFAULT_CONNECT_TIMEOUT = 20 # Timeout for establishing a connection
DEFAULT_SOCK_READ_TIMEOUT = DEFAULT_PAGE_TIMEOUT # Timeout for reading from a socket

DEFAULT_SEARCH_DEPTH = 3
DEFAULT_PARSER_THREADS = 2
DEFAULT_DOWNLOADER_THREADS = 4
DEFAULT_THREADS_PER_FILE = 1 # For multi-threaded download of a single file

# Media Filtering
DEFAULT_MIN_IMAGE_WIDTH = 100
DEFAULT_MIN_IMAGE_HEIGHT = 100
DEFAULT_MIN_IMAGE_SIZE_KB = 0 # 0 means no minimum size
DEFAULT_MIN_VIDEO_SIZE_KB = 0 # 0 means no minimum size

# Domain Health & Quarantine
DEFAULT_QUARANTINE_FAILURE_THRESHOLD = 3
DEFAULT_DOMAIN_PROBATION_TIMEOUT = 2  # Timeout for downloads from domains on "probation"
DEFAULT_DOMAIN_PROBATION_RETRIES = 0 # Retries for downloads from domains on "probation"


# HTTP Headers
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
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

# Stop words for URL filtering (example, can be expanded)
DEFAULT_STOP_WORDS = [
    "login", "register", "cart", "checkout", "about_us", "contact", "privacy_policy", "terms_of_service", "careers"
]

# Session state filename
SESSION_STATE_FILENAME = "last_session.pkl"
SESSION_STATE_SUBDIR = "sessions"

# Blocklist filename
DOMAIN_BLOCKLIST_FILENAME = "domain_blocklist.txt"

# Logging
LOG_CLEAR_HISTORY_ON_START = False # If true, clears log window every time app starts.

# Max items to process from quarantine queue in one go
QUARANTINE_BATCH_PROCESS_SIZE = 10

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
SETTING_STAY_IN_DOMAIN = "stay_in_domain"
SETTING_USE_PATTERNS = "use_patterns"
SETTING_CUSTOM_PATTERN_PATH = "custom_pattern_path"
SETTING_PROCESS_JS = "process_js" # This now controls all advanced content extraction
# SETTING_PROCESS_DYNAMIC = "process_dynamic" # Removed
SETTING_BYPASS_COOKIE_CONSENT = "bypass_cookie_consent"
SETTING_BYPASS_JS_REDIRECTS = "bypass_js_redirects"
SETTING_STOP_WORDS = "stop_words" # List of strings
SETTING_MAX_DOWNLOAD_SPEED = "max_download_speed" # in KB/s, 0 for unlimited
SETTING_PAGE_TIMEOUT = "page_timeout" # Timeout for page loading/parsing

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
    SETTING_PROCESS_JS: DEFAULT_PROCESS_JS,
    # SETTING_PROCESS_DYNAMIC: DEFAULT_PROCESS_DYNAMIC, # Removed
    SETTING_BYPASS_COOKIE_CONSENT: DEFAULT_BYPASS_COOKIE_CONSENT,
    SETTING_BYPASS_JS_REDIRECTS: DEFAULT_BYPASS_JS_REDIRECTS,
    SETTING_STOP_WORDS: DEFAULT_STOP_WORDS,
    SETTING_MAX_DOWNLOAD_SPEED: 0, # KB/s
    SETTING_PAGE_TIMEOUT: DEFAULT_PAGE_TIMEOUT,
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
