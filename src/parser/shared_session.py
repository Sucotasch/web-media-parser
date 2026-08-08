#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shared asynchronous HTTP client session manager
"""

import aiohttp
import logging
from typing import Dict, Any, Optional

from src import constants as K

logger = logging.getLogger(__name__)

# Try to import brotli for content-encoding support.
# noqa below: side-effect import — aiohttp needs the module present to decode br.
try:
    import brotli  # noqa: F401
    HAS_BROTLI = True
    logger.info("Brotli support detected.")
except ImportError:
    HAS_BROTLI = False
    logger.info("Brotli support not detected.")

# Ensure consistent brotli support detection (fallback if primary import fails)
if not HAS_BROTLI:
    try:
        # This import is for side-effects if aiohttp needs help finding brotli
        from src.fix_brotli import BrotliSupportFix
        HAS_BROTLI = BrotliSupportFix.patch()
        if HAS_BROTLI:
            logger.info("Brotli support enabled via fix_brotli.")
    except ImportError:
        logger.info("fix_brotli module not found, Brotli support may be limited.")
        pass # HAS_BROTLI remains False


class AsyncClientManager:
    """
    Manages a shared aiohttp.ClientSession for asynchronous HTTP requests.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Initialize the session manager.

        Args:
            settings: A dictionary of settings, typically from the application's configuration.
                      Expected keys:
                      - "page_timeout" (int): Total timeout for a request.
                      - "user_agent" (str): User-Agent string.
                      - "accept_language" (str): Accept-Language string.
        """
        self.settings = settings
        self._session: Optional[aiohttp.ClientSession] = None
        # Align defaults with src/constants.py (page_timeout, connect, sock_read)
        # instead of local hard-coded values that drifted from the settings keys.
        page_timeout = self.settings.get(K.SETTING_PAGE_TIMEOUT, K.DEFAULT_PAGE_TIMEOUT)
        self._timeout_config = aiohttp.ClientTimeout(
            total=page_timeout,
            connect=self.settings.get("connect_timeout", K.DEFAULT_CONNECT_TIMEOUT),
            sock_read=self.settings.get("sock_read_timeout", K.DEFAULT_SOCK_READ_TIMEOUT)
        )

    def _get_default_headers(self) -> Dict[str, str]:
        """
        Get default request headers with browser simulation.
        Modern headers (Chrome 130+) to better emulate real users.
        """
        headers = {
            "User-Agent": self.settings.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            ),
            "Accept-Language": self.settings.get("accept_language", "en-US,en;q=0.9,ru;q=0.8"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Cache-Control": "max-age=0",
            "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
            "Connection": "keep-alive",
        }
        # Add cookies from browser extension context
        ext_cookies = self.settings.get("extension_cookies", "")
        if ext_cookies:
            headers["Cookie"] = ext_cookies
        return headers

    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get the managed aiohttp.ClientSession instance.
        Creates the session if it doesn't exist.
        """
        if self._session is None or self._session.closed:
            logger.info("Creating new aiohttp.ClientSession.")
            # aiohttp handles brotli automatically when installed; no custom
            # connector is needed (removed dead connector_args branch).
            self._session = aiohttp.ClientSession(
                timeout=self._timeout_config,
                headers=self._get_default_headers(),
            )
            logger.info(f"New aiohttp.ClientSession created. Brotli enabled in session: {HAS_BROTLI and self._session.headers.get('Accept-Encoding','').lower().startswith('gzip, deflate, br')}")
        return self._session

    async def close(self):
        """
        Close the managed aiohttp.ClientSession if it exists and is open.
        """
        if self._session and not self._session.closed:
            logger.info("Closing shared aiohttp.ClientSession.")
            await self._session.close()
            self._session = None
        else:
            logger.info("Shared aiohttp.ClientSession was already closed or not initialized.")

    async def __aenter__(self) -> aiohttp.ClientSession:
        """
        Async context manager entry point. Returns the session.
        """
        return await self.get_session()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit point. Closes the session.
        """
        await self.close()

