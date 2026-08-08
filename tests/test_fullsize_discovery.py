#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for sieve link->url->res fullsize discovery (thumbnail transitions)
and the stay-in-domain exemptions for media/thumbnail links.

Background: the extension resolves external image-host thumbnails (e.g.
imx.to) via the Imagus chain link + url(:imgContinue=) + res. The desktop
app must mirror that; otherwise the external transition links are dropped by
stay-in-domain checks and only low-res thumbnails (if any) get downloaded.
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.site_pattern_manager import SitePatternManager
from src.parser.priority_url_queue import PriorityURLQueue
from src.parser.parser_manager import ParserManager
from src import constants as K

SIEVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Imagus_sieve_2026.04.01_849.json")
IMX_LINK = "https://imx.to/i/6xt7ux"
IMX_THUMB = "https://image.imx.to/u/t/2026/08/02/6xt7ux.jpg"
IMX_FULL = "https://image.imx.to/u/i/2026/08/02/6xt7ux.jpg"


class TestSieveLinkChain(unittest.TestCase):
    """SitePatternManager: link rule matching + url transform + res extraction."""

    @classmethod
    def setUpClass(cls):
        cls.pm = SitePatternManager(enable_built_in=False, imagus_sieve_path=SIEVE)

    def test_get_link_rule_matches_imx(self):
        rule, m = self.pm.get_link_rule(IMX_LINK)
        self.assertIsNotNone(rule)
        self.assertIn("imx", rule.get("link", "").lower())

    def test_get_link_rule_returns_none_for_unmatched(self):
        # Empty/invalid inputs never match any rule.
        self.assertIsNone(self.pm.get_link_rule(""))
        self.assertIsNone(self.pm.get_link_rule(None))

    def test_apply_url_transform_imx_post(self):
        rule, m = self.pm.get_link_rule(IMX_LINK)
        res = self.pm.apply_link_url_transform(rule, m)
        self.assertIsNotNone(res)
        fetch_url, post_data = res
        self.assertEqual(fetch_url, IMX_LINK)
        self.assertEqual(post_data, "imgContinue=")

    def test_extract_res_urls(self):
        rule, m = self.pm.get_link_rule(IMX_LINK)
        html = ('<html><body><div><img class="centred" '
                'src="%s" alt=""/></div></body></html>' % IMX_FULL)
        urls = self.pm.extract_res_urls(rule, html)
        self.assertEqual(urls, [IMX_FULL])

    def test_extract_res_fallback_img_scan(self):
        rule, m = self.pm.get_link_rule(IMX_LINK)
        html = '<img src="%s" alt=""/>' % IMX_FULL
        urls = self.pm.extract_res_urls(rule, html)
        self.assertIn(IMX_FULL, urls)


class TestDomainExemptions(unittest.TestCase):
    """stay-in-domain must not restrict media lookups / thumbnail transitions."""

    def setUp(self):
        self.queue = PriorityURLQueue(settings={K.SETTING_STAY_IN_DOMAIN: True})

    def test_from_image_external_allowed(self):
        self.assertTrue(self.queue._is_downward_url(
            IMX_LINK, "https://vipergirls.to/threads/123", {"from_image": True}))

    def test_direct_media_allowed(self):
        self.assertTrue(self.queue._is_downward_url(
            IMX_THUMB, "https://vipergirls.to/threads/123", {}))

    def test_plain_cross_domain_still_rejected(self):
        self.assertFalse(self.queue._is_downward_url(
            "https://other-site.com/page", "https://vipergirls.to/threads/123", {}))


class MockGUILogHandler:
    def __init__(self, *args, **kwargs): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass
    def debug(self, msg): pass


