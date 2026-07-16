#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Utility functions for parser module
"""

import re
import os
from urllib.parse import urlparse, urljoin
from src import constants as K


def format_proxy_url(proxy_str):
    """
    Normalize proxy string to include scheme if missing.
    e.g. '127.0.0.1:8080' -> 'http://127.0.0.1:8080'
    """
    if not proxy_str or not isinstance(proxy_str, str):
        return None
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None
    
    # If it already has a scheme, leave it
    if "://" in proxy_str:
        return proxy_str
    
    # Default to http:// for simple host:port strings
    return f"http://{proxy_str}"


def is_valid_url(url):
    """
    Check if URL is valid
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and result.scheme in [
            "http",
            "https",
        ]
    except Exception:
        return False


def is_image_url(url):
    """
    Check if URL is likely to be a direct image file based on extension or pattern
    """
    url_lower = url.lower()
    
    # Parse URL to handle query parameters properly
    parsed_url = urlparse(url_lower)
    path = parsed_url.path
    
    # Basic extension check using centralized constants
    if (any(path.endswith(ext) for ext in K.IMAGE_EXTENSIONS) or 
        any(f"{ext}?" in path for ext in K.IMAGE_EXTENSIONS)):
        return True
        
    # Advanced pattern matching based on RipUtils.java
    image_pattern = re.compile(r"(https?://[a-zA-Z0-9\-.]+\.[a-zA-Z]{2,3}(/\S*)\.(jpg|jpeg|gif|png|webp|avif)(\?.*)?)", re.IGNORECASE)
    
    return bool(image_pattern.match(url_lower))


def is_trash_media(url):
    """
    Check if URL is a known junk/trash format (.ico, .svg, .gif, .cur)
    """
    if not url:
        return False
        
    url_lower = url.lower()
    parsed_url = urlparse(url_lower)
    path = parsed_url.path
    
    return any(path.endswith(ext) for ext in K.TRASH_MEDIA_EXTENSIONS)


def get_domain(url):
    """
    Extract domain from URL
    """
    try:
        parsed_url = urlparse(url)
        return parsed_url.netloc
    except Exception:
        return ""


def is_webpage_url(url):
    """
    Check if URL is likely to be a webpage rather than a media file
    """
    # First, do an explicit check for media URLs - these are NOT webpages
    if is_media_url(url):
        return False
        
    # Common webpage file extensions
    webpage_extensions = [
        ".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".jspx",
        ".cfm", ".cfml", ".py", ".rb", ".pl", ".cgi", ".shtml", ".xhtml"
    ]
    
    url_lower = url.lower()
    
    # Check for explicit media extensions
    media_extensions = [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif",
        ".mp4", ".webm", ".ogg", ".mov", ".mp3", ".wav", ".pdf"
    ]
    if any(url_lower.endswith(ext) for ext in media_extensions):
        return False
        
    # Check for URL patterns that typically indicate media files rather than webpages
    # These are common patterns across many sites indicating fullsize or original images
    fullsize_indicators = ['full', 'large', 'original', 'highres', 'hires', 'hi-res', 'big']
    if any(indicator in url_lower for indicator in fullsize_indicators) and any(ext in url_lower for ext in media_extensions):
        # URLs with both a fullsize indicator and a media extension are likely media files, not webpages
        return False
    
    # Check for explicit webpage extensions
    if any(url_lower.endswith(ext) for ext in webpage_extensions):
        return True
        
    # Check for typical CMS URL patterns that likely point to HTML pages
    patterns = [
        r'/post/', r'/article/', r'/page/', r'/entry/', r'/view/', 
        r'/gallery/', r'/album/', r'/photo/', r'/collection/',
        r'/\d+\.\d+\.\d+/', r'/category/', r'/tag/',
        r'\?id=', r'&id=', r'\?page=', r'&page=',
        r'view\.php', r'index\.php', r'gallery\.php'
    ]
    
    for pattern in patterns:
        if re.search(pattern, url_lower):
            return True
    
    # Check for absence of file extension (likely a dynamic page)
    parsed_url = urlparse(url_lower)
    path = parsed_url.path
    
    # If URL has a query string but no file extension, it's likely a webpage
    if parsed_url.query:
        # If no media extension is found, it's likely a webpage
        return not any(path.endswith(ext) for ext in media_extensions)
    
    return False
    

