#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test URL detection functions
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.utils import is_media_url, is_image_url, is_video_url


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