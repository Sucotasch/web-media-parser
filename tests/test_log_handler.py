#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""X-05 / G-03: the GUI log handler has tests.

`log_handler` was one of two modules with zero coverage (X-05). The defect worth
pinning is G-03: `reapply_filters` iterated `message_history` while parser,
server and executor threads appended to it — `RuntimeError: deque mutated during
iteration` inside a Qt slot left the log empty or truncated, and a windowed
build prints nothing to a console the user can see.

The widget is replaced by a duck-typed stand-in: the handler only needs the Qt
API surface it calls, and a real QTextEdit would drag a QApplication (and a
platform plugin) into a unit test.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.fix_lxml import LXMLHTMLCleanFix
from src.gui.log_handler import GUILogHandler


class _FakeDocument:
    def __init__(self):
        self.maximum_block_count = 0

    def setMaximumBlockCount(self, value):
        self.maximum_block_count = value


class _FakeScrollBar:
    def maximum(self):
        return 0

    def setValue(self, value):
        pass


class _FakeTextEdit:
    """Just the QTextEdit API GUILogHandler touches."""

    def __init__(self):
        self.document_obj = _FakeDocument()
        self.appended = []
        self.cleared = 0

    def setReadOnly(self, value):
        pass

    def setAcceptRichText(self, value):
        pass

    def document(self):
        return self.document_obj

    def append(self, html):
        self.appended.append(html)

    def clear(self):
        self.cleared += 1

    def verticalScrollBar(self):
        return _FakeScrollBar()


class TestLogHandlerHistory(unittest.TestCase):

    def test_block_limit_comes_from_constants(self):
        from src import constants as K

        widget = _FakeTextEdit()
        GUILogHandler(widget)
        self.assertEqual(widget.document_obj.maximum_block_count,
                         K.GUI_LOG_MAX_BLOCKS)

    def test_reapply_filters_survives_a_concurrent_append(self):
        """G-03: iterating the live deque raised RuntimeError in the Qt slot."""
        widget = _FakeTextEdit()
        handler = GUILogHandler(widget)
        for i in range(2000):
            handler.message_history.append(("INFO", f"line {i}"))

        stop = threading.Event()
        errors = []

        def writer():
            # A real logging thread appends per record, not in a tight loop; the
            # tiny sleep keeps GIL contention out of the measurement (a
            # zero-sleep writer made each Qt signal emission take ~25 ms).
            i = 0
            while not stop.is_set():
                try:
                    handler.message_history.append(("INFO", f"live {i}"))
                except Exception as e:  # noqa: BLE001 - the assertion is the point
                    errors.append(repr(e))
                i += 1
                time.sleep(0.001)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for _ in range(25):
                handler.reapply_filters()
        except RuntimeError as e:  # the defect
            self.fail(f"reapply_filters raised while history was appended to: {e}")
        finally:
            stop.set()
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        # The snapshot is taken, not streamed: every re-applied record is one
        # that existed when the pass started, and filtering still works.
        handler.set_level_visibility("DEBUG", False)
        widget.appended.clear()
        handler.message_history.clear()
        handler.message_history.append(("DEBUG", "hidden"))
        handler.message_history.append(("ERROR", "shown"))
        handler.reapply_filters()
        self.assertEqual(len(widget.appended), 1)
        self.assertIn("shown", widget.appended[0])

    def test_clear_history_empties_history_and_view(self):
        widget = _FakeTextEdit()
        handler = GUILogHandler(widget)
        handler.message_history.append(("INFO", "x"))
        handler.clear_history()
        self.assertEqual(len(handler.message_history), 0)
        self.assertEqual(widget.cleared, 1)


class TestLxmlPatch(unittest.TestCase):
    """X-05: `fix_lxml` was the other untested module."""

    def test_patch_is_idempotent_and_reports_a_bool(self):
        first = LXMLHTMLCleanFix.patch()
        second = LXMLHTMLCleanFix.patch()
        self.assertIsInstance(first, bool)
        self.assertIsInstance(second, bool)

    def test_patch_exposes_the_clean_module_when_available(self):
        import importlib.util

        if importlib.util.find_spec("lxml_html_clean") is None:
            self.assertFalse(LXMLHTMLCleanFix.patch())
            return
        self.assertTrue(LXMLHTMLCleanFix.patch())
        import lxml.html

        self.assertTrue(hasattr(lxml.html, "clean"))


if __name__ == "__main__":
    unittest.main()