def is_media_url(url):
    """
    Check if URL is likely to be a media file based on extension, pattern, or path
    """
    url_lower = url.lower()
    parsed_url = urlparse(url_lower)
    path = parsed_url.path
    
    # Check for standard media file extensions from constants
    media_extensions = K.IMAGE_EXTENSIONS + K.VIDEO_EXTENSIONS + K.AUDIO_EXTENSIONS
    
    # Direct extension check - most reliable method (handles query params via path)
    if any(path.endswith(ext) for ext in media_extensions):
        return True
    
    # Check for fullsize pattern combined with media extension - common across many sites
    fullsize_indicators = ['full', 'large', 'original', 'highres', 'hires', 'hi-res', 'max', 'big']
    
    # If URL contains both a fullsize indicator and a media extension, it's very likely a media file
    if any(indicator in url_lower for indicator in fullsize_indicators):
        for ext in media_extensions:
            if ext in url_lower:
                return True
    
    # Quick check for common webpage file extensions that should NOT be treated as media
    non_media_extensions = [
        # Webpage extensions
        ".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".jspx",
        ".cfm", ".cfml", ".py", ".rb", ".pl", ".cgi", ".shtml", ".xhtml",
        # Script and data extensions
        ".js", ".jsx", ".ts", ".tsx", ".coffee", ".es6", ".mjs",
        ".css", ".scss", ".sass", ".less", 
        ".json", ".xml", ".rss", ".atom", ".yaml", ".yml", 
        ".wasm", ".map"
    ]
    
    if any(url_lower.endswith(ext) for ext in non_media_extensions):
        return False
        
    # Check for media file extensions (use constants + streaming/document formats)
    media_extensions = K.IMAGE_EXTENSIONS + K.VIDEO_EXTENSIONS + K.AUDIO_EXTENSIONS + [
        ".mpd", ".m3u8",  # Streaming formats
        ".pdf",  # Documents that should be downloaded, not parsed as HTML
    ]

    parsed_url = urlparse(url_lower)
    domain = parsed_url.netloc
    path = parsed_url.path

    # Advanced regex patterns from RipUtils.java
    image_pattern = re.compile(r"(https?://[a-zA-Z0-9\-.]+\.[a-zA-Z]{2,3}(/\S*)\.(jpg|jpeg|gif|png|webp|avif|svg|tiff)(\?.*)?)", re.IGNORECASE)
    video_pattern = re.compile(r"(https?://[a-zA-Z0-9\-.]+\.[a-zA-Z]{2,3}(/\S*)\.(mp4|webm|ogg|mov|avi|wmv|flv|mkv|m4v|ts|m3u8)(\?.*)?)", re.IGNORECASE)
    streaming_pattern = re.compile(r"(https?://[^\s]+\.(m3u8|mpd)(\?[^\s]*)?|https?://[^\s]+/playlist\.m3u8|https?://[^\s]+/manifest\.mpd)", re.IGNORECASE)

    if image_pattern.match(url_lower) or video_pattern.match(url_lower) or streaming_pattern.match(url_lower):
        return True

    # Check for embedded video platforms
    for platform in K.VIDEO_PLATFORM_INDICATORS:
        if platform in url_lower:
            return True

    # Check extensions
    for ext in media_extensions:
        if url_lower.endswith(ext) or f"{ext}?" in url_lower or f"{ext}&" in url_lower:
            return True

    # Check CDN domains
    for cdn in K.CDN_MEDIA_DOMAINS:
        if cdn in domain:
            return True

    # Check paths
    for media_path in K.MEDIA_URL_PATHS:
        if media_path in path:
            return True

    # Check for media-related query parameters
    query = parsed_url.query.lower()
    for param in K.MEDIA_URL_PARAMS:
        if f"{param}=" in query or f"{param}_id=" in query:
            return True

    return False


