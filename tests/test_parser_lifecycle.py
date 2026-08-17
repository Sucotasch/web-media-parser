#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for parser lifecycle fixes (Phase 1):

CORE-10: an exception while processing a page counts it as processed, so the
completion monitor (which requires pages_processed > 0) can still trigger
natural finish. Before the fix, an exception on the only seeded URL left the
task hanging forever: queues empty, pages_processed == 0, monitor kept
skipping.

CORE-11: _main_task emits task_ended("failed") even when the failure happens
BEFORE the parser workers start (state load / seeding / shared session
creation). Before the fix such an exception escaped _main_task without any
signal, so the GUI kept showing a zombie "running" task forever.
"""

import asyncio
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.parser_manager import ParserManager
from src.parser.priority_url_queue import PriorityURLQueue
from src import constants as K

from helpers import MockGUILogHandler


def _make_manager(url="https://site.com/album"):
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


class TestFailedPageCountsAsProcessed(unittest.TestCase):
    """CORE-10: a page that throws is counted as processed (no hang)."""

    def test_worker_exception_increments_pages_processed(self):
        pm = _make_manager()
        broken = "https://site.com/broken"

        async def run():
            pm.is_running = True
            pm._stop_event = asyncio.Event()
            pm._pause_event = asyncio.Event()
            pm._pause_event.set()
            pm.download_queue = asyncio.Queue()
            pm.quarantine_queue = asyncio.Queue()
            pm.url_queue = PriorityURLQueue(settings=pm.settings)
            pm.url_queue.reset_async_primitives()
            pm._processed_lock = asyncio.Lock()
            # Normally created in start_parsing(); the test drives the worker
            # directly, so provide the per-domain semaphore registry manually.
            pm._domain_semaphores = {}
            # bypass_checks=True: skip the priority/relationship gate (same as
            # state restoration) — the queue must not drop our seed URL
            # because a bare path isn't "downward" from itself.
            await pm.url_queue.put(
                broken, 0, broken,
                {"is_start_url": True, "start_url": broken},
                bypass_checks=True,
            )

            async def boom(*args, **kwargs):
                pm._stop_event.set()  # end the worker loop after one page
                raise RuntimeError("network failure")

            pm._invoke_parser = boom
            pm._handle_empty_queues_and_quarantine = AsyncMock(return_value=True)
            await pm._parser_worker(session=AsyncMock())

        asyncio.run(run())

        self.assertEqual(pm.stats["pages_processed"], 1)
        self.assertIn(broken, pm.processed_urls)


class TestMonitorThreadStopsOnFinish(unittest.TestCase):
    """MONITOR-LEAK: _main_task.finally must stop the progress monitor thread
    even on natural completion / early crash — stop_parsing() is the only other
    place that clears is_running/_thread_stop_event."""

    def test_finally_stops_monitor_on_early_crash(self):
        pm = _make_manager()
        pm._thread_stop_event = threading.Event()
        pm.is_running = True
        emitted = []
        pm.task_ended.connect(lambda task_id, reason: emitted.append(reason))

        async def run():
            pm.load_state = AsyncMock(side_effect=RuntimeError("boom"))
            await pm._main_task()

        asyncio.run(run())
        self.assertFalse(pm.is_running)
        self.assertTrue(pm._thread_stop_event.is_set())


class TestScheduleOnLoop(unittest.TestCase):
    """CORE-12: call_soon_threadsafe must never raise out of GUI callbacks
    (loop can close between the is_closed() check and the call)."""

    def test_falls_back_when_loop_none(self):
        pm = _make_manager()
        pm.loop = None
        calls = []
        pm._schedule_on_loop(lambda: calls.append(1))
        self.assertEqual(calls, [1])

    def test_falls_back_when_call_raises_runtime_error(self):
        pm = _make_manager()
        fake_loop = MagicMock()
        fake_loop.is_closed.return_value = False
        fake_loop.call_soon_threadsafe.side_effect = RuntimeError("Event loop is closed")
        pm.loop = fake_loop
        calls = []
        pm._schedule_on_loop(lambda: calls.append(2))
        self.assertEqual(calls, [2])

    def test_routes_to_loop_when_open(self):
        pm = _make_manager()
        fake_loop = MagicMock()
        fake_loop.is_closed.return_value = False
        pm.loop = fake_loop
        cb = lambda: None
        pm._schedule_on_loop(cb)
        fake_loop.call_soon_threadsafe.assert_called_once_with(cb)


class TestMainTaskEmitsFailedOnPreCrash(unittest.TestCase):
    """CORE-11: crash before workers start still emits task_ended('failed')."""

    def test_state_load_crash_emits_failed(self):
        pm = _make_manager()
        emitted = []
        pm.task_ended.connect(lambda task_id, reason: emitted.append(reason))

        async def run():
            pm.load_state = AsyncMock(side_effect=RuntimeError("state corrupt"))
            await pm._main_task()

        asyncio.run(run())
        self.assertEqual(emitted, ["failed"])

    def test_shared_session_crash_emits_failed(self):
        pm = _make_manager()
        emitted = []
        pm.task_ended.connect(lambda task_id, reason: emitted.append(reason))

        async def run():
            pm.load_state = AsyncMock()
            with patch(
                "src.parser.parser_manager.create_shared_downloader_session",
                side_effect=RuntimeError("no session"),
            ):
                await pm._main_task()

        asyncio.run(run())
        self.assertEqual(emitted, ["failed"])


if __name__ == "__main__":
    unittest.main()
