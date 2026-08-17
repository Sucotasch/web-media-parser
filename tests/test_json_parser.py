#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for the JSON API parser branch (CORE-1) and its format-allowlist
handling (CORE-13).

CORE-1: ParserManager._invoke_parser used `async with JSONWebpageParser(...)`,
but JSONWebpageParser deliberately has no __aenter__/__aexit__ (its session is
managed externally by AsyncClientManager). Every JSON-classified URL therefore
raised TypeError and the whole JSON API path was dead in production. The fix
calls parse() directly.

CORE-13: the list branch of JSONWebpageParser._process_potential_media
bypassed the format-allowlist, so disabled formats (e.g. GIF/SVG) were queued
as media when they arrived inside a JSON array. It now mirrors the scalar
branch: disabled formats fall through to links.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.json_parser import JSONWebpageParser
from src.parser.parser_manager import ParserManager
from src import constants as K

from helpers import MockGUILogHandler


class FakeJsonResp:
    """Minimal aiohttp-response stand-in usable with `async with`."""

    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    @property
    def headers(self):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._data

    async def text(self):
        return json.dumps(self._data)


def _make_manager(url="https://api.example.com/feed"):
    settings = {
        K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
        K.SETTING_STAY_IN_DOMAIN: False,
        K.SETTING_STOP_WORDS: [],
        K.SETTING_USE_PATTERNS: False,
    }
    with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
        return ParserManager(
            url=url, download_path="x", settings=settings,
            log_handler=MockGUILogHandler(),
        )


class TestInvokeParserJsonBranch(unittest.TestCase):
    """CORE-1: JSON-classified URLs actually run the JSON parser."""

    def test_json_branch_extracts_media(self):
        pm = _make_manager()
        # Plain MagicMock, NOT AsyncMock: JSONWebpageParser._get_json uses
        # `async with self.session.get(...)` and async-with does not await the
        # expression — the fake must return an object with __aenter__ directly
        # (exactly what a real aiohttp session returns).
        session = MagicMock()
        session.get.return_value = FakeJsonResp({
            "images": ["https://cdn.example.com/pic.jpg"],
        })

        async def run():
            return await pm._invoke_parser(
                "https://api.example.com/feed", session, True, {})

        links, media = asyncio.run(run())

        self.assertEqual(
            [(t, u) for t, u, _ in media],
            [("image", "https://cdn.example.com/pic.jpg")],
        )
        # A JSON API must also surface pagination links, not only media.
        self.assertIsInstance(links, set)

    def test_json_branch_does_not_raise_typeerror(self):
        """Regression: the old `async with JSONWebpageParser(...)` raised
        TypeError (no __aenter__/__aexit__ on the parser)."""
        pm = _make_manager()
        session = MagicMock()
        session.get.return_value = FakeJsonResp({"ok": True})

        async def run():
            return await pm._invoke_parser(
                "https://api.example.com/ping", session, True, {})

        links, media = asyncio.run(run())  # would raise TypeError before the fix
        self.assertEqual(media, [])


class TestJsonListBranchFormatAllowlist(unittest.TestCase):
    """CORE-13 + CORE-19: disabled formats are never queued as media (and,
    being direct media URLs, are dropped entirely — not kept as crawl links)."""

    def _parser(self):
        return JSONWebpageParser(
            url="https://api.example.com/feed", settings={},
            external_session=MagicMock())

    def test_disabled_format_dropped_from_media_and_links(self):
        p = self._parser()
        p._process_potential_media([
            "https://cdn.example.com/anim.gif",
            "https://cdn.example.com/pic.jpg",
        ], "gallery")
        self.assertEqual(
            [(t, u) for t, u, _ in p.media_files],
            [("image", "https://cdn.example.com/pic.jpg")],
        )
        self.assertNotIn("https://cdn.example.com/anim.gif", p.links)

    def test_disabled_format_scalar_branch_dropped(self):
        p = self._parser()
        p._process_potential_media("https://cdn.example.com/anim.gif", "gallery")
        self.assertEqual(p.media_files, [])
        self.assertNotIn("https://cdn.example.com/anim.gif", p.links)

    def test_dict_items_respect_allowlist(self):
        p = self._parser()
        p._process_potential_media([
            {"url": "https://cdn.example.com/anim.gif"},
            {"url": "https://cdn.example.com/pic.jpg"},
        ], "gallery")
        self.assertEqual(
            [(t, u) for t, u, _ in p.media_files],
            [("image", "https://cdn.example.com/pic.jpg")],
        )
        self.assertNotIn("https://cdn.example.com/anim.gif", p.links)


if __name__ == "__main__":
    unittest.main()