def is_video_url(url):
    """
    Check if URL is likely to be a direct video file based on extension or pattern
    """
    video_extensions = [
        ".mp4", ".webm", ".ogg", ".mov", ".avi", ".wmv", ".flv", ".mkv", ".m4v", ".ts", ".m3u8", ".mpd",
        ".3gp", ".vob", ".mxf", ".f4v", ".mpg", ".mpeg", ".asf", ".rm", ".rmvb"
    ]
    url_lower = url.lower()
    
    # Parse URL to handle query parameters properly
    parsed_url = urlparse(url_lower)
    path = parsed_url.path
    query = parsed_url.query
    
    # Basic extension check
    if (any(path.endswith(ext) for ext in video_extensions) or 
        any(f"{ext}?" in path for ext in video_extensions)):
        return True
    
    # Check for video platforms and CDNs
    video_domains = [
        'vimeo.com', 'youtube.com', 'youtu.be', 'dailymotion.com', 'twitch.tv',
        'streamable.com', 'vimeocdn.com', 'jwplatform.com', 'brightcove.net',
        'vidyard.com', 'wistia.com', 'jwplayer.com', 'bitchute.com',
        'vk.com/video', 'facebook.com/watch', 'redgifs.com', 'gfycat.com'
    ]
    
    domain = parsed_url.netloc
    if any(video_domain in domain for video_domain in video_domains):
        return True
    
    # Check for streaming formats and common video API patterns
    streaming_patterns = [
        '/playlist.m3u8', '/manifest.mpd', '/master.m3u8',
        '/hls/', '/dash/', '/streaming/', '/video/',
        '/media/', '/player/', '/videos/', '/embed/',
        '/get_video', '/download/video', '/v/'
    ]
    
    if any(pattern in path for pattern in streaming_patterns):
        return True
    
    # Check for common video parameter patterns in query strings
    video_params = ['video_id', 'vid', 'v', 'video', 'clip', 'file']
    if query and any(f"{param}=" in query for param in video_params):
        return True
    
    # Advanced pattern matching
    video_pattern = re.compile(r"(https?://[a-zA-Z0-9\-.]+\.[a-zA-Z]{2,3}(/\S*)\.(mp4|webm|ogg|mov|avi|wmv|flv|mkv|m4v|ts|m3u8)(\?.*)?)", re.IGNORECASE)
    streaming_pattern = re.compile(r"(https?://[^\s]+\.(m3u8|mpd)(\?[^\s]*)?|https?://[^\s]+/playlist\.m3u8|https?://[^\s]+/manifest\.mpd)", re.IGNORECASE)
    
    # Check for common video filename patterns
    video_filename_patterns = [
        r'/video[_-]\d+', r'/clip[_-]\d+', r'/movie[_-]\d+',
        r'/media_[a-f0-9]{32}', r'/player_[a-f0-9]{8}',
        r'/play\.php\?vid=', r'/watch\?v='
    ]
    
    for pattern in video_filename_patterns:
        if re.search(pattern, url_lower):
            return True
    
    return bool(video_pattern.match(url_lower) or streaming_pattern.match(url_lower))


def is_same_domain(url1, url2):
    """
    Check if two URLs belong to the same domain
    """
    domain1 = get_domain(url1)
    domain2 = get_domain(url2)

    if not domain1 or not domain2:
        return False

    # Extract base domain (example.com from sub.example.com)
    base_domain1 = (
        ".".join(domain1.split(".")[-2:]) if len(domain1.split(".")) > 1 else domain1
    )
    base_domain2 = (
        ".".join(domain2.split(".")[-2:]) if len(domain2.split(".")) > 1 else domain2
    )

    return base_domain1 == base_domain2


