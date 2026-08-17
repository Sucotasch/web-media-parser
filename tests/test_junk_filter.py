#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for the P2-lite junk filter (src/parser/junk_filter.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser import junk_filter
from src.parser.utils import is_banner_or_ad, should_skip_crawl_url


# --- ad URL classification -----------------------------------------------

@pytest.mark.parametrize("url,page", [
    ("https://cdn.doubleclick.net/x/photo.jpg", "https://gallery.com/"),
    ("https://ads.googleadservices.com/i.jpg", "https://gallery.com/"),
    ("https://foo.taboola.com/img/1.jpg", "https://gallery.com/"),
    ("https://example.com/adsense/creative/2.jpg", "https://example.com/"),
    ("https://example.com/banner_300x250.jpg", "https://gallery.com/"),
])
def test_is_ad_url_positives(url, page):
    assert junk_filter.is_ad_url(url, page) is True, url


@pytest.mark.parametrize("url,page", [
    # LEGIT image hosts must never be flagged
    ("https://image.imx.to/u/i/2026/02/05/6mhuyv.jpeg", "https://vipergirls.to/"),
    ("https://i001.imx.to/i/2026/02/05/6mhuyv.jpeg", "https://vipergirls.to/"),
    ("https://imgbox.com/9b/9a/bKxus8Rp_o.jpg", "https://vipergirls.to/"),
    ("https://i.imgur.com/abc123.jpg", "https://imgur.com/"),
    ("https://pixhost.to/gallery/x/y.jpg", "https://forum.example/"),
    ("https://postimg.cc/abc/xyz.jpg", "https://forum.example/"),
    # CORE-4: "pixel-art" galleries and "creative-portfolio" hosts are
    # legit content — the old path tokens silently dropped them
    ("https://cdn.artstation.com/pixel-art/scene1.png", "https://artstation.com/"),
    ("https://i.imgur.com/creative-portfolio/shot.jpg", "https://imgur.com/"),
    # host token NOT a real ad network (suffix match precision)
    ("https://myadserver.example-gallery.com/photo.jpg", "https://example-gallery.com/"),
    # compound token (banner123 != banner) — precision
    ("https://example.com/banner123/photo.jpg", "https://example.com/"),
    # same-domain banner size = weak alone, never fires
    ("https://gallery.com/i/300x250/thumb.jpg", "https://gallery.com/"),
])
def test_is_ad_url_negatives(url, page):
    assert junk_filter.is_ad_url(url, page) is False, url


def test_is_ad_url_weak_third_party_size():
    # cross-domain banner size (no other signal) — weak+third-party fires
    assert junk_filter.is_ad_url(
        "https://cdn.other.net/i/300x250/1.jpg", "https://gallery.com/") is True
    # same-domain size alone stays safe
    assert junk_filter.is_ad_url(
        "https://gallery.com/i/300x250/1.jpg", "https://gallery.com/") is False


def test_is_ad_url_third_party_uses_registrable_domain():
    # CORE-6: bbc.co.uk vs evil.co.uk are DIFFERENT sites — a cross-site
    # banner pixel on a co.uk page is third-party and fires the weak rule.
    assert junk_filter.is_ad_url(
        "https://evil.co.uk/i/300x250/1.jpg", "https://bbc.co.uk/") is True
    # same registrable domain (subdomain) stays same-party
    assert junk_filter.is_ad_url(
        "https://img.bbc.co.uk/i/300x250/1.jpg", "https://bbc.co.uk/") is False


def test_is_ad_url_allowlist_override(monkeypatch):
    monkeypatch.setattr(junk_filter, "load_allowlist", lambda: {"doubleclick.net"})
    assert junk_filter.is_ad_url(
        "https://cdn.doubleclick.net/x.jpg", "https://gallery.com/") is False


# --- junk transitions -----------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://vipergirls.to/threads/newreply.php?do=newreply&p=264891380",
    "https://vipergirls.to/threads/search.php?search_type=1",
    "https://vipergirls.to/threads/newthread.php",
    "https://vipergirls.to/threads/usercp.php",
    "https://vipergirls.to/threads/members/424045-Pixel",
    "https://vipergirls.to/threads/16328688-x?p=264860330&viewfull=1",
    "https://site.com/wp-admin/index.php",
])
def test_should_skip_junk_url_positives(url):
    assert junk_filter.should_skip_junk_url(url) is True, url


@pytest.mark.parametrize("url", [
    # Hubs and content paths must NEVER be skipped (user feedback)
    "https://vipergirls.to/forum.php",
    "https://vipergirls.to/threads/16328688-Akiramai-x62-July-26-2026",
    "https://smf.example/index.php?board=12.0",           # SMF listing via index.php
    "https://vipergirls.to/album.php?albumid=123",          # vBulletin user albums = content
    "https://vipergirls.to/threads/album/photo.jpg",
])
def test_should_skip_junk_url_negatives(url):
    assert junk_filter.should_skip_junk_url(url) is False, url


def test_allowlist_passes_junk_transitions(monkeypatch):
    monkeypatch.setattr(junk_filter, "load_allowlist", lambda: {"vipergirls.to"})
    assert junk_filter.should_skip_junk_url(
        "https://vipergirls.to/threads/search.php") is False


# --- integration through existing entry points ----------------------------

def test_is_banner_or_ad_integration():
    # ad-network host flagged, legit host not
    assert is_banner_or_ad("https://cdn.doubleclick.net/x.jpg", {}) is True
    assert is_banner_or_ad("https://image.imx.to/u/i/x.jpeg", {}) is False


def test_should_skip_crawl_url_integration():
    # junk transition skipped, hub kept
    assert should_skip_crawl_url(
        "https://vipergirls.to/threads/newreply.php?do=newreply&p=1") is True
    assert should_skip_crawl_url("https://vipergirls.to/forum.php") is False
    assert should_skip_crawl_url(
        "https://vipergirls.to/threads/16328688-Akiramai") is False
