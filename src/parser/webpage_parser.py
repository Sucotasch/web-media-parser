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

from src.parser.utils import is_image_url, is_media_url, is_valid_url, get_domain, is_same_domain
from src.parser.site_pattern_manager import SitePatternManager
from src import constants as K 

import aiohttp 
import requests 
import chardet
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
        
        self.sync_session = self._create_sync_session() 
        
        if external_session is None:
            logger.debug("WebpageParser initialized without external_session. HTTP fetching will be disabled.")
        self.session = external_session 
        
        self.pattern_manager = pattern_manager
        self.links: Dict[str, Dict[str, Any]] = {} 
        self.media_files: List[Tuple[str, str, Dict[str, Any]]] = []
        self._mime_type: Optional[str] = None
        self.js_redirect_count = 0 

    def get_discovered_urls(self) -> Dict[str, Dict[str, Any]]:
        return self.links

    def _create_sync_session(self) -> requests.Session: 
        session = requests.Session()
        retry_strategy = Retry(
            total=self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT),
            backoff_factor=0.5, 
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter); session.mount("https://", adapter)
        headers = {
            "User-Agent": self.settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT),
            "Accept-Language": self.settings.get(K.SETTING_ACCEPT_LANGUAGE, K.DEFAULT_ACCEPT_LANGUAGE),
            "Accept": K.DEFAULT_ACCEPT_HEADER, 
            "Accept-Encoding": "gzip, deflate, br" if HAS_BROTLI else "gzip, deflate", 
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1", "DNT": "1",
        }
        referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
        if referrer_policy == "origin": headers["Referer"] = get_domain(self.url)
        elif referrer_policy == "auto": headers["Referer"] = self.url
        session.headers.update(headers)
        return session

    async def _get_content(self) -> Tuple[Optional[str], Optional[str], str, Optional[int]]:
        """
        Get webpage content.
        Returns: (content_string, error_status, error_message, http_status_code)
        """
        if self.js_redirect_count > K.MAX_JS_REDIRECTS:
            msg = f"Exceeded maximum JS redirects ({K.MAX_JS_REDIRECTS}) for URL: {self.url}"
            logger.error(msg)
            return None, K.PARSER_JS_REDIRECT_MAX_EXCEEDED, msg, None

        try:
            request_specific_headers = {} 
            referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
            if referrer_policy == "origin": request_specific_headers["Referer"] = get_domain(self.url)
            elif referrer_policy == "auto": request_specific_headers["Referer"] = self.url
            
            cookies = {}
            if self.settings.get(K.SETTING_BYPASS_COOKIE_CONSENT, K.DEFAULT_BYPASS_COOKIE_CONSENT):
                consent_cookies = { 
                    'cookieconsent_status': 'dismiss', 'gdpr_accepted': 'true', 
                    'cookies_accepted': 'true', 'euconsent': 'true', 'CookieConsent': 'true',
                    'cc_cookie_accept': '1', 'cookie_consent': 'true', 'privacy_policy_accepted': 'true'
                }
                cookies.update(consent_cookies)
            
            page_timeout_val = self.settings.get(K.SETTING_PAGE_TIMEOUT, K.DEFAULT_PAGE_TIMEOUT)
            request_timeout_config = aiohttp.ClientTimeout(total=page_timeout_val)

            async with self.session.get(self.url, headers=request_specific_headers, cookies=cookies, timeout=request_timeout_config) as response:
                http_status = response.status
                if 400 <= http_status < 500:
                    msg = f"Client HTTP error {http_status} for {self.url}"
                    logger.error(msg)
                    return None, K.PARSER_HTTP_ERROR_4XX, msg, http_status
                elif 500 <= http_status < 600:
                    msg = f"Server HTTP error {http_status} for {self.url}"
                    logger.error(msg)
                    return None, K.PARSER_HTTP_ERROR_5XX, msg, http_status
                
                if http_status != 200: 
                    msg = f"Non-200 HTTP status {http_status} for {self.url}"
                    logger.warning(msg) 
                
                content_bytes = await response.read()
                
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

        except asyncio.TimeoutError as e:
            msg = f"Timeout error fetching {self.url}: {str(e)}"
            logger.error(msg)
            return None, K.PARSER_TIMEOUT_ERROR, msg, None
        except aiohttp.ClientResponseError as e: 
            msg = f"HTTP ClientResponseError for {self.url}: Status {e.status}, Message: {e.message}"
            logger.error(msg)
            err_status = K.PARSER_HTTP_ERROR_4XX if 400 <= e.status < 500 else K.PARSER_HTTP_ERROR_5XX if 500 <= e.status < 600 else K.PARSER_NETWORK_ERROR
            return None, err_status, msg, e.status
        except aiohttp.ClientError as e: 
            msg = f"Network (aiohttp.ClientError) fetching {self.url}: {str(e)}"
            logger.error(msg)
            return None, K.PARSER_NETWORK_ERROR, msg, None
        except Exception as e:
            msg = f"Generic error fetching content for {self.url}: {str(e)}"
            logger.error(msg, exc_info=True)
            return None, K.PARSER_UNKNOWN_ERROR, msg, None

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
                priority = 100 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0
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
                transformed_url = self.pattern_manager.transform_image_url(best_url, self.url)
                if transformed_url != best_url:
                    best_attrs["original_url"] = best_url; best_attrs["transformed"] = True
                    best_url = transformed_url
            return best_url, best_attrs
        return None, attributes

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
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    # Heuristic: Skip obvious tracking pixels and trackers
                    if any(kw in abs_url.lower() for kw in ['pixel', 'tracker', 'telemetry', 'metrics', 'doubleclick', 'analytics']):
                        continue
                        
                    if hasattr(self, 'pattern_manager') and self.pattern_manager:
                        abs_url = self.pattern_manager.transform_image_url(abs_url, self.url)
                        
                    attrs = {"width": source_data.get("width"), "media": source_data.get("media"), "type": source_data.get("type"), "source": source_data.get("source"), "is_cdn": self._is_cdn_url(abs_url, "img")}
                    self.media_files.append(("image", abs_url, attrs)); found += 1
        
        for img in soup.find_all("img"):
            url, attrs = self._get_best_image_url(img)
            if url:
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    # Heuristic: Skip obvious tracking pixels and trackers
                    if any(kw in abs_url.lower() for kw in ['pixel', 'tracker', 'telemetry', 'metrics', 'doubleclick', 'analytics']):
                        continue
                        
                    if hasattr(self, 'pattern_manager') and self.pattern_manager:
                        transformed = self.pattern_manager.transform_image_url(abs_url, self.url)
                        if transformed != abs_url:
                            attrs['is_pattern_transformed'] = True
                            abs_url = transformed
                            
                    attrs["is_cdn"] = self._is_cdn_url(abs_url, "img")
                    self.media_files.append(("image", abs_url, attrs)); found += 1
                    
                    parent_a = img.find_parent('a', href=True)
                    if parent_a and parent_a.get('href'):
                        link_url = parent_a.get('href')
                        link_abs_url = urljoin(self.url, link_url)
                        
                        if hasattr(self, 'pattern_manager') and self.pattern_manager:
                            link_abs_url = self.pattern_manager.transform_image_url(link_abs_url, self.url)
                            
                        if link_abs_url.startswith(("http://", "https://")):
                            if is_image_url(link_abs_url):
                                link_attrs = attrs.copy(); link_attrs['source'] = 'parent-link'
                                self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                                if link_abs_url in self.links: del self.links[link_abs_url]
                            elif is_media_url(link_abs_url) or any(kw in link_abs_url for kw in ['full','large','original']): 
                                link_attrs = attrs.copy(); link_attrs['source'] = 'fullsize-link'
                                self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                                if link_abs_url in self.links: del self.links[link_abs_url]
                            else: 
                                # Universal Container Rule (Image/Video)
                                media_type_hint = "image"
                                link_url_lower = link_abs_url.lower()
                                img_class = img.get('class', '')
                                img_class_str = " ".join(img_class).lower() if isinstance(img_class, list) else str(img_class).lower()
                                img_alt = str(img.get('alt', '')).lower()
                                
                                video_keywords = ['watch', 'video', 'play', 'embed', 'clip', 'reel']
                                is_video = any(kw in link_url_lower for kw in video_keywords) or \
                                           any(kw in img_class_str for kw in ['video', 'thumb', 'poster', 'preview']) or \
                                           any(kw in img_alt for kw in ['video', 'play'])
                                           
                                if is_video:
                                    media_type_hint = "video"
                                
                                self.links[link_abs_url] = {
                                    'from_image': True, 
                                    'thumbnail_url': abs_url, 
                                    'is_webpage': True, 
                                    'is_media_container': True,
                                    'media_type_hint': media_type_hint,
                                    'priority': 100.0
                                }
        for elem in soup.find_all(attrs={"style": True}):
            for url in self._extract_inline_css_images(elem):
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"source": "css", "element": elem.name, "is_cdn": self._is_cdn_url(abs_url, "img")}
                    self.media_files.append(("image", abs_url, attrs)); found += 1
                    
                    # Track links hiding behind CSS background images (modern SPA thumbnails)
                    parent_a = elem if elem.name == 'a' and elem.has_attr('href') else elem.find_parent('a', href=True)
                    if parent_a and parent_a.get('href'):
                        link_url, link_abs_url = parent_a.get('href'), urljoin(self.url, parent_a.get('href'))
                        if link_abs_url.startswith(("http://", "https://")):
                            if is_image_url(link_abs_url):
                                link_attrs = attrs.copy(); link_attrs['source'] = 'parent-link'
                                self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                            elif is_media_url(link_abs_url) or any(kw in link_abs_url for kw in ['full','large','original']): 
                                link_attrs = attrs.copy(); link_attrs['source'] = 'fullsize-link'
                                self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                            else: 
                                # Universal Container Rule for CSS backgrounds
                                media_type_hint = "image"
                                link_url_lower = link_abs_url.lower()
                                video_keywords = ['watch', 'video', 'play', 'embed', 'clip', 'reel']
                                if any(kw in link_url_lower for kw in video_keywords):
                                    media_type_hint = "video"
                                    
                                self.links[link_abs_url] = {
                                    'from_image': True, 
                                    'thumbnail_url': abs_url, 
                                    'is_webpage': True, 
                                    'is_media_container': True,
                                    'media_type_hint': media_type_hint,
                                    'priority': 100.0
                                }
        
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
                    else:
                        # Generic iframe - treat as media container
                        self.links[abs_url] = {
                            'from_iframe': True,
                            'is_webpage': True,
                            'is_media_container': True,
                            'media_type_hint': 'video', # Most iframes are for video players
                            'priority': 80.0
                        }
        
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
        for a_tag in soup.find_all("a", href=True):
            # --- V7: Visibility Filter (Honeypot Evasion) ---
            style = a_tag.get("style", "").lower()
            if "display: none" in style or "display:none" in style or "visibility: hidden" in style or "visibility:hidden" in style:
                continue
            parent_style = a_tag.parent.get("style", "").lower() if a_tag.parent else ""
            if "display: none" in parent_style or "display:none" in parent_style or "visibility: hidden" in parent_style or "visibility:hidden" in parent_style:
                continue
                
            href = a_tag["href"].strip()
            if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")): continue
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

    async def parse(self) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str, Dict[str, Any]]], Optional[str], str, Optional[int]]:
        """
        Parse webpage and extract media files and links.
        Returns: (links, media_files, error_status, error_message, http_status_code)
        """
        self.js_redirect_count = 0 
        content, error_status, error_message, http_status_code = await self._get_content()

        if error_status: 
            return {}, [], error_status, error_message, http_status_code
        
        if not content: 
            return {}, [], K.PARSER_UNKNOWN_ERROR, "No content fetched and no specific error reported.", http_status_code

        try:
            soup = BeautifulSoup(content, "html.parser")
            await self._extract_images(soup) 
            await self._extract_videos(soup)
            await self._extract_links(soup)
            if self.process_js: # Changed from self.process_dynamic
                await self._handle_dynamic_content(soup)
            
            return self.links, self.media_files, K.PARSER_SUCCESS, "Successfully parsed.", http_status_code
        except Exception as e:
            msg = f"Error during parsing HTML content of {self.url}: {str(e)}"
            logger.error(msg, exc_info=True)
            return self.links, self.media_files, K.PARSER_UNKNOWN_ERROR, msg, http_status_code


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
                            self.media_files.append((media_type, abs_url, attrs))
            except json.JSONDecodeError: pass 
        elif is_media_url(value): 
            abs_url = urljoin(self.url, value)
            attrs = {"source": f"data-direct-{attr_name}"}
            media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
            self.media_files.append((media_type, abs_url, attrs))

    def get_media_files(self) -> List[Tuple[str, str, Dict[str, Any]]]: return self.media_files
