#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Regression tests for audit §3 fixes.

Covers:
- §3.2: _extract_videos applies _is_significant_media to <video> files
- §3.4: likely_thumbnail soft hint wiring + priority deprioritization
- §3.5: json_parser _guess_media_type maps unknown media to "image"
- §3.9: sanitize_settings normalizes corrupted stop_words
"""

import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# --- §3.2: video significance ---------------------------------------------

class TestVideoSignificance:
    def _parser(self):
        from src.parser.webpage_parser import WebpageParser
        return WebpageParser(
            url="https://example.com/",
            settings={},
            process_js=False,
            external_session=MagicMock(),
        )

    def test_tiny_video_player_filtered(self):
        # <video width="16" height="16"> is a tiny/embedded player → filtered
        parser = self._parser()
        attrs = {"width": "16", "height": "16", "type": "video/mp4", "is_cdn": False}
        assert not parser._is_significant_media("video", "https://example.com/v.mp4", attrs)

    def test_normal_video_passes(self):
        parser = self._parser()
        attrs = {"width": "640", "height": "360", "type": "video/mp4", "is_cdn": False}
        assert parser._is_significant_media("video", "https://example.com/v.mp4", attrs)

    def test_video_url_with_image_ignore_substring_passes(self):
        # Video URLs must NOT be dropped by IMAGE-oriented SIGNIFICANT_MEDIA_IGNORE_PATTERNS
        # (e.g. "ad-" inside "load-"): the noise-pattern step is skipped for video.
        parser = self._parser()
        attrs = {"width": "640", "height": "360", "type": "video/mp4", "is_cdn": False}
        assert parser._is_significant_media("video", "https://cdn.example.com/load-movie.mp4", attrs)

    def test_platform_embed_not_blocked_by_ignore_patterns(self):
        # A YouTube embed URL must not be dropped by SIGNIFICANT_MEDIA_IGNORE_PATTERNS
        # (contains "youtube") — embeds are filtered only via format + is_banner_or_ad.
        attrs = {"width": "560", "height": "315", "platform": "youtube", "type": "embed"}
        from src.parser.utils import is_format_allowed, is_banner_or_ad
        url = "https://www.youtube.com/embed/abc123"
        assert is_format_allowed(url, "video", {})
        assert not is_banner_or_ad(url, attrs)


# --- §3.4: likely_thumbnail hint -------------------------------------------

class TestLikelyThumbnail:
    """TST-1: exercise the REAL _extract_images path (attrs it produces), not
    an inline copy of its logic — the old tests passed even if the feature
    were deleted."""

    @staticmethod
    def _extract(html):
        import asyncio
        from bs4 import BeautifulSoup
        from src.parser.webpage_parser import WebpageParser
        parser = WebpageParser(
            url="https://example.com/",
            settings={},
            process_js=False,
            external_session=MagicMock(),
        )
        soup = BeautifulSoup(html, "lxml")
        asyncio.run(parser._extract_images(soup))
        return parser.media_files

    def test_thumbnail_hint_marks_attrs(self):
        html = ('<html><body><img src="https://example.com/thumbs/photo.jpg"'
                ' width="800" height="600"></body></html>')
        media = self._extract(html)
        assert len(media) >= 1
        _url, _mtype, attrs = media[0]
        assert attrs.get("likely_thumbnail") is True

    def test_non_thumbnail_not_marked(self):
        html = ('<html><body><img src="https://example.com/gallery/photo.jpg"'
                ' width="800" height="600"></body></html>')
        media = self._extract(html)
        assert len(media) >= 1
        _url, _mtype, attrs = media[0]
        assert attrs.get("likely_thumbnail") is None


# --- §3.5: json_parser media type ------------------------------------------

class TestJsonMediaType:
    def _parser(self):
        from src.parser.json_parser import JSONWebpageParser
        return JSONWebpageParser(
            url="https://api.example.com/feed",
            settings={},
            external_session=MagicMock(),
        )

    def test_unknown_media_defaults_to_image(self):
        parser = self._parser()
        # CDN media URL without a recognizable extension
        assert parser._guess_media_type("https://cdn.example.com/media?id=123") == "image"

    def test_video_platform_still_video(self):
        parser = self._parser()
        assert parser._guess_media_type("https://www.youtube.com/watch?v=abc") == "video"

    def test_extension_still_respected(self):
        parser = self._parser()
        assert parser._guess_media_type("https://cdn.example.com/pic.png") == "image"
        assert parser._guess_media_type("https://cdn.example.com/clip.mp4") == "video"


# --- §3.9: sanitize_settings stop_words ------------------------------------

class TestSanitizeStopWords:
    def _sanitize(self, settings):
        from src.gui.settings_dialog import SettingsDialog
        return SettingsDialog.sanitize_settings(settings)

    def test_string_stop_words_normalized_to_list(self):
        out = self._sanitize({"stop_words": "login"})
        assert out["stop_words"] == ["login"]

    def test_list_with_junk_normalized(self):
        out = self._sanitize({"stop_words": [" login ", "", 42, "privacy", "login"]})
        assert out["stop_words"] == ["login", "privacy"]

    def test_missing_stop_words_untouched(self):
        out = self._sanitize({})
        assert "stop_words" not in out

    def test_proxy_crlf_stripped(self):
        # §5.1: CR/LF in proxy must be stripped (header-injection guard)
        out = self._sanitize({"proxy": "127.0.0.1:8080\r\nX-Evil: 1"})
        assert "\r" not in out["proxy"] and "\n" not in out["proxy"]
        assert out["proxy"] == "127.0.0.1:8080X-Evil: 1"

    def test_user_agent_crlf_stripped(self):
        out = self._sanitize({"user_agent": "UA\r\nInjected: 1"})
        assert "\r" not in out["user_agent"] and "\n" not in out["user_agent"]


# --- §4 Q8: add_task deep-copies settings -----------------------------------

class TestAddTaskDeepCopy:
    def test_settings_deep_copied(self):
        from src.core.task_queue_manager import TaskQueueManager
        tm = TaskQueueManager(base_download_dir="/tmp")
        settings = {"stop_words": ["login", "privacy"], "retry_count": 3}
        task = tm.add_task("https://example.com/", settings, "/tmp/out")
        # Mutating the original list must NOT affect the stored task settings
        settings["stop_words"].append("evil")
        assert task.settings["stop_words"] == ["login", "privacy"]

    def test_nested_default_settings_isolated_between_tasks(self):
        from src.core.task_queue_manager import TaskQueueManager
        tm = TaskQueueManager(base_download_dir="/tmp")
        settings = {"stop_words": ["login"]}
        t1 = tm.add_task("https://a.example/", settings, "/tmp/a")
        t2 = tm.add_task("https://b.example/", settings, "/tmp/b")
        t1.settings["stop_words"].append("x")
        assert t2.settings["stop_words"] == ["login"]


# --- §4 Q13: pattern with BOTH transformation sections applies both ---------

class TestPatternBothSections:
    def _manager(self):
        import json
        import tempfile
        from src.parser.site_pattern_manager import SitePatternManager

        pattern = {
            "site": "test-site",
            "domains": ["example.com"],
            # Section 1: adds "-full" before the extension
            "image_transformations": {
                "replace_patterns": [
                    {"source": r"\.jpg$", "target": "-full.jpg"},
                ]
            },
            # Section 2: strips "_thumb" from the path
            "imagus_patterns": {
                "image": [
                    {"source": r"_thumb", "target": ""},
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "patterns.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"patterns": [pattern]}, f)
            return SitePatternManager(enable_built_in=False, custom_pattern_path=path)

    def test_both_sections_applied(self):
        mgr = self._manager()
        # _thumb.jpg → section2 strips _thumb → .jpg → section1 adds -full.jpg
        result = mgr.transform_image_url(
            "https://example.com/img/photo_thumb.jpg",
            "https://example.com/gallery",
        )
        assert any("-full.jpg" in u for u in result)
        assert any("_thumb" not in u for u in result)

    def test_empty_target_strip_rule_applied(self):
        # Regression: a rule with an EMPTY target ("thumbs/th_" -> "") is a
        # legal delete/strip rule and must not be skipped by "source and target".
        import json
        import tempfile
        from src.parser.site_pattern_manager import SitePatternManager
        pattern = {
            "site": "test-site",
            "domains": ["example.com"],
            "image_transformations": {
                "replace_patterns": [
                    {"source": r"thumbs/th_", "target": ""},
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "patterns.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"patterns": [pattern]}, f)
            mgr = SitePatternManager(enable_built_in=False, custom_pattern_path=path)
            result = mgr.transform_image_url(
                "https://example.com/thumbs/th_photo.jpg",
                "https://example.com/gallery",
            )
            assert result == ["https://example.com/photo.jpg"]


# --- §5.3: backup.py excludes sensitive files -------------------------------

class TestBackupExcludes:
    def test_settings_and_queue_excluded_from_zip(self):
        """TST-1: run the REAL backup.create_backup with the project root
        redirected into a temp dir (the old test verified a re-implementation)."""
        import tempfile
        import zipfile
        import backup

        with tempfile.TemporaryDirectory() as tmp:
            # Fake project root with sensitive files + a code file
            for name in ("settings.json", "task_queue.json", "main.py"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write("x")
            os.makedirs(os.path.join(tmp, "sessions"), exist_ok=True)
            with open(os.path.join(tmp, "sessions", "last.pkl"), "w") as f:
                f.write("y")

            real_file = backup.__file__
            try:
                backup.__file__ = os.path.join(tmp, "backup.py")
                result = backup.create_backup()
            finally:
                backup.__file__ = real_file

            names = set()
            with zipfile.ZipFile(result) as zf:
                names = set(zf.namelist())
            assert "settings.json" not in names
            assert "task_queue.json" not in names
            assert not any(n.startswith("sessions") for n in names)
            assert "main.py" in names


# --- Crawler URL-inflation guard (34MB-log bug) ----------------------------

class TestUrlInflationGuard:
    """Regression: relative vBulletin links (threads/members/...) urljoined onto a
    slug base without trailing slash produce threads/threads/threads/... URLs that
    grew without bound, flooding the queue and the log (34MB).
    """

    def test_normalize_collapses_adjacent_repeated_segments(self):
        from src.parser.utils import normalize_url
        assert normalize_url(
            "https://vipergirls.to/threads/threads/threads/threads/16341233-Shameless#top"
        ) == "https://vipergirls.to/threads/16341233-Shameless"

    def test_normalize_collapses_run_of_three(self):
        from src.parser.utils import normalize_url
        assert normalize_url(
            "https://a.example/b/b/b/c"
        ) == "https://a.example/b/c"

    def test_normalize_keeps_legit_doubled_directory(self):
        # A genuine doubled directory (/a/a/b) must be preserved — normalize_url
        # also runs on download URLs where collapsing could 404 real CDN paths.
        from src.parser.utils import normalize_url
        assert normalize_url(
            "https://a.example/img/img/hero.jpg"
        ) == "https://a.example/img/img/hero.jpg"

    def test_normalize_keeps_non_adjacent_repeats(self):
        # Non-adjacent duplicates are legitimate (e.g. /threads/foo/threads)
        from src.parser.utils import normalize_url
        assert normalize_url(
            "https://a.example/threads/foo/threads"
        ) == "https://a.example/threads/foo/threads"

    def test_urljoin_loop_converges_to_clean_url(self):
        # Simulate the crawler loop: urljoin of a relative self-link grows the
        # URL each round; normalize_url must collapse it back to the canonical form.
        from urllib.parse import urljoin
        from src.parser.utils import normalize_url
        url = "https://vipergirls.to/threads/16341233-Shameless"
        for _ in range(6):
            url = urljoin(url, "threads/16341233-Shameless?styleid=65")
        assert normalize_url(url) == "https://vipergirls.to/threads/16341233-Shameless?styleid=65"

    def test_alternating_bloat_rejected_by_queue_guard(self):
        # threads/members/threads/members/... — a single segment repeated >3 times
        # is a urljoin-bloat signature; PriorityURLQueue.put() drops it.
        import asyncio
        from src.parser.priority_url_queue import PriorityURLQueue

        async def main():
            q = PriorityURLQueue(settings={})
            q.reset_async_primitives()
            bloat = (
                "https://vipergirls.to/threads/members/threads/members/"
                "threads/members/threads/members/520129-Hemhemo1103"
            )
            await q.put(bloat, 1, "https://vipergirls.to/threads/x", {})
            return q.empty()

        assert asyncio.run(main())

    def test_normal_urls_still_queued(self):
        import asyncio
        from src.parser.priority_url_queue import PriorityURLQueue

        async def main():
            q = PriorityURLQueue(settings={})
            q.reset_async_primitives()
            await q.put("https://vipergirls.to/threads/16341233-Shameless", 1, "https://vipergirls.to/threads/x", {})
            return not q.empty()

        assert asyncio.run(main())
