#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for the crawler-frontier fixes (F1-F4).

Background: the crawler stopped declaring "done" after ~49 pages even though
the site had hundreds of reachable galleries. Root causes, all fixed here:

- F1: PriorityURLQueue.put() had no dedup — every parsed page re-pushed the
      same link set at deeper depths with higher priority, so pages were
      parsed AT max_depth where their links are dropped (dead ends).
- F2: _process_parser_results gated ALL links (including from_image media
      lookups) behind depth < max_depth — galleries reached deep never got
      crawled.
- F3: _is_downward_url required a shared path prefix even within the same
      domain, silently dropping content hubs (/c/tags, /recent, /popular).
- F4: page_limit counted downloaded FILES instead of source pages (the
      settings tooltip promises "stop after downloading files from N pages").
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.priority_url_queue import PriorityURLQueue
from src.parser.parser_manager import ParserManager
from src import constants as K

from helpers import MockGUILogHandler


def run(coro):
    return asyncio.run(coro)


class TestQueueDedup(unittest.TestCase):
    """F1: put() queues each URL once at its shallowest depth."""

    def setUp(self):
        self.q = PriorityURLQueue(settings={})
        self.q.reset_async_primitives()

    def test_same_url_pushed_twice_is_queued_once(self):
        async def go():
            await self.q.put("https://site.com/album/1.html", 1, "https://site.com/", {})
            await self.q.put("https://site.com/album/1.html", 2, "https://site.com/", {})
            return [(i.url, i.depth) for i in self.q._queue]

        self.assertEqual(run(go()), [("https://site.com/album/1.html", 1)])

    def test_shallowest_depth_wins(self):
        async def go():
            await self.q.put("https://site.com/album/1.html", 1, "https://site.com/", {})
            # Deep from_image re-discovery must not displace the shallow entry
            await self.q.put("https://site.com/album/1.html", 3, "https://site.com/",
                             {"from_image": True, "thumbnail_url": "t.jpg"})
            return [i.depth for i in self.q._queue]

        self.assertEqual(run(go()), [1])

    def test_interstitial_retry_exempt_from_dedup(self):
        async def go():
            await self.q.put("https://site.com/media/x.jpg", 1, "https://site.com/", {})
            await self.q.put("https://site.com/media/x.jpg", 1, "https://site.com/",
                             {"interstitial_retry": True})
            return len(self.q._queue)

        # Deliberate re-parse (media->webpage recovery) must still be queued.
        self.assertEqual(run(go()), 2)

    def test_bypass_checks_exempt_from_dedup(self):
        async def go():
            await self.q.put("https://site.com/album/1.html", 1, "https://site.com/", {})
            await self.q.put("https://site.com/album/1.html", 1, "https://site.com/",
                             {}, bypass_checks=True)
            return len(self.q._queue)

        # State restoration must re-queue everything.
        self.assertEqual(run(go()), 2)

    def test_distinct_urls_not_deduped(self):
        async def go():
            await self.q.put("https://site.com/album/1.html", 1, "https://site.com/", {})
            await self.q.put("https://site.com/album/2.html", 1, "https://site.com/", {})
            return len(self.q._queue)

        self.assertEqual(run(go()), 2)


class TestSameDomainRelationship(unittest.TestCase):
    """F3: same-domain links pass the relationship gate regardless of path."""

    def setUp(self):
        self.q = PriorityURLQueue(settings={K.SETTING_STAY_IN_DOMAIN: True})
        self.q.reset_async_primitives()

    def test_tag_section_link_passes_same_domain(self):
        self.assertTrue(self.q._is_downward_url(
            "https://www.pictoa.com/c/brunette-14",
            "https://www.pictoa.com/albums/some-album-123.html", {}))

    def test_listing_hub_passes_same_domain(self):
        self.assertTrue(self.q._is_downward_url(
            "https://www.pictoa.com/recent",
            "https://www.pictoa.com/albums/some-album-123.html", {}))

    def test_cross_domain_still_rejected(self):
        self.assertFalse(self.q._is_downward_url(
            "https://other-site.com/page",
            "https://www.pictoa.com/albums/some-album-123.html", {}))

    def test_from_image_external_still_allowed(self):
        self.assertTrue(self.q._is_downward_url(
            "https://imx.to/i/6xt7ux",
            "https://vipergirls.to/threads/123", {"from_image": True}))


