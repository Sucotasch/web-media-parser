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
