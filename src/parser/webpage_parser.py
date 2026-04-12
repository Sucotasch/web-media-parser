#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Webpage parser class for extracting media files and links from webpages
"""

import re
import os
import time 
import json
import asyncio
import logging
import mimetypes 
from typing import Set, List, Tuple, Dict, Any, Optional, Union
from urllib.parse import urlparse, urljoin 

import filetype 
try:
    import brotli 
    HAS_BROTLI = True 
except ImportError:
    HAS_BROTLI = False

from src.parser.utils import is_image_url, is_media_url, is_valid_url, get_domain, is_same_domain, normalize_url
from src.parser.site_pattern_manager import SitePatternManager
from src import constants as K 

import aiohttp 
import chardet
from bs4 import BeautifulSoup

if not HAS_BROTLI:
    try:
        from src.fix_brotli import BrotliSupportFix 
        HAS_BROTLI = BrotliSupportFix.patch()
    except ImportError:
        pass 

logger = logging.getLogger(__name__)


class WebpageParser:
    """
    Enhanced webpage parser class for extracting media files and links from webpages
    """
    CDN_PATTERNS = { 
        "img": [r"\.cloudfront\.net", r"\.akamaized\.net", r"\.cloudinary\.com", r"\.fastly\.net", r"\.imgix\.net", r"\.cdn\.", r"images?[0-9]*\.", r"cdn[0-9]*\.", r"static\.", r"media\."],
        "video": [r"\.brightcove\.net", r"\.jwplatform\.com", r"\.vimeocdn\.com", r"\.ytimg\.com", r"\.streamable\.com", r"video\.", r"videos?\.", r"media\."]
    }
    VIDEO_PLATFORMS = { 
        "youtube": [r"youtube\.com", r"youtu\.be", r"youtube-nocookie\.com"], "vimeo": [r"vimeo\.com", r"player\.vimeo\.com", r"vimeocdn\.com"], "dailymotion": [r"dailymotion\.com", r"dai\.ly", r"dm-static\.com"], "twitch": [r"twitch\.tv", r"ttvnw\.net", r"jtvnw\.net"], "facebook": [r"facebook\.com/watch", r"facebook\.com/video", r"fbcdn\.net", r"fb\.watch"], "instagram": [r"instagram\.com/tv", r"instagram\.com/reel", r"instagram\.com/p", r"cdninstagram\.com"], "tiktok": [r"tiktok\.com", r"musical\.ly", r"tiktokcdn\.com"], "vk": [r"vk\.com/video", r"vk\.ru/video"], "reddit": [r"reddit\.com/r/.*/video/", r"v\.redd\.it"], "twitter": [r"twitter\.com/.*/status/", r"t\.co", r"twimg\.com", r"pbs\.twimg\.com"], "redgifs": [r"redgifs\.com", r"gifdeliverynetwork\.com"], "bilibili": [r"bilibili\.com", r"bilivideo\.com", r"b23\.tv"], "streamable": [r"streamable\.com"], "imgur": [r"imgur\.com/a", r"imgur\.com/gallery", r"imgur\.com/\w+\.gifv", r"imgur\.com/\w+\.mp4"], "gfycat": [r"gfycat\.com"], "soundcloud": [r"soundcloud\.com"], "xvideos": [r"xvideos\.com"], "xhamster": [r"xhamster\.com"], "pornhub": [r"pornhub\.com"], "youporn": [r"youporn\.com"],
    }
    LAZY_LOAD_PATTERNS = { 
        "data-attributes": ["data-src", "data-original", "data-lazy", "data-load", "data-source", "data-srcset", "data-bg", "data-poster", "data-image", "data-original-src"],
        "class-patterns": [r"lazy", r"lazyload", r"b-lazy", r"delayed", r"deferred", r"preload", r"progressive"],
        "placeholder-patterns": [r"placeholder", r"blur-up", r"lqip", r"loading"]
    }
    DYNAMIC_PATTERNS = { 
        "infinite-scroll": [r"infinite[_-]?scroll", r"load[_-]?more", r"next[_-]?page", r"pagination"],
        "ajax-load": [r"ajax[_-]?load", r"dynamic[_-]?load", r"async[_-]?load", r"on[_-]?demand"],
        "content-placeholders": [r"content[_-]?placeholder", r"skeleton[_-]?loader", r"loading[_-]?placeholder"]
    }
    MEDIA_SOURCES = { 
        "img": [("src", "string"), ("srcset", "srcset"), ("data-src", "string"), ("data-srcset", "srcset"), ("data-original", "string"), ("style", "background")],
        "video": [("src", "string"), ("data-src", "string"), ("poster", "string"), ("data-poster", "string")],
        "source": [("src", "string"), ("srcset", "srcset"), ("data-src", "string"), ("data-srcset", "srcset")],
        "picture": [("source", "nested")]
    }
    JS_PATTERNS = { 
        "image_sources": [r'["\'](https?://[^"\']+\.(?:jpg|jpeg|png|gif|webp))["\']', r'\.src\s*=\s*["\'](https?://[^"\']+)["\']', r'loadImage\s*\(\s*["\'](https?://[^"\']+)["\']', r'background(?:-image)?\s*:\s*url\(["\']?(https?://[^"\']+)["\']?\)',],
        "video_sources": [r'["\'](https?://[^"\']+\.(?:mp4|webm|ogg))["\']', r'\.src\s*=\s*["\'](https?://[^"\']+\.(?:mp4|webm|ogg))["\']', r'loadVideo\s*\(\s*["\'](https?://[^"\']+)["\']',],
        "data_attributes": [r'data-(?:src|original|lazy|load|image|video|poster|bg|background|url)\s*=\s*["\'](https?://[^"\']+)["\']', r'data-srcset\s*=\s*["\'](https?://[^"\']+(?:\s+\d+[wx])?(?:,\s*https?://[^"\']+(?:\s+\d+[wx])?)*)["\']',],
        "framework_patterns": {"react": r'className\s*=\s*["\'](lazy-load|image-loader)["\']', "vue": r'v-lazy\s*=\s*["\'](https?://[^"\']+)["\']', "angular": r'\[lazyLoad\]\s*=\s*["\'](https?://[^"\']+)["\']',}
    }

    def __init__(
        self, url: str, settings: Dict[str, Any],
        process_js: bool, # This will now control all advanced content extraction
        external_session: aiohttp.ClientSession, 
        pattern_manager: Optional[SitePatternManager] = None,
    ):
        self.url = url
        self.settings = settings 
        self.process_js = process_js 
        # self.process_dynamic = process_dynamic # Removed, covered by process_js
        self.domain = get_domain(url)
        
        if external_session is None:
            raise ValueError("WebpageParser requires an external_session (aiohttp.ClientSession).")
        self.session = external_session 
        
        self.pattern_manager = pattern_manager
        self.links: Dict[str, Dict[str, Any]] = {} 
        self.media_files: List[Tuple[str, str, Dict[str, Any]]] = []
        self._mime_type: Optional[str] = None
        self.js_redirect_count = 0 
        self._sync_session = None # Lazy-loaded persistent session for fallback
        self._bypass_attempts = 0 # Track bypass attempts to prevent loops

    def get_discovered_urls(self) -> Dict[str, Dict[str, Any]]:
        return self.links

    async def _get_content(self) -> Tuple[Optional[str], Optional[str], str, Optional[int]]:
        """
        Get webpage content.
        Returns: (content_string, error_status, error_message, http_status_code)
        """
        if self.js_redirect_count > K.MAX_JS_REDIRECTS:
            msg = f"Exceeded maximum JS redirects ({K.MAX_JS_REDIRECTS}) for URL: {self.url}"
            logger.error(msg)
            return None, K.PARSER_JS_REDIRECT_MAX_EXCEEDED, msg, None

        max_retries = self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT)
        content_bytes = None
        http_status = None
        
        # 1. Very fast aiohttp attempt (or two)
        for attempt in range(max_retries + 1):
            try:
                request_specific_headers = {}
                referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
                
                if referrer_policy != "none" and self.js_redirect_count == 0:
                    source_url = self.settings.get("_source_url")
                    if source_url and source_url != self.url:
                        request_specific_headers["Referer"] = source_url
                    elif referrer_policy == "origin":
                        request_specific_headers["Referer"] = get_domain(self.url)
                
                cookies = {}
                if self.settings.get(K.SETTING_BYPASS_COOKIE_CONSENT, K.DEFAULT_BYPASS_COOKIE_CONSENT):
                    consent_cookies = { 
                        'cookieconsent_status': 'dismiss', 'gdpr_accepted': 'true', 
                        'cookies_accepted': 'true', 'euconsent': 'true', 'CookieConsent': 'true',
                        'cc_cookie_accept': '1', 'cookie_consent': 'true', 'privacy_policy_accepted': 'true',
                        # Pre-emptive strike for age gates and gateways
                        'age_verified': '1', 'vantage': '1', 'over18': '1', 'nw': '1', 'nsfw': '1', 'terms': '1'
                    }
                    cookies.update(consent_cookies)
                
                page_timeout_val = self.settings.get(K.SETTING_PAGE_TIMEOUT, K.DEFAULT_PAGE_TIMEOUT)
                # VERY Aggressive connect timeout (5 secs max), so we don't hang queues
                request_timeout_config = aiohttp.ClientTimeout(
                    total=page_timeout_val, 
                    connect=min(5, page_timeout_val // 2),
                    sock_read=page_timeout_val - 2
                )

                if attempt > 0:
                    logger.info(f"Retrying fetch of {self.url} via aiohttp (Attempt {attempt+1}/{max_retries+1})...")
                    await asyncio.sleep(0.5 * attempt) # Fast backoff

                async with self.session.get(self.url, headers=request_specific_headers, cookies=cookies, timeout=request_timeout_config) as response:
                    http_status = response.status
                    if 400 <= http_status < 500:
                        msg = f"Client HTTP error {http_status} for {self.url}"
                        logger.error(msg)
                        return None, K.PARSER_HTTP_ERROR_4XX, msg, http_status
                    elif 500 <= http_status < 600:
                        msg = f"Server HTTP error {http_status} for {self.url}"
                        logger.error(msg)
                        if attempt < max_retries: continue
                        return None, K.PARSER_HTTP_ERROR_5XX, msg, http_status
                    
                    content_bytes = await response.read()
                    break # Success with aiohttp
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                # If it's a network/timeout error, we break out early and use the requests fallback 
                # instead of blindly retrying and wasting time on IP/TLS blocks.
                logger.debug(f"aiohttp failed for {self.url} ({str(e)}). Switching to fallback...")
                break
            except Exception as e:
                msg = f"Generic error fetching content for {self.url}: {str(e)}"
                logger.error(msg, exc_info=True)
                return None, K.PARSER_UNKNOWN_ERROR, msg, None

        # 2. Fallback to requests if aiohttp couldn't fetch bytes (TLS fingerprint / block)
        if not content_bytes:
            try:
                logger.info(f"Using sync fallback (requests) for {self.url}")
                loop = asyncio.get_event_loop()
                
                # Capture headers to pass into the synchronous call
                fb_headers = {"User-Agent": K.DEFAULT_USER_AGENT}
                if request_specific_headers.get("Referer"):
                    fb_headers["Referer"] = request_specific_headers["Referer"]
                
                def _sync_fetch():
                    session = self._get_sync_session()
                    # Aggressive timeout for sync to prevent blocking thread pool
                    fb_timeout = 10 
                    resp = session.get(self.url, headers=fb_headers, timeout=fb_timeout, allow_redirects=True, verify=False)
                    return resp

                resp = await loop.run_in_executor(None, _sync_fetch)
                http_status = resp.status_code
                if 400 <= http_status < 600:
                    return None, K.PARSER_HTTP_ERROR_4XX if http_status < 500 else K.PARSER_HTTP_ERROR_5XX, f"HTTP {http_status} via fallback", http_status
                content_bytes = resp.content
            except Exception as fb_err:
                logger.error(f"Fallback failed for {self.url}: {fb_err}")
                return None, K.PARSER_NETWORK_ERROR, f"Fallback failed: {str(fb_err)}", http_status

        if not content_bytes:
            return None, K.PARSER_NETWORK_ERROR, "Failed to retrieve content bytes after all attempts", http_status

        encoding = await self._detect_encoding(content_bytes)
        decoded_content: Optional[str] = None
        try:
            decoded_content = content_bytes.decode(encoding, errors="replace")
        except (UnicodeDecodeError, LookupError) as e:
            msg = f"Failed to decode with {encoding} for {self.url}, falling back to utf-8: {str(e)}"
            logger.warning(msg)
            try:
                decoded_content = content_bytes.decode("utf-8", errors="replace")
            except (UnicodeDecodeError, LookupError) as e_utf8:
                msg_utf8 = f"UTF-8 fallback decoding also failed for {self.url}: {str(e_utf8)}"
                logger.error(msg_utf8)
                return None, K.PARSER_CONTENT_DECODE_ERROR, msg_utf8, http_status
        
        if self.settings.get(K.SETTING_BYPASS_JS_REDIRECTS, K.DEFAULT_BYPASS_JS_REDIRECTS) and decoded_content:
            redirect_url = self._extract_js_redirect(decoded_content)
            if redirect_url:
                self.js_redirect_count += 1
                logger.info(f"Detected JS redirect from {self.url} to {redirect_url} (Count: {self.js_redirect_count})")
                abs_redirect_url = urljoin(self.url, redirect_url)
                self.url = abs_redirect_url 
                return await self._get_content() 
        
        return decoded_content, None, "Success", http_status 

    async def _detect_encoding(self, content_bytes: bytes) -> str:
        if content_bytes.startswith(b"\xef\xbb\xbf"): return "utf-8-sig"
        elif content_bytes.startswith(b"\xff\xfe") or content_bytes.startswith(b"\xfe\xff"): return "utf-16"
        detected = chardet.detect(content_bytes[:2048]) 
        encoding = detected["encoding"] if detected["encoding"] else "utf-8"
        return encoding
        
    def _extract_js_redirect(self, content: str) -> Optional[str]:
        if not content: return None
        patterns = [
            r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\.replace\s*\(\s*["\']([^"\']+)["\']\s*\)',
            r'document\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
            r'<meta[^>]*?http-equiv=["\']?refresh["\']?[^>]*?content=["\']?\d+;\s*url=([^\s"\'>]+)["\']?',
        ]
        for pattern in patterns:
            try:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches: return matches[0] 
            except Exception: continue 
        return None

    def _is_cdn_url(self, url: str, media_type: str) -> bool:
        patterns = self.CDN_PATTERNS.get(media_type, [])
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in patterns)

    def _get_sync_session(self):
        """Lazy-loader for a persistent sync session to maintain cookies during bypass"""
        if self._sync_session is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            
            self._sync_session = requests.Session()
            # Disable internal retries to allow immediate termination via UI Stop button 
            adapter = HTTPAdapter(max_retries=Retry(total=0, connect=None, read=None, redirect=None, status=None))
            self._sync_session.mount("http://", adapter)
            self._sync_session.mount("https://", adapter)
            # Pre-set standard headers
            self._sync_session.headers.update({"User-Agent": K.DEFAULT_USER_AGENT})
            
            # Explicitly pre-set cookies for known major sites to reduce friction
            if "livejournal.com" in self.domain:
                self._sync_session.cookies.set("adult_explicit", "1", domain=".livejournal.com", path="/")
                self._sync_session.cookies.set("adult_view", "1", domain=".livejournal.com", path="/")
                logger.debug("Pre-injected adult cookies for LiveJournal")

        return self._sync_session

    async def _execute_bypass(self, action: Dict[str, Any]) -> bool:
        """Universal utility to 'click' a gateway button, supporting GET/POST and Referer"""
        target_url = action.get('url')
        if not target_url: return False
        
        # Ensure absolute URL
        if not target_url.startswith(("http://", "https://")):
            target_url = urljoin(self.url, target_url)

        method = action.get('method', 'GET').upper()
        form_tag = action.get('form_tag')
        
        # Prepare headers (Crucial: Include Referer to satisfy security checks)
        headers = {"User-Agent": K.DEFAULT_USER_AGENT, "Referer": self.url}
        
        # Collect data if it's a form
        data = {}
        if form_tag:
            for inp in form_tag.find_all('input'):
                name = inp.get('name')
                val = inp.get('value', '')
                if name: data[name] = val
        
        logger.info(f"Executing bypass ({method}): {target_url} (Referer: {self.url})")
        
        try:
            loop = asyncio.get_event_loop()
            def _sync_bypass():
                session = self._get_sync_session()
                if method == 'POST':
                    resp = session.post(target_url, data=data, headers=headers, timeout=10, verify=False, allow_redirects=True)
                else:
                    resp = session.get(target_url, headers=headers, timeout=10, verify=False, allow_redirects=True)
                return resp.status_code < 400
            
            return await loop.run_in_executor(None, _sync_bypass)
        except Exception as e:
            logger.debug(f"Universal bypass execution failed: {e}")
            return False

    def _get_video_platform(self, url: str) -> Optional[str]:
        parsed_url = urlparse(url.lower()); domain = parsed_url.netloc; path = parsed_url.path
        video_extensions = [".mp4", ".webm", ".avi", ".mov", ".flv", ".mkv", ".wmv", ".ts"]
        if any(ext in path for ext in video_extensions): return "direct-video"
        for platform, patterns in self.VIDEO_PLATFORMS.items(): 
            if any(re.search(pattern, domain) for pattern in patterns): return platform
        return None

    def _get_best_image_url(self, element: Any) -> Tuple[Optional[str], Dict[str, Any]]:
        attributes = {}; candidates = []
        sources = {
            "src": element.get("src", ""), "data-src": element.get("data-src", ""),
            "data-original": element.get("data-original", ""), "data-lazy": element.get("data-lazy", ""),
            "data-lazy-src": element.get("data-lazy-src", ""), "data-original-src": element.get("data-original-src", ""),
            "data-hi-res-src": element.get("data-hi-res-src", ""), "data-high-res": element.get("data-high-res", ""),
            "data-hires": element.get("data-hires", ""), "data-retina": element.get("data-retina", ""),
            "data-full": element.get("data-full", ""), "data-fullsize": element.get("data-fullsize", ""),
            "data-fullsizeurl": element.get("data-fullsizeurl", ""), "data-max-res": element.get("data-max-res", ""),
            "data-maxres": element.get("data-maxres", ""),
        }
        for attr_name, url_val in sources.items():
            if url_val:
                priority = 100 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0
                candidates.append({"url": url_val, "width": priority, "source": attr_name})
        for srcset_attr_name in ["srcset", "data-srcset", "data-lazy-srcset"]:
            srcset_val = element.get(srcset_attr_name, "")
            if srcset_val: candidates.extend(self._parse_srcset(srcset_val))
        for attr_name, value in element.attrs.items():
            if isinstance(value, str) and re.search(r"\.(jpg|jpeg|png|webp|gif|avif|tiff|bmp)", value.lower()):
                priority = 999999 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0
                candidates.append({"url": value, "width": priority, "source": attr_name})

        width_str, height_str = element.get("width", ""), element.get("height", "")
        min_img_width = self.settings.get(K.SETTING_MIN_IMG_WIDTH, K.DEFAULT_MIN_IMAGE_WIDTH)
        min_img_height = self.settings.get(K.SETTING_MIN_IMG_HEIGHT, K.DEFAULT_MIN_IMAGE_HEIGHT)

        if width_str and height_str:
            try:
                width_val, height_val = int(width_str), int(height_str)
                attributes["dimensions"] = {"width": width_val, "height": height_val}
                high_quality_threshold = max(800, min_img_width * 2) 
                for c in candidates:
                    if c["width"] == 0 and (width_val > high_quality_threshold or height_val > high_quality_threshold):
                        c["width"] = max(width_val, height_val)
            except (ValueError, TypeError): pass
        
        attributes["alt"] = element.get("alt", ""); attributes["title"] = element.get("title", "")
        substantial_candidates = [c for c in candidates if (c["width"] >= min_img_width and c["width"] > 0) or ("dimensions" in attributes and attributes["dimensions"].get("height", 0) >= min_img_height) or c["width"] == 0]
        filtered_candidates = substantial_candidates if substantial_candidates else candidates
        filtered_candidates.sort(key=lambda x: x["width"], reverse=True)

        if filtered_candidates:
            best_url, best_attrs = filtered_candidates[0]["url"], attributes
            best_attrs["source"] = filtered_candidates[0]["source"]
            best_attrs["original_width"] = filtered_candidates[0]["width"]
            
            if self.pattern_manager and best_url:
                transformed_results = self.pattern_manager.transform_image_url(best_url, self.url)
                # transform_image_url now always returns a list of at least one item
                if transformed_results and (len(transformed_results) > 1 or transformed_results[0] != best_url):
                    best_attrs["original_url"] = best_url
                    best_attrs["transformed"] = True
                    return transformed_results, best_attrs
            
            return [best_url], best_attrs
        return [], attributes

    def _parse_srcset(self, srcset: str) -> List[Dict[str, Any]]:
        candidates = []
        for item in srcset.split(","):
            item = item.strip(); parts = item.split()
            if not parts: continue
            url, width = parts[0], 0
            if len(parts) > 1:
                desc = parts[1]
                if desc.endswith("w"):
                    try:
                        width = int(desc[:-1])
                    except ValueError:
                        pass
                elif desc.endswith("x"):
                    try:
                        density = float(desc[:-1])
                        width = int(density * 1000)
                    except ValueError:
                        pass 
            candidates.append({"url": url, "width": width, "source": "srcset"})
        return candidates

    def _extract_inline_css_images(self, element: Any) -> List[str]:
        images, style = [], element.get("style", "")
        if style:
            urls = re.findall(r'url\(["\']?([^)"\']+)["\']?\)', style)
            images.extend(u for u in urls if re.search(r"\.(jpg|jpeg|png|webp|gif|avif)", u.lower()))
        return images

    def _extract_picture_sources(self, picture_elem: Any) -> List[Dict[str, Any]]:
        sources = []
        for source_tag in picture_elem.find_all("source"):
            srcset = source_tag.get("srcset", "")
            if srcset:
                candidates = self._parse_srcset(srcset)
                media, type_ = source_tag.get("media", ""), source_tag.get("type", "")
                for c in candidates: c.update({"media": media, "type": type_}); sources.append(c)
        img_tag = picture_elem.find("img")
        if img_tag:
            url, attrs = self._get_best_image_url(img_tag)
            if url: sources.append({"url": url, "width": attrs.get("original_width", 0), "source": "img", "media": "", "type": ""})
        return sources

    async def _extract_images(self, soup: BeautifulSoup) -> None: 
        found = 0
        for picture in soup.find_all("picture"):
            for source_data in self._extract_picture_sources(picture):
                url = source_data.get("url")
                if not url: continue
                # Handle possible list of URLs from transformation
                urls_to_process = url if isinstance(url, list) else [url]
                for u in urls_to_process:
                    abs_url = urljoin(self.url, u)
                    if abs_url.startswith(("http://", "https://")):
                        attrs = {
                            "width": source_data.get("width"), 
                            "media": source_data.get("media"), 
                            "type": source_data.get("type"), 
                            "source": source_data.get("source"), 
                            "is_cdn": self._is_cdn_url(abs_url, "img")
                        }
                        if self._is_significant_media("image", abs_url, attrs):
                            self.media_files.append(("image", abs_url, attrs))
                            found += 1
        
        for img in soup.find_all("img"):
            urls, attrs = self._get_best_image_url(img)
            for url in urls:
                if not url: continue
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    # Create a copy of attrs for each variant to avoid shared state mutations
                    variant_attrs = attrs.copy()
                    variant_attrs["is_cdn"] = self._is_cdn_url(abs_url, "img")
                    if self._is_significant_media("image", abs_url, variant_attrs):
                        self.media_files.append(("image", abs_url, variant_attrs))
                        found += 1
                    parent_a = img.find_parent('a', href=True)
                    if parent_a and parent_a.get('href'):
                        link_url, link_abs_url = parent_a.get('href'), urljoin(self.url, parent_a.get('href'))
                        if link_abs_url.startswith(("http://", "https://")):
                            if is_image_url(link_abs_url):
                                link_attrs = attrs.copy(); link_attrs['source'] = 'parent-link'
                                if self._is_significant_media("image", link_abs_url, link_attrs):
                                    self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                            elif is_media_url(link_abs_url) or any(kw in link_abs_url for kw in ['full','large','original']): 
                                link_attrs = attrs.copy(); link_attrs['source'] = 'fullsize-link'
                                if self._is_significant_media("image", link_abs_url, link_attrs):
                                    self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                            else: 
                                self.links[link_abs_url] = {'from_image': True, 'thumbnail_url': abs_url, 'is_webpage': True, 'potential_media_container': True, 'priority': 15.0}
        
        for elem in soup.find_all(attrs={"style": True}):
            for url in self._extract_inline_css_images(elem):
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"source": "css", "element": elem.name, "is_cdn": self._is_cdn_url(abs_url, "img")}
                    self.media_files.append(("image", abs_url, attrs)); found += 1
        
        for link_tag in soup.find_all("link", rel=re.compile(r"icon|apple-touch-icon")):
            href = link_tag.get("href")
            if href:
                abs_url = urljoin(self.url, href)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"rel": link_tag.get("rel", []), "sizes": link_tag.get("sizes", ""), "type": link_tag.get("type", "")}
                    self.media_files.append(("image", abs_url, attrs)); found += 1
        
        for meta_tag in soup.find_all("meta", property=re.compile(r"og:image|twitter:image")):
            content = meta_tag.get("content")
            if content:
                abs_url = urljoin(self.url, content)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"property": meta_tag.get("property", ""), "source": "meta", "is_cdn": self._is_cdn_url(abs_url, "img")}
                    self.media_files.append(("image", abs_url, attrs)); found += 1
        logger.info(f"Found {found} images on {self.url}")


    async def _extract_videos(self, soup: BeautifulSoup) -> None: 
        found = 0
        for video_tag in soup.find_all("video"):
            sources = []
            if video_tag.get("src"): sources.append({"url": video_tag.get("src"), "type": video_tag.get("type", "")})
            for source_elem in video_tag.find_all("source"):
                if source_elem.get("src"): sources.append({"url": source_elem.get("src"), "type": source_elem.get("type", "")})
            
            for source_data in sources:
                url = source_data["url"]; abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"width": video_tag.get("width", ""), "height": video_tag.get("height", ""), "poster": video_tag.get("poster", ""), "type": source_data["type"], "is_cdn": self._is_cdn_url(abs_url, "video")}
                    self.media_files.append(("video", abs_url, attrs)); found += 1

        for iframe_tag in soup.find_all("iframe"):
            src = iframe_tag.get("src", "") or iframe_tag.get("data-src", "") 
            if src:
                abs_url = urljoin(self.url, src)
                if abs_url.startswith(("http://", "https://")):
                    platform = self._get_video_platform(abs_url)
                    if platform:
                        attrs = {"width": iframe_tag.get("width", ""), "height": iframe_tag.get("height", ""), "platform": platform, "type": "embed"}
                        self.media_files.append(("video", abs_url, attrs)); found += 1
        
        for meta_tag in soup.find_all("meta", property=re.compile(r"og:video|twitter:player")):
            content = meta_tag.get("content")
            if content:
                abs_url = urljoin(self.url, content)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"property": meta_tag.get("property", ""), "source": "meta", "platform": self._get_video_platform(abs_url)}
                    self.media_files.append(("video", abs_url, attrs)); found += 1
        logger.info(f"Found {found} videos on {self.url}")

    async def _extract_links(self, soup: BeautifulSoup) -> None: 
        found = 0
        filter_hidden = self.settings.get(K.SETTING_FILTER_HIDDEN_LINKS, K.DEFAULT_FILTER_HIDDEN_LINKS)
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")): continue
            
            # Bot-trap defense
            if filter_hidden and not self._is_element_visible(a_tag):
                continue
            abs_url = urljoin(self.url, href)
            if abs_url.startswith(("http://", "https://")):
                self.links[abs_url] = {'from_image': False, 'element': 'a', 'text': a_tag.get_text(strip=True, separator=" ")[:100]} 
                found += 1
        
        canonical_tag = soup.find("link", rel="canonical", href=True)
        if canonical_tag and canonical_tag.get("href"):
            href = canonical_tag["href"].strip(); abs_url = urljoin(self.url, href)
            if abs_url.startswith(("http://", "https://")):
                self.links[abs_url] = {'from_image': False, 'element': 'canonical', 'priority': 2.0}
                found += 1
        logger.info(f"Found {found} valid links on {self.url}")

    def _is_element_visible(self, element: Any) -> bool:
        """Heuristic to check if an element is hidden via CSS (honeypot/bot-trap)"""
        hidden_keywords = K.VISIBILITY_HIDDEN_KEYWORDS
        hidden_classes = K.VISIBILITY_HIDDEN_CLASSES
        
        # Check attributes
        if element.get("hidden") is not None or element.get("aria-hidden") == "true":
            return False
            
        tags_to_check = [element]
        if element.parent:
            tags_to_check.append(element.parent)
            
        for tag in tags_to_check:
            # Check classes
            classes = tag.get("class", [])
            if isinstance(classes, list):
                if any(c in hidden_classes for c in classes):
                    return False
            elif isinstance(classes, str): # sometimes class is a string
                if any(c in classes for c in hidden_classes):
                    return False
            
            # Check inline styles
            style = str(tag.get("style", "")).lower()
            if style and any(kw in style for kw in hidden_keywords):
                return False
                
        return True

    def _is_significant_media(self, media_type: str, url: str, attrs: Dict[str, Any]) -> bool:
        """Heuristic to filter out icons, avatars, and UI elements"""
        url_lower = url.lower()
        
        # 1. Filter by extension
        if url_lower.endswith(('.svg', '.ico', '.cur')):
            return False
            
        # 2. Filter by common noise patterns in URL
        ignore_patterns = K.SIGNIFICANT_MEDIA_IGNORE_PATTERNS
        if any(p in url_lower for p in ignore_patterns):
            return False
            
        # 3. Filter by explicit dimensions if present in HTML
        try:
            width = int(attrs.get("width", 1000))
            height = int(attrs.get("height", 1000))
            if width < K.SIGNIFICANT_MEDIA_MIN_DIMENSION or height < K.SIGNIFICANT_MEDIA_MIN_DIMENSION:
                return False
        except (ValueError, TypeError):
            pass
            
        return True

    async def _handle_gateways(self, soup) -> Optional[Dict[str, Any]]:
        """Detect and return structured gateway bypass action (link or form)"""
        # Detection trigger logic:
        # A page is suspicious if it has < 5 images AND contains gateway keywords or overlays
        
        text_content = soup.get_text().lower()
        overlay_keywords = [
            "confirm your age", "18 years old", "adult content", "войти", "подтвердите", "18 лет",
            "proceed", "leave", "enter", "over 18", "agree", "confirm", "i agree"
        ]
        is_suspicious = len(self.media_files) < 5 or any(kw in text_content for kw in overlay_keywords)
        
        if not is_suspicious:
            return None
            
        logger.debug(f"Potential gateway detected on: {self.url} (Media count: {len(self.media_files)})")
            
        patterns = [p.lower() for p in K.GATEWAY_TEXT_PATTERNS]
        keyword_patterns = ["agree", "confirm", "enter", "over18", "accept", "continue", "verify", "18"]
        blacklist_patterns = ["legal", "terms", "tos", "policy", "agreement", "rules", "copyright", "privacy", "help", "about"]
        
        # Search for buttons or links
        candidates = []
        for tag in soup.find_all(['a', 'button', 'input']):
            if tag.name == 'input' and tag.get('type') != 'submit':
                continue
                
            text = tag.get_text(separator=" ", strip=True).lower()
            if not text and tag.get('value'):
                text = str(tag.get('value')).lower()
                
            tag_id = str(tag.get('id', '')).lower()
            tag_classes = " ".join(tag.get('class', [])).lower() if isinstance(tag.get('class'), list) else str(tag.get('class', '')).lower()
            
            # Check 1: Text content match
            text_match = any(p in text for p in patterns)
            
            # Check 2: ID or Class keyword match
            attr_match = any(kw in tag_id or kw in tag_classes for kw in keyword_patterns)
            
            if text_match or attr_match:
                href = None
                method = "GET"
                form_tag = None
                
                if tag.name == 'a' and tag.get('href'):
                    href = tag.get('href')
                else:
                    # Check for form parent
                    parent_form = tag.find_parent('form')
                    if parent_form:
                        href = parent_form.get('action') or self.url
                        method = parent_form.get('method', 'GET').upper()
                        form_tag = parent_form
                
                # Check for JS onClick if still no href
                if not href and tag.get('onclick'):
                    onclick = tag.get('onclick')
                    url_match = re.search(r"['\"](?P<url>/[^'\"]+|https?://[^'\"]+)['\"]", onclick)
                    if url_match: href = url_match.group("url")

                if href:
                    href_lower = href.lower()
                    # Check against blacklist (ignore TOS/Legal pages)
                    is_blacklisted = any(bp in text or bp in href_lower for bp in blacklist_patterns)
                    
                    if is_blacklisted:
                        continue

                    score = 0
                    if any(p == text for p in patterns if len(p) > 5): score += 100
                    elif text_match: score += 20
                    if attr_match: score += 10
                    
                    candidates.append({
                        "score": score,
                        "url": href,
                        "method": method,
                        "form_tag": form_tag,
                        "text": text[:30]
                    })

        if candidates:
            # Sort by score descending and return the best one
            candidates.sort(key=lambda x: x["score"], reverse=True)
            best = candidates[0]
            logger.info(f"SUCCESS: Selecting gateway {best['method']} action (Score {best['score']}): {best['url']}")
            return best
            
        return None

    async def parse(self) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str, Dict[str, Any]]], Optional[str], str, Optional[int], Optional[Dict[str, str]]]:
        """
        Parse webpage and extract media files and links.
        Returns: (links, media_files, error_status, error_message, http_status_code, cookies)
        """
        if not hasattr(self, '_bypass_attempts'):
            self._bypass_attempts = 0 

        content, error_status, error_message, http_status_code = await self._get_content()

        if error_status: 
            return {}, [], error_status, error_message, http_status_code, None
        
        if not content: 
            return {}, [], K.PARSER_UNKNOWN_ERROR, "No content fetched and no specific error reported.", http_status_code, None

        try:
            soup = BeautifulSoup(content, "lxml")
            
            # 1. Image extraction
            await self._extract_images(soup) 
            
            # 2. Video extraction
            await self._extract_videos(soup)

            # 3. Gateway handling (Dynamic Bypass)
            # Prevent infinite loops: only 3 attempts per URL
            if self._bypass_attempts < 3:
                gateway_action = await self._handle_gateways(soup)
                if gateway_action:
                    self._bypass_attempts += 1
                    logger.info(f"Gateway Detected. Bypassing attempt {self._bypass_attempts} via: {gateway_action['url']}")
                    
                    success = await self._execute_bypass(gateway_action)
                    if success:
                        logger.info(f"Cookies updated. RE-FETCHING original content: {self.url}")
                        # Reset discovered content before re-fetching
                        self.media_files.clear()
                        self.links.clear()
                        # Re-parse the same URL with updated session cookies
                        return await self.parse()
            elif self._bypass_attempts >= 3:
                logger.warning(f"Maximum bypass attempts reached for {self.url}. Proceeding with current content.")

            # 4. Link extraction
            await self._extract_links(soup)
            
            if self.process_js: 
                await self._handle_dynamic_content(soup)

            # 5. Extract cookies for the downloader (if any were set during bypass/fallback)
            cookies = None
            if self._sync_session:
                cookies = self._sync_session.cookies.get_dict()

            return self.links, self.media_files, K.PARSER_SUCCESS, "Successfully parsed.", http_status_code, cookies
        except Exception as e:
            msg = f"Error during parsing HTML content of {self.url}: {str(e)}"
            logger.error(msg, exc_info=False)
            return self.links, self.media_files, K.PARSER_UNKNOWN_ERROR, msg, http_status_code, None


    async def _handle_dynamic_content(self, soup: BeautifulSoup) -> None:
        # The check `if not self.process_js: return` is no longer strictly needed here
        # because the call to this method is already gated by self.process_js.
        # However, keeping it doesn't harm and adds an extra layer of safety if called from elsewhere.
        try:
            if not self.process_js: return 
            for script_tag in soup.find_all("script"):
                if script_tag.string: self._extract_media_from_js(script_tag.string)
            for elem in soup.find_all(True): 
                for framework, pattern in self.JS_PATTERNS["framework_patterns"].items():
                    if re.search(pattern, str(elem)): self._process_framework_element(elem, framework)
                for attr_name in elem.attrs:
                    if attr_name.startswith("data-"): self._process_data_attribute(elem, attr_name)
            for data_attr_pattern in self.LAZY_LOAD_PATTERNS["data-attributes"]:
                for elem in soup.find_all(attrs={data_attr_pattern: True}):
                    url_val = elem.get(data_attr_pattern)
                    if url_val and is_media_url(url_val): 
                        abs_url = urljoin(self.url, url_val)
                        attrs = {"source": f"lazy-data-{data_attr_pattern}"}
                        media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
                        if self._is_significant_media(media_type, abs_url, attrs):
                            self.media_files.append((media_type, abs_url, attrs))
        except Exception as e:
            logger.error(f"Error in static JS/dynamic content analysis for {self.url}: {str(e)}", exc_info=True)

    def _extract_media_from_js(self, js_content: str) -> None:
        for pattern_type, patterns in self.JS_PATTERNS.items():
            if pattern_type in ["image_sources", "video_sources", "data_attributes"]:
                media_hint = "image" if "image" in pattern_type else "video" if "video" in pattern_type else "image" 
                for pattern in patterns:
                    for match in re.finditer(pattern, js_content):
                        url = match.group(1) 
                        if url and url.startswith(("http://", "https://", "/")) and is_media_url(url):
                            abs_url = urljoin(self.url, url)
                            attrs = {"source": f"js-static-{pattern_type}"}
                            if self._is_significant_media(media_hint, abs_url, attrs):
                                self.media_files.append((media_hint, abs_url, attrs))

    def _process_framework_element(self, elem: Any, framework: str) -> None:
        attrs = {"source": f"framework-{framework}"}
        src_val = None
        if framework == "react": src_val = elem.get("data-src") or elem.get("data-lazy")
        elif framework == "vue": src_val = elem.get("v-lazy")
        elif framework == "angular": src_val = elem.get("lazyLoad") or elem.get("ng-src")
        if src_val and is_media_url(src_val):
            abs_url = urljoin(self.url, src_val)
            media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
            if self._is_significant_media(media_type, abs_url, attrs):
                self.media_files.append((media_type, abs_url, attrs))

    def _process_data_attribute(self, elem: Any, attr_name: str) -> None:
        value = elem.get(attr_name, "").strip()
        if not value: return
        if value.startswith("{") and value.endswith("}"): 
            try:
                data = json.loads(value)
                if isinstance(data, dict):
                    for k, v_val in data.items():
                        if isinstance(v_val, str) and is_media_url(v_val):
                            abs_url = urljoin(self.url, v_val)
                            attrs = {"source": f"data-json-{attr_name}-{k}"}
                            media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
                            if self._is_significant_media(media_type, abs_url, attrs):
                                self.media_files.append((media_type, abs_url, attrs))
            except json.JSONDecodeError: pass 
        elif is_media_url(value): 
            abs_url = urljoin(self.url, value)
            attrs = {"source": f"data-direct-{attr_name}"}
            media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
            if self._is_significant_media(media_type, abs_url, attrs):
                self.media_files.append((media_type, abs_url, attrs))

    def get_media_files(self) -> List[Tuple[str, str, Dict[str, Any]]]: return self.media_files
