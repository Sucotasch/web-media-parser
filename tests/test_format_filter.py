#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for the settings-driven media format allowlist (is_format_allowed)."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.utils import is_format_allowed
from src import constants as K


def test_default_image_formats():
    """Default allowlist keeps PNG/JPEG/WEBP but excludes GIF/SVG/ICO."""
    assert is_format_allowed("https://example.com/photo.png", "image")
    assert is_format_allowed("https://example.com/photo.jpg", "image")
    assert is_format_allowed("https://example.com/photo.webp", "image")
    assert is_format_allowed("https://example.com/photo.avif", "image")
    assert not is_format_allowed("https://example.com/anim.gif", "image")
    assert not is_format_allowed("https://example.com/icon.svg", "image")
    assert not is_format_allowed("https://example.com/favicon.ico", "image")


def test_default_video_formats():
    """All video formats are allowed by default."""
    assert is_format_allowed("https://example.com/clip.mp4", "video")
    assert is_format_allowed("https://example.com/clip.webm", "video")
    assert is_format_allowed("https://example.com/clip.m3u8", "video")
    assert not is_format_allowed("https://example.com/clip.gif", "video")


def test_enable_gif_via_settings():
    """Enabling .gif in settings unblocks it."""
    settings = {K.SETTING_ENABLED_IMAGE_FORMATS: K.DEFAULT_ENABLED_IMAGE_FORMATS + [".gif"]}
    assert is_format_allowed("https://example.com/anim.gif", "image", settings)
    # Other formats unaffected
    assert not is_format_allowed("https://example.com/icon.svg", "image", settings)


def test_extensionless_and_unknown_types_pass():
    """URLs without an extension and unknown media types are never blocked."""
    assert is_format_allowed("https://example.com/photo", "image")
    assert is_format_allowed("https://example.com/media?id=1", "video")
    assert is_format_allowed("https://example.com/clip.mp4", "file")
    assert is_format_allowed("https://example.com/song.mp3", "audio")
    assert not is_format_allowed("https://example.com/clip.mp4", "audio")  # mp4 is video container


def test_case_insensitive_extension():
    """File extensions are matched case-insensitively."""
    assert is_format_allowed("https://example.com/photo.PNG", "image")
    assert not is_format_allowed("https://example.com/anim.GIF", "image")


def test_query_string_ignored():
    """Query parameters do not affect the extension check."""
    assert is_format_allowed("https://example.com/photo.png?size=large&token=abc", "image")


def test_cur_remains_blocked():
    """.cur is historical trash — never in an allowlist, so it stays blocked."""
    assert not is_format_allowed("https://example.com/cursor.cur", "image")
    assert not is_format_allowed("https://example.com/cursor.cur", "video")


def test_streaming_manifests_allowed():
    """HLS/DASH manifests must stay enabled (core feature, preserved on settings save)."""
    assert is_format_allowed("https://example.com/stream.m3u8", "video")
    assert is_format_allowed("https://example.com/manifest.mpd", "video")


def test_json_parser_keeps_video_urls_as_media():
    """Regression: video URLs in JSON strings must not be demoted to links."""
    from src.parser.json_parser import JSONWebpageParser

    parser = JSONWebpageParser(
        url="https://example.com/api", settings={}, external_session=object()
    )
    parser._process_potential_media("https://example.com/video.mp4", "data.video")
    assert len(parser.media_files) == 1
    assert parser.media_files[0][0] == "video"

    # Disabled format (GIF by default) becomes a link, not media
    parser2 = JSONWebpageParser(
        url="https://example.com/api", settings={}, external_session=object()
    )
    parser2._process_potential_media("https://example.com/anim.gif", "data.img")
    assert len(parser2.media_files) == 0
    assert parser2.links == {"https://example.com/anim.gif"}
