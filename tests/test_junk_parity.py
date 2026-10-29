#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""E-09: the extension's junk tables must stay equal to the desktop's.

The two sides classify the same URLs for different consumers ("Save in Chrome"
vs the download queue). They already drifted once (AD_PATH_TOKENS vs
_AD_KEYWORDS), so the tables are pinned here instead of trusted by eye.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.junk_filter import AD_HOST_SUFFIXES
from src.parser.utils import _AD_KEYWORDS

_CONTENT_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "extension", "content_script.js")


def _js_array(name):
    with open(_CONTENT_SCRIPT, encoding="utf-8") as f:
        source = f.read()
    match = re.search(name + r"\s*=\s*\[(.*?)\];", source, re.S)
    assert match, f"{name} not found in content_script.js"
    return re.findall(r'"([^"]+)"', match.group(1))


class TestJunkTableParity(unittest.TestCase):

    def test_ad_host_suffixes_match(self):
        self.assertEqual(sorted(_js_array("AD_HOST_SUFFIXES")),
                         sorted(AD_HOST_SUFFIXES))

    def test_ad_keywords_match(self):
        self.assertEqual(sorted(_js_array("AD_KEYWORDS")), sorted(_AD_KEYWORDS))

    def test_token_split_keeps_underscore_like_python(self):
        """`icon_arrow.png` is ONE token on both sides (Python `\\w` has `_`)."""
        with open(_CONTENT_SCRIPT, encoding="utf-8") as f:
            source = f.read()
        self.assertIn(r"/[^\p{L}\p{N}_]+/u", source)


if __name__ == "__main__":
    unittest.main()
