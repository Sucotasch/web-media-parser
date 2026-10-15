#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""C-08 (2026-10-15): the extension thread reads the queue; the GUI thread writes it.

`/api/status` and `/api/queue` are served from the aiohttp thread while the GUI
thread adds, starts, pauses and removes tasks. Before the fix the manager had no
lock at all and the status callback composed three separate reads
(`active_task`, `len(queue)`, `active.files_downloaded`), so one HTTP response
could mix two different queue states.

What each test below is worth:

- `test_a_mutation_waits_for_an_in_progress_read` is the deterministic one: it
  holds the manager's lock and shows that `add_task` from another thread cannot
  proceed until the read is done (this fails on the unlocked implementation).
- `test_status_requests_during_heavy_queue_churn` runs the real HTTP endpoint
  against a real writer thread and asserts the invariants that must hold for
  every response (this one is a smoke/invariant test, not a proof).
- The source-level checks pin the wiring: the callback must go through
  `snapshot()`, and signals must be emitted after the lock is released.
"""

import asyncio
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.task_queue_manager import TaskQueueManager
from src.core.task_item import TaskStatus
from src.server.http_server import ExtensionServer

MAIN_WINDOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "gui", "main_window.py")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestQueueLock(unittest.TestCase):

    def setUp(self):
        self.tm = TaskQueueManager(base_download_dir=os.path.dirname(__file__))

    def test_a_mutation_waits_for_an_in_progress_read(self):
        """Mutual exclusion, asserted without timing luck."""
        finished = threading.Event()

        def writer():
            self.tm.add_task("https://a.example/1.jpg", {}, "/tmp/out/a")
            finished.set()

        self.tm._lock.acquire()          # stands in for a read in progress
        try:
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            self.assertFalse(
                finished.wait(timeout=0.3),
                "add_task proceeded while a read held the lock — the queue is "
                "still mutated without synchronization")
        finally:
            self.tm._lock.release()
        self.assertTrue(finished.wait(timeout=5),
                        "add_task must proceed once the reader is done")

    def test_snapshot_returns_one_consistent_state(self):
        first = self.tm.add_task("https://a.example/1.jpg", {}, "/tmp/out/a")
        second = self.tm.add_task("https://a.example/2.jpg", {}, "/tmp/out/b")

        snap = self.tm.snapshot()
        self.assertEqual(snap["queue_length"], 2)
        self.assertEqual(sorted(snap["task_ids"]), sorted([first.id, second.id]))
        self.assertIsNone(snap["active_task_id"])

        self.tm.start_task(second.id)
        snap = self.tm.snapshot()
        self.assertEqual(snap["active_task_id"], second.id)
        self.assertIn(snap["active_task_id"], snap["task_ids"])
        self.assertEqual(snap["active_task_status"], TaskStatus.RUNNING.value)

    def test_signals_are_emitted_after_the_lock_is_released(self):
        """A slot may call back into the manager — from another thread, too.

        With the signal emitted while the lock is held, that second thread
        blocks on the lock and the join below times out. This used to be the
        shape the plan warned about ("лок + emit внутри лока = дедлок").
        """
        stuck = []

        def on_added(_task_id):
            done = threading.Event()

            def reader():
                self.tm.queue          # needs the lock
                done.set()

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            t.join(timeout=1.0)
            stuck.append(not done.is_set())

        self.tm.task_added.connect(on_added)
        self.tm.add_task("https://a.example/1.jpg", {}, "/tmp/out/a")

        self.assertEqual(stuck, [False],
                         "task_added was emitted while the lock was held")

    def test_concurrent_churn_keeps_the_queue_intact(self):
        """No lost inserts and no exceptions under 4 writer threads."""
        writers, per_writer = 4, 60
        errors = []

        def writer(n):
            try:
                for i in range(per_writer):
                    task = self.tm.add_task(
                        f"https://a.example/{n}-{i}.jpg", {}, f"/tmp/out/{n}")
                    self.tm.start_task(task.id)
                    self.tm.clear_active()
            except Exception as e:  # noqa: BLE001 - the assertion is the point
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(n,), daemon=True)
                   for n in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(self.tm.queue), writers * per_writer)

    def test_removing_a_task_does_not_disturb_the_active_one(self):
        active = self.tm.add_task("https://a.example/a.jpg", {}, "/tmp/out/a")
        other = self.tm.add_task("https://a.example/b.jpg", {}, "/tmp/out/b")
        self.tm.start_task(active.id)

        self.assertFalse(self.tm.remove_task(active.id))
        self.assertTrue(self.tm.remove_task(other.id))
        self.assertEqual(self.tm.snapshot()["active_task_id"], active.id)

    def test_pause_active_task_emits_status_once(self):
        task = self.tm.add_task("https://a.example/a.jpg", {}, "/tmp/out/a")
        self.tm.start_task(task.id)
        seen = []
        self.tm.task_status_changed.connect(lambda tid, st: seen.append((tid, st)))

        self.assertTrue(self.tm.pause_active_task())

        self.assertEqual(seen, [(task.id, TaskStatus.PAUSED.value)])
        self.assertIsNone(self.tm.snapshot()["active_task_id"])


class TestStatusEndpointDuringQueueChurn(unittest.TestCase):
    """Runtime half: the real endpoint, the real lock, a real writer thread."""

    def test_status_requests_during_heavy_queue_churn(self):
        tm = TaskQueueManager(base_download_dir=os.path.dirname(__file__))
        server = ExtensionServer(port=_free_port())

        def get_status():
            snap = tm.snapshot()
            return {
                "active_task": snap["active_task_id"],
                "queue_length": snap["queue_length"],
                "task_ids": snap["task_ids"],
                "files_downloaded": snap["files_downloaded"],
            }

        server.set_callbacks(lambda urls, one_shot, **kw: {"added": 0}, get_status)
        stop = threading.Event()

        def churn():
            i = 0
            while not stop.is_set():
                task = tm.add_task(f"https://a.example/{i}.jpg", {}, "/tmp/out/x")
                tm.start_task(task.id)
                tm.clear_active()
                i += 1

        writer = threading.Thread(target=churn, daemon=True)

        async def run():
            await server.start()
            try:
                port = server._site._server.sockets[0].getsockname()[1]
                import aiohttp
                async with aiohttp.ClientSession() as cli:
                    responses = []
                    for _ in range(40):
                        async with cli.get(f"http://127.0.0.1:{port}/api/status") as r:
                            responses.append((r.status, await r.json()))
                    return responses
            finally:
                await server.stop()

        writer.start()
        time.sleep(0.05)
        try:
            responses = asyncio.run(run())
        finally:
            stop.set()
            writer.join(timeout=5)

        self.assertEqual(len(responses), 40)
        for status, payload in responses:
            self.assertEqual(status, 200)
            self.assertEqual(payload["queue_length"], len(payload["task_ids"]))
            if payload["active_task"] is not None:
                self.assertIn(payload["active_task"], payload["task_ids"],
                              "one response mixed two queue states")
            self.assertEqual(payload["files_downloaded"], 0)
        self.assertGreater(len(tm.queue), 0, "the writer thread never ran")


class TestStatusCallbackWiring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(MAIN_WINDOW_PATH, encoding="utf-8") as f:
            cls.source = f.read()

    def test_the_callback_reads_through_snapshot(self):
        start = self.source.index("        def get_status():")
        body = self.source[start:self.source.index("\n        self.extension_server", start)]
        self.assertIn("self.task_queue.snapshot()", body,
                      "C-08: /api/status still reads the queue piecemeal")
        self.assertNotIn("len(self.task_queue.queue)", body)

    def test_payload_list_is_guarded_and_drained_atomically(self):
        self.assertIn("self._payload_lock = threading.Lock()", self.source)
        self.assertIn("with self._payload_lock:", self.source)
        self.assertIn("self._drain_pending_extension_payloads()", self.source)
        self.assertNotIn(
            "payloads, self._pending_extension_payloads = (\n            self._pending_extension_payloads, [])",
            self.source,
            "C-08: the racing tuple swap is back")


if __name__ == "__main__":
    unittest.main()
