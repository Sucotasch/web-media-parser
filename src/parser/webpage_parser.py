#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Webpage parser class for extracting media files and links from webpages
"""

import re
import json
import asyncio
import logging
import yarl
from typing import List, Tuple, Dict, Any, Optional
from urllib.parse import urlparse, urljoin 

# DL-6: aiohttp announces/decodes 'br' itself when the brotli module is
# importable — no patching here (force-announcing 'br' without a decoder
# breaks every such page fetch).

from src.parser.utils import (
    is_image_url, is_media_url, get_domain,
    normalize_url, is_trash_media, format_proxy_url,
    is_banner_or_ad, is_format_allowed, is_same_domain
)
from src.parser.site_pattern_manager import SitePatternManager
from src import constants as K 

import aiohttp 
import chardet
from bs4 import BeautifulSoup

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
        # CORE-14: the data-srcset regex was removed — its group(1) captured the
        # whole srcset string (multi-URL srcsets became one garbage URL that
        # is_media_url rejects); single-URL data-srcset is already covered by the
        # lazy-data-* scan below.
        "data_attributes": [r'data-(?:src|original|lazy|load|image|video|poster|bg|background|url)\s*=\s*["\'](https?://[^"\']+)["\']',],
        "framework_patterns": {"react": r'className\s*=\s*["\'](lazy-load|image-loader)["\']', "vue": r'v-lazy\s*=\s*["\'](https?://[^"\']+)["\']', "angular": r'\[lazyLoad\]\s*=\s*["\'](https?://[^"\']+)["\']',}
    }

    def __init__(
        self, url: str, settings: Dict[str, Any],
        process_js: bool, # This will now control all advanced content extraction
        external_session: aiohttp.ClientSession, 
        pattern_manager: Optional[SitePatternManager] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        self.url = url
        self.settings = settings 
        self.process_js = process_js 
        # self.process_dynamic = process_dynamic # Removed, covered by process_js
        self.domain = get_domain(url)
        self.context = context or {}
        
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
        self._js_gateway_tried = False  # P4: one DOM-click attempt per page, ever
        self._last_http_status: Optional[int] = None  # For P3 escalation error reporting
        self._escalated_binary: bool = False  # Set when escalation returned binary media
        self._last_html: Optional[str] = None  # P4: raw HTML for the DOM gateway click
        self._js_gateway_html: Optional[str] = None  # P4: mutated DOM to parse instead of refetch

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
        escalation_tried = False  # P3: one curl_cffi attempt on explicit block, ever
        
        # 1. Very fast aiohttp attempt (or two)
        for attempt in range(max_retries + 1):
            try:
                request_specific_headers = {}
                referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
                
                if referrer_policy != "none" and self.js_redirect_count == 0:
                    # A-3: `_source_url` was never written anywhere — read the
                    # crawl context's source_url too, else Referer for policy
                    # 'auto' stayed dead code and photo hosts 403'd.
                    source_url = self.settings.get("_source_url") or (self.context or {}).get("source_url")
                    if source_url and source_url != self.url:
                        request_specific_headers["Referer"] = source_url
                    elif referrer_policy == "origin":
                        parsed = urlparse(self.url)
                        if parsed.scheme and parsed.netloc:
                            request_specific_headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}"
                        else:
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
                # P4: per-domain consent cookies learned from a prior successful
                # bypass on this site (ParserManager._consent_cookies) — override
                # the generic defaults with what the site actually accepted.
                learned = (self.context or {}).get("consent_cookies")
                if learned:
                    cookies.update(learned)
                # P4: cookies already set on the sync session during an earlier
                # bypass of THIS page (URL-bypass or JS-bypass). aiohttp's
                # cookie_jar.update_cookies() is unreliable (it does not retain
                # cookies for IP hosts and domain-less cookies), so we pass them
                # explicitly — the requests stack and the aiohttp cookie= param
                # both carry them reliably.
                if self._sync_session is not None:
                    try:
                        cookies.update(self._sync_session.cookies.get_dict())
                    except Exception:
                        pass
                
                page_timeout_val = self.settings.get(K.SETTING_PAGE_TIMEOUT, K.DEFAULT_PAGE_TIMEOUT)
                # VERY Aggressive connect timeout (5 secs max), so we don't hang queues
                request_timeout_config = aiohttp.ClientTimeout(
                    total=page_timeout_val, 
                    connect=min(5, page_timeout_val // 2),
                    # CORE-18: clamp so hand-edited settings with page_timeout<=2
                    # cannot produce sock_read<=0 (instant timeout on everything).
                    sock_read=max(1, page_timeout_val - 2)
                )

                if attempt > 0:
                    logger.info(f"Retrying fetch of {self.url} via aiohttp (Attempt {attempt+1}/{max_retries+1})...")
                    await asyncio.sleep(0.5 * attempt) # Fast backoff

                proxy_url = format_proxy_url(self.settings.get(K.SETTING_PROXY))
                async with self.session.get(self.url, headers=request_specific_headers, cookies=cookies, timeout=request_timeout_config, proxy=proxy_url) as response:
                    http_status = response.status
                    if http_status == 429:
                        # Rate limited — backoff and retry.
                        # Retry-After may be seconds or an HTTP-date (RFC 7231).
                        raw_retry_after = response.headers.get("Retry-After", "5")
                        try:
                            retry_after = max(0, int(raw_retry_after))
                        except ValueError:
                            try:
                                from email.utils import parsedate_to_datetime
                                from datetime import datetime, timezone
                                retry_after = max(0, int((parsedate_to_datetime(raw_retry_after) - datetime.now(timezone.utc)).total_seconds()))
                            except Exception:
                                retry_after = 5
                        # CORE-3: cap Retry-After to the page timeout — a
                        # 'Retry-After: 86400' must not park the worker (and the
                        # whole per-domain semaphore) for a day; the only way out
                        # was a manual Stop.
                        retry_after = min(retry_after, max(1, page_timeout_val))
                        logger.warning(f"HTTP 429 Rate limited for {self.url}, retry after {retry_after}s")
                        if attempt < max_retries:
                            await asyncio.sleep(retry_after)
                            continue
                        msg = f"Rate limited (429) after {max_retries+1} attempts for {self.url}"
                        logger.error(msg)
                        return None, K.PARSER_HTTP_ERROR_4XX, msg, http_status
                    elif http_status == 403:
                        # P3 auto-escalation: 403 is the canonical "explicit block"
                        # signal — try ONE curl_cffi browser-TLS fetch before
                        # giving up. Bounded (single attempt, no retry loop) so it
                        # is invisible to the domain-health/quarantine counters: a
                        # successful escalation never reaches the failure counter;
                        # a failed one returns the same 4xx error as today.
                        msg = f"Client HTTP error {http_status} for {self.url}"
                        block_status = http_status
                        from src.parser import http_engine as _he
                        if _he.should_escalate(self.settings, http_status):
                            logger.info(f"HTTP 403 (block?) for {self.url} — escalating to curl_cffi...")
                            content_bytes, http_status, is_binary = await self._try_escalate_fetch()
                            escalation_tried = True
                            if content_bytes is not None:
                                self._escalated_binary = is_binary
                                break
                        logger.error(msg)
                        return None, K.PARSER_HTTP_ERROR_4XX, msg, block_status
                    elif 400 <= http_status < 500:
                        msg = f"Client HTTP error {http_status} for {self.url}"
                        logger.error(msg)
                        return None, K.PARSER_HTTP_ERROR_4XX, msg, http_status
                    elif 500 <= http_status < 600:
                        msg = f"Server HTTP error {http_status} for {self.url}"
                        logger.error(msg)
                        if attempt < max_retries: continue
                        # P3 auto-escalation on 5xx (after retries exhausted):
                        # bot-protection often answers 5xx to non-browser TLS.
                        from src.parser import http_engine as _he2
                        if not escalation_tried and _he2.should_escalate(self.settings, http_status):
                            server_status = http_status
                            logger.info(f"HTTP {server_status} for {self.url} after retries — escalating to curl_cffi...")
                            content_bytes, http_status, is_binary = await self._try_escalate_fetch()
                            escalation_tried = True
                            if content_bytes is not None:
                                self._escalated_binary = is_binary
                                break
                            return None, K.PARSER_HTTP_ERROR_5XX, msg, server_status
                        return None, K.PARSER_HTTP_ERROR_5XX, msg, http_status
                    
                    # Binary media guard: if a "webpage" actually answers with an
                    # image/video/audio payload (photo hosts 302 image URLs to a
                    # CDN), do NOT read megabytes + run lxml/JS analysis on it —
                    # that hangs the parser (observed: imx.to JPEG shell) and
                    # blocks the domain semaphore. Report a clean empty parse.
                    resp_ct = (response.headers.get("Content-Type") or "").lower()
                    if resp_ct.startswith(("image/", "video/", "audio/")):
                        logger.debug(f"Binary media content ({resp_ct}) for {self.url} — skipping HTML parse")
                        return "", K.PARSER_SUCCESS, "Binary media content, not HTML", http_status

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
        if not content_bytes and not escalation_tried:
            try:
                logger.info(f"Using sync fallback (requests) for {self.url}")
                loop = asyncio.get_event_loop()
                
                # Capture headers to pass into the synchronous call.
                # UA is only set for the plain requests path — an impersonated
                # curl_cffi session already carries the browser UA from its
                # profile, and overriding it would break the TLS fingerprint.
                fb_headers = {}
                from src.parser import http_engine
                if not http_engine.engine_uses_curl(self.settings):
                    fb_headers["User-Agent"] = self.settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT)
                if request_specific_headers.get("Referer"):
                    fb_headers["Referer"] = request_specific_headers["Referer"]
                
                def _sync_fetch():
                    session = self._get_sync_session()
                    # Aggressive timeout for sync to prevent blocking thread pool
                    fb_timeout = 10 
                    resp = session.get(self.url, headers=fb_headers, timeout=fb_timeout, allow_redirects=True, verify=http_engine.tls_verify(self.settings))
                    return resp

                resp = await loop.run_in_executor(None, _sync_fetch)
                http_status = resp.status_code
                if 400 <= http_status < 600:
                    return None, K.PARSER_HTTP_ERROR_4XX if http_status < 500 else K.PARSER_HTTP_ERROR_5XX, f"HTTP {http_status} via fallback", http_status
                content_bytes = resp.content
            except Exception as fb_err:
                logger.error(f"Fallback failed for {self.url}: {fb_err}")
                return None, K.PARSER_NETWORK_ERROR, f"Fallback failed: {str(fb_err)}", http_status

        if getattr(self, "_escalated_binary", False) and content_bytes == b"":
            # Escalation returned binary media — clean empty parse, mirroring
            # the aiohttp binary-media guard above. (An empty HTML/body is NOT
            # binary: _try_escalate_fetch only sets the flag on image/video/
            # audio Content-Type.)
            return "", K.PARSER_SUCCESS, "Binary media content, not HTML", http_status
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
                abs_redirect_url = normalize_url(urljoin(self.url, redirect_url))
                self.url = abs_redirect_url 
                return await self._get_content() 
        
        return decoded_content, None, "Success", http_status 

    async def _try_escalate_fetch(self) -> Tuple[Optional[bytes], Optional[int], bool]:
        """P3 auto-escalation: fetch self.url ONCE through a curl_cffi browser-TLS
        session when the regular stack was explicitly blocked (HTTP 403/5xx).

        Returns (content_bytes, http_status, is_binary) on success (is_binary
        True for image/video/audio payloads), (None, http_status, False) on
        failure. Bounded by construction — exactly one attempt, no retry loop —
        so the caller's domain-health/quarantine accounting is unchanged (a
        failed escalation returns the same error the caller would have produced
        without it). Returns (None, status, False) immediately when curl_cffi is
        unavailable or http_escalate is disabled.
        """
        if not self.settings.get(K.SETTING_HTTP_ESCALATE, K.DEFAULT_HTTP_ESCALATE):
            return None, self._last_http_status, False
        from src.parser import http_engine
        session = http_engine.create_escalation_session(self.settings)
        if session is None:
            return None, self._last_http_status, False
        try:
            loop = asyncio.get_running_loop()
            headers = {}
            # A-3: same fallback as _get_content — context is the only real
            # source of the source page URL.
            source_url = self.settings.get("_source_url") or (self.context or {}).get("source_url")
            if source_url and source_url != self.url:
                headers["Referer"] = source_url

            def _esc_fetch():
                # Aggressive timeout: escalation is a bonus, never a hang.
                resp = session.get(
                    self.url, headers=headers, timeout=10,
                    allow_redirects=True, verify=http_engine.tls_verify(self.settings),
                )
                return resp

            resp = await loop.run_in_executor(None, _esc_fetch)
            status = resp.status_code
            self._last_http_status = status
            if status >= 400:
                logger.debug(f"Escalation fetch returned HTTP {status} for {self.url}")
                return None, status, False
            content_type = (resp.headers.get("Content-Type") or "").lower()
            body = resp.content
            if content_type.startswith(("image/", "video/", "audio/")):
                logger.debug(f"Escalation got binary media ({content_type}) for {self.url}")
                return b"", status, True
            if body:
                logger.info(f"Escalation succeeded for {self.url} (HTTP {status}, {len(body)} bytes)")
            return body, status, False
        except Exception as e:
            logger.debug(f"Escalation fetch failed for {self.url}: {e}")
            return None, self._last_http_status, False
        finally:
            try:
                session.close()
            except Exception:
                pass

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
        """Lazy-loader for a persistent sync session to maintain cookies during bypass

        P3: honors http_engine. With curl_cffi the session impersonates a real
        browser TLS fingerprint (impersonate=chrome). Because curl_cffi sets
        browser-consistent headers (UA/Sec-CH-UA/Accept) from the profile, the
        manual header block below is only applied to the plain requests path;
        overriding User-Agent on an impersonated session would defeat the JA3
        fingerprint.
        """
        if self._sync_session is None:
            from src.parser import http_engine
            from src.parser.utils import format_proxy_url
            
            if http_engine.engine_uses_curl(self.settings):
                self._sync_session = http_engine.create_sync_session(self.settings)
            else:
                import requests
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                
                self._sync_session = requests.Session()
                # Disable internal retries to allow immediate termination via UI Stop button 
                adapter = HTTPAdapter(max_retries=Retry(total=0, connect=None, read=None, redirect=None, status=None))
                self._sync_session.mount("http://", adapter)
                self._sync_session.mount("https://", adapter)
                # Pre-set standard headers (match aiohttp session for consistent fingerprint)
                self._sync_session.headers.update({
                    "User-Agent": self.settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT),
                    "Accept-Language": self.settings.get(K.SETTING_ACCEPT_LANGUAGE, K.DEFAULT_ACCEPT_LANGUAGE),
                    "Accept-Encoding": "gzip, deflate, br",
                    "Accept": K.DEFAULT_ACCEPT_HEADER,
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                    "DNT": "1",
                })
            
            # Apply proxy if configured (both engines support session.proxies)
            proxy_url = format_proxy_url(self.settings.get(K.SETTING_PROXY))
            if proxy_url:
                self._sync_session.proxies = {"http": proxy_url, "https": proxy_url}
                logger.debug(f"Sync session configured with proxy: {proxy_url}")
            
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
        
        # Prepare headers (Crucial: Include Referer to satisfy security checks).
        # UA only for the plain requests path — an impersonated curl_cffi
        # session already carries its browser UA from the profile.
        from src.parser import http_engine
        headers = {"Referer": self.url}
        if not http_engine.engine_uses_curl(self.settings):
            headers["User-Agent"] = K.DEFAULT_USER_AGENT
        
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
                    resp = session.post(target_url, data=data, headers=headers, timeout=10, verify=http_engine.tls_verify(self.settings), allow_redirects=True)
                else:
                    resp = session.get(target_url, headers=headers, timeout=10, verify=http_engine.tls_verify(self.settings), allow_redirects=True)
                return resp.status_code < 400
            
            return await loop.run_in_executor(None, _sync_bypass)
        except Exception as e:
            logger.debug(f"Universal bypass execution failed: {e}")
            return False

    # --- P4: JS-only consent/gateway buttons (no href / no form) -------------

    @staticmethod
    def _extract_consent_cookies_from_js(handler_text: str) -> Tuple[Dict[str, str], bool]:
        """P4 Level 1: statically extract consent cookies from a JS event handler.

        Handles the common patterns found on consent/age-gate buttons:
          - direct document.cookie assignments (first name=value pair only)
          - helper calls: setCookie('n','v'[,...]), createCookie(...)
          - bare function calls (acceptCookies(), agreeToTerms()) are NOT
            resolved here (their body lives in a <script> block — see
            _find_inline_function_body) but their *name* is returned via the
            callers' function-resolution pass.
        Returns (cookies, needs_reload). needs_reload is True when the handler
        calls location.reload()/location.href= — the page must be re-fetched
        after the cookies are applied.
        """
        cookies: Dict[str, str] = {}
        needs_reload = False
        if not handler_text:
            return cookies, needs_reload
        text = handler_text

        # Direct cookie writes: document.cookie = 'name=value[; ...attrs]'
        # The quoted value may itself contain a quote (document.cookie="oops='x'") —
        # use a backreference so the outer pair boundary is the SAME quote char.
        for m in re.finditer(r"document\.cookie\s*=\s*(['\"])(.*?)\1", text, re.IGNORECASE):
            pair = m.group(2).split(";", 1)[0].strip()
            if "=" in pair:
                name, _, value = pair.partition("=")
                name = name.strip()
                # Skip pairs whose value contains a quote — consent cookie
                # values are simple tokens ("oops='x'" is not a usable pair).
                if name and "'" not in value and '"' not in value:
                    cookies[name] = value.strip()

        # Helper-call cookies: setCookie('n','v'[,...]) / createCookie(...)
        for m in re.finditer(
            r"(?:setCookie|createCookie|set_cookie)\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]",
            text, re.IGNORECASE,
        ):
            name = m.group(1).strip()
            if name:
                cookies[name] = m.group(2).strip()

        # location.reload() / location.href = '...' → page must be re-fetched
        if re.search(r"location\s*\.\s*(?:reload|href)\s*(?:\(|=)", text, re.IGNORECASE) or \
           re.search(r"window\s*\.\s*location", text, re.IGNORECASE):
            needs_reload = True

        return cookies, needs_reload

    def _find_inline_function_body(self, soup, fn_name: str) -> Optional[str]:
        """P4 Level 1: locate a function body in inline <script> blocks.

        Matches `function fnName(...) {...}`, `const fnName = (...) => {...}`,
        `var fnName = function(...) {...}` and returns the raw body text.
        Bounded to 3 script tags and balanced-brace scanning to avoid runaway.
        """
        if not fn_name:
            return None
        patterns = [
            re.compile(r"function\s+" + re.escape(fn_name) + r"\s*\([^)]*\)\s*\{"),
            re.compile(r"(?:const|let|var)\s+" + re.escape(fn_name) + r"\s*=\s*[\w$]*\s*\([^)]*\)\s*=>\s*\{"),
            re.compile(r"(?:const|let|var)\s+" + re.escape(fn_name) + r"\s*=\s*function\s*\([^)]*\)\s*\{"),
        ]
        for script in soup.find_all("script", limit=3):
            src = script.string or ""
            for pat in patterns:
                m = pat.search(src)
                if not m:
                    continue
                # Find the opening brace, then scan balanced braces to the close.
                brace_start = src.find("{", m.start())
                if brace_start == -1:
                    continue
                depth = 0
                for i in range(brace_start, len(src)):
                    ch = src[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return src[brace_start + 1:i]
        return None

    async def _execute_js_bypass(self, action: Dict[str, Any]) -> bool:
        """P4: apply cookies extracted from a JS-only gateway button.

        Level 1: cookies were already extracted statically during gateway
        detection (action['cookies']). We apply them to the sync session so
        the existing re-parse loop picks them up. Returns True when at least
        one cookie was applied (or the handler requested a reload), False
        otherwise (nothing to do).
        """
        # Cookies were already resolved (static extraction + inline function
        # bodies) by _handle_gateways when it built this action. Apply them to
        # the sync session so the re-parse loop picks them up.
        cookies = action.get("cookies") or {}
        if not cookies:
            return False

        session = self._get_sync_session()
        for name, value in cookies.items():
            try:
                session.cookies.set(name, value)
                logger.info(f"P4 JS consent cookie set: {name}={value}")
            except Exception as e:
                logger.debug(f"P4 cookie set failed for {name}: {e}")
        return True

    async def _try_js_gateway_click(self, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """P4 Level 2: click the consent button in a real DOM (Deno worker).

        Returns the worker result dict {cookies, html_after, redirect,
        reload_requested} or None (fail-open: no engine, unavailable, error,
        or the page already had a DOM click). Bounded: one attempt per page
        (self._js_gateway_tried) so it can never loop.
        """
        if self._js_gateway_tried:
            return None
        self._js_gateway_tried = True
        if self.pattern_manager is None or self.pattern_manager.js_engine is None:
            return None
        engine = self.pattern_manager.js_engine
        if not engine.dom_available():
            logger.debug("P4: DOM gateway click skipped — Deno DOM worker unavailable")
            return None
        html = self._last_html or ""
        if not html:
            return None
        logger.info(f"P4: attempting DOM gateway click for {self.url} via Deno worker")
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: engine.run_gateway_click(
                    html=html,
                    page_url=self.url,
                    text_patterns=[p for p in K.GATEWAY_TEXT_PATTERNS],
                    overlay_selectors=list(K.GATEWAY_OVERLAY_SELECTORS) + list(K.GATEWAY_GENERIC_OVERLAY_SELECTORS),
                ),
            )
            if result and (result.get("cookies") or result.get("html_after")):
                logger.info(f"P4: DOM gateway click returned {len(result.get('cookies') or {})} cookies, html_after={len(result.get('html_after') or '')} chars")
            return result
        except Exception as e:
            logger.debug(f"P4: DOM gateway click failed: {e}")
            return None

    # CORE-7: direct-video extension matched as a COMPLETE path extension (end
    # of path or followed by / ? #), never as a substring — ".ts" must not
    # classify "/user.tsuji/page" as a direct video.
    _DIRECT_VIDEO_RE = re.compile(r"\.(?:mp4|webm|avi|mov|flv|mkv|wmv|ts)(?:$|[/?#])", re.I)

    def _get_video_platform(self, url: str) -> Optional[str]:
        parsed_url = urlparse(url.lower()); domain = parsed_url.netloc; path = parsed_url.path
        if self._DIRECT_VIDEO_RE.search(path): return "direct-video"
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
                # CORE-17: priority tier goes into `score`, real pixels into
                # `width`. Mixing them (width=100 for named hi-res attrs) made
                # those attrs drop out of substantial_candidates once the user
                # set min_image_width > 100 — the explicit fullsize attribute
                # silently lost to the plain thumbnail src.
                score = 100 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0
                candidates.append({"url": url_val, "width": 0, "score": score, "source": attr_name})
        for srcset_attr_name in ["srcset", "data-srcset", "data-lazy-srcset"]:
            srcset_val = element.get(srcset_attr_name, "")
            if srcset_val: candidates.extend(self._parse_srcset(srcset_val))
        for attr_name, value in element.attrs.items():
            if isinstance(value, str) and re.search(r"\.(jpg|jpeg|png|webp|gif|avif|tiff|bmp)", value.lower()):
                score = 999999 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0
                candidates.append({"url": value, "width": 0, "score": score, "source": attr_name})

        width_str, height_str = element.get("width", ""), element.get("height", "")
        min_img_width = self.settings.get(K.SETTING_MIN_IMG_WIDTH, K.DEFAULT_MIN_IMAGE_WIDTH)
        min_img_height = self.settings.get(K.SETTING_MIN_IMG_HEIGHT, K.DEFAULT_MIN_IMAGE_HEIGHT)

        if width_str and height_str:
            try:
                # Strip a trailing unit ("78px" -> 78); sites commonly emit
                # width/height with a px suffix, and int("78px") would raise,
                # silently dropping the dimensions attribute so UI icons
                # (e.g. /img/messenger-cam.png at 78px) bypass the
                # min-dimension filter in _is_significant_media.
                # Only absolute px (and pt) are meaningful as a dimension;
                # relative units (%/em) raise -> dimension filter stays
                # fail-open for them, exactly as before.
                def _parse_dim(raw):
                    raw = str(raw).strip().lower()
                    raw = re.sub(r"(?:px|pt)$", "", raw).strip()
                    return int(raw)
                width_val, height_val = _parse_dim(width_str), _parse_dim(height_str)
                attributes["dimensions"] = {"width": width_val, "height": height_val}
                high_quality_threshold = max(800, min_img_width * 2) 
                for c in candidates:
                    if c["width"] == 0 and (width_val > high_quality_threshold or height_val > high_quality_threshold):
                        c["width"] = max(width_val, height_val)
            except (ValueError, TypeError): pass
        
        attributes["alt"] = element.get("alt", ""); attributes["title"] = element.get("title", "")
        # CORE-17: with named attrs now width=0 (score carries the tier), the
        # substantial gate keeps them via the width==0 fail-open — no more
        # drop-out at min_image_width > 100. Sort prefers the explicit
        # hi-res tier first, then real pixel width.
        substantial_candidates = [c for c in candidates if (c["width"] >= min_img_width and c["width"] > 0) or ("dimensions" in attributes and attributes["dimensions"].get("height", 0) >= min_img_height) or c["width"] == 0]
        filtered_candidates = substantial_candidates if substantial_candidates else candidates
        filtered_candidates.sort(key=lambda x: (x["score"], x["width"]), reverse=True)

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
            # CORE-17: srcset entries carry their real pixel width; the
            # priority tier (score) is 0 — real pixels are their signal.
            candidates.append({"url": url, "width": width, "score": 0, "source": "srcset"})
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
            # 1. Primary Visibility Check (Bot-Trap Defense)
            if not self._is_element_visible(img):
                logger.debug(f"Skipping hidden img element (bot-trap defense) in {self.url}")
                continue

            urls, attrs = self._get_best_image_url(img)
            for url in urls:
                if not url: continue
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    # Create a copy of attrs for each variant to avoid shared state mutations
                    variant_attrs = attrs.copy()
                    variant_attrs["is_cdn"] = self._is_cdn_url(abs_url, "img")
                    # Soft thumbnail hint — marks attrs for priority (deprioritize), never hard-filter
                    if any(h in abs_url.lower() for h in K.THUMBNAIL_URL_HINTS):
                        variant_attrs["likely_thumbnail"] = True
                    
                    # 2. Extract media only if significant (not trash)
                    significant = self._is_significant_media("image", abs_url, variant_attrs)
                    is_interstitial_retry = self.context.get("interstitial_retry", False)

                    if is_interstitial_retry and not significant:
                        # Loosen rules for interstitial recovery, but still filter trash
                        if is_format_allowed(abs_url, "image", self.settings) and not any(p in abs_url.lower() for p in K.SIGNIFICANT_MEDIA_IGNORE_PATTERNS):
                             logger.debug(f"Loosening significance rules for interstitial recovery: {abs_url}")
                             significant = True

                    has_parent_webpage_link = False
                    if significant and is_format_allowed(abs_url, "image", self.settings):
                        # Check if this thumbnail has a parent <a> link to a webpage
                        # If so, skip the thumbnail — the linked page will be crawled for fullsize
                        is_interstitial_retry = self.context.get("interstitial_retry", False)
                        parent_a = img.find_parent('a', href=True)
                        has_parent_webpage_link = False
                        if parent_a and parent_a.get('href') and self._is_element_visible(parent_a):
                            link_url = parent_a.get('href')
                            link_abs_url = urljoin(self.url, link_url)
                            if link_abs_url.startswith(("http://", "https://")) and link_abs_url != abs_url:
                                if not is_image_url(link_abs_url) and not is_trash_media(link_abs_url):
                                    # Only defer to the linked page when it is on the SAME domain.
                                    # With stay_in_domain, an external parent link (e.g.
                                    # imx.to/i/... hosting thumbnails on image.imx.to) would be
                                    # dropped by the out-of-domain filter later — the thumbnail
                                    # would be lost entirely. In that case keep the thumbnail.
                                    if is_same_domain(link_abs_url, self.url):
                                        has_parent_webpage_link = True
                                        # Add the linked page for crawling
                                        if not is_interstitial_retry or link_abs_url != self.url:
                                            self.links[link_abs_url] = {'from_image': True, 'thumbnail_url': abs_url, 'is_webpage': True, 'priority': 15.0}

                        if not has_parent_webpage_link:
                            self.media_files.append(("image", abs_url, variant_attrs))
                            found += 1
                        elif variant_attrs.get("transformed"):
                            # Sieve transformed thumb → fullsize; keep it even with parent-link
                            self.media_files.append(("image", abs_url, variant_attrs))
                            found += 1
                    
                    # Follow parent link for fullsize discovery
                    parent_a = img.find_parent('a', href=True)
                    if parent_a and parent_a.get('href') and self._is_element_visible(parent_a):
                        link_url, link_abs_url = parent_a.get('href'), urljoin(self.url, parent_a.get('href'))
                        if link_abs_url.startswith(("http://", "https://")):
                            is_interstitial_retry = self.context.get("interstitial_retry", False)
                            
                            if is_interstitial_retry and link_abs_url == self.url:
                                continue

                            if is_image_url(link_abs_url):
                                if is_format_allowed(link_abs_url, "image", self.settings):
                                    link_attrs = attrs.copy(); link_attrs['source'] = 'parent-link'
                                    if self._is_significant_media("image", link_abs_url, link_attrs):
                                        self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                                # CORE-19: disabled-format direct media is DROPPED,
                                # not queued as a from_image crawl link — queueing
                                # made the crawler fetch image bytes as a
                                # "webpage" on every such URL (media-lookup
                                # bypasses stay-in-domain/depth).
                            elif is_media_url(link_abs_url) or any(kw in link_abs_url for kw in ['full','large','original']): 
                                if is_format_allowed(link_abs_url, "image", self.settings):
                                    link_attrs = attrs.copy(); link_attrs['source'] = 'fullsize-link'
                                    if self._is_significant_media("image", link_abs_url, link_attrs):
                                        self.media_files.append(("image", link_abs_url, link_attrs)); found += 1
                                # CORE-19: same drop as above for disabled formats.
                            elif not has_parent_webpage_link: 
                                self.links[link_abs_url] = {'from_image': True, 'thumbnail_url': abs_url, 'is_webpage': True, 'potential_media_container': True, 'priority': 15.0}
        
        for elem in soup.find_all(attrs={"style": True}):
            for url in self._extract_inline_css_images(elem):
                abs_url = urljoin(self.url, url)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"source": "css", "element": elem.name, "is_cdn": self._is_cdn_url(abs_url, "img")}
                    if self._is_significant_media("image", abs_url, attrs):
                        self.media_files.append(("image", abs_url, attrs)); found += 1
        
        for link_tag in soup.find_all("link", rel=re.compile(r"icon|apple-touch-icon")):
            href = link_tag.get("href")
            if href:
                abs_url = urljoin(self.url, href)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"rel": link_tag.get("rel", []), "sizes": link_tag.get("sizes", ""), "type": link_tag.get("type", "")}
                    # Icons are never page content. Two independent gates:
                    # (1) numeric sizes — drop the 57..152 family that vBulletin
                    # emits as a dozen separate <link> tags (observed: 12 favicon
                    # failures per page in the download queue); (2) full
                    # significance filter — drops "apple-touch-icon"/"icon"-
                    # named URLs even at 180x180+. A large icon whose URL is not
                    # icon-named (e.g. /logo.png) still has to pass both.
                    sizes = str(attrs.get("sizes", "")).strip().lower()
                    max_dim = 0
                    for dim in re.findall(r"(\d+)x(\d+)", sizes):
                        try:
                            max_dim = max(max_dim, int(dim[0]), int(dim[1]))
                        except ValueError:
                            pass
                    is_small_icon = max_dim < K.APPLE_TOUCH_ICON_MIN_DIM if max_dim else True
                    if not is_small_icon and self._is_significant_media("image", abs_url, attrs):
                        self.media_files.append(("image", abs_url, attrs)); found += 1
        
        for meta_tag in soup.find_all("meta", property=re.compile(r"og:image|twitter:image")):
            content = meta_tag.get("content")
            if content:
                abs_url = urljoin(self.url, content)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"property": meta_tag.get("property", ""), "source": "meta", "is_cdn": self._is_cdn_url(abs_url, "img")}
                    if self._is_significant_media("image", abs_url, attrs):
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
                    # WP-2.3: direct video files go through the full significance filter
                    # (blocks tiny/ad players); embeds are handled separately below to
                    # avoid the image-oriented SIGNIFICANT_MEDIA_IGNORE_PATTERNS
                    # (e.g. "youtube") dropping legit platform embeds.
                    if self._is_significant_media("video", abs_url, attrs):
                        self.media_files.append(("video", abs_url, attrs)); found += 1

        for iframe_tag in soup.find_all("iframe"):
            src = iframe_tag.get("src", "") or iframe_tag.get("data-src", "") 
            if src:
                abs_url = urljoin(self.url, src)
                if abs_url.startswith(("http://", "https://")):
                    platform = self._get_video_platform(abs_url)
                    if platform:
                        attrs = {"width": iframe_tag.get("width", ""), "height": iframe_tag.get("height", ""), "platform": platform, "type": "embed"}
                        # Embeds: format + ad/tracker URL check only — full significance
                        # would drop youtube/vimeo embeds via SIGNIFICANT_MEDIA_IGNORE_PATTERNS.
                        if is_format_allowed(abs_url, "video", self.settings) and not is_banner_or_ad(abs_url, attrs):
                            self.media_files.append(("video", abs_url, attrs)); found += 1
        
        for meta_tag in soup.find_all("meta", property=re.compile(r"og:video|twitter:player")):
            content = meta_tag.get("content")
            if content:
                abs_url = urljoin(self.url, content)
                if abs_url.startswith(("http://", "https://")):
                    attrs = {"property": meta_tag.get("property", ""), "source": "meta", "platform": self._get_video_platform(abs_url)}
                    if is_format_allowed(abs_url, "video", self.settings) and not is_banner_or_ad(abs_url, attrs):
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
                text = a_tag.get_text(strip=True, separator=" ")[:100]
                existing = self.links.get(abs_url)
                if existing is not None:
                    # Preserve from_image: True set by image extraction
                    if existing.get("from_image") or existing.get("priority", 0) >= 10:
                        if text and not existing.get("text"):
                            existing["text"] = text
                        continue
                self.links[abs_url] = {'from_image': False, 'element': 'a', 'text': text}
                found += 1
        
        canonical_tag = soup.find("link", rel="canonical", href=True)
        if canonical_tag and canonical_tag.get("href"):
            href = canonical_tag["href"].strip(); abs_url = urljoin(self.url, href)
            if abs_url.startswith(("http://", "https://")):
                self.links[abs_url] = {'from_image': False, 'element': 'canonical', 'priority': 2.0}
                found += 1
        logger.info(f"Found {found} valid links on {self.url}")

    def _extract_jsonld_media(self, soup: BeautifulSoup) -> None:
        """Extract media URLs from JSON-LD structured data (schema.org)."""
        found = 0
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            stack = data if isinstance(data, list) else [data]
            for obj in stack:
                if not isinstance(obj, dict):
                    continue
                obj_type = obj.get("@type", "")
                # Collect URLs from common schema.org types
                urls = []
                if "VideoObject" in str(obj_type):
                    for key in ("contentUrl", "embedUrl", "url"):
                        u = obj.get(key)
                        if isinstance(u, str) and u.startswith("http"):
                            urls.append(("video", u))
                if "ImageObject" in str(obj_type) or "image" in obj:
                    # CORE-9: accept str/dict/list (incl. ImageObject.image as a
                    # list, which was previously lost entirely).
                    for u in self._jsonld_image_urls(obj.get("image") or obj.get("url")):
                        urls.append(("image", u))
                # Article / Gallery / Post with image
                if "image" in obj and "ImageObject" not in str(obj_type):
                    for u in self._jsonld_image_urls(obj.get("image")):
                        urls.append(("image", u))
                seen = set()
                for media_type, u in urls:
                    if u in seen:
                        continue
                    seen.add(u)
                    if not is_media_url(u):
                        continue
                    # CORE-9: JSON-LD media passes the same significance gate as
                    # every other extraction path (format allowlist, ad/junk,
                    # icon/logo URL noise) — before, VideoObject previews and
                    # small Article thumbnails went straight to the queue.
                    if self._is_significant_media(media_type, u, {"source": "json-ld"}):
                        self.media_files.append((media_type, u, {"source": "json-ld"}))
                        found += 1
        if found:
            logger.info(f"Found {found} media URLs from JSON-LD on {self.url}")

    def _jsonld_image_urls(self, img: Any, limit: int = 5) -> List[str]:
        """Recursively collect image URLs from a JSON-LD image field.

        Accepts a plain URL string, a dict with url/contentUrl, or a list of
        either (CORE-9: the ImageObject.image list form was previously lost).
        """
        out: List[str] = []
        def walk(node: Any) -> None:
            if len(out) >= limit:
                return
            if isinstance(node, str):
                if node.startswith("http"):
                    out.append(node)
            elif isinstance(node, dict):
                u = node.get("url") or node.get("contentUrl")
                if isinstance(u, str) and u.startswith("http"):
                    out.append(u)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(img)
        return out

    def _select_one_safe(self, soup, selector: str):
        """soup.select_one() that never raises on malformed CSS selectors."""
        try:
            return soup.select_one(selector)
        except Exception:
            return None

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
                class_tokens = classes.split()
                if any(c in hidden_classes for c in class_tokens):
                    return False
            
            # Check inline styles
            style = str(tag.get("style", "")).lower()
            if style and any(kw in style for kw in hidden_keywords):
                return False
                
        return True

    def _is_significant_media(self, media_type: str, url: str, attrs: Dict[str, Any]) -> bool:
        """Heuristic to filter out icons, avatars, and UI elements"""
        # 1. Filter by extension (centralized, settings-driven format allowlist)
        if not is_format_allowed(url, media_type, self.settings):
            return False
            
        url_lower = url.lower()
            
        # 2. Filter by banner/ad/tracker patterns
        if is_banner_or_ad(url, attrs):
            return False
            
        # 3. Filter by common noise patterns in URL. These are IMAGE-oriented
        # (icons/social/trackers); direct video file paths must not be dropped
        # by them (e.g. "load-movie.mp4" contains "ad-"). Video noise is
        # handled by is_banner_or_ad + dimensions; embeds are filtered in
        # _extract_videos separately.
        if media_type != "video":
            ignore_patterns = K.SIGNIFICANT_MEDIA_IGNORE_PATTERNS
            if any(p in url_lower for p in ignore_patterns):
                return False
            
        # 4. Filter by explicit dimensions if present in HTML.
        # _get_best_image_url stores parsed width/height under attrs["dimensions"]
        # (a dict), while the <picture> path passes a raw width attribute at the
        # top level. Support both so the min-dimension filter stays alive for
        # every caller (mirrors is_banner_or_ad).
        dims = attrs.get("dimensions") if isinstance(attrs.get("dimensions"), dict) else {}
        width = dims.get("width", attrs.get("width", 0))
        height = dims.get("height", attrs.get("height", 0))
        if width or height:
            try:
                if 0 < int(width) < K.SIGNIFICANT_MEDIA_MIN_DIMENSION or 0 < int(height) < K.SIGNIFICANT_MEDIA_MIN_DIMENSION:
                    return False
            except (ValueError, TypeError):
                pass
            
        return True

    async def _handle_gateways(self, soup) -> Optional[Dict[str, Any]]:
        """Detect and return structured gateway bypass action (link or form)"""
        # Detection trigger logic:
        # A page is suspicious if it has < 5 images AND contains gateway keywords or overlays
        
        text_content = soup.get_text().lower()
        # F6: an age phrase is only a gate signal on MEDIA-POOR pages and must
        # not come from footer/legal boilerplate. Ordinary content pages carry
        # 18+ record-keeping notices ("18 years", "adult content" — e.g. the
        # §2257 disclaimer) in their <footer>; treating that as a gateway made
        # every viewer page trip the bypass loop (observed: 457 false
        # "Potential gateway detected" -> 3 bypass attempts and 4x re-parses
        # per page). Real age gates are stubs: they hide the content
        # (media-poor by definition) and usually use an explicit overlay
        # (.age-gate etc.), which still triggers directly below.
        media_poor = len(self.media_files) < K.GATEWAY_MIN_MEDIA_THRESHOLD
        has_age_phrase = False
        if media_poor:
            # Age-phrase scan text with footer/legal boilerplate stripped (the
            # §2257 "18 years" notice sits in <footer>/disclaimer elements on
            # ordinary pages). Only computed for media-poor pages — the clause
            # it feeds requires media_poor anyway, so content pages skip the
            # extra tree passes entirely.
            age_scan_text = text_content
            for el in soup.find_all(["footer", "aside"]) + \
                     soup.find_all(class_=re.compile(r"disclaimer|2257|record-?keep|legal", re.IGNORECASE)) + \
                     soup.find_all(id=re.compile(r"disclaimer|2257|record-?keep|legal", re.IGNORECASE)):
                # get_text() without a separator matches the way text_content
                # was built above (soup.get_text() concatenates with no
                # separator), so the replace actually removes it. All
                # occurrences are removed — duplicated boilerplate (e.g.
                # mobile + desktop disclaimer variants) must not leave a copy
                # in the scan text.
                el_text = el.get_text().lower()
                if el_text:
                    age_scan_text = age_scan_text.replace(el_text, " ")
            has_age_phrase = any(kw in age_scan_text for kw in (
                "confirm your age", "18 years", "over 18", "adult content",
                "мне есть 18", "старше 18", "вход только",
            ))

        # WP-5.3 gateway suspicion: require an unambiguous overlay element, an
        # age phrase on a media-poor page, or consent text combined with a
        # generic modal/footer selector / media absence. Generic modal/footer
        # selectors (.modal-content, #disclaimer) appear on many ordinary
        # sites, so they only count when consent text is present. Bare consent
        # words alone trigger only when media is absent.
        has_overlay = any(self._select_one_safe(soup, sel) for sel in K.GATEWAY_OVERLAY_SELECTORS)
        has_generic_overlay = any(
            self._select_one_safe(soup, sel) for sel in K.GATEWAY_GENERIC_OVERLAY_SELECTORS
        )
        consent_phrase = any(kw in text_content for kw in (
            "i agree", "cookie", "согласен", "accept", "agree"
        ))
        is_suspicious = (
            has_overlay
            or (media_poor and has_age_phrase)
            or (has_generic_overlay and consent_phrase)
            or (media_poor and consent_phrase)
        )

        if not is_suspicious:
            return None
            
        logger.debug(f"Potential gateway detected on: {self.url} (Media count: {len(self.media_files)})")
            
        patterns = [p.lower() for p in K.GATEWAY_TEXT_PATTERNS]
        keyword_patterns = ["agree", "confirm", "enter", "over18", "accept", "continue", "verify", "18"]
        blacklist_patterns = ["legal", "terms", "tos", "policy", "agreement", "rules", "copyright", "privacy", "help", "about"]
        avoid_keywords = [k.lower() for k in K.GATEWAY_AVOID_KEYWORDS]
        
        # P4: when an unambiguous gateway overlay is present, scope candidate
        # search to it — a login modal / cookie footer on the same page must
        # never be clicked as a "consent" action.
        overlay_root = soup
        for sel in K.GATEWAY_OVERLAY_SELECTORS:
            found = self._select_one_safe(soup, sel)
            if found is not None:
                overlay_root = found
                break
        
        # Search for buttons or links
        candidates = []
        js_candidates = []
        
        # P4: include JS-only elements (div/span/... with onclick/onmousedown)
        # in addition to classic a/button/input. De-dupe by element identity.
        seen = set()
        classic = overlay_root.find_all(['a', 'button', 'input'])
        js_elems = overlay_root.find_all(attrs={"onclick": True}) + \
                   overlay_root.find_all(attrs={"onmousedown": True}) + \
                   overlay_root.find_all(attrs={"onkeypress": True})
        for tag in classic + js_elems:
            if id(tag) in seen:
                continue
            seen.add(id(tag))
            if tag.name == 'input' and tag.get('type') not in (None, 'submit', 'button'):
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
                # P4: never treat account/technical sections as a consent action
                if any(kw in text for kw in avoid_keywords):
                    continue
                # P4: skip forms that collect credentials — never auto-submit them
                parent_form = tag.find_parent('form')
                if parent_form is not None and parent_form.find("input", {"type": "password"}):
                    continue

                href = None
                method = "GET"
                form_tag = None
                
                if tag.name == 'a' and tag.get('href'):
                    href = tag.get('href')
                else:
                    # Check for form parent
                    if parent_form:
                        href = parent_form.get('action') or self.url
                        method = parent_form.get('method', 'GET').upper()
                        form_tag = parent_form
                
                # Check for JS onClick if still no href
                js_handler = (tag.get('onclick') or tag.get('onmousedown') or tag.get('onkeypress')) or ""
                if not href and js_handler:
                    url_match = re.search(r"['\"](?P<url>/[^'\"]+|https?://[^'\"]+)['\"]", js_handler)
                    if url_match: href = normalize_url(url_match.group("url"))

                if href:
                    href_lower = href.lower()
                    # Check against blacklist (ignore TOS/Legal pages)
                    is_blacklisted = any(bp in text or bp in href_lower for bp in blacklist_patterns)
                    # P4: also never navigate to login/sign-up/account sections
                    if any(kw in href_lower for kw in avoid_keywords):
                        is_blacklisted = True
                    
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
                elif js_handler and not form_tag:
                    # P4: pure-JS button — no URL, no form. Only act when the
                    # text is a strong consent match (avoids random JS buttons).
                    # Strong = exact match of a long pattern, or a short
                    # button label that CONTAINS a consent pattern ("I am
                    # 18+" ⊇ "i am 18"), or a distinctive consent word.
                    strong_text = bool(text) and (
                        any(p == text for p in patterns if len(p) > 5)
                        or (len(text) <= 40 and text_match)
                        or text in ("agree", "accept", "confirm", "yes", "enter", "ok",
                                    "согласен", "принимаю", "подтверждаю", "да")
                    )
                    if strong_text or attr_match:
                        cookies, needs_reload = self._extract_consent_cookies_from_js(js_handler)
                        # Resolve named function bodies from inline scripts
                        if not cookies:
                            for fn in re.findall(r"([A-Za-z_$][\w$]*)\s*\(", js_handler):
                                if fn.lower() in ("if", "for", "while", "switch", "function", "return"):
                                    continue
                                body = self._find_inline_function_body(soup, fn)
                                if body:
                                    sub_cookies, sub_reload = self._extract_consent_cookies_from_js(body)
                                    cookies.update(sub_cookies)
                                    needs_reload = needs_reload or sub_reload
                        score = 100 if strong_text else 20
                        js_candidates.append({
                            "score": score + (10 if attr_match else 0),
                            "kind": "js",
                            "js_handler": js_handler,
                            "cookies": cookies,
                            "needs_reload": needs_reload,
                            "text": text[:30]
                        })

        if candidates:
            # Sort by score descending and return the best one
            candidates.sort(key=lambda x: x["score"], reverse=True)
            best = candidates[0]
            logger.info(f"SUCCESS: Selecting gateway {best['method']} action (Score {best['score']}): {best['url']}")
            return best
            
        if js_candidates:
            js_candidates.sort(key=lambda x: x["score"], reverse=True)
            best = js_candidates[0]
            if best.get("cookies"):
                logger.info(f"P4: Selecting JS consent action (Score {best['score']}): cookies={list(best['cookies'])} reload={best['needs_reload']}")
            else:
                logger.info(f"P4: Selecting JS consent action (Score {best['score']}): no static cookies — will try DOM click")
            return best
            
        return None

    async def parse(self) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str, Dict[str, Any]]], Optional[str], str, Optional[int], Optional[Dict[str, str]]]:
        """
        Parse webpage and extract media files and links.
        Returns: (links, media_files, error_status, error_message, http_status_code, cookies)
        """
        # P4: a previous DOM click may have produced a mutated document — parse
        # it directly instead of re-fetching the same gateway page.
        if self._js_gateway_html:
            content = self._js_gateway_html
            self._js_gateway_html = None
            error_status, error_message, http_status_code = None, "Success", self._last_http_status
            logger.info(f"P4: parsing post-click mutated DOM for {self.url} (no re-fetch)")
        else:
            content, error_status, error_message, http_status_code = await self._get_content()
            self._last_html = content if content else None

        if error_status: 
            return {}, [], error_status, error_message, http_status_code, None
        
        if not content: 
            return {}, [], K.PARSER_UNKNOWN_ERROR, "No content fetched and no specific error reported.", http_status_code, None

        try:
            loop = asyncio.get_running_loop()
            soup = await loop.run_in_executor(None, BeautifulSoup, content, "lxml")
            
            # 1. Image extraction
            await self._extract_images(soup) 
            
            # 2. Video extraction
            await self._extract_videos(soup)

            # 2b. JSON-LD structured data
            self._extract_jsonld_media(soup)

            # 3. Gateway handling (Dynamic Bypass)
            # Prevent infinite loops: only 3 attempts per URL
            if self._bypass_attempts < 3:
                gateway_action = await self._handle_gateways(soup)
                if gateway_action:
                    self._bypass_attempts += 1
                    success = False
                    if gateway_action.get("kind") == "js":
                        # P4: JS-only consent button (no href / no form)
                        if gateway_action.get("cookies"):
                            logger.info(f"Gateway Detected. JS consent bypass attempt {self._bypass_attempts} (static cookies)")
                            success = await self._execute_js_bypass(gateway_action)
                        if not success:
                            logger.info(f"Gateway Detected. JS DOM-click attempt {self._bypass_attempts} via Deno worker")
                            js_result = await self._try_js_gateway_click(gateway_action)
                            if js_result:
                                js_cookies = js_result.get("cookies") or {}
                                html_after = js_result.get("html_after")
                                needs_fetch = bool(js_result.get("reload_requested") or js_result.get("redirect"))
                                if js_cookies:
                                    session = self._get_sync_session()
                                    for name, value in js_cookies.items():
                                        try:
                                            session.cookies.set(name, value)
                                            logger.info(f"P4 DOM-click cookie set: {name}={value}")
                                        except Exception as e:
                                            logger.debug(f"P4 DOM-click cookie set failed for {name}: {e}")
                                if html_after and not needs_fetch:
                                    # DOM mutated in place (overlay removed, content
                                    # revealed) — parse the new document directly.
                                    self._js_gateway_html = html_after
                                    success = True
                                elif js_cookies:
                                    # Cookie was set (reload/redirect) — re-fetch.
                                    success = True
                                else:
                                    logger.debug("P4 DOM click changed nothing usable")
                    else:
                        logger.info(f"Gateway Detected. Bypassing attempt {self._bypass_attempts} via: {gateway_action['url']}")
                        success = await self._execute_bypass(gateway_action)
                    if success:
                        if not self._js_gateway_html:
                            logger.info(f"Cookies updated. RE-FETCHING original content: {self.url}")
                            # Sync bypass cookies to aiohttp session so re-fetch includes them
                            if self._sync_session:
                                for c in self._sync_session.cookies:
                                    self.session.cookie_jar.update_cookies(
                                        {c.name: c.value},
                                        yarl.URL(f"{urlparse(self.url).scheme}://{urlparse(self.url).netloc}/")
                                    )
                        # Reset discovered content before re-fetching / re-parsing
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
                self._sync_session.close()
                self._sync_session = None

            return self.links, self.media_files, K.PARSER_SUCCESS, "Successfully parsed.", http_status_code, cookies
        except Exception as e:
            if self._sync_session:
                try:
                    self._sync_session.close()
                except Exception:
                    pass
                self._sync_session = None
            msg = f"Error during parsing HTML content of {self.url}: {str(e)}"
            logger.error(msg, exc_info=False)
            return self.links, self.media_files, K.PARSER_UNKNOWN_ERROR, msg, http_status_code, None


    def _is_preview_transition(self, elem) -> bool:
        """True if this element is a thumbnail image wrapped in a link to a
        PAGE (thumbnail -> viewer/fullsize transition), so its own URL is a
        preview, not the content.

        Mirrors the parent-link logic in _extract_images: an <img> whose
        parent <a href> points at a webpage (not a media file) is a gallery
        thumbnail whose fullsize lives on the linked page. Dynamic-content
        scans (lazy data-src, data-* attributes, JS regex) must not queue such
        previews as media — the linked page is crawled and yields the real
        image (observed: 444 t1.pictoa previews queued per viewer page, 244 of
        them rejected as 'File too small').
        """
        if elem is None or getattr(elem, "name", None) != "img":
            return False
        parent_a = elem.find_parent("a", href=True)
        if not parent_a or not self._is_element_visible(parent_a):
            return False
        link_abs = urljoin(self.url, parent_a.get("href", ""))
        if not link_abs.startswith(("http://", "https://")):
            return False
        # A link straight to a media file is not a transition — it IS the media.
        if is_image_url(link_abs) or is_media_url(link_abs):
            return False
        return True

    async def _handle_dynamic_content(self, soup: BeautifulSoup) -> None:
        # The check `if not self.process_js: return` is no longer strictly needed here
        # because the call to this method is already gated by self.process_js.
        # However, keeping it doesn't harm and adds an extra layer of safety if called from elsewhere.
        try:
            if not self.process_js: return 
            # Pre-collect the URLS of thumbnail->page transitions so every
            # dynamic scan below (lazy data-src, data-* attributes, JS regex)
            # can skip previews that will be resolved as fullsize when the
            # linked page is crawled. Without this the three scans each re-add
            # the same preview URLs as media (observed: 444 t1.pictoa previews
            # in the download queue, 244 rejected 'File too small' — junk that
            # churns HEAD requests and inflates the queue).
            preview_urls = set()
            for img in soup.find_all("img"):
                if not self._is_preview_transition(img):
                    continue
                for attr in ("src", "data-src", "data-lazy-src", "data-original",
                             "data-lazy", "data-srcset", "data-lazy-srcset"):
                    val = img.get(attr)
                    if not val:
                        continue
                    for piece in val.split(","):
                        piece = piece.strip().split()[0] if piece.strip() else ""
                        if piece and is_media_url(piece):
                            preview_urls.add(urljoin(self.url, piece))
            if preview_urls:
                logger.debug(f"Skipping {len(preview_urls)} thumbnail-transition preview URLs on {self.url}")

            for script_tag in soup.find_all("script"):
                if script_tag.string: self._extract_media_from_js(script_tag.string, preview_urls)
            for elem in soup.find_all(True): 
                if self._is_preview_transition(elem):
                    # Skip data-* extraction on preview thumbnails — the URL is
                    # a transition thumbnail, not page content.
                    continue
                # CORE-8: check the framework's marker attributes directly
                # instead of re-serializing the element for each regex
                # (str(elem) is O(subtree) per pattern — quadratic on big pages).
                for framework in self.JS_PATTERNS["framework_patterns"]:
                    if self._framework_attr_present(elem, framework):
                        self._process_framework_element(elem, framework)
                for attr_name in elem.attrs:
                    if attr_name.startswith("data-"): self._process_data_attribute(elem, attr_name)
            for data_attr_pattern in self.LAZY_LOAD_PATTERNS["data-attributes"]:
                for elem in soup.find_all(attrs={data_attr_pattern: True}):
                    url_val = elem.get(data_attr_pattern)
                    if url_val and is_media_url(url_val): 
                        abs_url = urljoin(self.url, url_val)
                        if abs_url in preview_urls:
                            continue
                        attrs = {"source": f"lazy-data-{data_attr_pattern}"}
                        media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
                        if self._is_significant_media(media_type, abs_url, attrs):
                            self.media_files.append((media_type, abs_url, attrs))
        except Exception as e:
            logger.error(f"Error in static JS/dynamic content analysis for {self.url}: {str(e)}", exc_info=True)

    def _extract_media_from_js(self, js_content: str, preview_urls: set = None) -> None:
        preview_urls = preview_urls or set()
        for pattern_type, patterns in self.JS_PATTERNS.items():
            if pattern_type in ["image_sources", "video_sources", "data_attributes"]:
                media_hint = "image" if "image" in pattern_type else "video" if "video" in pattern_type else "image" 
                for pattern in patterns:
                    for match in re.finditer(pattern, js_content):
                        url = match.group(1) 
                        if url and url.startswith(("http://", "https://", "/")) and is_media_url(url):
                            abs_url = urljoin(self.url, url)
                            if abs_url in preview_urls:
                                continue
                            attrs = {"source": f"js-static-{pattern_type}"}
                            if self._is_significant_media(media_hint, abs_url, attrs):
                                self.media_files.append((media_hint, abs_url, attrs))

    def _framework_attr_present(self, elem: Any, framework: str) -> bool:
        """True when the element carries the framework's lazy-image marker.

        CORE-8: attribute-based equivalent of the old regex-on-str(elem) checks
        (those re-serialized the whole subtree per framework — O(page x depth)).
        Mirrors exactly what _process_framework_element reads per framework.
        """
        if framework == "react":
            return bool(elem.get("data-src") or elem.get("data-lazy"))
        if framework == "vue":
            return bool(elem.get("v-lazy"))
        if framework == "angular":
            # lxml lowercases attribute names, so the source "lazyLoad" appears
            # as "lazyload" in the parsed tree — accept both cases.
            return bool(elem.get("lazyLoad") or elem.get("lazyload") or elem.get("ng-src"))
        return False

    def _process_framework_element(self, elem: Any, framework: str) -> None:
        attrs = {"source": f"framework-{framework}"}
        src_val = None
        if framework == "react": src_val = elem.get("data-src") or elem.get("data-lazy")
        elif framework == "vue": src_val = elem.get("v-lazy")
        elif framework == "angular": src_val = elem.get("lazyLoad") or elem.get("lazyload") or elem.get("ng-src")
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
