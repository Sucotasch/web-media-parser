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
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.parser_manager import ParserManager
from src.parser.priority_url_queue import PriorityURLQueue
from src.gui.main_window import (
    apply_extension_payloads, build_extension_task_settings,
    build_extension_task_payload, _folder_name_is_safe, task_folder_from_url,
)
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

        def cb():
            pass

        pm._schedule_on_loop(cb)
        fake_loop.call_soon_threadsafe.assert_called_once_with(cb)


class TestExtensionSettingsIsolation(unittest.TestCase):
    """A-2: extension one-shot overrides must never touch the global settings dict."""

    def test_extension_add_does_not_mutate_global_settings(self):
        base = {"user_agent": "GUI-UA", "page_limit": 1000}
        result = build_extension_task_settings(
            base, user_agent="EXT-UA", cookies="session=abc"
        )
        # Overrides applied to the copy...
        self.assertEqual(result["user_agent"], "EXT-UA")
        self.assertEqual(result["extension_cookies"], "session=abc")
        self.assertEqual(result["page_limit"], 1000)
        # ...and the original is untouched.
        self.assertEqual(base["user_agent"], "GUI-UA")
        self.assertNotIn("extension_cookies", base)

    def test_extension_add_without_overrides_keeps_base_values(self):
        base = {"user_agent": "GUI-UA"}
        result = build_extension_task_settings(base)
        self.assertEqual(result["user_agent"], "GUI-UA")
        self.assertNotIn("extension_cookies", result)
        self.assertIsNot(result, base)


class TestExtensionPayloadBuffering(unittest.TestCase):
    """A-5: payload computation is pure — no FS/queue side effects — so tasks
    can be materialized on the GUI thread."""

    def test_extension_payload_buffered_one_shot(self):
        payload = build_extension_task_payload(
            [{"url": "https://img.example/a.jpg", "type": "image",
              "referer": "https://page.example/gallery"}],
            settings={"user_agent": "UA"},
            download_dir=os.path.join("dl"),
            one_shot=True,
            timestamp="20260101_000000",
        )
        self.assertIsNotNone(payload)
        self.assertTrue(payload["one_shot"])
        self.assertEqual(payload["added"], 1)
        self.assertEqual(payload["task_url"], "https://page.example/gallery")
        self.assertEqual(payload["items"][0]["url"], "https://img.example/a.jpg")
        # Pure function: no side effects beyond the returned dict
        self.assertFalse(os.path.exists(payload["download_path"]))

    def test_extension_payload_buffered_multi(self):
        payload = build_extension_task_payload(
            [{"url": "https://site1.com/page"}, {"url": "https://site2.com/page"}],
            settings={}, download_dir="dl", one_shot=False,
            timestamp="20260101_000000",
        )
        self.assertFalse(payload["one_shot"])
        self.assertEqual(payload["added"], 2)
        self.assertEqual(len(payload["tasks"]), 2)

    def test_extension_payload_rejects_invalid(self):
        self.assertIsNone(build_extension_task_payload([], {}, "dl", True))
        self.assertIsNone(build_extension_task_payload(
            [{"url": "ftp://bad"}], {}, "dl", True))

    def test_extension_payload_folder_is_a_legal_path_one_shot(self):
        """Live s8 finding: a source page carrying a PORT put ':' into the task
        folder (`127_0_0_1:9811_20261015_193650`). On Windows `os.makedirs`
        raised NotADirectoryError (WinError 267) inside the GUI slot, so the
        extension's task was dropped after the HTTP reply had already said
        ok:true — invisible to the popup."""
        payload = build_extension_task_payload(
            [{"url": "http://127.0.0.1:9811/full/photo1.png", "type": "image",
              "referer": "http://127.0.0.1:9811/index.html"}],
            settings={}, download_dir="dl", one_shot=True,
            timestamp="20260101_000000",
        )
        folder = payload["task_folder"]
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, folder, "illegal path character in %r" % folder)
        self.assertIn("127_0_0_1", folder)
        # The GUI thread's first act is to create this folder — that is exactly
        # what failed live, so the test does it too.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, folder))

    def test_extension_payload_folder_is_a_legal_path_batch(self):
        payload = build_extension_task_payload(
            [{"url": "http://127.0.0.1:9811/album"}],
            settings={}, download_dir="dl", one_shot=False,
            timestamp="20260101_000000",
        )
        download_path = payload["tasks"][0][2]
        self.assertNotIn(":", os.path.basename(download_path))
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, os.path.basename(download_path)))


