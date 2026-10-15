#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A-10 / A-12 / NF-2 / NF-7 — the shared substitution vectors, Python side.

`extension/tests/vectors/substitution.json` is the same file the node suite
(`extension/tests/substitution.test.mjs`) feeds to sieve.js's `getSubstitution`.
There, every expected value is re-derived from V8's own `String.replace` before
the implementation is checked; here the Python implementation is checked against
those same frozen expectations. Neither side owns the fixture.

What this gate is for: the three dialects that used to exist (`sieve.js`,
`background.js`, `site_pattern_manager.py`) meant `$&` was never substituted in
the extension, `$11200x1200` with two groups collapsed to `x1200` there, and
`$0` silently became the whole match in Python.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.site_pattern_manager import (
    apply_substitution_variants,
    get_substitution,
    normalize_variant,
)

VECTORS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "extension", "tests", "vectors", "substitution.json",
)


class _Match:
    """The group protocol `get_substitution` needs, for the vector's match
    arrays (the node suite passes a real Array — same idea)."""

    def __init__(self, groups, string=""):
        self._groups = list(groups)
        self.string = string or (groups[0] if groups else "")

    def group(self, n=0):
        return self._groups[n] if n < len(self._groups) else None

    def groups(self):
        return tuple(self._groups[1:])

    def start(self):
        return 0


class TestSubstitutionVectors(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(VECTORS, encoding="utf-8") as f:
            cls.data = json.load(f)

    def _flags(self, case):
        return re.IGNORECASE if "i" in case.get("flags", "") else 0

    def test_the_fixture_is_present_and_populated(self):
        self.assertGreaterEqual(len(self.data["substitution"]), 25)
        self.assertGreaterEqual(len(self.data["expansion"]), 4)

    def test_every_substitution_vector(self):
        failures = []
        for case in self.data["substitution"]:
            regex = re.compile(case["regex"], self._flags(case))
            match = regex.search(case["url"])
            if match is None:
                failures.append(f"{case['name']}: fixture regex does not match its url")
                continue
            actual = get_substitution(case["template"], match)
            if actual != case["expected"]:
                failures.append(
                    f"{case['name']}: template={case['template']!r} url={case['url']!r} "
                    f"expected={case['expected']!r} actual={actual!r}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_expansion_vector(self):
        """`apply_substitution_variants` is the twin of the node suite's
        `applySubstitutionAll` — the vectors drive the same call on both sides."""
        failures = []
        for case in self.data["expansion"]:
            match = _Match(list(case["match"]))
            actual = apply_substitution_variants(case["template"], match)
            if actual != case["expected"]:
                failures.append(
                    f"{case['name']}: template={case['template']!r} "
                    f"expected={case['expected']!r} actual={actual!r}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_the_two_bugs_the_vectors_exist_for(self):
        two = re.search(r"^(\d)(\d)$", "12")
        # NF-7: used to be "x1200" in the extension (greedy digit run + `|| ""`)
        self.assertEqual(get_substitution("$11200x1200", two), "11200x1200")
        # A-12: `$&` used to stay literal in the extension
        whole = re.search(r"(\w+)", "abc123")
        self.assertEqual(get_substitution("https://cdn/$&", whole), "https://cdn/abc123")
        # Python used to resolve `$0` to the whole match (the spec says literal)
        self.assertEqual(get_substitution("$0", two), "$0")
        self.assertEqual(get_substitution("$$1", two), "$1")

    def test_variants_normalize_to_absolute_urls(self):
        """NF-1/A-06: the desktop twin of the extension's normalizeVariant."""
        page = "http://page.example/post/1"
        self.assertEqual(normalize_variant("//cdn.example/x.jpg", page),
                         "http://cdn.example/x.jpg")
        self.assertEqual(normalize_variant("/x.jpg", page),
                         "http://page.example/x.jpg")
        self.assertEqual(normalize_variant("https://cdn/x.jpg", page),
                         "https://cdn/x.jpg")
        self.assertEqual(normalize_variant("data:,x", page), "data:,x")
        self.assertEqual(normalize_variant("", page), "")


if __name__ == "__main__":
    unittest.main()
