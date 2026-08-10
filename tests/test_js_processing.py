#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
from src.parser.webpage_parser import WebpageParser
import asyncio
from bs4 import BeautifulSoup


class _DummySession:
    """Minimal stand-in for aiohttp.ClientSession (icon parsing makes no I/O)."""

    def __init__(self):
        self.cookie_jar = _DummyCookieJar()


class _DummyCookieJar:
    def update_cookies(self, *a, **k):
        pass


def _make_parser(html):
    parser = WebpageParser(
        url="https://example.com/threads/123-test",
        settings={},
        process_js=False,
        external_session=_DummySession(),
        pattern_manager=None,
        context={},
    )
    soup = BeautifulSoup(html, "lxml")
    return parser, soup


async def _extract(parser, soup):
    await parser._extract_images(soup)
    return parser.media_files


def _make_parser_html(html, url="https://example.com/threads/123-test", process_js=False):
    parser = WebpageParser(
        url=url,
        settings={},
        process_js=process_js,
        external_session=_DummySession(),
        pattern_manager=None,
        context={},
    )
    soup = BeautifulSoup(html, "lxml")
    return parser, soup


async def _dynamic(parser, soup):
    await parser._handle_dynamic_content(soup)
    return parser.media_files


def test_preview_transition_skipped_in_dynamic_scan():
    """A thumbnail img wrapped in <a href=viewer-page> is a transition preview:
    the dynamic-content scans (data-src lazy-load, data-* attrs, JS regex)
    must NOT queue it as media — the linked page will yield the fullsize.
    Mirrors the parent-link logic already in _extract_images."""
    html = """
    <html><body>
      <a href="/albums/name-1/53000424.html">
        <img src="/img/1.png" data-src="https://t1.pictoa.com/media/g/1/355244158c9f50783375.jpg" width="320" height="400">
      </a>
      <a href="https://t1.pictoa.com/media/g/1/real.jpg">
        <img src="/img/1.png" data-src="https://t1.pictoa.com/media/g/1/real.jpg">
      </a>
      <img src="/img/1.png" data-src="https://t1.pictoa.com/media/g/2/standalone.jpg">
      <script>var data = "https://t1.pictoa.com/media/g/1/355244158c9f50783375.jpg";</script>
    </body></html>
    """
    parser, soup = _make_parser_html(html, process_js=True)
    media = asyncio.run(_dynamic(parser, soup))
    urls = [u for _, u, _ in media]
    # Transition preview (parent link to a page) is skipped, including its JS echo.
    assert not any("355244158c9f50783375" in u for u in urls), urls
    # Direct media link IS kept (it is the content, not a transition).
    assert any("real.jpg" in u for u in urls), urls
    # Standalone lazy img without a parent link is still extracted.
    assert any("standalone.jpg" in u for u in urls), urls


def test_px_sized_icons_filtered_at_parse():
    """width/height attrs with a px suffix must still feed the min-dimension
    filter (int('78px') used to raise -> dimensions dropped -> icon passed).
    Relative units (%/em) keep the old fail-open behavior."""
    html = """
    <html><body>
      <img src="https://cdn.example.com/icons/messenger-cam.png" width="78px" height="78px">
      <img src="https://cdn.example.com/full/photo1.jpg" width="800px" height="1200px">
      <img src="https://cdn.example.com/full/wideshot.jpg" width="100%" height="100%">
    </body></html>
    """
    parser, soup = _make_parser_html(html)
    media = asyncio.run(_extract(parser, soup))
    urls = [u for _, u, _ in media]
    assert not any("messenger-cam.png" in u for u in urls), urls
    assert any("photo1.jpg" in u for u in urls), urls
    # % is ambiguous -> fail-open (not filtered by the small-icon dimension gate)
    assert any("wideshot.jpg" in u for u in urls), urls


def test_apple_touch_icons_filtered_at_parse():
    """P2-lite: the vBulletin apple-touch-icon family (57..180) must be dropped
    at parse time, not reach the download queue (observed: 12 favicon failures
    per page). A normal <img> must still be kept."""
    html = """<html><head>
      <link rel="apple-touch-icon" sizes="57x57" href="/apple-touch-icon-57x57.png">
      <link rel="apple-touch-icon" sizes="152x152" href="/apple-touch-icon-152x152.png">
      <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon-180x180.png">
      <link rel="icon" href="/favicon.ico">
    </head><body>
      <img src="/content/photo-1.jpg" width="640" height="480">
      <img src="/content/photo-2.jpg" width="640" height="480">
    </body></html>"""
    parser, soup = _make_parser(html)
    media = asyncio.run(_extract(parser, soup))
    urls = {u for _, u, _ in media}
    assert urls == {"https://example.com/content/photo-1.jpg",
                    "https://example.com/content/photo-2.jpg"}, urls


def test_regular_link_tag_not_treated_as_media():
    """Icons with sizes="any" or no sizes must not leak into media either."""
    html = """<html><head>
      <link rel="icon" href="/images/site-icon.png">
      <link rel="apple-touch-icon" sizes="any" href="/logo-512.png">
    </head><body><img src="/img/real.jpg" width="640" height="480"></body></html>"""
    parser, soup = _make_parser(html)
    media = asyncio.run(_extract(parser, soup))
    urls = {u for _, u, _ in media}
    assert urls == {"https://example.com/img/real.jpg"}, urls


@pytest.mark.skip(reason="Requires network access and external session — manual test only")
def test_js_processing():
    """Test that JavaScript processing works correctly.
    
    Requires network and aiohttp session — not suitable for automated CI.
    Run manually: python -m pytest tests/test_js_processing.py -v -k test_js_processing --no-header -rN
    """
    pass
