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
# DL-6: aiohttp announces Accept-Encoding itself based on what it can decode,
# so HAS_BROTLI here is informational only — never force-announce "br".
try:
    import brotli  # noqa: F401
    HAS_BROTLI = True
    logger.info("Brotli support detected.")
except ImportError:
    HAS_BROTLI = False
    logger.info("Brotli support not detected.")


class AsyncClientManager:
    """
    Manages a shared aiohttp.ClientSession for asynchronous HTTP requests.
    """

    def __init__(self, settings: Dict[str, Any], start_url: str = ""):
        """
        Initialize the session manager.

        Args:
            settings: A dictionary of settings, typically from the application's configuration.
                      Expected keys:
                      - "page_timeout" (int): Total timeout for a request.
                      - "user_agent" (str): User-Agent string.
                      - "accept_language" (str): Accept-Language string.
            start_url: The task's start URL. Browser-extension cookies are scoped
                      to this domain only (DL-7) instead of being sent to every
                      host the task crawls.
        """
        self.settings = settings
        self.start_url = start_url or ""
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
        # NOTE (DL-7): browser-extension cookies are intentionally NOT added to
        # the session headers — a session-level Cookie header would leak the
        # tab's cookies to every crawled host. They are loaded into the session's
        # cookie jar scoped to the task's start domain instead (see
        # _apply_extension_cookies), so aiohttp only sends them to that domain.
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
            self._apply_extension_cookies(self._session)
            logger.info(f"New aiohttp.ClientSession created. Brotli enabled in session: {HAS_BROTLI and self._session.headers.get('Accept-Encoding','').lower().startswith('gzip, deflate, br')}")
        return self._session

    def _apply_extension_cookies(self, session: aiohttp.ClientSession) -> None:
        """Load browser-extension cookies into the session's jar scoped to the
        task's start domain (DL-7).

        The tab's cookies are only meaningful for the start site; a session-level
        Cookie header leaked them to every host the task crawls (CDNs, third
        parties). aiohttp's CookieJar only sends a cookie to hosts matching its
        domain, so loading the pairs anchored at the start URL both fixes the
        leak and keeps authenticated access to the start site working.
        """
        ext_cookies = self.settings.get("extension_cookies", "")
        if not ext_cookies or not self.start_url:
            return
        try:
            from yarl import URL
            pairs: Dict[str, str] = {}
            for part in ext_cookies.split(";"):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                name, _, value = part.partition("=")
                if name.strip():
                    pairs[name.strip()] = value.strip()
            if not pairs:
                return
            session.cookie_jar.update_cookies(pairs, response_url=URL(self.start_url))
            logger.info(f"Scoped {len(pairs)} extension cookie(s) to start domain {URL(self.start_url).host}")
        except Exception as e:
            logger.warning(f"Could not scope extension cookies to start domain: {e}")

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

