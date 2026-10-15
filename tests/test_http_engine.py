#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for P3: http_engine selection (aiohttp default vs curl_cffi impersonation).

Covers:
  - engine_uses_curl() honors the setting and fails open without curl_cffi
  - create_sync_session() returns the right session type per engine
  - impersonate profile default + override
  - exception tuples cover both requests and curl_cffi hierarchies
  - create_shared_downloader_session() and MediaDownloader._create_session()
    build the configured engine's session and keep proxy wiring
"""

import sys
import os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import constants as K
from src.parser import http_engine
from src.downloader.media_downloader import (
    MediaDownloader,
    create_shared_downloader_session,
)


# --- engine_uses_curl -------------------------------------------------------

def test_default_engine_is_aiohttp():
    assert K.DEFAULT_HTTP_ENGINE == "aiohttp"
    assert http_engine.engine_uses_curl({}) is False
    assert http_engine.engine_uses_curl({K.SETTING_HTTP_ENGINE: "aiohttp"}) is False


def test_engine_uses_curl_when_enabled():
    settings = {K.SETTING_HTTP_ENGINE: "curl_cffi"}
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    assert http_engine.engine_uses_curl(settings) is True


def test_engine_fails_open_without_curl(monkeypatch):
    """When curl_cffi is missing, the setting must NOT crash — just fall back."""
    monkeypatch.setattr(http_engine, "CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(http_engine, "_curl_requests", None)
    settings = {K.SETTING_HTTP_ENGINE: "curl_cffi"}
    assert http_engine.engine_uses_curl(settings) is False


# --- impersonate profile ----------------------------------------------------

def test_impersonate_profile_default():
    assert http_engine.impersonate_profile({}) == "chrome"
    assert http_engine.impersonate_profile({K.SETTING_HTTP_IMPERSONATE: "safari"}) == "safari"


# --- create_sync_session ----------------------------------------------------

def test_create_sync_session_requests_by_default():
    session = http_engine.create_sync_session({})
    import requests
    assert isinstance(session, requests.Session)
    session.close()


def test_create_sync_session_curl_when_enabled():
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    session = http_engine.create_sync_session({K.SETTING_HTTP_ENGINE: "curl_cffi"})
    assert type(session).__module__.startswith("curl_cffi")
    session.close()


def test_create_sync_session_requests_when_curl_missing(monkeypatch):
    monkeypatch.setattr(http_engine, "CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(http_engine, "_curl_requests", None)
    import requests
    session = http_engine.create_sync_session({K.SETTING_HTTP_ENGINE: "curl_cffi"})
    assert isinstance(session, requests.Session)
    session.close()


def test_create_sync_session_fails_open_on_bad_profile(monkeypatch):
    """An unknown impersonate profile must never crash session creation.

    curl_cffi 0.14 silently accepts any profile string; older releases raised
    ValueError. Our guard must be robust for both: a bad profile either falls
    back to requests (raise path) or produces a usable curl session (silent
    path). The invariant is "no exception escapes to the caller".
    """
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    import requests
    monkeypatch.setattr(http_engine, "_missing_warned", False)
    settings = {
        K.SETTING_HTTP_ENGINE: "curl_cffi",
        K.SETTING_HTTP_IMPERSONATE: "definitely-not-a-real-profile-xyz",
    }
    session = http_engine.create_sync_session(settings)  # must not raise
    try:
        assert isinstance(session, (requests.Session,)) or type(session).__module__.startswith("curl_cffi")
    finally:
        session.close()


# --- exception tuples -------------------------------------------------------

def test_network_error_exceptions_covers_both_engines():
    import requests as _r
    assert _r.exceptions.RequestException in http_engine.NETWORK_ERROR_EXCEPTIONS
    if http_engine.CURL_CFFI_AVAILABLE:
        assert http_engine._CURL_REQ_EXC in http_engine.NETWORK_ERROR_EXCEPTIONS
    assert http_engine.HTTP_ERROR_EXCEPTIONS
    assert _r.exceptions.HTTPError in http_engine.HTTP_ERROR_EXCEPTIONS


# --- downloader session factory --------------------------------------------

def test_shared_downloader_session_default_is_requests():
    session = create_shared_downloader_session({})
    import requests
    assert isinstance(session, requests.Session)
    assert getattr(session, "_cookie_lock", None) is not None
    session.close()


def test_shared_downloader_session_curl_when_enabled():
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    session = create_shared_downloader_session({K.SETTING_HTTP_ENGINE: "curl_cffi"})
    assert type(session).__module__.startswith("curl_cffi")
    assert getattr(session, "_cookie_lock", None) is not None
    session.close()


def test_shared_downloader_session_proxy_wiring():
    """Proxy must be applied to the curl session too (session.proxies)."""
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    settings = {
        K.SETTING_HTTP_ENGINE: "curl_cffi",
        K.SETTING_PROXY: "127.0.0.1:9999",
    }
    session = create_shared_downloader_session(settings)
    try:
        proxies = session.proxies or {}
        assert proxies.get("https") == "http://127.0.0.1:9999"
    finally:
        session.close()


def test_local_downloader_session_curl_when_enabled():
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    dl = MediaDownloader(
        url="https://example.com/i.jpg",
        filepath="/tmp/i.jpg",
        settings={K.SETTING_HTTP_ENGINE: "curl_cffi"},
        media_type="image",
        source_url="https://example.com/page",
    )
    assert type(dl.session).__module__.startswith("curl_cffi")
    # Per-file headers must still be applied on top of the impersonated session.
    assert dl.session.headers.get("Accept")
    assert dl.session.headers.get("Referer") == "https://example.com/page"
    dl.session.close()


def test_local_downloader_session_default_is_requests():
    dl = MediaDownloader(
        url="https://example.com/i.jpg",
        filepath="/tmp/i.jpg",
        settings={},
        media_type="image",
    )
    import requests
    assert isinstance(dl.session, requests.Session)
    dl.session.close()


# --- P3 auto-escalation (bounded single attempt on explicit block) -----------

def test_should_escalate_statuses():
    dl = MediaDownloader(
        url="https://example.com/i.jpg", filepath="/tmp/i.jpg", settings={}
    )
    # 403 and 5xx are explicit-block signals -> escalate
    assert dl._should_escalate(403) is True
    assert dl._should_escalate(500) is True
    assert dl._should_escalate(503) is True
    # 429 (rate limit) and other 4xx are never escalated
    assert dl._should_escalate(429) is False
    assert dl._should_escalate(404) is False
    assert dl._should_escalate(401) is False
    assert dl._should_escalate(None) is False


def test_should_escalate_disabled_by_setting():
    dl = MediaDownloader(
        url="https://example.com/i.jpg", filepath="/tmp/i.jpg",
        settings={K.SETTING_HTTP_ESCALATE: False},
    )
    assert dl._should_escalate(403) is False


def test_should_escalate_off_when_engine_is_curl(monkeypatch):
    """No escalation when the primary engine is already curl_cffi — the block
    happened on curl itself; a second curl session is a wasted duplicate."""
    monkeypatch.setattr(http_engine, "CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(http_engine, "_curl_requests", type("M", (), {"Session": lambda **k: None})())
    dl = MediaDownloader(
        url="https://example.com/i.jpg", filepath="/tmp/i.jpg",
        settings={K.SETTING_HTTP_ENGINE: "curl_cffi"},
    )
    assert dl._should_escalate(403) is False
    assert dl._should_escalate(503) is False
    assert http_engine.should_escalate({K.SETTING_HTTP_ENGINE: "curl_cffi"}, 403) is False
    assert http_engine.should_escalate({K.SETTING_HTTP_ENGINE: "aiohttp"}, 403) is True
    assert http_engine.should_escalate({}, 429) is False


def test_escalate_get_is_single_attempt(monkeypatch):
    """_try_escalate_get is bounded: called once per download, guarded."""
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    dl = MediaDownloader(
        url="https://example.com/i.jpg", filepath="/tmp/i.jpg", settings={}
    )
    dl.session = MagicMock()

    class FakeResp:
        status_code = 200
        def close(self):
            pass

    fake_session = MagicMock()
    fake_session.get.return_value = FakeResp()

    def fake_create(settings):
        return fake_session

    monkeypatch.setattr(http_engine, "create_escalation_session", fake_create)
    monkeypatch.setattr(dl, "_escalation_tried", False)
    first = dl._try_escalate_get({}, 10)
    second = dl._try_escalate_get({}, 10)  # must be a no-op (guarded)
    assert first is True
    assert second is False
    assert fake_session.get.call_count == 1
    assert dl._escalation_tried is True
    dl._close_escalation_session()


def test_escalate_get_fails_open_when_curl_missing(monkeypatch):
    """Escalation without curl_cffi returns False — caller fails through."""
    dl = MediaDownloader(
        url="https://example.com/i.jpg", filepath="/tmp/i.jpg", settings={}
    )
    monkeypatch.setattr(dl, "_escalation_tried", False)
    monkeypatch.setattr(http_engine, "create_escalation_session", lambda s: None)
    assert dl._try_escalate_get({}, 10) is False


def test_escalate_get_rejects_http_error(monkeypatch):
    """Escalation GET answering 4xx/5xx returns False (no success)."""
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    dl = MediaDownloader(
        url="https://example.com/i.jpg", filepath="/tmp/i.jpg", settings={}
    )
    dl.session = MagicMock()
    monkeypatch.setattr(dl, "_escalation_tried", False)

    class FakeResp403:
        status_code = 403
        def close(self):
            pass

    fake_session = MagicMock()
    fake_session.get.return_value = FakeResp403()
    monkeypatch.setattr(http_engine, "create_escalation_session", lambda s: fake_session)
    assert dl._try_escalate_get({}, 10) is False
    assert dl._escalation_tried is True  # still counted — single attempt


def test_webpage_parser_escalate_disabled_returns_none():
    """Parser escalation returns (None, status, False) immediately when disabled."""
    import asyncio
    from src.parser.webpage_parser import WebpageParser
    p = WebpageParser(
        url="https://example.com/",
        settings={K.SETTING_HTTP_ESCALATE: False},
        process_js=False,
        external_session=MagicMock(),
    )
    out = asyncio.run(p._try_escalate_fetch())
    assert out == (None, None, False)


def test_webpage_parser_escalate_binary_flag(monkeypatch):
    """Escalation returning image/* sets is_binary=True (content-type, not bytes)."""
    if not http_engine.CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed")
    import asyncio
    from src.parser.webpage_parser import WebpageParser
    p = WebpageParser(
        url="https://example.com/",
        settings={},
        process_js=False,
        external_session=MagicMock(),
    )

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "image/jpeg"}
        content = b""

    fake_session = MagicMock()
    fake_session.get.return_value = FakeResp()
    monkeypatch.setattr(http_engine, "create_escalation_session", lambda s: fake_session)
    out = asyncio.run(p._try_escalate_fetch())
    assert out == (b"", 200, True)
# --- B-14: one-shot warning flags under concurrency --------------------------

def test_one_shot_warnings_are_emitted_once_under_concurrency(caplog, monkeypatch):
    """B-14: 16 threads × 10 rounds produce exactly ONE warning per flag.

    Companion to the deterministic lock tests below, which are the actual
    detectors: this one stresses the same paths for the properties a stress test
    can see — no deadlock, no exception, no duplicate line on this scheduler.
    Measured: it stays green even without the lock (the GIL keeps the
    check-then-set window too narrow to hit reliably), so it is deliberately not
    presented as a race detector.
    """
    import logging
    import sys
    import threading

    from src.parser import http_engine as he

    with he._warn_lock:
        he._missing_warned = False
        he._unverified_warned = False
    monkeypatch.setattr(he, "CURL_CFFI_AVAILABLE", False)

    settings = {K.SETTING_HTTP_ENGINE: "curl_cffi", K.SETTING_VERIFY_TLS: False}
    barrier = threading.Barrier(16)
    errors = []

    def worker():
        try:
            for _ in range(10):
                barrier.wait()
                he.engine_uses_curl(settings)
                he.tls_verify(settings)
        except Exception as e:  # a lock bug would surface as a deadlock/error
            errors.append(e)

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    threads = [threading.Thread(target=worker) for _ in range(16)]
    try:
        with caplog.at_level(logging.WARNING, logger="src.parser.http_engine"):
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
    finally:
        sys.setswitchinterval(interval)

    assert not errors, f"workers raised: {errors}"
    assert not any(t.is_alive() for t in threads), "a worker hung (deadlock?)"

    fallback = [r for r in caplog.records if "curl_cffi is not installed" in r.getMessage()]
    unverified = [r for r in caplog.records if "TLS verification is disabled" in r.getMessage()]
    assert len(fallback) == 1, f"curl_cffi fallback warning logged {len(fallback)} times"
    assert len(unverified) == 1, f"unverified-TLS warning logged {len(unverified)} times"
def test_warning_decision_for_unverified_tls_is_made_under_the_lock(monkeypatch):
    """B-14 (detector): the flag+warning pair must serialize on `_warn_lock`.

    Deterministic, unlike a stress test: the test holds `_warn_lock` itself, so
    a caller that does not take the lock logs immediately and is caught here,
    while a caller that does take it cannot log until the lock is released.
    """
    import threading

    from src.parser import http_engine as he

    he._unverified_warned = False
    logged = threading.Event()
    real_warning = he.logger.warning

    def spy(*args, **kwargs):
        logged.set()
        real_warning(*args, **kwargs)

    monkeypatch.setattr(he.logger, "warning", spy)
    settings = {K.SETTING_VERIFY_TLS: False}

    with he._warn_lock:
        t = threading.Thread(target=lambda: he.tls_verify(settings))
        t.start()
        assert not logged.wait(0.3), (
            "tls_verify logged its one-shot warning while _warn_lock was held — "
            "the one-shot decision is not protected"
        )
    t.join(timeout=5)
    assert logged.is_set(), "tls_verify never logged after the lock was released"


def test_warning_decision_for_missing_curl_is_made_under_the_lock(monkeypatch):
    """B-14 (detector): the curl_cffi fallback warning is protected too."""
    import threading

    from src.parser import http_engine as he

    with he._warn_lock:
        he._missing_warned = False
    monkeypatch.setattr(he, "CURL_CFFI_AVAILABLE", False)
    logged = threading.Event()
    real_warning = he.logger.warning

    def spy(*args, **kwargs):
        logged.set()
        real_warning(*args, **kwargs)

    monkeypatch.setattr(he.logger, "warning", spy)
    settings = {K.SETTING_HTTP_ENGINE: "curl_cffi"}

    with he._warn_lock:
        t = threading.Thread(target=lambda: he.engine_uses_curl(settings))
        t.start()
        assert not logged.wait(0.3), (
            "engine_uses_curl logged the fallback warning while _warn_lock was "
            "held — the one-shot decision is not protected"
        )
    t.join(timeout=5)
    assert logged.is_set(), "engine_uses_curl never logged after the lock was released"