class TestDiscoveryIntegration(unittest.TestCase):
    """ParserManager._discover_linked_fullsize end-to-end with a fake session."""

    class FakeResp:
        def __init__(self, html=None, ct="text/html", url=None):
            self._html = html
            self._ct = ct
            self.url = url

        @property
        def headers(self):
            return {"Content-Type": self._ct}

        async def text(self):
            return self._html

    def _make_manager(self):
        settings = {
            K.SETTING_STAY_IN_DOMAIN: True,
            K.SETTING_USE_PATTERNS: True,
            K.SETTING_STOP_WORDS: [],
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            pm = ParserManager(
                url="https://vipergirls.to/threads/123",
                download_path="x",
                settings=settings,
                log_handler=MockGUILogHandler(),
            )
        pm.pattern_manager = SitePatternManager(enable_built_in=False, imagus_sieve_path=SIEVE)
        return pm

    def test_discover_resolves_fullsize_and_drops_thumbnail(self):
        pm = self._make_manager()
        session = AsyncMock()
        session.post.return_value = self.FakeResp(
            html='<img class="centred" src="%s" alt=""/>' % IMX_FULL)

        async def run():
            return await pm._discover_linked_fullsize(
                [(IMX_LINK, {"from_image": True, "thumbnail_url": IMX_THUMB})],
                "https://vipergirls.to/threads/123", session)

        discovered, resolved, consumed = asyncio.run(run())

        self.assertEqual([m[1] for m in discovered], [IMX_FULL])
        self.assertEqual(resolved, {IMX_THUMB})
        self.assertEqual(consumed, {IMX_LINK})
        # POST with imgContinue= form body (imx.to chain)
        kwargs = session.post.call_args.kwargs
        self.assertEqual(kwargs["data"], "imgContinue=")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/x-www-form-urlencoded")

    def test_discover_link_direct_image_response(self):
        pm = self._make_manager()
        session = AsyncMock()
        session.post.return_value = self.FakeResp(ct="image/jpeg", url=IMX_FULL)

        async def run():
            return await pm._discover_linked_fullsize(
                [(IMX_LINK, {"from_image": True, "thumbnail_url": IMX_THUMB})],
                "https://vipergirls.to/threads/123", session)

        discovered, resolved, consumed = asyncio.run(run())
        self.assertEqual(discovered[0][1], IMX_FULL)
        self.assertEqual(discovered[0][0], "image")
        self.assertEqual(resolved, {IMX_THUMB})

    def test_unmatched_link_not_consumed(self):
        pm = self._make_manager()
        session = AsyncMock()

        async def run():
            return await pm._discover_linked_fullsize(
                [("https://example.com/somepage", {"from_image": True, "thumbnail_url": "x"})],
                "https://vipergirls.to/threads/123", session)

        discovered, resolved, consumed = asyncio.run(run())
        self.assertEqual(discovered, [])
        self.assertEqual(resolved, set())
        self.assertEqual(consumed, set())
        session.post.assert_not_called()


class TestProcessResultsDiscoveryWiring(unittest.TestCase):
    """Manager-level: discovery drops resolved thumbnails, adds fullsize,
    and consumed links are not queued for crawling."""

    class FakeResp:
        def __init__(self, html=None, ct="text/html"):
            self._html = html
            self._ct = ct

        @property
        def headers(self):
            return {"Content-Type": self._ct}

        async def text(self):
            return self._html

    def test_thumbnail_drop_and_consumed_skip(self):
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
        pm.download_queue = asyncio.Queue()
        pm._processed_lock = asyncio.Lock()
        pm.url_queue.put = AsyncMock()

        session = AsyncMock()
        session.post.return_value = self.FakeResp(
            html='<img class="centred" src="%s" alt=""/>' % IMX_FULL)

        links_data = {
            IMX_LINK: {"from_image": True, "thumbnail_url": IMX_THUMB},
            "https://other-site.com/page": {"text": "regular"},
        }
        media_files = [("image", IMX_THUMB, {"source": "img"})]

        asyncio.run(pm._process_parser_results(
            url="https://vipergirls.to/threads/123", depth=0,
            links_data=links_data, media_files=media_files,
            original_url_context={"start_url": "https://vipergirls.to/threads/123"},
            session=session))

        # Consumed imx link must NOT be queued for crawling.
        queued = [call[0][0] for call in pm.url_queue.put.call_args_list]
        self.assertNotIn(IMX_LINK, queued)
        # Regular external link dropped by stay-in-domain.
        self.assertNotIn("https://other-site.com/page", queued)

        # Download queue must contain the fullsize, NOT the thumbnail.
        drained = []
        while not pm.download_queue.empty():
            drained.append(pm.download_queue.get_nowait())
        urls_in_queue = [d["url"] for d in drained]
        self.assertIn(IMX_FULL, urls_in_queue)
        self.assertNotIn(IMX_THUMB, urls_in_queue)

    def test_from_image_links_not_capped_by_excavation_limit(self):
        """Media lookups bypass max_links_per_page; only excavation is capped."""
        settings = {
            K.SETTING_STAY_IN_DOMAIN: True,
            K.SETTING_USE_PATTERNS: False,
            K.SETTING_STOP_WORDS: [],
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
            "max_links_per_page": 2,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            pm = ParserManager(
                url="https://vipergirls.to/threads/123", download_path="x",
                settings=settings, log_handler=MockGUILogHandler())
        pm.url_queue.put = AsyncMock()

        links_data = {}
        for i in range(3):  # 3 media lookups (external, stay-in-domain exempt)
            links_data[f"https://imx.to/i/code{i}"] = {
                "from_image": True, "thumbnail_url": f"https://image.imx.to/u/t/x{i}.jpg"}
        for i in range(3):  # 3 excavation links (capped to 2)
            links_data[f"https://vipergirls.to/threads/other{i}"] = {"text": "x"}

        asyncio.run(pm._process_parser_results(
            url="https://vipergirls.to/threads/123", depth=0,
            links_data=links_data, media_files=[],
            original_url_context={"start_url": "https://vipergirls.to/threads/123"},
            session=None))

        queued = [call[0][0] for call in pm.url_queue.put.call_args_list]
        for i in range(3):
            self.assertIn(f"https://imx.to/i/code{i}", queued)  # all lookups kept
        excavation_queued = [u for u in queued if "vipergirls.to/threads/other" in u]
        self.assertEqual(len(excavation_queued), 2)  # excavation capped

    def test_same_domain_from_image_not_discovered(self):
        """Same-domain thumbnail links keep the normal crawl path."""
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
        pm.url_queue.put = AsyncMock()
        session = AsyncMock()

        same_domain_link = "https://vipergirls.to/album/456"
        links_data = {same_domain_link: {"from_image": True, "thumbnail_url": IMX_THUMB}}
        asyncio.run(pm._process_parser_results(
            url="https://vipergirls.to/threads/123", depth=0,
            links_data=links_data, media_files=[],
            original_url_context={"start_url": "https://vipergirls.to/threads/123"},
            session=session))
        queued = [call[0][0] for call in pm.url_queue.put.call_args_list]
        self.assertIn(same_domain_link, queued)
        session.post.assert_not_called()
        session.get.assert_not_called()


class TestProcessResultsDomainFiltering(unittest.TestCase):
    """from_image transitions pass stay_in_domain; plain links still filtered."""

    def setUp(self):
        self.settings = {
            K.SETTING_STAY_IN_DOMAIN: True,
            K.SETTING_USE_PATTERNS: False,  # No discovery in this test
            K.SETTING_STOP_WORDS: [],
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            self.pm = ParserManager(
                url="https://vipergirls.to/threads/123",
                download_path="x",
                settings=self.settings,
                log_handler=MockGUILogHandler(),
            )
        self.pm.url_queue.put = AsyncMock()

    def test_from_image_external_queued_plain_external_dropped(self):
        links_data = {
            IMX_LINK: {"from_image": True, "thumbnail_url": IMX_THUMB},
            "https://other-site.com/page": {"text": "regular"},
        }
        asyncio.run(self.pm._process_parser_results(
            url="https://vipergirls.to/threads/123", depth=0,
            links_data=links_data, media_files=[],
            original_url_context={"start_url": "https://vipergirls.to/threads/123"},
        ))
        queued = [call[0][0] for call in self.pm.url_queue.put.call_args_list]
        self.assertIn(IMX_LINK, queued)
        self.assertNotIn("https://other-site.com/page", queued)


if __name__ == "__main__":
    unittest.main()
