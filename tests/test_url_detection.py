#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test URL detection functions
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.utils import (
    is_media_url, is_image_url, is_video_url, is_same_domain, registrable_domain,
    is_banner_or_ad, should_skip_crawl_url,
)


def test_media_url_detection():
    """Test media URL detection"""
    # Valid media URLs
    assert is_media_url("https://example.com/image.jpg")
    assert is_media_url("https://example.com/video.mp4")
    assert is_media_url("https://example.com/document.pdf")
    assert is_media_url("https://example.com/images/photo.png")
    assert is_media_url("https://cdn.example.com/image.jpg")
    
    # URLs that should NOT be detected as media
    assert not is_media_url("https://example.com/page.html")
    assert not is_media_url("https://example.com/index.php")
    assert not is_media_url("https://example.com/article.asp")
    assert not is_media_url("https://example.com/profile.jsp")
    assert not is_media_url("https://example.com/gallery.aspx")
    
    # Special cases
    assert is_media_url("https://example.com/images/gallery/")
    assert is_media_url("https://example.com/download/file?name=image.jpg")


def test_image_url_detection():
    """Test image URL detection"""
    assert is_image_url("https://example.com/image.jpg")
    assert is_image_url("https://example.com/photo.png")
    assert not is_image_url("https://example.com/video.mp4")
    assert not is_image_url("https://example.com/page.html")


def test_video_url_detection():
    """Test video URL detection"""
    assert is_video_url("https://example.com/video.mp4")
    assert is_video_url("https://example.com/movie.webm")
    assert not is_video_url("https://example.com/image.jpg")
    assert not is_video_url("https://example.com/page.html")


def test_is_banner_or_ad_pixel_art_allowed():
    """CORE-4 completion / CORE-15: the utils._AD_KEYWORDS duplicate of the
    'pixel' token silently re-dropped pixel-art galleries via is_banner_or_ad
    even after junk_filter was fixed. Real ads must still be flagged."""
    assert not is_banner_or_ad("https://cdn.artstation.com/pixel-art/scene1.png", {})
    assert not is_banner_or_ad("https://i.imgur.com/creative-portfolio/shot.jpg", {})
    assert is_banner_or_ad("https://ads.example.com/banner_300x250.gif", {})
    assert is_banner_or_ad("https://doubleclick.net/dclk/xyz", {})


def test_should_skip_crawl_url_ad_hosts_no_substring_false_positive():
    """CORE-15: ad-network hosts are skipped via junk_filter (dot-boundary);
    the old substring _AD_HOSTS rule no longer matches facebook.com/try-this."""
    assert should_skip_crawl_url("https://doubleclick.net/foo")
    assert should_skip_crawl_url("https://adservice.google.com/foo")
    assert not should_skip_crawl_url("https://facebook.com/try-this")
    assert not should_skip_crawl_url("https://example.com/gallery")


def test_is_image_url_query_string_handling():
    """CORE-16: the dead f"{ext}?" in path checks were removed (path from
    urlparse never contains '?') — detection of a media extension in the PATH
    with a separate query string must be unchanged."""
    assert is_image_url("https://example.com/img.jpg?size=large&x=1")
    assert is_image_url("https://example.com/a/b/photo.png?v=2")
    assert is_video_url("https://example.com/video.mp4?t=10")


def test_media_url_fullsize_indicator_before_webpage_exclusion():
    """CORE-5: a page URL containing a fullsize word AND a media-extension
    substring must still be a webpage, not media (the exclusion used to run
    after the fullsize block)."""
    assert not is_media_url("https://site.com/large/photo.mp4.html")
    assert not is_media_url("https://site.com/original/scan.jpg.php")
    # unchanged: real fullsize URLs still detected as media
    assert is_media_url("https://site.com/large/photo.jpg")
    assert is_media_url("https://site.com/original/scan.png")


def test_is_same_domain_two_level_ccTLD():
    """CORE-6: co.uk hosts with different registrable domains are NOT the
    same site (the old last-two-labels base collapsed them to co.uk)."""
    assert not is_same_domain("https://bbc.co.uk/x", "https://evil.co.uk/y")
    assert is_same_domain("https://blog.bbc.co.uk/a", "https://bbc.co.uk/b")
    assert is_same_domain("https://www.example.co.uk/a", "https://example.co.uk/b")
    assert is_same_domain("https://www.example.com/a", "https://example.com/b")


def test_registrable_domain():
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("blog.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("evil.co.uk") == "evil.co.uk"
    assert registrable_domain("cdn.artstation.com") == "artstation.com"