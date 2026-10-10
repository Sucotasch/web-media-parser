#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JSON parser for extracting media files from JSON APIs
"""

import re
import json
import logging
from typing import Dict, Any, List, Tuple, Optional, Set
from urllib.parse import urlparse, urljoin

from src.parser.utils import is_image_url, is_media_url, format_proxy_url, is_format_allowed
from src import constants as K

logger = logging.getLogger(__name__)


class JSONWebpageParser:
    """
    Parser class for extracting media files from JSON APIs
    """

    def __init__(self, url: str, settings: Dict[str, Any], external_session=None):
        """
        Initialize the JSON parser

        Args:
            url: URL to parse
            settings: Settings dictionary
            external_session: Optional external aiohttp session
        """
        self.url = url
        self.settings = settings
        
        if external_session is None:
            raise ValueError("JSONWebpageParser requires an external_session (aiohttp.ClientSession).")
        self.session = external_session # Always use the provided external session

        # Store discovered media and links
        self.media_files: List[Tuple[str, str, Dict[str, Any]]] = []
        self.links: Set[str] = set()

    # __aenter__ and __aexit__ removed, session managed externally by AsyncClientManager
    # _get_headers and _get_timeout removed, managed by AsyncClientManager

    async def parse(self) -> Tuple[Set[str], List[Tuple[str, str, Dict[str, Any]]]]:
        """
        Parse JSON from the URL and extract media files

        Returns:
            Tuple of (discovered_links, media_files)
        """
        try:
            # No longer using 'async with self' as session is managed externally
            json_data = await self._get_json()
            if not json_data:
                return set(), []

            # Process JSON data
            self._extract_media_from_json(json_data)
            self._extract_links_from_json(json_data)

            return self.links, self.media_files
        except Exception as e:
            logger.error(f"Error parsing JSON from {self.url}: {str(e)}")
            raise

    async def _get_json(self) -> Optional[Dict[str, Any]]:
        """
        Get JSON data from URL

        Returns:
            Parsed JSON data or None if failed
        """
        try:
            # Headers for this specific request, if any, can be added here.
            # Session default headers are set by AsyncClientManager.
            # For JSON, usually, specific 'Accept' header might be useful if not default.
            request_specific_headers = {
                 "Accept": self.settings.get("json_accept_header", "application/json, text/javascript, */*; q=0.01"),
                 # Add other headers if needed, e.g. X-Requested-With
                 "X-Requested-With": "XMLHttpRequest",
            }

            proxy_url = format_proxy_url(self.settings.get(K.SETTING_PROXY))
            async with self.session.get(self.url, headers=request_specific_headers, proxy=proxy_url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch JSON from {self.url}: {response.status}")
                    return None

                try:
                    return await response.json()
                except Exception as e:
                    logger.error(f"Failed to parse JSON from {self.url}: {str(e)}")
                    # Try to parse as text in case it's not valid JSON
                    content = await response.text()
                    if content.strip().startswith('{') or content.strip().startswith('['):
                        try:
                            return json.loads(content)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to decode JSON content from {self.url}")
                    return None

        except Exception as e:
            logger.error(f"Network error fetching JSON from {self.url}: {str(e)}")
            return None

    def _extract_media_from_json(self, data: Any, path: str = "") -> None:
        """
        Recursively extract media files from JSON data

        Args:
            data: JSON data to process
            path: Current JSON path for debugging
        """
        if isinstance(data, dict):
            # Process dictionary
            for key, value in data.items():
                new_path = f"{path}.{key}" if path else key
                # Check if this key is likely to contain media
                if self._is_media_key(key):
                    self._process_potential_media(value, new_path)
                else:
                    # Continue recursion
                    self._extract_media_from_json(value, new_path)
        elif isinstance(data, list):
            # Process list
            for i, item in enumerate(data):
                new_path = f"{path}[{i}]" if path else f"[{i}]"
                self._extract_media_from_json(item, new_path)

    def _is_media_key(self, key: str) -> bool:
        """
        Check if a JSON key is likely to contain media

        Args:
            key: JSON key to check

        Returns:
            True if the key is likely to contain media
        """
        key_lower = key.lower()
        # R-C-8: single source of truth (constants) — the local copy had drifted
        # and silently missed keys the shared list already knew (e.g. "asset").
        media_key_patterns = K.JSON_MEDIA_KEY_PATTERNS
        
        return any(pattern in key_lower for pattern in media_key_patterns)

    def _process_potential_media(self, value: Any, path: str) -> None:
        """
        Process a value that might be media

        Args:
            value: Value to process
            path: JSON path for context
        """
        # R-C-1: a dict under a media-ish key ({"data": {...}}, {"media": {...}},
        # {"metadata": {...}}) is not a URL container itself. It used to be
        # dropped silently — descend instead so nested media is still found.
        if isinstance(value, dict):
            self._extract_media_from_json(value, path)
            return
        if isinstance(value, str):
            # Check if the string is a URL
            if self._looks_like_url(value):
                # Make absolute URL
                abs_url = urljoin(self.url, value)
                # Determine media type (used to pick the right format allowlist)
                media_type = self._guess_media_type(abs_url)
                if is_image_url(abs_url) or is_media_url(abs_url):
                    # CORE-19: a direct media URL with a disabled format is
                    # DROPPED — queueing it as a crawl link made the crawler
                    # fetch image bytes as a "webpage" on every such URL
                    # (media-lookup bypasses stay-in-domain/depth).
                    if is_format_allowed(abs_url, media_type, self.settings):
                        self.media_files.append((media_type, abs_url, {"source": f"json-{path}", "path": path}))
                else:
                    # Non-media URL — keep as link for further processing
                    self.links.add(abs_url)
        elif isinstance(value, list):
            # Process list of potential URLs
            for item in value:
                if isinstance(item, str) and self._looks_like_url(item):
                    abs_url = urljoin(self.url, item)
                    media_type = self._guess_media_type(abs_url)
                    if is_media_url(abs_url):
                        # CORE-19: disabled-format media dropped (see above).
                        if is_format_allowed(abs_url, media_type, self.settings):
                            self.media_files.append((media_type, abs_url, {"source": f"json-{path}", "path": path}))
                elif isinstance(item, dict) and "url" in item:
                    # Handle objects with url field
                    url_value = item["url"]
                    if isinstance(url_value, str) and self._looks_like_url(url_value):
                        abs_url = urljoin(self.url, url_value)
                        media_type = self._guess_media_type(abs_url)
                        if is_media_url(abs_url):
                            # CORE-19: disabled-format media dropped (see above).
                            if is_format_allowed(abs_url, media_type, self.settings):
                                attrs = {k: v for k, v in item.items() if k != "url"}
                                attrs["source"] = f"json-{path}"
                                attrs["path"] = path
                                self.media_files.append((media_type, abs_url, attrs))
                        else:
                            self.links.add(abs_url)
                else:
                    # R-C-1: list items that are neither a URL string nor a dict
                    # with "url" ({"images": [{"src": …}]}) were dropped silently;
                    # descend into them instead (strings are ignored downstream).
                    self._extract_media_from_json(item, path)

    def _extract_links_from_json(self, data: Any, path: str = "") -> None:
        """
        Extract links from JSON data that might lead to more media

        Args:
            data: JSON data to process
            path: Current JSON path for debugging
        """
        # R-C-4: pagination lives under data/meta on most APIs, so the whole
        # payload is searched instead of only the top level. Iterative (no
        # RecursionError on deep payloads) and bounded by a node cap.
        # Only pagination-specific keys are honoured: K.JSON_LINK_KEY_PATTERNS also
        # contains generic url/href/link entries which would turn every URL in the
        # payload into a crawl link (crawl explosion, out-of-domain fetches).
        stack = [(data, 0)]
        visited = 0
        while stack:
            node, depth = stack.pop()
            visited += 1
            if visited > K.JSON_LINK_SCAN_MAX_NODES or depth > K.JSON_LINK_SCAN_MAX_DEPTH:
                logger.debug(f"Pagination scan stopped early ({visited} nodes, depth {depth})")
                break
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in K.JSON_PAGINATION_KEY_PATTERNS:
                        if isinstance(value, str) and self._looks_like_url(value):
                            abs_url = urljoin(self.url, value)
                            self.links.add(abs_url)
                            logger.debug(f"Found next page link: {abs_url}")
                        elif isinstance(value, dict) and "url" in value:
                            next_url = value["url"]
                            if isinstance(next_url, str) and self._looks_like_url(next_url):
                                abs_url = urljoin(self.url, next_url)
                                self.links.add(abs_url)
                                logger.debug(f"Found next page link: {abs_url}")
                    # Always descend: {"meta": {"pagination": {"next": …}}}
                    # has the pagination key one level above the actual link.
                    stack.append((value, depth + 1))
            elif isinstance(node, list):
                for item in node:
                    stack.append((item, depth + 1))

    def _looks_like_url(self, text: str) -> bool:
        """
        Check if a string looks like a URL

        Args:
            text: String to check

        Returns:
            True if the string looks like a URL
        """
        if not text:
            return False
            
        # Check if it's an absolute URL
        if text.startswith(("http://", "https://")):
            return True
            
        # Check if it's a relative URL
        if text.startswith("/") or "./" in text:
            return True
            
        # Check for URL-like patterns
        url_pattern = re.compile(r"^[\w\-\.]+\.[a-zA-Z]{2,}(/[\w\-\./\?\&\=\#\%]*)?$")
        return bool(url_pattern.match(text))

    def _guess_media_type(self, url: str) -> str:
        """
        Guess the media type from the URL using centralized constants
        """
        url_lower = url.lower()
        parsed_url = urlparse(url_lower)
        path = parsed_url.path
        
        # Check for image extensions from constants
        if any(path.endswith(ext) for ext in K.IMAGE_EXTENSIONS):
            return "image"
            
        # Check for video extensions from constants
        if any(path.endswith(ext) for ext in K.VIDEO_EXTENSIONS):
            return "video"
            
        # Check for audio extensions from constants
        if any(path.endswith(ext) for ext in K.AUDIO_EXTENSIONS):
            return "audio"
            
        # Check for known video platforms from constants
        if any(platform in url_lower for platform in K.VIDEO_PLATFORM_INDICATORS):
            return "video"

        # Unknown extension but is_media_url matched (CDN path, media query…):
        # most such URLs are images; parser_manager stats/filenames only know
        # image/video, and "file" fell through to a wrong .mp4 default name.
        return "image"

    def get_media_files(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        Get discovered media files

        Returns:
            List of (media_type, url, attributes) tuples
        """
        return self.media_files

    def get_discovered_urls(self) -> Set[str]:
        """
        Get discovered URLs

        Returns:
            Set of discovered URLs
        """
        return self.links