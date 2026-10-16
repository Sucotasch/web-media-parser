#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""P4 (Errors.txt 2): chunked synchronisation of excavation with downloads.

Measured growth (my own runs, 2026-10-16) — the reason this gate exists:

  reactor.cc, 67 s : found +6.0/s  vs downloaded +2.8/s -> backlog +181
  reactor.cc, 64 s : found +15.9/s vs downloaded +2.6/s -> backlog 113 -> 936
  fixture,    149 s: found +22.6/s vs downloaded +7.5/s -> backlog 57 -> 2303

Page excavation creates work (links to follow, media to download) faster than the
downloader can consume it, so requests, queue entries and RSS all grew for as long
as the run lasted. The gate stops STARTING pages while the download backlog is at
or above the high watermark and resumes once it has drained to the low one.

Why the gate is on the DOWNLOAD queue and not on the URL frontier: pending URLs
only shrink when a parser worker pops one, and those are exactly the workers a
frontier watermark would suspend — the crawl would freeze with no drain left.
Downloader workers drain their own queue, so this gate always releases.
"""

import asyncio
import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import constants as K
from src.parser.parser_manager import ParserManager

from helpers import MockGUILogHandler


def run(coro):
    return asyncio.run(coro)


def make_manager(**overrides):
    settings = {
        K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
        K.SETTING_STAY_IN_DOMAIN: False,
        K.SETTING_STOP_WORDS: [],
        K.SETTING_USE_PATTERNS: False,
    }
    settings.update(overrides)
    with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
        pm = ParserManager(
            url="https://page.example/gallery", download_path="x",
            settings=settings, log_handler=MockGUILogHandler(),
        )
    pm.download_queue = asyncio.Queue()
    pm._stop_event = asyncio.Event()
    return pm


class TestBacklogGate(unittest.TestCase):
    """The gate itself: engage at HWM, release at LWM, always release."""

    def test_disabled_gate_never_holds(self):
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 0})
        for _ in range(5000):
            pm.download_queue.put_nowait("f")

        started = time.time()
        run(pm._await_download_capacity())
        self.assertLess(time.time() - started, 0.3,
                        "a disabled gate (0) must not delay excavation at all")
        self.assertEqual(pm.backlog_pauses, 0)

    def test_below_high_watermark_is_not_gated(self):
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 10,
                             K.SETTING_CRAWL_BACKLOG_LWM: 2})
        for _ in range(9):
            pm.download_queue.put_nowait("f")

        started = time.time()
        run(pm._await_download_capacity())
        self.assertLess(time.time() - started, 0.3, "9 < 10 must not pause the crawl")

    def test_gate_holds_at_high_watermark_and_releases_at_low(self):
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 10,
                             K.SETTING_CRAWL_BACKLOG_LWM: 2})
        for _ in range(10):
            pm.download_queue.put_nowait("f")

        async def go():
            task = asyncio.create_task(pm._await_download_capacity())
            await asyncio.sleep(1.0)
            held = not task.done()
            # drain to just above the low watermark: still no release
            for _ in range(7):
                pm.download_queue.get_nowait()
            await asyncio.sleep(1.0)
            held_above_lwm = not task.done()
            # drain to the low watermark: release
            for _ in range(1):
                pm.download_queue.get_nowait()
            await asyncio.wait_for(task, timeout=5.0)
            return held, held_above_lwm

        held, held_above_lwm = run(go())
        self.assertTrue(held, "the gate did not engage at the high watermark")
        self.assertTrue(held_above_lwm,
                        "the gate released above the low watermark (chunk too small)")
        self.assertEqual(pm.backlog_pauses, 1)
        self.assertGreater(pm.backlog_pause_seconds, 1.5,
                           "time spent gated must be recorded")

    def test_stop_releases_the_gate(self):
        """Stop must not wait for the backlog to drain."""
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 5,
                             K.SETTING_CRAWL_BACKLOG_LWM: 1})
        for _ in range(50):
            pm.download_queue.put_nowait("f")

        async def go():
            task = asyncio.create_task(pm._await_download_capacity())
            await asyncio.sleep(0.6)
            pm._stop_event.set()
            await asyncio.wait_for(task, timeout=3.0)

        run(go())  # raises TimeoutError if the gate ignores stop

    def test_stall_valve_resumes_a_jammed_backlog(self):
        """A hung downloader must not freeze the crawl forever."""
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 5,
                             K.SETTING_CRAWL_BACKLOG_LWM: 1})
        for _ in range(50):
            pm.download_queue.put_nowait("f")

        with patch.object(K, "CRAWL_BACKLOG_STALL_TIMEOUT", 1.5):
            started = time.time()
            run(asyncio.wait_for(pm._await_download_capacity(), timeout=10.0))
            elapsed = time.time() - started

        self.assertGreaterEqual(elapsed, 1.0, "the valve fired before the stall timeout")
        self.assertLess(elapsed, 8.0, "the valve never fired — the crawl would hang here")

    def test_low_watermark_at_or_above_high_is_sanitised(self):
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 100,
                             K.SETTING_CRAWL_BACKLOG_LWM: 100})
        self.assertEqual(pm.backlog_lwm, 20,
                         "resume >= pause would make the gate release instantly")

    def test_invalid_settings_fall_back_to_defaults(self):
        for bad in (None, "", "abc"):
            pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: bad,
                                 K.SETTING_CRAWL_BACKLOG_LWM: bad})
            self.assertEqual(pm.backlog_hwm, K.DEFAULT_CRAWL_BACKLOG_HWM, bad)
            self.assertEqual(pm.backlog_lwm, K.DEFAULT_CRAWL_BACKLOG_LWM, bad)

    def test_defaults_are_on_and_sane(self):
        pm = make_manager()
        self.assertGreater(pm.backlog_hwm, 0, "the gate must be on by default")
        self.assertLess(pm.backlog_lwm, pm.backlog_hwm)

    def test_missing_download_queue_does_not_break_the_worker(self):
        pm = make_manager(**{K.SETTING_CRAWL_BACKLOG_HWM: 5})
        pm.download_queue = None
        run(pm._await_download_capacity())  # must be a no-op, not a crash


class TestGateIsWiredIntoTheWorker(unittest.TestCase):
    """Source-level: the worker must actually await the gate before popping a URL.

    A correct gate that nothing calls is the defect this guards against (the same
    class of mistake as F-12's dead state and the never-written `_source_url`).
    """

    def test_parser_worker_awaits_the_gate_before_getting_a_url(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "src", "parser", "parser_manager.py")
        with io.open(path, encoding="utf-8") as f:
            source = f.read()
        worker = source[source.index("async def _parser_worker"):]
        body = worker[:worker.index("\n    async def ", 10)]
        self.assertIn("await self._await_download_capacity()", body,
                      "the backlog gate is not awaited by _parser_worker")
        self.assertLess(body.index("await self._await_download_capacity()"),
                        body.index("url_data = await self._get_next_url_to_parse()"),
                        "the gate must run BEFORE the worker takes new work")

    def test_frontier_is_not_used_as_the_gate_signal(self):
        """Regression for the design error: a watermark on the URL frontier deadlocks.

        Pending URLs shrink only when a worker pops one; gating that pop on the
        frontier's own depth leaves nothing able to reduce it.
        """
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "src", "parser", "parser_manager.py")
        with io.open(path, encoding="utf-8") as f:
            source = f.read()
        start = source.index("async def _await_download_capacity")
        body = source[start:source.index("\n    def ", start)]
        self.assertNotIn("url_queue.pending()", body,
                         "the gate is reading the URL frontier depth again — that "
                         "stops the only thing that can drain it")
        self.assertIn("download_queue.qsize()", body)


if __name__ == "__main__":
    unittest.main()
