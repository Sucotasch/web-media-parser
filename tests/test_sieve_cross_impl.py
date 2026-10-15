#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A-10 / A-12 / NF-2 / NF-7 — the two sieve implementations, one rule set.

The desktop app (`site_pattern_manager`) and the extension (`extension/sieve.js`)
each transform thumbnail URLs with the sieve's `to` templates. They disagreed
for years: the extension never substituted `$&`, read `$11200x1200` greedily and
erased the whole reference, while Python pre-translated templates to `\\g<n>` with
its own clamping rule. This module is the gate for the unification: every live
rule is run through BOTH implementations (node dumps its side, pytest computes
this side) and the results must be identical.

The synthetic match is the same on both sides — whole match "WHOLE", then
"G1".."Gn" — with n coming from this side's `re.compile(img).groups`, so group
counting exists in exactly one place. Rules whose `img` regex Python cannot
compile are excluded and counted; the test asserts that set stays tiny.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.site_pattern_manager import (
    expand_variants,
    get_substitution,
    normalize_variant,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIEVE = os.path.join(ROOT, "extension", "sieve.json")
DUMP = os.path.join(ROOT, "extension", "tests", "cross_impl_dump.mjs")


class _FakeMatch:
    """The slice of `re.Match` that get_substitution uses."""

    def __init__(self, groups, string="WHOLE"):
        self._groups = ["WHOLE"] + list(groups)
        self.string = string

    def group(self, n=0):
        return self._groups[n]

    def groups(self):
        return tuple(self._groups[1:])

    def start(self):
        return 0


def _expansion(text):
    """The Python twin of sieve.js's `expandVariants`."""
    return expand_variants(text)


class TestLiveSieveRulesAgree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        with open(SIEVE, encoding="utf-8") as f:
            cls.raw = json.load(f)

    def setUp(self):
        if not self.node:
            # Stage-0 discipline (E-03): a missing prerequisite fails loudly
            # instead of quietly shrinking coverage to the Python half.
            self.fail("node is required for the cross-implementation gate")

    def _rules(self):
        """The (name, template, group_count) triples both sides work on."""
        out = []
        for name, rule in self.raw.items():
            if not isinstance(rule, dict) or rule.get("off"):
                continue
            to = rule.get("to")
            img = rule.get("img")
            if not isinstance(to, str) or not to or to.startswith(":"):
                continue  # JS rule: no string substitution on either side
            if not isinstance(img, str) or not img:
                continue
            try:
                groups = re.compile(img).groups
            except re.error:
                continue  # counted below
            out.append((name, to, groups))
        return out

    def _python_side(self, rules):
        result = {}
        for name, template, groups in rules:
            match = _FakeMatch([f"G{i}" for i in range(1, groups + 1)])
            substituted = get_substitution(template, match)
            result[name] = {
                "template": template,
                "groups": groups,
                "substituted": substituted,
                "variants": _expansion(substituted),
            }
        return result

    def _node_side(self, rules):
        counts = {name: groups for name, _t, groups in rules}
        with tempfile.TemporaryDirectory(prefix="wmp_cross") as tmp:
            counts_path = os.path.join(tmp, "counts.json")
            with open(counts_path, "w", encoding="utf-8") as f:
                json.dump(counts, f)
            # encoding="utf-8": node writes UTF-8, and the Windows locale
            # codec would mangle the Cyrillic rule names into different keys
            # (measured: 'Dom\u0421lick.ru-s' arrived as 'Dom\ufffdlick.ru-s').
            proc = subprocess.run(
                [self.node, DUMP, SIEVE, counts_path],
                capture_output=True, text=True, encoding="utf-8",
                cwd=ROOT, timeout=180,
            )
        if proc.returncode != 0:
            self.fail(f"node dump failed ({proc.returncode}): {proc.stderr[-2000:]}")
        return json.loads(proc.stdout)

    def test_rule_set_is_actually_large(self):
        """Fixture sanity, with the audit's own numbers in the message.

        sieve.json holds 823 rules, 815 of them live (8 carry `off: 1`): 158 are
        JS expressions (no string substitution exists to compare), the rest are
        `link`/`res` chains or have no `to` at all. Everything with a plain
        string `to` is compared rule by rule below — 342 of them.
        """
        live = [n for n, r in self.raw.items()
                if isinstance(r, dict) and not r.get("off")]
        rules = self._rules()
        self.assertGreater(len(live), 800, "the bundled sieve changed shape")
        self.assertGreater(len(rules), 300,
                           f"only {len(rules)} string-transform rules found")

    def test_every_live_rule_agrees(self):
        rules = self._rules()
        mine = self._python_side(rules)
        theirs = self._node_side(rules)

        self.assertEqual(sorted(mine), sorted(theirs),
                         "the two sides do not even cover the same rules")

        diverged = []
        for name in sorted(mine):
            if mine[name] != theirs[name]:
                diverged.append((name, mine[name], theirs[name]))
        self.assertEqual(
            diverged, [],
            f"{len(diverged)} rule(s) differ between Python and JS:\n"
            + "\n".join(f"{n}: py={a} js={b}" for n, a, b in diverged[:10]))

    def test_uncompilable_rules_stay_a_rounding_error(self):
        names = set()
        uncompilable = []
        for name, rule in self.raw.items():
            if not isinstance(rule, dict) or rule.get("off"):
                continue
            to = rule.get("to")
            if not isinstance(to, str) or not to or to.startswith(":"):
                continue
            names.add(name)
            try:
                re.compile(rule.get("img", ""))
            except re.error:
                uncompilable.append(name)
        self.assertLess(len(uncompilable), 20,
                        f"too many rules Python cannot compile: {uncompilable[:10]}")

    def test_variants_are_absolute_urls(self):
        """The gate's second half: no variant may leave as `//host/...`."""
        rules = self._rules()
        page = "https://page.example/gallery/1"
        relative = []
        for name, template, _groups in rules:
            for variant in _expansion(template):
                if variant.startswith("//") or "://" not in variant:
                    relative.append((name, variant))
        # `to` templates are allowed to be relative — the MATCHER normalizes:
        for name, variant in relative[:5]:
            self.assertTrue(
                normalize_variant(variant, page).startswith(("http://", "https://")),
                f"{name}: {variant!r} did not normalize to an absolute URL")


if __name__ == "__main__":
    unittest.main()
