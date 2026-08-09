#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HTTP engine selection (P3): aiohttp/requests (default) vs curl_cffi.

Why P3 exists: the primary page fetch uses aiohttp and the sync fallback /
downloader use requests. Both present a python-OpenSSL TLS stack whose JA3/JA4
fingerprint (no GREASE, no HTTP/2, python UA) is trivially detectable by
Cloudflare/Akamai/DataDome-grade protection — they can block at the TLS
handshake, before any header is inspected. curl_cffi links against a patched
libcurl (curl-impersonate) that reproduces the exact TLS/HTTP2 fingerprint of a
real browser, so the sync paths become genuinely browser-like.

Design (minimum cost / maximum benefit):
  - The PRIMARY async page fetch stays aiohttp (fast, proven, zero risk).
  - The SYNC paths (parser fallback + gateway bypass + media downloader) use
    curl_cffi with impersonate="chrome" when the http_engine setting says so.
  - Fail-open: if curl_cffi is not installed, the setting silently falls back
    to requests with a one-line warning (same pattern as the Deno JS engine).
  - When impersonating, curl_cffi sets browser-consistent headers (UA,
    Sec-CH-UA, Accept, ...) itself. Callers must NOT override User-Agent with
    a custom string on impersonated sessions — a UA that mismatches the JA3
    profile defeats the impersonation (that's a detectable signal).
"""

import logging
import threading
from typing import Any, Dict, Tuple

from src import constants as K

logger = logging.getLogger(__name__)

# Default impersonation profile. "chrome" auto-tracks the latest Chrome
# profile shipped with the installed curl_cffi version.
DEFAULT_IMPERSONATE = "chrome"

# One-time fallback warning (fail-open without curl_cffi / with an unknown
# impersonate profile) — announced once, not per request.
_missing_warned = False

# Lazy import; curl_cffi is an optional dependency (opt-in engine).
try:
    from curl_cffi import requests as _curl_requests  # type: ignore
    CURL_CFFI_AVAILABLE = True
except Exception as e:  # pragma: no cover - depends on environment
    _curl_requests = None
    CURL_CFFI_AVAILABLE = False
    logger.debug(f"curl_cffi not available: {e}")


def engine_uses_curl(settings: Dict[str, Any]) -> bool:
    """True when the http_engine setting selects curl_cffi AND it is installed."""
    global _missing_warned
    if settings.get(K.SETTING_HTTP_ENGINE, K.DEFAULT_HTTP_ENGINE) != "curl_cffi":
        return False
    if not CURL_CFFI_AVAILABLE:
        if not _missing_warned:
            _missing_warned = True
            logger.warning(
                "http_engine=curl_cffi requested but curl_cffi is not installed — "
                "falling back to requests (aiohttp engine)."
            )
        return False
    return True


def impersonate_profile(settings: Dict[str, Any]) -> str:
    """Impersonation profile from settings (default 'chrome')."""
    return settings.get(K.SETTING_HTTP_IMPERSONATE, DEFAULT_IMPERSONATE) or DEFAULT_IMPERSONATE


def should_escalate(settings: Dict[str, Any], code) -> bool:
    """True when an HTTP status is an explicit-block signal worth one curl_cffi
    retry: 403 (canonical block) or 5xx (bot-protection often answers 5xx to
    non-browser TLS). 429 is never escalated — rate-limit backoff already exists
    and a different TLS fingerprint cannot help. Also never escalates when the
    primary engine is already curl_cffi (the block happened on curl itself; a
    second curl session is a wasted duplicate request).
    """
    if not settings.get(K.SETTING_HTTP_ESCALATE, K.DEFAULT_HTTP_ESCALATE):
        return False
    if engine_uses_curl(settings):
        return False
    if code is None:
        return False
    return code == 403 or 500 <= code < 600


def create_escalation_session(settings: Dict[str, Any]):
    """Create a curl_cffi session for explicit-block escalation (P3 auto-fallback).

    Unlike create_sync_session this does NOT depend on the global http_engine
    setting: it is used as a bounded second chance when a request was explicitly
    blocked (HTTP 403/5xx) on the regular stack. Returns None when curl_cffi is
    unavailable so callers can fail through to their normal error path.

    Bounded by construction: callers must attempt AT MOST ONE request through
    this session and then close it — never retry through it. This keeps the
    escalation invisible to the domain-health/quarantine counters (a successful
    escalation never reaches the failure counter; a failed one returns the same
    error as if no escalation had been attempted).
    """
    if not CURL_CFFI_AVAILABLE:
        return None
    profile = impersonate_profile(settings)
    try:
        return _curl_requests.Session(impersonate=profile)
    except Exception as e:  # pragma: no cover - depends on curl_cffi version
        logger.warning(f"curl_cffi escalation session unavailable ({e})")
        return None


def create_sync_session(settings: Dict[str, Any]):
    """Create a requests-compatible sync Session honoring http_engine.

    Returns a `curl_cffi.requests.Session` (impersonating the browser profile)
    when the engine is curl_cffi and available, otherwise a plain
    `requests.Session`. The returned object supports the requests API used by
    the callers (get/post/head with stream=True, iter_content, headers,
    cookies.set, proxies). Proxy + UA wiring is done by the caller exactly as
    it does today for requests — except UA must NOT be overridden on an
    impersonated session (see module docstring).

    Fail-open: if curl_cffi is unavailable OR the configured impersonate
    profile is unknown to the installed version, falls back to requests with a
    one-time warning — never raises into the caller's task.
    """
    global _missing_warned
    if engine_uses_curl(settings):
        profile = impersonate_profile(settings)
        try:
            logger.info(f"Creating curl_cffi sync session (impersonate={profile})")
            return _curl_requests.Session(impersonate=profile)
        except Exception as e:
            if not _missing_warned:
                _missing_warned = True
                logger.warning(
                    f"curl_cffi impersonate profile {profile!r} unavailable "
                    f"({e}) — falling back to requests (aiohttp engine)."
                )
    import requests
    return requests.Session()


# Exception tuples for `except` clauses that must cover BOTH engines.
# curl_cffi raises its own exception hierarchy (curl_cffi.requests.exceptions.*),
# which is NOT a subclass of requests.exceptions.* — callers that catch
# "network errors" must use these tuples instead of bare requests classes.
import requests as _requests

if CURL_CFFI_AVAILABLE:
    _CURL_REQ_EXC = _curl_requests.exceptions.RequestException
    _CURL_HTTP_EXC = _curl_requests.exceptions.HTTPError
    NETWORK_ERROR_EXCEPTIONS: Tuple[Any, ...] = (
        _requests.exceptions.RequestException, _CURL_REQ_EXC,
    )
    HTTP_ERROR_EXCEPTIONS: Tuple[Any, ...] = (
        _requests.exceptions.HTTPError, _CURL_HTTP_EXC,
    )
else:  # pragma: no cover - depends on environment
    NETWORK_ERROR_EXCEPTIONS = (_requests.exceptions.RequestException,)
    HTTP_ERROR_EXCEPTIONS = (_requests.exceptions.HTTPError,)


def attach_cookie_lock(session) -> None:
    """Attach a threading.Lock for concurrent cookie-jar writes (shared sessions)."""
    session._cookie_lock = threading.Lock()
