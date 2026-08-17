#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for Phase 2 content-loss fixes:

- CORE-7: _get_video_platform must match a direct-video EXTENSION, not a
  substring (".ts" in "/user.tsuji/page" was misclassified as direct video).
- CORE-9: JSON-LD media — ImageObject.image list form is now extracted, and
  all JSON-LD media passes the significance gate (icons/logos dropped).
- CORE-17: _get_best_image_url separates the priority tier (score) from real
  pixels (width); named hi-res attrs no longer drop out at min_image_width > 100.
- CORE-19 (webpage_parser): disabled-format direct media links are dropped,
  not queued as from_image crawl links.
- CORE-20: a failed fullsize discovery releases the link back to the crawl
  path instead of silently consuming it.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bs4 import BeautifulSoup

from src.parser.webpage_parser import WebpageParser
from src.parser.parser_manager import ParserManager
from src.parser.site_pattern_manager import SitePatternManager
from src import constants as K

from helpers import MockGUILogHandler, SIEVE_PATH

SIEVE = SIEVE_PATH  # TST-5: single canonical sieve snapshot (July)
IMX_LINK = "https://imx.to/i/6xt7ux"
IMX_THUMB = "https://image.imx.to/u/t/2026/08/02/6xt7ux.jpg"


def _make_parser(settings=None, url="https://example.com/"):
    return WebpageParser(
        url=url, settings=settings or {}, process_js=False,
        external_session=MagicMock(),
    )


class TestVideoPlatformExtension(unittest.TestCase):
    """CORE-7: direct-video detection needs a real extension boundary."""

    def test_substring_not_a_video_extension(self):
        p = _make_parser()
        self.assertIsNone(p._get_video_platform("https://site.com/user.tsuji/page"))
        self.assertIsNone(p._get_video_platform("https://site.com/avatar/photo.jpg"))

    def test_real_direct_video_still_detected(self):
        p = _make_parser()
        self.assertEqual(p._get_video_platform("https://site.com/videos/clip.mp4"), "direct-video")
        self.assertEqual(p._get_video_platform("https://site.com/videos/clip.ts"), "direct-video")
        self.assertEqual(p._get_video_platform("https://site.com/videos/clip.webm/"), "direct-video")


class TestJsonLdMedia(unittest.TestCase):
    """CORE-9: JSON-LD image lists are extracted and the significance gate applies."""

    def _extract(self, ld_json):
        p = _make_parser()
        html = f'<html><head><script type="application/ld+json">{json.dumps(ld_json)}</script></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        p._extract_jsonld_media(soup)
        return p.media_files

    def test_imageobject_image_list_extracted(self):
        # Previously lost: the list form of ImageObject.image.
        media = self._extract({
            "@type": "ImageObject",
            "image": ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"],
        })
        urls = {u for _, u, _ in media}
        self.assertIn("https://cdn.example.com/a.jpg", urls)
        self.assertIn("https://cdn.example.com/b.jpg", urls)

    def test_article_image_mixed_list(self):
        media = self._extract({
            "@type": "Article",
            "image": [{"url": "https://cdn.example.com/x.jpg"},
                      "https://cdn.example.com/y.jpg"],
        })
        urls = {u for _, u, _ in media}
        self.assertIn("https://cdn.example.com/x.jpg", urls)
        self.assertIn("https://cdn.example.com/y.jpg", urls)

    def test_significance_gate_drops_icon_logo(self):
        # URL noise ("/logo.") must be filtered like every other extraction path.
        media = self._extract({
            "@type": "Article",
            "image": "https://cdn.example.com/logo.png",
        })
        self.assertEqual(media, [])

    def test_plain_image_kept(self):
        media = self._extract({
            "@type": "Article",
            "image": "https://cdn.example.com/gallery/photo.jpg",
        })
        self.assertEqual([u for _, u, _ in media], ["https://cdn.example.com/gallery/photo.jpg"])


class TestBestImageUrlScoreWidth(unittest.TestCase):
    """CORE-17: named hi-res attrs survive min_image_width > 100."""

    def test_named_hi_res_survives_high_min_width(self):
        settings = {K.SETTING_MIN_IMG_WIDTH: 200}
        p = _make_parser(settings)
        soup = BeautifulSoup(
            '<img src="thumb.jpg" data-hi-res-src="full.jpg" width="400" height="300">',
            "html.parser")
        urls, attrs = p._get_best_image_url(soup.find("img"))
        self.assertEqual(urls, ["full.jpg"])
        self.assertEqual(attrs["source"], "data-hi-res-src")

    def test_largest_srcset_wins_over_plain_src(self):
        p = _make_parser()
        soup = BeautifulSoup(
            '<img src="thumb.jpg" srcset="a.jpg 100w, b.jpg 2000w">',
            "html.parser")
        urls, _ = p._get_best_image_url(soup.find("img"))
        self.assertEqual(urls, ["b.jpg"])


class TestDisabledFormatParentLinkDropped(unittest.TestCase):
    """CORE-19 (webpage_parser): disabled-format direct media link is dropped."""

    def test_gif_parent_link_not_queued_as_crawl_link(self):
        p = _make_parser()
        soup = BeautifulSoup(
            '<a href="https://cdn.example.com/anim.gif"><img src="thumb.jpg"></a>',
            "html.parser")
        asyncio.run(p._extract_images(soup))
        urls = {u for _, u, _ in p.media_files}
        self.assertIn("https://example.com/thumb.jpg", urls)
        self.assertNotIn("https://cdn.example.com/anim.gif", urls)
        self.assertNotIn("https://cdn.example.com/anim.gif", p.links)


class TestDiscoveryFailureReleasesLink(unittest.TestCase):
    """CORE-20: failed discovery must not consume the link."""

    def _make_manager(self):
        settings = {
            K.SETTING_STAY_IN_DOMAIN: True,
            K.SETTING_USE_PATTERNS: True,
            K.SETTING_STOP_WORDS: [],
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            pm = ParserManager(
                url="https://vipergirls.to/threads/123", download_path="x",
                settings=settings, log_handler=MockGUILogHandler())
        pm.pattern_manager = SitePatternManager(enable_built_in=False, imagus_sieve_path=SIEVE)
        return pm

    def test_network_error_releases_link_back_to_crawl(self):
        pm = self._make_manager()
        session = AsyncMock()
        session.post.side_effect = RuntimeError("network down")

        async def run():
            return await pm._discover_linked_fullsize(
                [(IMX_LINK, {"from_image": True, "thumbnail_url": IMX_THUMB})],
                "https://vipergirls.to/threads/123", session)

        discovered, resolved, consumed = asyncio.run(run())
        self.assertEqual(discovered, [])
        self.assertEqual(resolved, set())
        # CORE-20: the link is NOT consumed — it falls back to the normal crawl.
        self.assertEqual(consumed, set())


if __name__ == "__main__":
    unittest.main()