class TestExtensionPayloadIsolation(unittest.TestCase):
    """s8: a payload that fails to materialize must not silently take the rest
    of the drained batch with it (it used to raise out of the Qt slot, leaving
    only a stderr traceback while the extension had been told ok:true)."""

    def test_one_failed_payload_keeps_the_others(self):
        payloads = [{"tag": "a"}, {"tag": "boom"}, {"tag": "c"}]
        applied, errors = [], []

        def apply_one(payload):
            if payload["tag"] == "boom":
                raise NotADirectoryError(267, "not a directory")
            applied.append(payload["tag"])
            return payload["tag"].upper()

        result = apply_extension_payloads(
            payloads, apply_one, lambda p, e: errors.append((p["tag"], type(e))))

        self.assertEqual(applied, ["a", "c"], "the healthy payloads must still run")
        self.assertEqual([p["tag"] for p, _ in result], ["a", "c"])
        self.assertEqual([r for _, r in result], ["A", "C"])
        self.assertEqual(errors, [("boom", NotADirectoryError)])

    def test_a_broken_error_handler_still_does_not_break_the_batch(self):
        def apply_one(payload):
            raise ValueError("materialization failed")

        def on_error(payload, exc):
            raise RuntimeError("the error handler is broken too")

        # Defined here rather than asserted on a mock: the point is only that
        # the batch returns normally and no exception escapes to the caller.
        self.assertEqual(
            apply_extension_payloads([{"tag": "a"}, {"tag": "b"}],
                                     apply_one, on_error),
            [])

    def test_folder_guard_rejects_names_windows_cannot_create(self):
        for bad in ["", "  ", "trailing .", "a|b", 'a:b', "a\\b", "con", "NUL", "lpt1"]:
            self.assertFalse(_folder_name_is_safe(bad), f"accepted {bad!r}")
        for good in ["example_com_20260101_000000", "127_0_0_1_20260101_000000",
                     "console_1", "_x_"]:
            self.assertTrue(_folder_name_is_safe(good), f"rejected {good!r}")

    def test_payload_is_none_when_the_folder_guard_cannot_be_satisfied(self):
        # The sanitizer cannot produce an illegal component today — the guard is
        # a provocation: patch it to return one and the payload must refuse to
        # be built, so the HTTP reply stops claiming ok:true for a task that
        # would vanish at materialization (NF-11).
        with patch("src.gui.main_window._safe_domain_folder", return_value="bad:name"):
            self.assertIsNone(build_extension_task_payload(
                [{"url": "https://img.example/a.jpg", "referer": "https://page.example/g"}],
                {}, "dl", one_shot=True, timestamp="20260101_000000"))
            self.assertIsNone(build_extension_task_payload(
                [{"url": "https://page.example/g"}],
                {}, "dl", one_shot=False, timestamp="20260101_000000"))


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


class TestUrlInputFolderName(unittest.TestCase):
    """The URL field is user text: a netloc with a port / userinfo / IPv6 literal
    used to be pasted into the folder name verbatim, so `os.makedirs` raised
    NotADirectoryError (WinError 267) and Start silently did nothing
    (measured 2026-10-16 — the exception escaped out of start_parsing).

    The folder is built by `task_folder_from_url`, the same
    sanitise-then-verify rule the extension payloads already use, and the name
    is created for real here because creating it is the caller's first act.
    """

    TS = "20261016_120000"

    def _folder_that_creates(self, url):
        folder = task_folder_from_url(url, self.TS)
        self.assertIsNotNone(folder, f"no folder name produced for {url!r}")
        self.assertTrue(_folder_name_is_safe(folder), f"illegal name {folder!r}")
        self.assertNotIn(":", folder)
        self.assertTrue(folder.endswith("_" + self.TS))
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, folder))
        return folder

    def test_addresses_with_a_port_get_a_creatable_folder(self):
        for url in ("http://127.0.0.1:9845/album",
                    "https://localhost:8080/post/1",
                    "http://192.168.1.7:3000/",
                    "http://example.com:8080/a"):
            self._folder_that_creates(url)

    def test_userinfo_and_ipv6_literals_are_neutralised(self):
        self.assertTrue(self._folder_that_creates("http://user:pass@example.com/p")
                        .startswith("example_com_"))
        self._folder_that_creates("http://[::1]:8080/p")

    def test_the_hostname_is_still_the_folder_name(self):
        # Unchanged for the addresses that always worked.
        self.assertEqual(task_folder_from_url("https://reactor.cc/post/6382634", self.TS),
                         f"reactor_cc_{self.TS}")
        self.assertEqual(task_folder_from_url("https://www.sub.example.co.uk/a", self.TS),
                         f"www_sub_example_co_uk_{self.TS}")

    def test_a_name_that_cannot_be_made_legal_is_refused_not_raised(self):
        # The sanitizer cannot produce an illegal component today — this pins the
        # refusal so the caller stops before makedirs instead of starting a task
        # whose folder never appears.
        with patch("src.gui.main_window._safe_domain_folder", return_value="bad:name"):
            self.assertIsNone(task_folder_from_url("https://example.com/a", self.TS))

    def test_a_url_without_a_netloc_falls_back_instead_of_raising(self):
        # "https://" alone reaches here after the scheme is prepended.
        self.assertEqual(task_folder_from_url("https://", self.TS), f"site_{self.TS}")


if __name__ == "__main__":
    unittest.main()
