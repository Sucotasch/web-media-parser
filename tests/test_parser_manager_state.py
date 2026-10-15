#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""F-13 (2026-10-15): ParserManager session-state round trip.

Before this module, `save_state` had zero test coverage and every `load_state`
mention in the suite was an `AsyncMock` stand-in (VERIFY_2026-10-15 §3.5/F-13).
Pause / close-window persistence — the feature the whole pause-vs-stop design
rests on — was therefore only ever verified by inspection, while stages 2-7 of
FIXPLAN_2026-10-15 rewrite the queues and the download path that this pickle
serializes.

What is asserted here:
  * a full save → load round trip restores sets, stats, domain health, the
    pending download/quarantine items and the queued URLs;
  * the write is atomic (no `*.tmp` left next to `last_session.pkl`);
  * a missing or corrupt state file is a silent no-op (never raises into the
    task thread);
  * state is scoped per task id (task B must not resume task A);
  * the payload still carries the keys the loader reads.
"""

import asyncio
import os
import pickle
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.app_paths import task_state_path
from src import constants as K
from src.parser.parser_manager import ParserManager
from src.parser import parser_manager as parser_manager_module

from helpers import MockGUILogHandler

PAGE = "https://site.com/album"
NEXT_PAGE = "https://site.com/page/2"
PENDING = ("https://cdn.example.com/pending.jpg", PAGE, {"media_type": "image"})
QUARANTINED = ("https://cdn.example.com/quarantined.jpg", PAGE, {})
DONE = "https://cdn.example.com/done.jpg"

# Keys ParserManager.load_state reads back; a rename here silently drops resume
# data instead of failing loudly.
STATE_KEYS = (
    "url_queue_items", "download_queue_items", "quarantine_queue_items",
    "processed_urls", "downloaded_files", "pages_with_downloads", "stats",
    "settings", "start_url", "download_path", "domain_health",
    "quarantined_domains",
    # A-03: the page/download in flight when the user hit Pause.
    "inflight_url_item", "inflight_media",
)


def _make_manager(task_id="task-1", download_path="x"):
    settings = {
        K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
        K.SETTING_STAY_IN_DOMAIN: False,
        K.SETTING_STOP_WORDS: [],
        K.SETTING_USE_PATTERNS: False,
    }
    with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()):
        return ParserManager(
            url=PAGE, download_path=download_path, settings=settings,
            log_handler=MockGUILogHandler(), task_id=task_id,
        )


def _prepare_loop_state(pm):
    """Create the asyncio primitives start_parsing() would have created.

    download_queue / quarantine_queue / _processed_lock are deliberately built
    inside the event-loop thread in production; these tests run their own loop
    through asyncio.run(), so they stand them up here (same shape).
    """
    pm.download_queue = asyncio.Queue()
    pm.quarantine_queue = asyncio.Queue()
    pm._processed_lock = asyncio.Lock()
    pm.url_queue.reset_async_primitives()


class TestStateRoundTrip(unittest.TestCase):

    def _tmp(self):
        path = tempfile.mkdtemp(prefix="wmp_state_")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def _save_full_state(self, download_path):
        """Save a state with every field populated (one full task run)."""

        async def run():
            pm = _make_manager(download_path=download_path)
            _prepare_loop_state(pm)
            await pm.url_queue.put(NEXT_PAGE, 1, PAGE, {"from_image": True},
                                   bypass_checks=True)
            await pm.download_queue.put(PENDING)
            await pm.quarantine_queue.put(QUARANTINED)
            pm.processed_urls.add(PAGE)
            pm.downloaded_files.add(DONE)
            pm._pages_with_downloads.add(PAGE)
            pm.stats["pages_processed"] = 3
            pm.stats["files_downloaded"] = 1
            pm.domain_health["cdn.example.com"] = {"successes": 2, "failures": 1}
            pm.quarantined_domains.add("bad.example")
            await pm.save_state(download_path)

        asyncio.run(run())

    def test_save_load_roundtrip_preserves_in_flight_state(self):
        download_path = os.path.join(self._tmp(), "album")
        self._save_full_state(download_path)

        state_file = task_state_path(download_path, "task-1")
        self.assertTrue(os.path.exists(state_file), state_file)
        self.assertFalse(os.path.exists(state_file + ".tmp"),
                         "atomic save left its temp file behind")

        async def run():
            pm = _make_manager(download_path=download_path)
            _prepare_loop_state(pm)
            await pm.load_state(download_path)
            return pm

        fresh = asyncio.run(run())

        self.assertEqual(fresh.processed_urls, {PAGE})
        self.assertEqual(fresh.downloaded_files, {DONE})
        self.assertEqual(fresh._pages_with_downloads, {PAGE})
        self.assertEqual(fresh.stats["pages_processed"], 3)
        self.assertEqual(fresh.stats["files_downloaded"], 1)
        self.assertEqual(fresh.domain_health,
                         {"cdn.example.com": {"successes": 2, "failures": 1}})
        self.assertEqual(fresh.quarantined_domains, {"bad.example"})
        self.assertEqual(fresh.start_url, PAGE)

        restored_urls = [
            (item.url, item.depth, item.source_url) if hasattr(item, "url") else item
            for item in fresh.url_queue._queue
        ]
        self.assertIn((NEXT_PAGE, 1, PAGE), restored_urls)

        self.assertEqual(fresh.download_queue.qsize(), 1)
        self.assertEqual(list(fresh.download_queue._queue), [PENDING])
        self.assertEqual(fresh.quarantine_queue.qsize(), 1)
        self.assertEqual(list(fresh.quarantine_queue._queue), [QUARANTINED])

    def test_payload_keeps_the_keys_the_loader_reads(self):
        download_path = os.path.join(self._tmp(), "album")
        self._save_full_state(download_path)

        with open(task_state_path(download_path, "task-1"), "rb") as f:
            state = pickle.load(f)
        for key in STATE_KEYS:
            self.assertIn(key, state,
                          f"state key '{key}' disappeared — resume would lose it")

    def test_state_path_is_scoped_per_task_id(self):
        download_path = os.path.join(self._tmp(), "album")
        self._save_full_state(download_path)

        async def run():
            other = _make_manager(task_id="task-2", download_path=download_path)
            _prepare_loop_state(other)
            await other.load_state(download_path)
            return other

        other = asyncio.run(run())
        self.assertEqual(other.processed_urls, set(),
                         "task-2 resumed task-1's state")
        self.assertEqual(other.download_queue.qsize(), 0)
        self.assertNotEqual(task_state_path(download_path, "task-1"),
                            task_state_path(download_path, "task-2"))


class TestStateLoadRobustness(unittest.TestCase):
    """A resume that cannot read its state must never fail the task thread."""

    def _tmp(self):
        path = tempfile.mkdtemp(prefix="wmp_state_")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def _load(self, download_path):
        async def run():
            pm = _make_manager(download_path=download_path)
            _prepare_loop_state(pm)
            await pm.load_state(download_path)  # must never raise
            return pm

        return asyncio.run(run())

    def test_missing_state_file_is_a_noop(self):
        download_path = os.path.join(self._tmp(), "album")
        pm = self._load(download_path)
        self.assertEqual(pm.processed_urls, set())
        self.assertEqual(pm.download_queue.qsize(), 0)

    def test_corrupt_state_file_is_swallowed(self):
        download_path = os.path.join(self._tmp(), "album")
        with open(task_state_path(download_path, "task-1"), "wb") as f:
            f.write(b"not a pickle at all")

        pm = self._load(download_path)
        self.assertEqual(pm.processed_urls, set())
        self.assertEqual(pm.download_queue.qsize(), 0)
        self.assertEqual(pm.quarantine_queue.qsize(), 0)
class TestInflightRestore(unittest.TestCase):
    """A-03: the page (and download) in flight at Pause survives the snapshot.

    An item taken out of a queue lives in no queue's storage, so it used to be
    absent from the pickle: pausing while a page was being parsed or a file was
    being downloaded lost that work silently — resume never re-parsed the page
    and never re-downloaded the file.
    """

    IN_FLIGHT_PAGE = ("https://site.com/album/2", 2, PAGE, {"source_url": PAGE})
    IN_FLIGHT_DOWNLOAD = {
        "url": "https://cdn.example.com/inflight.jpg",
        "filepath": "album/inflight.jpg",
        "media_type": "image",
        "source_url": PAGE,
    }

    def _tmp(self):
        path = tempfile.mkdtemp(prefix="wmp_state_")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def _round_trip(self, download_path):
        async def save():
            pm = _make_manager(download_path=download_path)
            _prepare_loop_state(pm)
            pm._inflight_url_item = self.IN_FLIGHT_PAGE
            pm._inflight_media = self.IN_FLIGHT_DOWNLOAD
            await pm.save_state(download_path)

        asyncio.run(save())

        async def load():
            fresh = _make_manager(download_path=download_path)
            _prepare_loop_state(fresh)
            await fresh.load_state(download_path)
            return fresh

        return asyncio.run(load())

    def test_inflight_page_and_download_are_requeued_on_resume(self):
        download_path = os.path.join(self._tmp(), "album")
        fresh = self._round_trip(download_path)

        queued = [(item.url, item.depth) for item in fresh.url_queue._queue]
        self.assertEqual(queued, [(self.IN_FLIGHT_PAGE[0], self.IN_FLIGHT_PAGE[1])],
                         "the interrupted page was not re-queued on resume")
        self.assertEqual(list(fresh.download_queue._queue), [self.IN_FLIGHT_DOWNLOAD],
                         "the interrupted download was not re-queued on resume")

    def test_a_finished_task_restores_empty_queues(self):
        """No in-flight item must not become a `None` queue entry."""
        download_path = os.path.join(self._tmp(), "album")

        async def save():
            pm = _make_manager(download_path=download_path)
            _prepare_loop_state(pm)
            await pm.save_state(download_path)

        asyncio.run(save())

        async def load():
            fresh = _make_manager(download_path=download_path)
            _prepare_loop_state(fresh)
            await fresh.load_state(download_path)
            return fresh

        fresh = asyncio.run(load())
        self.assertEqual(fresh.url_queue._queue, [])
        self.assertEqual(fresh.download_queue.qsize(), 0)


class TestStateSnapshot(unittest.TestCase):
    """B-01: the payload must not hold live references to worker-mutated state.

    `save_state` builds the state dict under `_processed_lock` but pickles it
    later, in an executor thread, after the lock is released. Storing `stats` /
    `domain_health` by reference meant a page finishing in that window was
    serialised into the snapshot — or blew up with "dictionary changed size
    during iteration" and lost the whole save.
    """

    def _tmp(self):
        path = tempfile.mkdtemp(prefix="wmp_state_")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_stats_and_domain_health_are_copied_before_pickling(self):
        download_path = os.path.join(self._tmp(), "album")
        pm = _make_manager(download_path=download_path)
        real_dumps = parser_manager_module.pickle.dumps

        def spy(obj, *args, **kwargs):
            # A worker mutating the live containers at the exact moment the
            # payload is serialised. With a live reference these writes land IN
            # the payload; a copy keeps the value observed under the lock.
            pm.stats["pages_processed"] = 999
            pm.domain_health["cdn.example.com"]["failures"] = 99
            return real_dumps(obj, *args, **kwargs)

        async def run():
            _prepare_loop_state(pm)
            pm.stats["pages_processed"] = 3
            pm.domain_health["cdn.example.com"] = {"failures": 1, "total": 2}
            with patch.object(parser_manager_module.pickle, "dumps", spy):
                await pm.save_state(download_path)

        asyncio.run(run())

        with open(task_state_path(download_path, "task-1"), "rb") as f:
            state = pickle.load(f)
        self.assertEqual(state["stats"]["pages_processed"], 3,
                         "the pickled stats kept a live reference to self.stats")
        self.assertEqual(state["domain_health"]["cdn.example.com"]["failures"], 1,
                         "the pickled domain_health kept a live reference")
        # Control: the mutation really happened, so the assertions above are
        # not describing a no-op.
        self.assertEqual(pm.stats["pages_processed"], 999)
        self.assertEqual(pm.domain_health["cdn.example.com"]["failures"], 99)
