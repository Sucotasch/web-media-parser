#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI-4 / GUI-9: task_queue.json atomic save, per-item load robustness,
and the narrowed *.part cleanup patterns."""

import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.task_queue_manager import TaskQueueManager
from src.core.task_item import TaskStatus


class TestQueuePersistence(unittest.TestCase):
    def _manager(self):
        return TaskQueueManager(base_download_dir=tempfile.mkdtemp(prefix="wmp_q_"))

    def test_save_load_roundtrip(self):
        tm = self._manager()
        t1 = tm.add_task("https://a.example/", {"retry_count": 3}, "/tmp/out/a")
        t2 = tm.add_task("https://b.example/", {}, "/tmp/out/b")
        t1.mark_running()
        t2.mark_paused()

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "task_queue.json")
            tm.save(path)
            # No leftover temp file after an atomic save
            self.assertFalse(os.path.exists(path + ".tmp"), "tmp file left after save")

            tm2 = self._manager()
            n = tm2.load(path)
            self.assertEqual(n, 2)
            ids = {t.id for t in tm2._queue}
            self.assertEqual(ids, {t1.id, t2.id})
            # RUNNING is reset to PAUSED after reload (nothing actually runs)
            statuses = {t.status.value for t in tm2._queue}
            self.assertNotIn("running", statuses)
            self.assertIn("paused", statuses)

    def test_corrupt_entry_does_not_erase_queue(self):
        tm = self._manager()
        tm.add_task("https://a.example/", {}, "/tmp/out/a")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "task_queue.json")
            tm.save(path)
            # Corrupt one entry: unknown status + broken date
            data = json.load(open(path, encoding="utf-8"))
            data["tasks"][0]["status"] = "bogus-status"
            data["tasks"][0]["created_at"] = "not-a-date"
            data["tasks"].append({"id": "ok-entry", "url": "https://c.example/",
                                  "created_at": "2026-01-01T00:00:00"})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            tm2 = self._manager()
            n = tm2.load(path)
            # The corrupt first entry is skipped, the healthy one survives
            self.assertEqual(n, 1)
            self.assertEqual([t.url for t in tm2._queue], ["https://c.example/"])

    def test_broken_json_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "task_queue.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ not valid json !!!")
            tm2 = self._manager()
            self.assertEqual(tm2.load(path), 0)
            self.assertEqual(tm2._queue, [])


class TestStartNextPositional(unittest.TestCase):
    """GUI-9 (owner decision): auto-start advances DOWNWARD from the finished
    task. Tasks placed above it keep their state and are never picked up;
    without an anchor the historical top-down scan is preserved."""

    def _queue_with(self, statuses):
        # Set statuses AFTER all adds: add_task inserts "after the last
        # non-terminal", so mutating status mid-loop would reorder the list.
        tm = TaskQueueManager(base_download_dir="/tmp")
        tasks = [tm.add_task(f"https://site{i}.example/", {}, f"/tmp/out{i}")
                 for i in range(len(statuses))]
        for t, st in zip(tasks, statuses):
            t.status = st
        return tm, [t.id for t in tasks]

    def test_advances_downward_from_completed_task(self):
        # 1,2 untouched (paused); start 3 -> next must be 4, NOT 1
        tm, ids = self._queue_with([TaskStatus.PAUSED, TaskStatus.PAUSED,
                                    TaskStatus.QUEUED, TaskStatus.QUEUED,
                                    TaskStatus.QUEUED])
        self.assertTrue(tm.start_next(ids[2]))
        self.assertEqual(tm._active_id, ids[3])

    def test_tasks_above_stay_when_nothing_below(self):
        # Start the LAST task -> nothing below -> nothing auto-starts; the
        # paused task above keeps its state.
        tm, ids = self._queue_with([TaskStatus.PAUSED, TaskStatus.QUEUED,
                                    TaskStatus.QUEUED])
        self.assertFalse(tm.start_next(ids[2]))
        self.assertIsNone(tm._active_id)
        self.assertEqual(tm.find_task(ids[0]).status, TaskStatus.PAUSED)
        self.assertEqual(tm.find_task(ids[1]).status, TaskStatus.QUEUED)

    def test_no_anchor_falls_back_to_top(self):
        # State-restore path: no anchor -> historical top-down scan
        tm, ids = self._queue_with([TaskStatus.QUEUED, TaskStatus.QUEUED])
        self.assertTrue(tm.start_next(None))
        self.assertEqual(tm._active_id, ids[0])

    def test_unknown_anchor_falls_back_to_top(self):
        tm, ids = self._queue_with([TaskStatus.QUEUED, TaskStatus.QUEUED])
        self.assertTrue(tm.start_next("does-not-exist"))
        self.assertEqual(tm._active_id, ids[0])

    def test_skips_terminal_tasks_below_anchor(self):
        # 3 completed, 4 failed/stopped -> next is 5
        tm, ids = self._queue_with([TaskStatus.QUEUED, TaskStatus.QUEUED,
                                    TaskStatus.COMPLETED, TaskStatus.FAILED,
                                    TaskStatus.QUEUED])
        self.assertTrue(tm.start_next(ids[2]))
        self.assertEqual(tm._active_id, ids[4])


class TestPartialCleanup(unittest.TestCase):
    def test_cleanup_removes_only_downloader_temp_patterns(self):
        tm = TaskQueueManager(base_download_dir="/tmp")
        task = tm.add_task("https://a.example/", {}, "/tmp/out")

        # Override with a fake path so we control the folder
        with tempfile.TemporaryDirectory() as d:
            task.download_path = d
            for name in (
                "photo.jpg.a1b2.partial",   # single-thread temp (DL-5 naming)
                "video.mp4.a1b2.part0",     # MT chunk
                "video.mp4.a1b2.part3",
                "user.partial.png",         # user file — must survive
                "album.jpg",                # finished download — must survive
            ):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write("x")
            # Empty temp that must be swept
            with open(os.path.join(d, "stuck.mp4.cafe.partial"), "w", encoding="utf-8"):
                pass

            tm.cleanup_partial_files(task)

            remaining = sorted(os.listdir(d))
            self.assertEqual(remaining, ["album.jpg", "user.partial.png"])
            self.assertFalse(os.path.exists(os.path.join(d, "stuck.mp4.cafe.partial")))


if __name__ == "__main__":
    unittest.main()