def normalize_url(url):
    """
    Normalize URL by removing fragments, normalizing path, and lowercase domain.
    Ensures consistency across parser restarts.
    """
    try:
        if not url: return ""
        parsed = urlparse(url)
        # Lowercase scheme and domain (netloc)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        
        # Remove fragment and normalize path
        path = parsed.path
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]
        
        # Reconstruct without fragment
        normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path, fragment="").geturl()
        return normalized
    except Exception:
        return url


def is_banner_or_ad(url, attrs):
    """
    Check if a media file is likely to be a banner or advertisement
    """
    # Keywords that suggest banners or ads (use word boundaries to avoid false positives)
    ad_keywords = [
        "ads", "advert", "advertisement",
        "banner", "promo", "promotion", "sponsor",
        "tracking", "pixel", "analytics", "marketing", "campaign",
        "popup", "popover", "cta", "calltoaction", "call-to-action",
    ]

    url_lower = url.lower()

    # Check URL for ad-related keywords
    for keyword in ad_keywords:
        if keyword in url_lower:
            return True

    # Check element attributes
    if attrs:
        # Check for small dimensions (common for ad pixels)
        width = attrs.get("width", "")
        height = attrs.get("height", "")

        try:
            if width and height:
                width_val = int(width)
                height_val = int(height)

                # Very small images are likely tracking pixels
                if (width_val <= 5 and height_val <= 5) or (
                    width_val == 1 and height_val == 1
                ):
                    return True

                # Banner-like aspect ratios
                if (width_val >= 3 * height_val) or (
                    height_val >= 5 * width_val and width_val < 300
                ):
                    return True
        except (ValueError, TypeError):
            pass

        # Check for ad-related classes or IDs
        element_class = attrs.get("class", "")
        element_id = attrs.get("id", "")
        element_alt = attrs.get("alt", "")

        for keyword in ad_keywords:
            if (
                (isinstance(element_class, str) and keyword in element_class.lower())
                or (isinstance(element_id, str) and keyword in element_id.lower())
                or (isinstance(element_alt, str) and keyword in element_alt.lower())
            ):
                return True

    return False


def extract_largest_image_from_srcset(srcset):
    """
    Extract the largest image URL from a srcset attribute
    """
    if not srcset:
        return None

    best_url = None
    max_width = 0

    # Parse srcset format: "url1 123w, url2 456w, ..."
    for src_item in srcset.split(","):
        parts = src_item.strip().split(" ")
        if len(parts) >= 2:
            url = parts[0].strip()

            # Parse width descriptor (e.g., 800w)
            width_match = re.search(r"(\d+)w", parts[1])
            if width_match:
                width = int(width_match.group(1))
                if width > max_width:
                    max_width = width
                    best_url = url

    return best_url


# --- Segment-aware URL classifier (WP-1) ---

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
    "search", "login.php", "wp-admin", "wp-login",
})

# Hosts that are ad networks
_AD_HOSTS = (
    "doubleclick.", "googlesyndication.", "googleadservices.",
    "facebook.com/tr", "adservice.", "adnxs.", "taboola.", "outbrain.",
)


def _path_segments(url):
    """Extract lowercase path segments from a URL."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return []
    return [s for s in path.split("/") if s]


def should_skip_crawl_url(url, extra_stop_words=None):
    """Return True if URL should not be queued for HTML parsing.

    Uses segment-aware matching (not substring) to avoid false positives
    like "ad" in "media" or "admin".
    """
    if not url:
        return True
    try:
        p = urlparse(url)
    except Exception:
        return True

    path = (p.path or "").lower()
    segs = _path_segments(url)

    # Explicit file junk
    if path.endswith((".css", ".js", ".map", ".xml", ".json", ".txt", ".ico", ".woff", ".woff2", ".ttf")):
        return True

    # Segment match
    skip = set(DEFAULT_LINK_SKIP_SEGMENTS)
    if extra_stop_words:
        for w in extra_stop_words:
            w = (w or "").strip().lower().strip("/")
            if len(w) >= 3:
                skip.add(w)

    if any(s in skip for s in segs):
        return True

    # Host-level ad networks
    full = url.lower()
    if any(h in full for h in _AD_HOSTS):
        return True

    return False
