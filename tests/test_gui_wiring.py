#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""C-01 / C-03 / C-06 / C-09 (2026-10-15): GUI wiring for failures and persistence.

C-01 — a busy extension port was swallowed in a background thread, so the window
       looked healthy while every extension request failed with "Desktop app not
       reachable". Asserted here against two real servers (no Qt).
C-03 — the sieve download read the last SAVED path while the user was looking at
       the widget, so an unsaved selection was ignored or the previously saved
       file was overwritten with the wrong content. Asserted against a real
       SettingsDialog (offscreen Qt).
C-06 — the replaced QThread must be handed to Qt before the reference is dropped
       (a timed-out wait() otherwise leads to "QThread: Destroyed while thread is
       still running").
C-09 — task_queue.json is written when the queue changes, not only on a clean
       window close.

C-06/C-09 are wiring inside MainWindow; instantiating it in a test would bind
port 19876, read and rewrite the real task_queue.json and start the 2 s stats
timer, so the GUI half is pinned at source level while the manager-side half of
C-09 (the signals the debounce listens to) is asserted at runtime.
"""

import asyncio
import io
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import constants as K
from src.core.task_queue_manager import TaskQueueManager
from src.server.http_server import ExtensionServer

MAIN_WINDOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "src", "gui", "main_window.py")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestExtensionServerPortConflict(unittest.TestCase):
    """C-01: the second instance must be able to say why the port is unavailable."""

    def test_starting_on_a_busy_port_records_the_reason(self):
        port = _free_port()

        async def go():
            first = ExtensionServer(port=port)
            second = ExtensionServer(port=port)
            await first.start()
            try:
                raised = None
                try:
                    await second.start()
                except Exception as e:  # noqa: BLE001 - the point is that it raises
                    raised = e
                return first, second, raised
            finally:
                await first.stop()

        first, second, raised = asyncio.run(go())

        self.assertIsNone(first.start_error,
                          "a successful start must not leave an error behind")
        self.assertIsNotNone(raised,
                             "starting on a busy port must not look like success")
        self.assertTrue(second.start_error,
                        "start_error must carry the reason for the GUI to display")
        self.assertIn(str(port), second.start_error,
                      "the recorded error should name the port that failed")

    def test_start_error_is_reset_on_a_later_successful_start(self):
        server = ExtensionServer(port=_free_port())
        server.start_error = "stale failure from a previous start"

        async def go():
            await server.start()
            error = server.start_error
            await server.stop()
            return error

        self.assertIsNone(asyncio.run(go()))


class TestSieveDownloadUsesTheVisiblePath(unittest.TestCase):
    """C-03: `Download latest` must target the path shown in the dialog.

    Runs a real SettingsDialog under Qt's offscreen platform; skips when Qt
    cannot initialize (no plugin available).
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:  # pragma: no cover - depends on environment
            raise unittest.SkipTest(f"PySide6 unavailable: {e}")
        try:
            cls.app = QApplication.instance() or QApplication([])
        except Exception as e:  # pragma: no cover - depends on environment
            raise unittest.SkipTest(f"Qt could not initialize: {e}")

    def _dialog(self):
        from src.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _tmpdir(self):
        path = tempfile.mkdtemp(prefix="wmp_gui_")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_unsaved_path_from_the_widget_wins(self):
        dialog = self._dialog()
        seen = {}

        class _FakePatternManager:
            def __init__(self, enable_built_in=False, imagus_sieve_path=None, **kwargs):
                seen["path"] = imagus_sieve_path

            def download_imagus_from_url(self, url, timeout=None):
                seen["url"] = url
                return True, "ok"

        widget_path = os.path.join(self._tmpdir(), "chosen.json")
        stale_path = os.path.join(self._tmpdir(), "previously_saved.json")
        dialog.imagus_sieve_edit.setText(widget_path)
        dialog.sieve_repo_edit.setText("https://example.com/sieve.json")
        dialog.settings[K.SETTING_IMAGUS_SIEVE_PATH] = stale_path

        with patch("src.parser.site_pattern_manager.SitePatternManager", _FakePatternManager):
            dialog._on_sieve_download()
            # C-02: the download now runs on a worker thread, so wait for its
            # queued signal before reading what it did.
            deadline = time.time() + 5
            while not seen and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertTrue(seen, "the background download never ran")

        self.assertEqual(seen.get("path"), widget_path,
                         "the download used the saved path instead of the visible one")
        self.assertEqual(dialog.settings[K.SETTING_IMAGUS_SIEVE_PATH], widget_path,
                         "the dialog's settings were left out of sync with its widget")

    def test_empty_path_asks_for_one_instead_of_downloading(self):
        dialog = self._dialog()
        dialog.imagus_sieve_edit.setText("")
        dialog.sieve_repo_edit.setText("https://example.com/sieve.json")
        dialog.settings[K.SETTING_IMAGUS_SIEVE_PATH] = ""

        with patch("src.parser.site_pattern_manager.SitePatternManager") as pm:
            dialog._on_sieve_download()

        pm.assert_not_called()
        self.assertIn("sieve file", dialog.sieve_status_label.text().lower())


class TestSieveDownloadRunsInABackgroundThread(unittest.TestCase):
    """C-02: `Download latest` must not run HTTP on the GUI thread.

    Measured before the fix: the click performed up to two 30 s HTTP attempts
    (primary + jsDelivr mirror) synchronously.
    """

    MAX_CLICK_SECONDS = 0.2

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:  # pragma: no cover - depends on environment
            raise unittest.SkipTest(f"PySide6 unavailable: {e}")
        try:
            cls.app = QApplication.instance() or QApplication([])
        except Exception as e:  # pragma: no cover - depends on environment
            raise unittest.SkipTest(f"Qt could not initialize: {e}")

    def _dialog(self):
        from src.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _tmpdir(self):
        path = tempfile.mkdtemp(prefix="wmp_gui_")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def _wait_for_final_label(self, dialog, timeout=5.0):
        """Pump the GUI event loop until the worker's queued signal arrives."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if dialog.sieve_download_btn.isEnabled():
                return dialog.sieve_status_label.text()
            time.sleep(0.02)
        self.fail("the download never reported back; the button stayed disabled")

    def _ready_dialog(self, dialog):
        dialog.imagus_sieve_edit.setText(os.path.join(self._tmpdir(), "sieve.json"))
        dialog.sieve_repo_edit.setText("https://example.com/sieve.json")

    def test_the_click_returns_immediately_and_reports_later(self):
        dialog = self._dialog()
        self._ready_dialog(dialog)
        threads_seen = []

        class _SlowPatternManager:
            def __init__(self, enable_built_in=False, imagus_sieve_path=None, **kw):
                pass

            def download_imagus_from_url(self, url, timeout=None):
                threads_seen.append(threading.get_ident())
                time.sleep(1.0)          # stands in for the 30 s HTTP timeout
                return True, "Downloaded 123 rules"

            def jsdelivr_mirror(self, url):
                return None

        with patch("src.parser.site_pattern_manager.SitePatternManager",
                   _SlowPatternManager):
            started = time.perf_counter()
            dialog._on_sieve_download()
            elapsed = time.perf_counter() - started

            self.assertLess(elapsed, self.MAX_CLICK_SECONDS,
                            f"the click blocked for {elapsed:.2f}s")
            self.assertFalse(dialog.sieve_download_btn.isEnabled())
            self.assertIn("download", dialog.sieve_status_label.text().lower())
            final = self._wait_for_final_label(dialog)

        self.assertEqual(final, "Downloaded 123 rules")
        self.assertTrue(dialog.sieve_download_btn.isEnabled())
        self.assertTrue(threads_seen, "the download never ran")
        self.assertNotEqual(threads_seen[0], threading.get_ident(),
                            "the download still ran on the GUI thread")

    def test_a_failure_is_reported_and_the_button_comes_back(self):
        dialog = self._dialog()
        self._ready_dialog(dialog)

        class _FailingPatternManager:
            def __init__(self, **kw):
                pass

            def download_imagus_from_url(self, url, timeout=None):
                raise RuntimeError("connection reset")

        with patch("src.parser.site_pattern_manager.SitePatternManager",
                   _FailingPatternManager):
            dialog._on_sieve_download()
            final = self._wait_for_final_label(dialog)

        self.assertIn("connection reset", final)
        self.assertTrue(dialog.sieve_download_btn.isEnabled())

    def test_the_jsdelivr_retry_happens_inside_the_worker(self):
        dialog = self._dialog()
        dialog.imagus_sieve_edit.setText(os.path.join(self._tmpdir(), "sieve.json"))
        raw_url = ("https://raw.githubusercontent.com/x/y/main/sieve.json")
        dialog.sieve_repo_edit.setText(raw_url)
        attempts = []

        class _MirrorPatternManager:
            def __init__(self, **kw):
                pass

            def download_imagus_from_url(self, url, timeout=None):
                attempts.append(url)
                if url == raw_url:
                    return False, "403 rate limit"
                return True, "Downloaded from jsDelivr"

            def jsdelivr_mirror(self, url):
                return "https://cdn.jsdelivr.net/gh/x/y@main/sieve.json"

        with patch("src.parser.site_pattern_manager.SitePatternManager",
                   _MirrorPatternManager):
            dialog._on_sieve_download()
            final = self._wait_for_final_label(dialog)

        self.assertEqual(final, "Downloaded from jsDelivr")
        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[1].startswith("https://cdn.jsdelivr.net/"), attempts)

    def test_a_missing_path_starts_no_thread(self):
        dialog = self._dialog()
        dialog.imagus_sieve_edit.setText("")
        dialog.sieve_repo_edit.setText("https://example.com/sieve.json")
        dialog._sieve_download_thread = "untouched"

        dialog._on_sieve_download()

        self.assertEqual(dialog._sieve_download_thread, "untouched")
        self.assertIn("sieve file", dialog.sieve_status_label.text().lower())


class TestMainWindowWiring(unittest.TestCase):
    """C-06 / C-09 / C-01: source-level wiring guards (see the module docstring)."""

    @classmethod
    def setUpClass(cls):
        with io.open(MAIN_WINDOW_PATH, encoding="utf-8") as f:
            cls.source = f.read()

    def test_replaced_qthread_is_handed_to_qt(self):
        self.assertIn("old_thread.finished.connect(old_thread.deleteLater)", self.source,
                      "C-06: the replaced QThread is never deleted by Qt")
        self.assertIn("self.parser_thread = None", self.source,
                      "C-06: the old thread reference is not cleared before replacement")

    def test_queue_signals_mark_the_file_dirty(self):
        for signal in ("task_added", "task_removed", "task_status_changed"):
            self.assertIn(f"self.task_queue.{signal}.connect(self._mark_queue_dirty)",
                          self.source, f"C-09: {signal} does not mark the queue dirty")

    def test_periodic_timer_autosaves_the_queue(self):
        self.assertIn("self._autosave_queue()", self.source)
        self.assertIn("self.task_queue.save(queue_path())", self.source)

    def test_server_start_failure_reaches_the_gui(self):
        self.assertIn("extension_server_failed = Signal(str)", self.source)
        self.assertIn("self.extension_server_failed.emit(", self.source)
        self.assertIn("QMessageBox.warning(self, \"Extension API unavailable\"", self.source)

    def _method_body(self, signature):
        start = self.source.index(signature)
        end = self.source.index("\n    def ", start + len(signature))
        return self.source[start:end]

    def test_user_text_url_paths_build_the_folder_through_the_sanitizer(self):
        # NF-11 (url field, 2026-10-16): the URL field and the CSV import are USER
        # text, and both built the folder name inline with
        # `url.split("/")[2].replace(".", "_")` — so a port went into
        # os.makedirs, Start did nothing, and one CSV row with a port aborted the
        # whole import. The rule now lives in one pure function (runtime-asserted
        # in test_parser_lifecycle); pin the three call sites here because
        # instantiating MainWindow binds port 19876 and rewrites the real queue.
        for signature in ("def _start_from_url_input(self):",
                          "def add_task_to_queue(self):",
                          "def _import_history_csv(self):"):
            body = self._method_body(signature)
            self.assertIn("task_folder = task_folder_from_url(", body,
                          f"{signature} does not sanitize its folder name")
            self.assertIn("if task_folder is None:", body,
                          f"{signature} would use an unusable folder name")


class TestLogViewIsABoundedWindow(unittest.TestCase):
    """Errors.txt (2): the GUI log view must not grow for the whole session.

    The 5 000-record message_history made the log look bounded, but the
    QTextEdit document kept every line ever logged (GUI/root logger are on
    DEBUG). Measured before the fix with the real handler: 200 000 appended
    records = +118 MB RSS while tracemalloc stayed flat at 1.6 MB — a
    QTextDocument lives in Qt's C++ heap, which no Python profiler sees.

    MainWindow itself cannot be instantiated here (it binds port 19876 and
    rewrites the real task_queue.json), so the production path is exercised
    through the same method it uses: setup_logging() on a widget owner.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:  # pragma: no cover - depends on environment
            raise unittest.SkipTest(f"PySide6 unavailable: {e}")
        try:
            cls.app = QApplication.instance() or QApplication([])
        except Exception as e:  # pragma: no cover - depends on environment
            raise unittest.SkipTest(f"Qt could not initialize: {e}")

    def _logging_setup(self):
        """Run MainWindow.setup_logging against a bare log widget."""
        from PySide6.QtWidgets import QTextEdit

        from src.gui.main_window import MainWindow

        stub = types.SimpleNamespace(log_text=QTextEdit())
        MainWindow.setup_logging(stub)
        self.addCleanup(logging.getLogger().removeHandler, stub.log_handler)
        return stub

    def test_the_real_setup_logging_path_caps_the_document(self):
        stub = self._logging_setup()

        self.assertEqual(stub.log_text.document().maximumBlockCount(),
                         K.GUI_LOG_MAX_BLOCKS,
                         "the log document is left uncapped and grows forever")

    def test_the_view_stops_growing_after_the_cap(self):
        stub = self._logging_setup()
        log = logging.getLogger("wmp.test.log_window")
        log.setLevel(logging.DEBUG)

        for i in range(K.GUI_LOG_MAX_BLOCKS * 2 + 100):
            log.debug(f"parsed page {i}: https://img10.reactor.cc/pics/post/full/x{i}.jpeg")

        self.assertLessEqual(stub.log_text.document().blockCount(), K.GUI_LOG_MAX_BLOCKS,
                             "the log document kept more lines than the history window")

        # Re-applying the level filters must not resurrect the whole session.
        stub.log_handler.reapply_filters()
        self.assertLessEqual(stub.log_text.document().blockCount(), K.GUI_LOG_MAX_BLOCKS)


class TestQueueSignalsThatDriveAutosave(unittest.TestCase):
    """Runtime half of C-09: the signals the debounce listens to really fire."""

    def test_add_status_and_remove_emit(self):
        tm = TaskQueueManager(base_download_dir=tempfile.mkdtemp(prefix="wmp_q_"))
        self.addCleanup(shutil.rmtree, tm.base_download_dir, True)
        fired = {"added": 0, "removed": 0, "status": 0}
        tm.task_added.connect(lambda *_: fired.__setitem__("added", fired["added"] + 1))
        tm.task_removed.connect(lambda *_: fired.__setitem__("removed", fired["removed"] + 1))
        tm.task_status_changed.connect(
            lambda *_: fired.__setitem__("status", fired["status"] + 1))

        task = tm.add_task("https://a.example/", {}, "/tmp/out/a")
        tm.start_task(task.id)
        tm.clear_active()  # an active task is deliberately not removable
        tm.remove_task(task.id)

        self.assertEqual(fired["added"], 1)
        self.assertGreaterEqual(fired["status"], 1)
        self.assertEqual(fired["removed"], 1)

    def test_save_is_atomic_and_round_trips(self):
        tm = TaskQueueManager(base_download_dir=tempfile.mkdtemp(prefix="wmp_q_"))
        self.addCleanup(shutil.rmtree, tm.base_download_dir, True)
        tm.add_task("https://a.example/", {"retry_count": 3}, "/tmp/out/a")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "task_queue.json")
            tm.save(path)
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.exists(path + ".tmp"))
            fresh = TaskQueueManager(base_download_dir=d)
            self.assertEqual(fresh.load(path), 1)
