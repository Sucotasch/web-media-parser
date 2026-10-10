#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Regression tests for audit bug fixes.

Covers:
- RISK-5: normalize_url query-param sorting + tracking-param stripping
- BUG-3: dimension-based filtering reads attrs["dimensions"] (imgs) and
  is_banner_or_ad recognizes 1x1 tracking pixels / banner ratios
- RISK-1: bounded downloader retries honor the Retry Count setting
"""

import sys
import os
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.utils import normalize_url, is_banner_or_ad


# --- RISK-5: normalize_url ---------------------------------------------------

class TestNormalizeUrl:
    def test_sorts_query_params(self):
        assert normalize_url("https://example.com/a.jpg?b=2&a=1") == "https://example.com/a.jpg?a=1&b=2"

    def test_strips_tracking_params(self):
        url = "https://example.com/photo.png?utm_source=news&id=5&fbclid=xyz"
        assert normalize_url(url) == "https://example.com/photo.png?id=5"

    def test_removes_fragment_and_trailing_slash(self):
        assert normalize_url("https://Example.com/path/") == "https://example.com/path"
        assert normalize_url("https://example.com/a?x=1#frag") == "https://example.com/a?x=1"

    def test_no_query_unchanged(self):
        assert normalize_url("https://example.com/a.jpg") == "https://example.com/a.jpg"

    def test_tracking_only_query_becomes_clean(self):
        assert normalize_url("https://example.com/a.jpg?utm_source=x") == "https://example.com/a.jpg"


# --- BUG-3: dimension-aware ad/trash filtering ------------------------------

class TestBannerOrAdDimensions:
    def test_tracking_pixel_via_dimensions_dict(self):
        attrs = {"dimensions": {"width": 1, "height": 1}}
        assert is_banner_or_ad("https://example.com/img/pixel.png", attrs)

    def test_large_image_not_banner(self):
        attrs = {"dimensions": {"width": 1200, "height": 800}}
        assert not is_banner_or_ad("https://example.com/img/photo.jpg", attrs)

    def test_banner_aspect_ratio(self):
        attrs = {"dimensions": {"width": 728, "height": 90}}
        assert is_banner_or_ad("https://example.com/img/banner.png", attrs)


class TestBannerOrAdTokenMatching:
    """§3.10: ad keywords match URL tokens, never free substrings."""

    def test_ads_token_in_path_flags(self):
        assert is_banner_or_ad("https://example.com/ads/top.jpg", {})

    def test_downloads_does_not_flag(self):
        # "ads" inside "downloads" must NOT be treated as an ad
        assert not is_banner_or_ad("https://example.com/downloads/photo.jpg", {})

    def test_media_does_not_flag(self):
        # "ad" in "media" is not a keyword token
        assert not is_banner_or_ad("https://example.com/media/photos/1.jpg", {})

    def test_banner_token_in_path_flags(self):
        assert is_banner_or_ad("https://example.com/banners/top.jpg", {})

    def test_ad_network_host_flags(self):
        assert is_banner_or_ad("https://doubleclick.net/imgad?id=1", {})

    def test_class_attr_token_flags(self):
        assert is_banner_or_ad("https://example.com/x.jpg", {"class": "ad-banner"})


class TestSignificantMediaDimensions:
    def _parser(self):
        from src.parser.webpage_parser import WebpageParser
        return WebpageParser(
            url="https://example.com/",
            settings={},
            process_js=False,
            external_session=MagicMock(),
        )

    def test_small_image_filtered_by_dimensions(self):
        parser = self._parser()
        attrs = {"dimensions": {"width": 16, "height": 16}}
        assert not parser._is_significant_media("image", "https://example.com/x/pic.png", attrs)

    def test_large_image_passes_dimensions(self):
        parser = self._parser()
        attrs = {"dimensions": {"width": 1920, "height": 1080}}
        assert parser._is_significant_media("image", "https://example.com/x/photo.jpg", attrs)

    def test_missing_dimensions_pass(self):
        parser = self._parser()
        assert parser._is_significant_media("image", "https://example.com/x/photo.jpg", {})

    def test_picture_source_top_level_width_filtered(self):
        # <picture> <source> entries pass width at the top level (no
        # "dimensions" dict) — the min-dimension filter must still apply.
        parser = self._parser()
        attrs = {"width": 64, "media": "(max-width: 600px)", "source": "srcset"}
        assert not parser._is_significant_media("image", "https://example.com/x/pic.jpg", attrs)

    def test_picture_source_top_level_large_passes(self):
        parser = self._parser()
        attrs = {"width": 1920, "media": "(min-width: 600px)", "source": "srcset"}
        assert parser._is_significant_media("image", "https://example.com/x/pic.jpg", attrs)

    def test_top_level_width_zero_ignored(self):
        # srcset entries without a width descriptor use width=0 — must pass
        # (no dimension info, not a filterable signal).
        parser = self._parser()
        attrs = {"width": 0, "source": "srcset"}
        assert parser._is_significant_media("image", "https://example.com/x/pic.jpg", attrs)

    def test_string_width_no_crash(self):
        # Raw HTML can carry non-numeric width (e.g. "auto") — must not crash
        # and must not filter (no reliable dimension signal).
        parser = self._parser()
        attrs = {"width": "auto", "source": "srcset"}
        assert parser._is_significant_media("image", "https://example.com/x/pic.jpg", attrs)

    def test_top_level_height_only_applies(self):
        # Height-only info must still be honored when no width is present.
        parser = self._parser()
        attrs = {"height": 32, "source": "srcset"}
        assert not parser._is_significant_media("image", "https://example.com/x/pic.jpg", attrs)


# --- RISK-1: bounded downloader retries -------------------------------------

class TestDownloaderRetries:
    def _downloader(self, stop_event=None):
        from src.downloader.media_downloader import MediaDownloader
        dl = MediaDownloader(
            url="https://example.com/x.jpg",
            filepath=os.path.join(os.path.dirname(__file__), "out_test.jpg"),
            settings={},
            stop_event=stop_event,
        )
        dl.session = MagicMock()
        return dl

    def test_retries_transient_network_error(self):
        dl = self._downloader()
        with patch.object(dl, "_do_download", side_effect=[
            {"success": False, "error": "Network error: timed out"},
            {"success": True, "message": "ok"},
        ]) as mock_dl:
            result = dl.download(retries=2)
        assert result["success"] is True
        assert mock_dl.call_count == 2

    def test_no_retry_for_content_skip(self):
        dl = self._downloader()
        with patch.object(dl, "_do_download", return_value={
            "success": False, "error": "File too small (1.00KB < 40KB)"
        }) as mock_dl:
            result = dl.download(retries=3)
        assert result["success"] is False
        assert mock_dl.call_count == 1

    def test_zero_retries_returns_first_failure(self):
        dl = self._downloader()
        with patch.object(dl, "_do_download", return_value={
            "success": False, "error": "Network error: timeout"
        }) as mock_dl:
            result = dl.download(retries=0)
        assert result["success"] is False
        assert mock_dl.call_count == 1

    def test_stop_event_aborts_before_retrying(self):
        stop_event = threading.Event()
        stop_event.set()
        dl = self._downloader(stop_event=stop_event)
        with patch.object(dl, "_do_download", return_value={
            "success": False, "error": "Network error: timeout"
        }) as mock_dl:
            result = dl.download(retries=5)
        assert result["success"] is False
        assert mock_dl.call_count == 0

# --- R-C-7: the aspect-ratio rule only applies to small creatives ----------

class TestBannerPolicyRc7:
    """A 3:1+ panorama is content; a small wide/tall creative is a banner."""

    @staticmethod
    def _banner(w, h):
        return is_banner_or_ad("https://cdn.example.com/p.jpg",
                               {"dimensions": {"width": w, "height": h}})

    def test_panoramas_are_not_dropped(self):
        for w, h in [(3000, 1000), (1920, 640), (4096, 1024), (8000, 2000),
                     (1500, 500), (1920, 1080), (400, 900)]:
            assert not self._banner(w, h), f"{w}x{h} wrongly treated as a banner"

    def test_small_creatives_are_still_dropped(self):
        for w, h in [(728, 90), (970, 250), (320, 50), (160, 600), (120, 600)]:
            assert self._banner(w, h), f"{w}x{h} banner missed"

    def test_existing_cases_unchanged(self):
        assert self._banner(1, 1)
        assert not self._banner(1200, 800)


# --- R-C-2: hidden-style detection must parse values -----------------------

class TestHiddenStyleParsingRc2:
    """"opacity: 0.9" / "height: 0.5em" are visible; zero-valued ones are not."""

    @staticmethod
    def _visible(style):
        from bs4 import BeautifulSoup
        from src.parser.webpage_parser import WebpageParser
        parser = WebpageParser.__new__(WebpageParser)  # no session/settings needed
        el = BeautifulSoup(f'<img style="{style}">', "lxml").img
        return parser._is_element_visible(el)

    def test_fractional_values_are_visible(self):
        for style in ["opacity: 0.9", "width: 0.5px", "height: 0.5em",
                      "max-width: 0.5%", "color: #c00; width: 0.5em",
                      "border-width: 0.5px", "opacity: 0.05"]:
            assert self._visible(style), f"{style!r} wrongly treated as hidden"

    def test_zero_valued_styles_are_hidden(self):
        for style in ["opacity: 0", "opacity:0", "width: 0", "width:0px",
                      "height: 0", "height:0%", "display: none", "display:none",
                      "visibility: hidden", "left: -9999px", "top: -9999px"]:
            assert not self._visible(style), f"{style!r} wrongly treated as visible"

    def test_normal_styles_are_visible(self):
        for style in ["width: 100%", "opacity: 1", "font-size: 0.9em"]:
            assert self._visible(style)


# --- R-P-1: the queue must save even when the path has no directory --------

class TestQueueSaveWithoutDirectoryRc:

    def test_save_to_bare_filename(self, tmp_path, monkeypatch):
        import os as _os
        from src.core.task_queue_manager import TaskQueueManager
        monkeypatch.chdir(tmp_path)
        manager = TaskQueueManager(base_download_dir=str(tmp_path))
        manager.save("task_queue.json")          # raised FileNotFoundError before
        assert _os.path.exists(tmp_path / "task_queue.json")

    def test_save_creates_missing_directory(self, tmp_path):
        import os as _os
        from src.core.task_queue_manager import TaskQueueManager
        manager = TaskQueueManager(base_download_dir=str(tmp_path))
        target = tmp_path / "nested" / "deeper" / "task_queue.json"
        manager.save(str(target))
        assert _os.path.exists(target)


# --- R-P-2: the extension must not become a mangled stub -------------------

class TestSanitizeFilenameRc:
    """A query/tracking tail used to survive as part of the extension."""

    @staticmethod
    def _sanitize(name):
        from src.parser.parser_manager import ParserManager
        return ParserManager._sanitize_filename(None, name)

    def test_real_extension_is_recovered(self):
        for raw, expected in [("photo.jpeg?w=100", ".jpeg"),
                              ("a.jpeg_small", ".jpeg"),
                              ("img.jpeg?resize=1000,2000", ".jpeg"),
                              ("photo.jpeg;param=1", ".jpeg"),
                              ("pic.avif?x=1&y=2", ".avif")]:
            import os as _os
            ext = _os.path.splitext(self._sanitize(raw))[1].lower()
            assert ext == expected, f"{raw!r} -> {self._sanitize(raw)!r}"

    def test_plain_names_unchanged(self):
        for name in ["image.webp", "clip.mp4", "shot.png", "wallpaper.avif", "x.tar.gz"]:
            assert self._sanitize(name) == name
