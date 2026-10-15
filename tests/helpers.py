#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared test helpers (TST-4): these were copy-pasted into 8 test files.

TST-5: SIEVE_PATH is the single canonical Imagus-sieve snapshot — the newest
file wins rule-name dedup in SitePatternManager, so every sieve-dependent test
pins the July build (previously April and July were pinned in different files).
"""

import os

SIEVE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Imagus_sieve_2026.07.15_823.json",
)


def require_sieve():
    """E-03 (2026-10-15): fail loudly when the pinned sieve snapshot is missing.

    `SitePatternManager` guards its sieve path with `os.path.exists(...)`, so a
    moved/renamed snapshot used to surface as `TypeError: 'NoneType' object is
    not subscriptable` deep inside a test (or as a silent `pytest.skip`)
    instead of "prerequisite missing".
    """
    if not os.path.exists(SIEVE_PATH):
        raise AssertionError(
            f"Imagus sieve snapshot missing: {SIEVE_PATH} — the sieve-dependent "
            f"tests cannot run (pin lives in tests/helpers.py SIEVE_PATH)."
        )
    return SIEVE_PATH


class MockGUILogHandler:
    """Minimal log-handler stand-in (accepts any constructor args)."""

    def __init__(self, *args, **kwargs):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass

    def debug(self, msg):
        pass


class _DummyCookieJar:
    def update_cookies(self, *a, **k):
        pass


class _DummySession:
    """Minimal stand-in for aiohttp.ClientSession (parser tests make no I/O)."""

    def __init__(self):
        self.cookie_jar = _DummyCookieJar()
