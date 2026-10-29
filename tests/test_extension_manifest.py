#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""E-02: two manifests, one per engine.

Chrome rejects a manifest_version 3 file that carries `background.scripts`
("cannot be used with manifest_version 3") — so the Firefox target cannot be
the same file, and adding the key next to `service_worker` would break Chrome
too. The Chrome manifest stays service_worker-only; the Firefox one runs the
same files as plain background scripts.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_EXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "extension")


def _manifest(name):
    with open(os.path.join(_EXT, name), encoding="utf-8") as f:
        return json.load(f)


class TestExtensionManifests(unittest.TestCase):

    def test_chrome_manifest_is_service_worker_only(self):
        manifest = _manifest("manifest.json")
        self.assertEqual(manifest["manifest_version"], 3)
        background = manifest["background"]
        self.assertIn("service_worker", background)
        self.assertNotIn("scripts", background,
                         "Chrome refuses background.scripts in a MV3 manifest")

    def test_firefox_manifest_runs_the_same_scripts_in_order(self):
        manifest = _manifest("manifest.firefox.json")
        self.assertEqual(manifest["manifest_version"], 3)
        background = manifest["background"]
        self.assertNotIn("service_worker", background)
        self.assertEqual(
            background["scripts"],
            ["shared.js", "sieve.js", "sieve_updater.js", "background.js"],
            "background.js relies on the other three being evaluated first")

    def test_firefox_manifest_needs_storage_session(self):
        gecko = _manifest("manifest.firefox.json")["browser_specific_settings"]["gecko"]
        self.assertGreaterEqual(int(gecko["strict_min_version"].split(".")[0]), 115)

    def test_both_manifests_agree_on_everything_but_the_background(self):
        chrome = _manifest("manifest.json")
        firefox = _manifest("manifest.firefox.json")
        for key in ("manifest_version", "name", "version", "permissions",
                    "host_permissions", "content_scripts", "commands", "icons"):
            self.assertEqual(chrome[key], firefox[key], key)


if __name__ == "__main__":
    unittest.main()
