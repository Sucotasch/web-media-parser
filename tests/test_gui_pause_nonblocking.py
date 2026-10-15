#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""C-07 (2026-10-15): Pause must not freeze the window while state is saved.

Measured before the fix: `toggle_pause` called
`asyncio.run_coroutine_threadsafe(pm.save_state(...), loop).result(timeout=15)`
ON the GUI thread and froze the window for 5.47 s with an active download.

The unit of work that was fixed is `MainWindow._save_then_stop`, and the handler
that calls it is `MainWindow.toggle_pause`. Both are exercised here on a
duck-typed window double (instantiating the real MainWindow would bind port
19876, rewrite the real task_queue.json and start its timers — see
test_gui_wiring.py's docstring for that decision). The double provides exactly
the attributes toggle_pause touches, so a change to those calls keeps failing
loudly instead of silently passing.
"""

import asyncio
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.task_item import TaskStatus
from src.gui.main_window import MainWindow

# The gate: a click must return far below the measured 5.47 s freeze. 0.2 s is
# loose enough for a loaded CI machine and orders of magnitude below the defect.
MAX_CLICK_SECONDS = 0.2


class _Task:
    """TaskItem stand-in: the pause path only touches these members."""

    def __init__(self, task_id="task-1"):
        self.id = task_id
        self.url = "https://a.example/gallery"
        self.download_path = os.path.join(os.path.dirname(__file__), "_pause_dl")
        self.status = TaskStatus.RUNNING
        self.mark_paused_calls = 0

    def mark_paused(self):
        self.mark_paused_calls += 1
        self.status = TaskStatus.PAUSED


class _Queue:
    def __init__(self, task):
        self._task = task
        self.cleared = 0

    @property
    def active_task(self):
        return self._task

    def clear_active(self):
        self.cleared += 1
        self._task = None


class _Log:
    def __init__(self):
        self.lines = []

    def _record(self, level, message):
        self.lines.append((level, message))

    def info(self, message, *a, **kw):
        self._record("info", message)

    def warning(self, message, *a, **kw):
        self._record("warning", message)

    def error(self, message, *a, **kw):
        self._record("error", message)


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, *a, **kw):
        self.messages.append(message)


class _FakeParserManager:
    """Records the ORDER of "save finished" vs "stop issued" on the loop."""

    def __init__(self, loop, save_seconds):
        self.loop = loop
        self.save_seconds = save_seconds
        self.order = []
        self.stopped = threading.Event()

    async def save_state(self, download_path):
        # A real save blocks in wait-for-in-flight + pickle; the sleep stands in
        # for it without touching the filesystem, and it must NOT run on the GUI
        # thread — asserting the thread id catches a regression that moves it
        # back there.
        self.save_thread = threading.get_ident()
        await asyncio.sleep(self.save_seconds)
        self.order.append("save")
        return True

    def stop_parsing(self):
        self.order.append("stop")
        self.stopped.set()


class _Window:
    """Only what MainWindow.toggle_pause/toggle_pause's callees touch."""

    def __init__(self, pm, queue, log, selected=None):
        self.parser_manager = pm
        self.task_queue = queue
        self.log_handler = log
        self.status_bar = _StatusBar()
        self._selected = selected
        self.ui_states = []
        self.rows = []
        self.button_states = 0

    # --- collaborators toggle_pause calls ---
    def _get_selected_task(self):
        return self._selected

    def _update_task_row(self, task_id):
        self.rows.append(task_id)

    def update_ui_state(self, running):
        self.ui_states.append(running)

    def _update_start_button_state(self):
        self.button_states += 1

    # --- the real implementation under test ---
    _save_then_stop = MainWindow._save_then_stop


class _LoopThread:
    """A real asyncio loop in its own thread, like the parser's QThread."""

    def __init__(self, test):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, name="fake-parser-loop", daemon=True)
        self.thread.start()
        test.addCleanup(self._shutdown)

    def _shutdown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class TestPauseDoesNotBlockTheGui(unittest.TestCase):

    def setUp(self):
        self.loop_thread = _LoopThread(self)

    def _window(self, save_seconds, selected=None):
        self.pm = _FakeParserManager(self.loop_thread.loop, save_seconds)
        self.task = _Task()
        self.queue = _Queue(self.task)
        self.log = _Log()
        return _Window(self.pm, self.queue, self.log, selected)

    def test_a_slow_save_does_not_freeze_the_click(self):
        window = self._window(save_seconds=1.0)

        started = time.perf_counter()
        MainWindow.toggle_pause(window)
        elapsed = time.perf_counter() - started

        self.assertLess(
            elapsed, MAX_CLICK_SECONDS,
            f"Pause blocked the GUI thread for {elapsed:.2f}s "
            f"(the 1.0s save was awaited on it)")
        # The state the UI relies on right after the click: the task is marked
        # paused (so a late task_ended cannot overwrite it) and the active slot
        # is free, while the save keeps running in the background.
        self.assertEqual(self.task.mark_paused_calls, 1)
        self.assertEqual(self.task.status, TaskStatus.PAUSED)
        self.assertEqual(self.queue.cleared, 1)
        self.assertEqual(window.ui_states, [False])
        self.assertFalse(self.pm.stopped.is_set(),
                         "the task was stopped before its state was saved")

    def test_the_stop_waits_for_the_save_and_then_happens(self):
        window = self._window(save_seconds=0.4)

        MainWindow.toggle_pause(window)

        self.assertTrue(self.pm.stopped.wait(timeout=5),
                        "the task was never stopped after the pause")
        self.assertEqual(self.pm.order, ["save", "stop"],
                         "the state must be written before the loop is stopped")
        self.assertNotEqual(self.pm.save_thread, threading.get_ident(),
                            "save_state ran on the calling (GUI) thread")

    def test_a_broken_save_still_stops_the_task(self):
        class _ExplodingPM(_FakeParserManager):
            async def save_state(self, download_path):
                raise RuntimeError("disk full")

        self.pm = _ExplodingPM(self.loop_thread.loop, 0)
        window = _Window(self.pm, _Queue(_Task()), _Log())

        MainWindow.toggle_pause(window)

        self.assertTrue(self.pm.stopped.wait(timeout=5),
                        "a failed save must not leave the task running")
        self.assertTrue(any(level == "error" for level, _ in window.log_handler.lines))

    def test_resume_path_is_untouched(self):
        """A PAUSED task with no active task resumes instead of re-pausing."""
        window = self._window(save_seconds=0)
        window._selected = self.task
        window._selected.status = TaskStatus.PAUSED
        window.task_queue = _Queue(None)
        window._launch_task_from_queue = lambda task_id: setattr(
            window, "launched", task_id)

        MainWindow.toggle_pause(window)

        self.assertEqual(getattr(window, "launched", None), self.task.id)
        self.assertEqual(self.task.mark_paused_calls, 0)


