#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""P4 tests: JS-only consent/gateway button bypass.

Covers:
  - static consent-cookie extraction from onclick handlers (_extract_consent_cookies_from_js)
  - inline function-body resolution (_find_inline_function_body)
  - gateway candidate selection with JS-only buttons, extended blacklist and
    overlay scoping (_handle_gateways)
  - Deno gateway worker click (run_gateway_click) — skipped when the engine
    or the happy-dom cache is unavailable on this machine.
"""

import os
import sys
import asyncio

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bs4 import BeautifulSoup

from src.parser.webpage_parser import WebpageParser


class _DummySession:
    """Minimal stand-in for aiohttp.ClientSession (gateway logic makes no I/O)."""

    def __init__(self):
        self.cookie_jar = _DummyCookieJar()


class _DummyCookieJar:
    def update_cookies(self, *a, **k):
        pass


def _make_parser(html, context=None, pattern_manager=None):
    parser = WebpageParser(
        url="https://example.com/threads/123-test",
        settings={},
        process_js=False,
        external_session=_DummySession(),
        pattern_manager=pattern_manager,
        context=context or {},
    )
    soup = BeautifulSoup(html, "lxml")
    return parser, soup


# --- Level 1: static cookie extraction ------------------------------------


def test_extract_cookies_direct_document_cookie():
    parser, _ = _make_parser("<html></html>")
    cookies, reload_ = parser._extract_consent_cookies_from_js(
        "document.cookie='age_verified=1; path=/';location.reload();"
    )
    assert cookies == {"age_verified": "1"}
    assert reload_ is True


def test_extract_cookies_setcookie_helper():
    parser, _ = _make_parser("<html></html>")
    cookies, reload_ = parser._extract_consent_cookies_from_js(
        "setCookie('consent','true',365);"
    )
    assert cookies == {"consent": "true"}
    assert reload_ is False


def test_extract_cookies_multiple_assignments():
    parser, _ = _make_parser("<html></html>")
    cookies, _ = parser._extract_consent_cookies_from_js(
        "document.cookie='a=1;path=/';document.cookie='b=2'"
    )
    assert cookies == {"a": "1", "b": "2"}


def test_extract_cookies_no_cookie_returns_empty():
    parser, _ = _make_parser("<html></html>")
    cookies, reload_ = parser._extract_consent_cookies_from_js(
        "alert('hello'); this.closest('div').remove();"
    )
    assert cookies == {}
    assert reload_ is False


def test_extract_cookies_ignores_single_quote_in_pairs():
    parser, _ = _make_parser("<html></html>")
    cookies, _ = parser._extract_consent_cookies_from_js(
        "document.cookie=\"oops='x'\";document.cookie='ok=1'"
    )
    assert cookies == {"ok": "1"}


# --- Level 1: inline function resolution -----------------------------------


def test_find_inline_function_body():
    parser, _ = _make_parser("<html></html>")
    body = parser._find_inline_function_body(
        BeautifulSoup("<script>function acceptCookies(){document.cookie='consent=1';}</script>", "lxml"),
        "acceptCookies",
    )
    assert body is not None and "consent=1" in body


def test_find_inline_function_body_arrow():
    parser, _ = _make_parser("<html></html>")
    body = parser._find_inline_function_body(
        BeautifulSoup("<script>const accept = () => { document.cookie='ok=1'; }</script>", "lxml"),
        "accept",
    )
    assert body is not None and "ok=1" in body


def test_find_inline_function_body_missing_returns_none():
    parser, _ = _make_parser("<html></html>")
    assert parser._find_inline_function_body(
        BeautifulSoup("<script>function other(){}</script>", "lxml"), "ghost") is None


# --- Level 1: gateway candidate selection ----------------------------------


def test_js_only_button_selected_as_js_candidate():
    html = """<html><body>
      <div class="age-gate">
        <button onclick="document.cookie='age_verified=1';location.reload();">I am 18+</button>
      </div>
    </body></html>"""
    parser, soup = _make_parser(html)
    action = asyncio.run(parser._handle_gateways(soup))
    assert action is not None
    assert action.get("kind") == "js"
    assert action["cookies"] == {"age_verified": "1"}
    assert action["needs_reload"] is True


def test_div_onclick_candidate_collected():
    html = """<html><body>
      <div class="age-gate"><div onclick="document.cookie='over18=1'">Agree</div></div>
    </body></html>"""
    parser, soup = _make_parser(html)
    action = asyncio.run(parser._handle_gateways(soup))
    assert action is not None and action.get("kind") == "js"
    assert action["cookies"] == {"over18": "1"}


def test_login_button_not_selected():
    # A login modal next to the consent gate must never be chosen.
    html = """<html><body>
      <div class="age-gate">
        <button onclick="document.cookie='age_verified=1'">I am 18+</button>
      </div>
      <div class="modal">
        <button onclick="doLogin()">Sign in</button>
      </div>
    </body></html>"""
    parser, soup = _make_parser(html)
    action = asyncio.run(parser._handle_gateways(soup))
    assert action is not None
    assert "login" not in action.get("text", "")


def test_password_form_skipped():
    html = """<html><body>
      <form action="/login"><input type="password" name="p">
        <button type="submit">Continue</button></form>
      <div class="age-gate"><button onclick="document.cookie='age=1'">Agree</button></div>
    </body></html>"""
    parser, soup = _make_parser(html)
    action = asyncio.run(parser._handle_gateways(soup))
    assert action is not None and action.get("kind") == "js"


def test_password_form_skipped_without_overlay():
    # No overlay selector on the page — suspicion comes from the consent
    # phrase + media<2, so the whole page is scanned. The password form's
    # "Continue" submit must NOT be picked (the password-input skip is what
    # protects here, not overlay scoping).
    html = """<html><body>
      <h1>Please accept our cookie policy to continue</h1>
      <form action="/login"><input type="password" name="p">
        <button type="submit" onclick="document.cookie='sess=1'">Continue</button></form>
    </body></html>"""
    parser, soup = _make_parser(html)
    action = asyncio.run(parser._handle_gateways(soup))
    # The only JS candidate is inside a password form → nothing to bypass.
    assert action is None or action.get("url") != "https://example.com/login"


def test_overlay_scoping_avoids_page_wide_false_positive():
    # A "Continue" link in the page body must not be clicked when the gateway
    # overlay is present — candidate search is scoped to the overlay root.
    html = """<html><body>
      <div class="age-gate"><button onclick="document.cookie='age=1'">Agree</button></div>
      <a href="/continue">Continue</a>
    </body></html>"""
    parser, soup = _make_parser(html)
    action = asyncio.run(parser._handle_gateways(soup))
    assert action is not None
    if action.get("kind") == "js":
        assert action["cookies"] == {"age": "1"}
    else:
        # Must NOT have picked the out-of-overlay "Continue" link.
        assert action.get("url") != "https://example.com/continue"


def test_plain_page_with_consent_word_not_suspicious():
    html = """<html><body>
      <h1>We use cookies to improve your experience</h1>
      <p>Our terms of service explain everything.</p>
      <img src="/content/photo-1.jpg" width="640" height="480">
      <img src="/content/photo-2.jpg" width="640" height="480">
    </body></html>"""
    parser, soup = _make_parser(html)
    asyncio.run(parser._extract_images(soup))
    action = asyncio.run(parser._handle_gateways(soup))
    assert action is None


# --- Level 2: Deno gateway worker -----------------------------------------


def _gateway_engine_or_skip():
    from src.parser.js_engine import DenoJsEngine
    engine = DenoJsEngine()
    if not engine.dom_available():
        pytest.skip("Deno DOM worker / happy-dom cache not available on this machine")
    return engine


def test_gateway_click_sets_cookie_and_mutates_dom():
    engine = _gateway_engine_or_skip()
    try:
        html = """<!doctype html><html><body>
          <div class="age-gate" id="gate">
            <button onclick="document.cookie='age_verified=1';document.getElementById('gate').style.display='none';">I am 18+</button>
          </div>
          <img id="c" src="/content/photo.jpg" width="640" height="480">
        </body></html>"""
        result = engine.run_gateway_click(
            html=html,
            page_url="https://example.com/",
            text_patterns=["i am 18", "i agree", "agree", "accept"],
            overlay_selectors=[".age-gate", "#consent-modal"],
        )
        assert result is not None
        assert result["cookies"].get("age_verified") == "1"
        # DOM mutated: overlay hidden / content present
        assert "display: none" in result.get("html_after", "") or "photo.jpg" in result.get("html_after", "")
    finally:
        engine.shutdown()


def test_gateway_click_no_button_returns_none():
    engine = _gateway_engine_or_skip()
    try:
        html = "<html><body><p>No buttons here</p></body></html>"
        result = engine.run_gateway_click(
            html=html, page_url="https://example.com/",
            text_patterns=["i agree"], overlay_selectors=[".age-gate"],
        )
        assert result is None
    finally:
        engine.shutdown()


def test_gateway_click_login_button_not_clicked():
    engine = _gateway_engine_or_skip()
    try:
        html = """<html><body>
          <button onclick="document.cookie='session=1'">Sign in</button>
          <button onclick="document.cookie='consent=1'">I Agree</button>
        </body></html>"""
        result = engine.run_gateway_click(
            html=html, page_url="https://example.com/",
            text_patterns=["i agree", "agree", "accept"], overlay_selectors=[],
        )
        assert result is not None
        assert result["cookies"].get("consent") == "1"
        assert "session" not in result["cookies"]
    finally:
        engine.shutdown()
