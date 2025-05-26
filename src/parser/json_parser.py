#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JSON parser for extracting media files from JSON APIs
"""

import re
import json
import logging
import asyncio
from typing import Dict, Any, List, Tuple, Optional, Set
from urllib.parse import urlparse, urljoin

from src.parser.webpage_parser import WebpageParser, HAS_BROTLI
from src.parser.utils import is_image_url, is_media_url, normalize_url

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

            async with self.session.get(self.url, headers=request_specific_headers) as response:
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
        media_key_patterns = [
            "url", "src", "source", "media", "image", "img", "photo", "picture",
            "thumbnail", "thumb", "icon", "avatar", "video", "file", "path",
            "href", "link", "content", "data", "original", "high_res", "hd",
        ]
        
        return any(pattern in key_lower for pattern in media_key_patterns)

    def _process_potential_media(self, value: Any, path: str) -> None:
        """
        Process a value that might be media

        Args:
            value: Value to process
            path: JSON path for context
        """
        if isinstance(value, str):
            # Check if the string is a URL
            if self._looks_like_url(value):
                # Make absolute URL
                abs_url = urljoin(self.url, value)
                # Determine media type
                if is_image_url(abs_url):
                    self.media_files.append(("image", abs_url, {"source": f"json-{path}", "path": path}))
                elif is_media_url(abs_url):
                    # Try to determine media type from URL
                    media_type = self._guess_media_type(abs_url)
                    self.media_files.append((media_type, abs_url, {"source": f"json-{path}", "path": path}))
                else:
                    # Add as link for further processing
                    self.links.add(abs_url)
        elif isinstance(value, list):
            # Process list of potential URLs
            for item in value:
                if isinstance(item, str) and self._looks_like_url(item):
                    abs_url = urljoin(self.url, item)
                    if is_media_url(abs_url):
                        media_type = self._guess_media_type(abs_url)
                        self.media_files.append((media_type, abs_url, {"source": f"json-{path}", "path": path}))
                elif isinstance(item, dict) and "url" in item:
                    # Handle objects with url field
                    url_value = item["url"]
                    if isinstance(url_value, str) and self._looks_like_url(url_value):
                        abs_url = urljoin(self.url, url_value)
                        if is_media_url(abs_url):
                            media_type = self._guess_media_type(abs_url)
                            attrs = {k: v for k, v in item.items() if k != "url"}
                            attrs["source"] = f"json-{path}"
                            attrs["path"] = path
                            self.media_files.append((media_type, abs_url, attrs))

    def _extract_links_from_json(self, data: Any, path: str = "") -> None:
        """
        Extract links from JSON data that might lead to more media

        Args:
            data: JSON data to process
            path: Current JSON path for debugging
        """
        if isinstance(data, dict):
            # Look for pagination or next page links
            for key in ["next", "next_page", "nextPage", "pagination", "paging", "links"]:
                if key in data and isinstance(data[key], str) and self._looks_like_url(data[key]):
                    abs_url = urljoin(self.url, data[key])
                    self.links.add(abs_url)
                    logger.debug(f"Found next page link: {abs_url}")
                elif key in data and isinstance(data[key], dict) and "url" in data[key]:
                    next_url = data[key]["url"]
                    if isinstance(next_url, str) and self._looks_like_url(next_url):
                        abs_url = urljoin(self.url, next_url)
                        self.links.add(abs_url)
                        logger.debug(f"Found next page link: {abs_url}")

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
        Guess the media type from the URL

        Args:
            url: URL to analyze

        Returns:
            Media type ("image", "video", or "file")
        """
        url_lower = url.lower()
        
        # Check for image extensions
        if any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tiff", ".bmp", ".avif"]):
            return "image"
            
        # Check for video extensions
        if any(ext in url_lower for ext in [".mp4", ".webm", ".ogg", ".mov", ".avi", ".wmv", ".flv", ".mkv", ".m4v", ".ts"]):
            return "video"
            
        # Check for audio extensions
        if any(ext in url_lower for ext in [".mp3", ".wav", ".aac", ".flac"]):
            return "audio"
            
        # Check for known video platforms
        video_platforms = ["youtube", "vimeo", "dailymotion", "twitch", "streamable", "redgifs"]
        if any(platform in url_lower for platform in video_platforms):
            return "video"
            
        # Default to generic file
        return "file"

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