class TestPauseWiringAtSourceLevel(unittest.TestCase):
    """The pause path must not wait on the loop from the GUI thread again."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "src", "gui", "main_window.py")
        with open(path, encoding="utf-8") as f:
            cls.source = f.read()

    def _function_body(self, name):
        start = self.source.index(f"    def {name}(")
        end = self.source.index("\n    def ", start + 1)
        return self.source[start:end]

    def test_toggle_pause_does_not_await_the_loop(self):
        body = self._function_body("toggle_pause")
        self.assertNotIn(".result(", body,
                         "C-07: toggle_pause blocks the GUI thread again")
        self.assertIn("self._save_then_stop(", body)

    def test_switch_path_does_not_await_the_loop(self):
        body = self._function_body("_pause_current_for_switch")
        self.assertNotIn(".result(", body,
                         "C-07: the task-switch path blocks the GUI thread again")
        self.assertIn("self._save_then_stop(", body)

    def test_the_save_precedes_the_stop_in_one_coroutine(self):
        body = self._function_body("_save_then_stop")
        # Only the chained coroutine counts — the "no loop to save on" branch
        # above it legitimately stops without saving.
        chained = body[body.index("async def _save_then_stop_on_loop"):]
        self.assertLess(chained.index("await pm.save_state("),
                        chained.index("pm.stop_parsing()"),
                        "the stop must be chained after the save, not issued beside it")


if __name__ == "__main__":
    unittest.main()
