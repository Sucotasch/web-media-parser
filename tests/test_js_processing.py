#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from src.parser.webpage_parser import WebpageParser
import asyncio
from bs4 import BeautifulSoup

from helpers import _DummySession


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


def test_framework_lazy_attributes_extracted():
    """CORE-8: framework lazy-load markers (vue v-lazy, angular lazyLoad,
    react data-src) are detected via attributes instead of re-serializing each
    element with str(elem) — the scan must keep extracting them."""
    html = """
    <html><body>
      <img v-lazy="https://example.com/vue-img.jpg">
      <img lazyLoad="https://example.com/ng-img.jpg">
      <img data-src="https://example.com/react-img.jpg" class="lazy-load">
    </body></html>
    """
    parser, soup = _make_parser_html(html, process_js=True)
    media = asyncio.run(_dynamic(parser, soup))
    urls = [u for _, u, _ in media]
    # v-lazy / lazyLoad are NOT covered by the lazy-data-* loop, so they prove
    # the framework path still fires after the str(elem) removal.
    assert "https://example.com/vue-img.jpg" in urls, urls
    assert "https://example.com/ng-img.jpg" in urls, urls
    assert "https://example.com/react-img.jpg" in urls, urls


def test_framework_attr_present_uses_attributes():
    """CORE-8: the attribute-based framework marker check mirrors what
    _process_framework_element reads per framework."""
    parser, _ = _make_parser_html("<html></html>")
    el_vue = BeautifulSoup('<img v-lazy="x">', "lxml").find("img")
    el_angular = BeautifulSoup('<img ng-src="x">', "lxml").find("img")
    el_plain = BeautifulSoup('<img src="x">', "lxml").find("img")
    assert parser._framework_attr_present(el_vue, "vue") is True
    assert parser._framework_attr_present(el_vue, "react") is False
    assert parser._framework_attr_present(el_angular, "angular") is True
    assert parser._framework_attr_present(el_plain, "react") is False
    assert parser._framework_attr_present(el_plain, "vue") is False
    assert parser._framework_attr_present(el_plain, "angular") is False


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

