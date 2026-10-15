#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A-16 / B-02 (FIXPLAN_2026-10-15 §3/Этап 4): blocking work off the event loop.

`engine.run_js` / `run_dom_js` are synchronous and hold a lock for the whole
call (up to the Deno timeout plus terminate escalation), and every call site ran
them ON the event loop: `webpage_parser._get_best_image_url` (via the image
extraction loop) and `parser_manager._discover_linked_fullsize.probe`.

Measured consequence: one rule that hangs (or a gallery of 300 thumbnails whose
cached key misses, `engine.py` includes `groups` in the key) froze the loop for
minutes — the completion monitor, progress signals and Stop all live there, so
the task could not be stopped. The audit measured a 5.02 s freeze for a
`while(true){}` rule; with the offload the loop keeps ticking.

These tests reproduce the mechanism with a blocking stand-in for the JS call and
assert on the event loop's own gap between ticks — the same quantity the audit
measured, without needing a Deno binary (absent on this machine).
"""

import asyncio
import os
import re
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bs4 import BeautifulSoup

from src.parser.parser_manager import ParserManager
from src.parser.webpage_parser import WebpageParser
from src import constants as K

from helpers import MockGUILogHandler

# Stand-in for a hanging JS rule: the real call blocks inside the Deno worker
# for up to K.DEFAULT_CALL_TIMEOUT. Shorter here, same mechanism.
BLOCK_SECONDS = 0.3
# The audit's threshold for "the loop is alive".
MAX_LOOP_GAP = 0.1

NO_MIN_FILTERS = {
    K.SETTING_MIN_IMG_WIDTH: 0,
    K.SETTING_MIN_IMG_HEIGHT: 0,
    K.SETTING_MIN_IMG_SIZE: 0,
}


def measure_max_loop_gap(coro_factory):
    """Run `coro_factory()` while ticking the loop; return (gaps, results)."""
    gaps = []

    async def go():
        last = time.monotonic()

        async def ticker():
            nonlocal last
            while True:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        tick = asyncio.create_task(ticker())
        try:
            result = await coro_factory()
        finally:
            tick.cancel()
        return result

    return asyncio.run(go()), gaps


class BlockingSieveManager:
    """A pattern manager whose sieve JS call hangs (the measured defect)."""

    js_engine = None

    def __init__(self, block=BLOCK_SECONDS):
        self.block = block

    def get_link_rule(self, url):
        return ({"img": r".*", "to": ""}, re.search(r".*", url))

    def apply_link_url_transform(self, rule, match, page_url=""):
        time.sleep(self.block)  # the hanging rule
        return None

    def transform_image_url(self, url, source_url=""):
        time.sleep(self.block)  # the hanging rule
        return [url]


class TestHtmlRouteOffload(unittest.TestCase):
    """A-16: the image-extraction path must not block the loop on a JS rule."""

    def test_blocked_js_rule_does_not_stall_the_event_loop(self):
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="js_engine")
        self.addCleanup(pool.shutdown, False)

        parser = WebpageParser(
            url="https://site.com/gallery", settings=dict(NO_MIN_FILTERS),
            process_js=False, external_session=MagicMock(),
            pattern_manager=BlockingSieveManager(), js_executor=pool,
        )
        soup = BeautifulSoup('<html><body><img src="https://site.com/a.jpg">'
                             '</body></html>', "lxml")

        result, gaps = measure_max_loop_gap(lambda: parser._extract_images(soup))

        self.assertTrue(gaps, "the ticker never ran")
        self.assertLess(max(gaps), MAX_LOOP_GAP,
                        f"the event loop stalled for {max(gaps):.3f}s "
                        f"while a JS rule blocked for {BLOCK_SECONDS}s")


class TestProbeRouteOffload(unittest.TestCase):
    """A-16: the fullsize-discovery probe must not block the loop either."""

    def _manager(self, block=BLOCK_SECONDS):
        settings = {
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
            K.SETTING_STAY_IN_DOMAIN: False,
            K.SETTING_STOP_WORDS: [],
            K.SETTING_USE_PATTERNS: False,
        }
        with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
            pm = ParserManager(
                url="https://page.example/gallery", download_path="x",
                settings=settings, log_handler=MockGUILogHandler(),
            )
        pm.pattern_manager = BlockingSieveManager(block)
        return pm

    def test_blocked_link_rule_does_not_stall_the_event_loop(self):
        pm = self._manager()
        # (link, context) pairs — the shape _process_parser_results passes.
        links = [
            ("https://thumb.host/view/1",
             {"from_image": True, "thumbnail_url": "https://thumb.host/1s.jpg"}),
        ]

        async def run():
            pm._js_executor = ThreadPoolExecutor(max_workers=2,
                                                 thread_name_prefix="js_engine")
            try:
                return await pm._discover_linked_fullsize(links,
                                                          "https://page.example/gallery",
                                                          MagicMock())
            finally:
                pm._js_executor.shutdown(wait=False)

        result, gaps = measure_max_loop_gap(run)

        self.assertTrue(gaps, "the ticker never ran")
        self.assertLess(max(gaps), MAX_LOOP_GAP,
                        f"the event loop stalled for {max(gaps):.3f}s "
                        f"while a link rule blocked for {BLOCK_SECONDS}s")


if __name__ == "__main__":
    unittest.main()