class TestProcessResultsDepthGate(unittest.TestCase):
    """F2: media lookups survive at max_depth; excavation stops there."""

    def _make_manager(self, depth=K.DEFAULT_SEARCH_DEPTH):
        settings = {
            K.SETTING_STAY_IN_DOMAIN: True,
            K.SETTING_USE_PATTERNS: False,
            K.SETTING_STOP_WORDS: [],
            K.SETTING_SEARCH_DEPTH: depth,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            pm = ParserManager(
                url="https://www.pictoa.com/albums/start-1.html",
                download_path="x", settings=settings,
                log_handler=MockGUILogHandler(),
            )
        pm.url_queue.put = AsyncMock()
        return pm

    def test_from_image_links_survive_at_max_depth(self):
        pm = self._make_manager(depth=2)
        links_data = {
            "https://www.pictoa.com/albums/start-1.html/54376303.html": {
                "from_image": True, "thumbnail_url": "https://t1.pictoa.com/t.jpg"},
            "https://www.pictoa.com/albums/other-2.html": {"text": "album"},
            "https://www.pictoa.com/c/brunette-14": {"text": "tag"},
        }
        asyncio.run(pm._process_parser_results(
            url="https://www.pictoa.com/albums/start-1.html", depth=2,
            links_data=links_data, media_files=[],
            original_url_context={"start_url": "https://www.pictoa.com/albums/start-1.html"},
        ))
        queued = [c[0][0] for c in pm.url_queue.put.call_args_list]
        self.assertIn("https://www.pictoa.com/albums/start-1.html/54376303.html", queued)
        self.assertNotIn("https://www.pictoa.com/albums/other-2.html", queued)
        self.assertNotIn("https://www.pictoa.com/c/brunette-14", queued)

    def test_direct_media_url_survives_at_max_depth(self):
        pm = self._make_manager(depth=2)
        links_data = {
            "https://t1.pictoa.com/media/galleries/223/661/full.jpg": {"text": "img link"},
        }
        asyncio.run(pm._process_parser_results(
            url="https://www.pictoa.com/albums/start-1.html", depth=2,
            links_data=links_data, media_files=[],
            original_url_context={"start_url": "https://www.pictoa.com/albums/start-1.html"},
        ))
        queued = [c[0][0] for c in pm.url_queue.put.call_args_list]
        self.assertIn("https://t1.pictoa.com/media/galleries/223/661/full.jpg", queued)

    def test_excavation_still_works_below_max_depth(self):
        pm = self._make_manager(depth=2)
        links_data = {
            "https://www.pictoa.com/albums/start-1.html/54376303.html": {
                "from_image": True, "thumbnail_url": "https://t1.pictoa.com/t.jpg"},
            "https://www.pictoa.com/c/brunette-14": {"text": "tag"},
        }
        asyncio.run(pm._process_parser_results(
            url="https://www.pictoa.com/albums/start-1.html", depth=1,
            links_data=links_data, media_files=[],
            original_url_context={"start_url": "https://www.pictoa.com/albums/start-1.html"},
        ))
        queued = [c[0][0] for c in pm.url_queue.put.call_args_list]
        self.assertIn("https://www.pictoa.com/albums/start-1.html/54376303.html", queued)
        self.assertIn("https://www.pictoa.com/c/brunette-14", queued)


class TestSameGalleryPriority(unittest.TestCase):
    """F5: structural same-gallery boost — the current gallery drains before
    foreign albums/sections.

    Site-agnostic rule: the gallery a page belongs to is a DIRECTORY of item
    pages in its URL tree. Children of the source path (album -> its viewers)
    get the strongest boost; equal-depth siblings sharing the parent
    directory (viewer -> next viewer of the same gallery) get a moderate one.
    """

    def setUp(self):
        self.q = PriorityURLQueue(settings={K.SETTING_STAY_IN_DOMAIN: True})

    def test_child_viewer_beats_foreign_album_viewer(self):
        album = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178"
        own = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178/53000424.html"
        foreign = "https://www.pictoa.com/albums/other-album-3522148/52999127.html"
        p_own = self.q._calculate_url_priority(own, 1, album, {"from_image": True})
        p_foreign = self.q._calculate_url_priority(foreign, 1, album, {"from_image": True})
        self.assertGreater(p_own, p_foreign)

    def test_html_suffix_album_child_still_wins(self):
        # Album URL ends with .html. After extension-normalized comparison its
        # viewer pages are still CHILDREN (own gallery), not generic section
        # siblings like other albums — without the fix both scored identically
        # (only the section segment in common) and the gallery was abandoned.
        album = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178.html"
        own = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178.html/53000424.html"
        foreign = "https://www.pictoa.com/albums/other-album-3522148.html/52999127.html"
        p_own = self.q._calculate_url_priority(own, 1, album, {"from_image": True})
        p_foreign = self.q._calculate_url_priority(foreign, 1, album, {"from_image": True})
        self.assertGreater(p_own, p_foreign)

    def test_next_viewer_sibling_beats_foreign_album(self):
        # From a viewer page, the NEXT viewer of the same gallery (same parent
        # directory) outranks a foreign album's viewer one level away.
        cur = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178/53000424.html"
        nxt = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178/53000425.html"
        foreign = "https://www.pictoa.com/albums/other-album-3522148/52999127.html"
        p_nxt = self.q._calculate_url_priority(nxt, 2, cur, {"from_image": True})
        p_foreign = self.q._calculate_url_priority(foreign, 2, cur, {"from_image": True})
        self.assertGreater(p_nxt, p_foreign)

    def test_own_gallery_beats_tag_section(self):
        album = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178"
        own = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178/53000424.html"
        tag = "https://www.pictoa.com/c/brunette-14"
        p_own = self.q._calculate_url_priority(own, 1, album, {"from_image": True})
        p_tag = self.q._calculate_url_priority(tag, 1, album, {})
        self.assertGreater(p_own, p_tag)

    def test_path_parts_strip_page_extensions(self):
        self.assertEqual(
            PriorityURLQueue._path_parts_for_compare("/albums/name.html"),
            ["albums", "name"],
        )
        self.assertEqual(
            PriorityURLQueue._path_parts_for_compare("/albums/name/53000424.html"),
            ["albums", "name", "53000424"],
        )
        # Non-page extensions (real file paths) are NOT stripped.
        self.assertEqual(
            PriorityURLQueue._path_parts_for_compare("/media/img/photo.jpg"),
            ["media", "img", "photo.jpg"],
        )

    def test_foreign_gallery_sibling_boost_uses_real_source(self):
        """F5 must compare gallery structure against the page the link was
        ACTUALLY found on (context['source_url']), not the effective source
        (put() substitutes start_url into the source_url parameter for
        stay-in-domain enforcement).

        Without this, once the crawler leaves the START gallery, sibling
        viewers of every other album share only the section segment with the
        start URL — the sibling boost never fires, and gallery completion is
        decided by the drift of _domain_scores instead (observed: opus drained
        2 of 12 viewers while femme-sexy, discovered a moment later with a
        marginally higher domain score, drained all 12 first).
        """
        start = "https://www.pictoa.com/albums/start-album-1.html"
        opus_viewer = "https://www.pictoa.com/albums/opus-album-2/54375794.html"
        own_sibling = "https://www.pictoa.com/albums/opus-album-2/54375824.html"
        foreign = "https://www.pictoa.com/albums/sapphire-album-3/54375827.html"
        # source_url param == start (effective source); real page in context.
        ctx_own = {"from_image": True, "start_url": start, "source_url": opus_viewer}
        ctx_foreign = {"from_image": True, "start_url": start, "source_url": opus_viewer}
        p_own = self.q._calculate_url_priority(own_sibling, 2, start, ctx_own)
        p_foreign = self.q._calculate_url_priority(foreign, 2, start, ctx_foreign)
        self.assertGreater(p_own, p_foreign)

    def test_real_source_falls_back_to_effective_source(self):
        """When context['source_url'] is absent (seed URL, state restore) the
        boost falls back to the effective source — same as before."""
        album = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178.html"
        own = "https://www.pictoa.com/albums/alisa-in-angel-a-3522178.html/53000424.html"
        # TST-3: also assert the from_image boost actually raises the priority
        # vs the same URL without it (was only "p > 0").
        boosted = self.q._calculate_url_priority(own, 1, album, {"from_image": True})
        plain = self.q._calculate_url_priority(own, 1, album, {})
        self.assertGreater(boosted, 0)
        self.assertGreater(boosted, plain)


class TestPageLimitSemantics(unittest.TestCase):
    """F4/TST-1: page_limit counts source pages with downloads, not files.

    Exercised through the REAL production workers (downloader worker records
    a source page per successful download; parser worker's gate stops the
    crawl), not by poking _pages_with_downloads directly.
    """

    def _make_manager(self, page_limit=1000):
        import threading
        settings = {
            K.SETTING_STAY_IN_DOMAIN: True,
            K.SETTING_USE_PATTERNS: False,
            K.SETTING_STOP_WORDS: [],
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
            "page_limit": page_limit,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            pm = ParserManager(
                url="https://site.com/album-a", download_path="x",
                settings=settings, log_handler=MockGUILogHandler(),
            )
        # Asyncio primitives that start_parsing() would create on the loop
        pm.download_queue = asyncio.Queue()
        pm.quarantine_queue = asyncio.Queue()
        pm._stop_event = asyncio.Event()
        pm._pause_event = asyncio.Event()
        pm._pause_event.set()
        pm._domain_semaphores = {}
        pm._thread_stop_event = threading.Event()
        pm.url_queue.reset_async_primitives()  # _lock/_not_empty are None until start_parsing
        return pm

    @staticmethod
    def _media_item(url, source):
        return {"url": url, "source_url": source, "media_type": "image",
                "attrs": {}, "filepath": os.path.join("x", "f.jpg")}

    def _run_downloader(self, pm, items):
        """Drive the real _downloader_worker with a stubbed MediaDownloader."""
        async def run():
            for it in items:
                await pm.download_queue.put(it)

            async def stopper():
                while not pm.download_queue.empty():
                    await asyncio.sleep(0.02)
                pm._stop_event.set()
                pm.is_running = False

            pm.is_running = True
            with patch("src.parser.parser_manager.MediaDownloader") as MD:
                MD.return_value.download.return_value = {"success": True}
                await asyncio.gather(pm._downloader_worker(), stopper())
        asyncio.run(run())

    def test_many_files_from_one_page_is_one_source(self):
        pm = self._make_manager(page_limit=2)
        # 50 downloads from the SAME source page — the downloader worker records
        # ONE source page (set semantics), so a 2-page limit is NOT reached.
        items = [self._media_item(f"https://img.example/f{i}.jpg", "https://site.com/album-a")
                 for i in range(50)]
        self._run_downloader(pm, items)
        self.assertEqual(len(pm._pages_with_downloads), 1)
        self.assertIn("https://site.com/album-a", pm._pages_with_downloads)

    def test_distinct_sources_trigger_limit(self):
        pm = self._make_manager(page_limit=2)
        items = [
            self._media_item("https://img.example/a.jpg", "https://site.com/album-a"),
            self._media_item("https://img.example/b.jpg", "https://site.com/album-b"),
        ]
        self._run_downloader(pm, items)
        self.assertEqual(len(pm._pages_with_downloads), 2)
        # Real parser worker: with 2 source pages recorded and limit 2, the
        # gate fires and the worker stops itself.
        pm.is_running = True
        pm._stop_event = asyncio.Event()
        asyncio.run(pm._parser_worker(None))
        self.assertTrue(pm._completed_naturally)
        self.assertTrue(pm._stop_event.is_set())

    def test_zero_page_limit_is_unlimited(self):
        pm = self._make_manager(page_limit=0)
        items = [
            self._media_item("https://img.example/a.jpg", "https://site.com/album-a"),
            self._media_item("https://img.example/b.jpg", "https://site.com/album-b"),
            self._media_item("https://img.example/c.jpg", "https://site.com/album-c"),
        ]
        self._run_downloader(pm, items)
        self.assertEqual(len(pm._pages_with_downloads), 3)

        async def run_gate():
            pm.is_running = True
            pm._stop_event = asyncio.Event()
            task = asyncio.create_task(pm._parser_worker(None))
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        asyncio.run(run_gate())
        # page_limit=0 → the gate never fires; the worker keeps looping.
        self.assertFalse(pm._completed_naturally)
        self.assertFalse(pm._stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
