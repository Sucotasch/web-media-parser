#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shared asynchronous HTTP client session manager
"""

import aiohttp
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Try to import brotli for content-encoding support
try:
    import brotli
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
        self._timeout_config = aiohttp.ClientTimeout(
            total=self.settings.get("page_timeout", 60),
            connect=self.settings.get("connect_timeout", 20), # Default connect timeout
            sock_read=self.settings.get("sock_read_timeout", self.settings.get("page_timeout", 60)) # Default socket read timeout
        )

    def _get_default_headers(self) -> Dict[str, str]:
        """
        Get default request headers with browser simulation.
        """
        return {
            "User-Agent": self.settings.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            ),
            "Accept-Language": self.settings.get("accept_language", "en-US,en;q=0.9"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
            # Let aiohttp handle Accept-Encoding based on installed libraries (like brotli)
            # "Accept-Encoding": "gzip, deflate, br" if HAS_BROTLI else "gzip, deflate",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1", # Do Not Track
        }

    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get the managed aiohttp.ClientSession instance.
        Creates the session if it doesn't exist.
        """
        if self._session is None or self._session.closed:
            logger.info("Creating new aiohttp.ClientSession.")
            # Define connector arguments based on Brotli support
            connector_args = {}
            if HAS_BROTLI:
                # aiohttp typically handles brotli automatically if installed and ClientSession is created without specific connector.
                # However, explicitly using TCPConnector with ssl=False if needed for specific environments.
                # For general cases, aiohttp's default connector is usually fine.
                # If issues arise with SSL verification on some sites:
                # connector = aiohttp.TCPConnector(ssl=False)
                # self._session = aiohttp.ClientSession(connector=connector, ...)
                pass # aiohttp handles brotli by default if available

            # Create session with or without explicit TCPConnector for brotli
            # Let aiohttp handle brotli by default.
            # If specific SSL handling is needed (e.g. self-signed certs on local dev),
            # a custom TCPConnector can be passed:
            # connector = aiohttp.TCPConnector(ssl=False) # Example: disable SSL verification
            # self._session = aiohttp.ClientSession(connector=connector, ...)
            self._session = aiohttp.ClientSession(
                timeout=self._timeout_config,
                headers=self._get_default_headers(),
                # connector_owner=False # If passing a shared connector
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

from typing import Optional # Add this if not already present at the top
