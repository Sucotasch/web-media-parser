#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for Phase 5 parser fixes:

CORE-3: Retry-After is capped to the page timeout — a 'Retry-After: 86400'
        must not park the worker (and the whole per-domain semaphore) for a day.

CORE-18: sock_read timeout is clamped to >= 1s so hand-edited settings with
         page_timeout <= 2 cannot produce an instant timeout on everything.
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.webpage_parser import WebpageParser
from src import constants as K


class FakeResp:
    """Minimal async-CM aiohttp response for _get_content tests."""

    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {"Content-Type": "text/html"}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self._body

    async def text(self):
        return self._body.decode("utf-8", "replace")


class TestRetryAfterCapped(unittest.TestCase):
    """CORE-3: huge Retry-After values are bounded by the page timeout."""

    def test_retry_after_86400_capped(self):
        settings = {K.SETTING_PAGE_TIMEOUT: 5, K.SETTING_RETRY_COUNT: 1}
        session = MagicMock()
        session.get.return_value = FakeResp(
            status=429, headers={"Retry-After": "86400"})

        parser = WebpageParser(
            url="https://site.com/page", settings=settings,
            process_js=True, external_session=session,
        )
        with patch("src.parser.webpage_parser.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            result = asyncio.run(parser._get_content())

        self.assertEqual(result[1], K.PARSER_HTTP_ERROR_4XX)
        self.assertEqual(result[3], 429)
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        self.assertTrue(sleeps, "expected at least one backoff sleep")
        # The Retry-After backoff (not the 0.5*attempt fast backoff) must be capped.
        self.assertLessEqual(max(sleeps), 5,
                             f"Retry-After 86400 must be capped to page_timeout, slept {max(sleeps)}")

    def test_normal_retry_after_kept(self):
        """A modest Retry-After under the cap is honored unchanged."""
        settings = {K.SETTING_PAGE_TIMEOUT: 30, K.SETTING_RETRY_COUNT: 1}
        session = MagicMock()
        session.get.return_value = FakeResp(
            status=429, headers={"Retry-After": "2"})

        parser = WebpageParser(
            url="https://site.com/page", settings=settings,
            process_js=True, external_session=session,
        )
        with patch("src.parser.webpage_parser.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            result = asyncio.run(parser._get_content())

        self.assertEqual(result[1], K.PARSER_HTTP_ERROR_4XX)
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        self.assertIn(2, sleeps, f"modest Retry-After should be honored, sleeps={sleeps}")


class TestSockReadClamped(unittest.TestCase):
    """CORE-18: page_timeout <= 2 must not yield sock_read <= 0."""

    def test_small_page_timeout_keeps_positive_sock_read(self):
        settings = {K.SETTING_PAGE_TIMEOUT: 1, K.SETTING_RETRY_COUNT: 0}
        session = MagicMock()
        session.get.return_value = FakeResp(status=200, body=b"<html></html>")

        parser = WebpageParser(
            url="https://site.com/page", settings=settings,
            process_js=True, external_session=session,
        )
        asyncio.run(parser._get_content())

        timeout = session.get.call_args.kwargs["timeout"]
        self.assertGreaterEqual(timeout.sock_read, 1,
                                f"sock_read must be clamped, got {timeout.sock_read}")
        self.assertGreaterEqual(timeout.connect, 0)


if __name__ == "__main__":
    unittest.main()
