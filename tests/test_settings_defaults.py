#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""F-01 (2026-10-15): one source of settings defaults.

`SettingsDialog.default_settings` was a second, drifted copy of
`K.DEFAULT_SETTINGS_VALUES`: 30 keys against 34, carrying its own 25-word
`stop_words` list (with a duplicate "login") while `K.DEFAULT_STOP_WORDS` had 9 —
so deleting settings.json silently narrowed the crawl filter by 2.7x — and
omitting 8 keys entirely (`filter_junk`, `js_engine`, `http_engine`,
`http_impersonate`, `http_escalate`, `verify_tls`, `enabled_audio_formats`,
`filter_hidden_links`), each of which then fell back to a *third* default at its
consumer site.

The dialog assertions are source-level on purpose: the defect *is* duplication,
and instantiating the dialog needs a QApplication.
"""

import ast
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import constants as K

DIALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "src", "gui", "settings_dialog.py")

# Keys that legitimately live only in the settings file — they have no SETTING_*
# constant and no consumer outside the dialog.
DIALOG_ONLY_KEYS = {"last_download_dir", "log_to_file", "log_file_path"}


def _default_settings_node():
    with io.open(DIALOG_PATH, encoding="utf-8") as f:
        source = f.read()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "default_settings":
                    return node.value
    raise AssertionError("SettingsDialog.default_settings assignment not found")


def _spread_names(node):
    """`**X` entries of a dict literal (key is None in the AST)."""
    return {ast.unparse(value) for key, value in zip(node.keys, node.values) if key is None}


def _literal_keys(node):
    """String keys written out longhand in the dict literal."""
    return {key.value for key in node.keys if key is not None}


class TestSingleSourceOfDefaults(unittest.TestCase):

    def test_dialog_spreads_the_canonical_defaults(self):
        spreads = _spread_names(_default_settings_node())
        self.assertTrue(spreads, "default_settings no longer spreads the canonical dict")
        self.assertIn("K.DEFAULT_SETTINGS_VALUES", spreads)

    def test_dialog_only_defines_keys_that_are_its_own(self):
        literal = _literal_keys(_default_settings_node())
        self.assertEqual(literal, DIALOG_ONLY_KEYS)
        overlap = literal & set(K.DEFAULT_SETTINGS_VALUES)
        self.assertEqual(overlap, set(),
                         f"the dialog redefines canonical keys: {sorted(overlap)}")

    def test_dialog_no_longer_ships_its_own_stop_words(self):
        literal = _literal_keys(_default_settings_node())
        self.assertNotIn("stop_words", literal,
                         "the dialog defines stop_words again — two sources for one key")

    def test_every_declared_setting_has_a_default(self):
        """No consumer should need a third fallback for a declared setting."""
        missing = sorted(
            value for name, value in vars(K).items()
            if name.startswith("SETTING_") and isinstance(value, str)
            and value not in K.DEFAULT_SETTINGS_VALUES
        )
        self.assertEqual(missing, [], f"declared settings without a default: {missing}")

    def test_stop_words_are_unique_and_lowercase(self):
        words = K.DEFAULT_STOP_WORDS
        self.assertEqual(len(words), len(set(words)),
                         "duplicate stop word (the old dialog list had two 'login')")
        self.assertTrue(all(w == w.lower() for w in words), words)
        self.assertGreaterEqual(
            len(words), 20,
            "the wide stop-word list users already had must not be narrowed again "
            "(F-01: 9-word fallbacks used to silently widen the crawl)",
        )
