#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.site_pattern_manager import SitePatternManager


def test_site_pattern_manager_loading():
    """Test that the SitePatternManager loads patterns correctly"""
    # Get path to site_patterns.json
    built_in_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "patterns",
        "site_patterns.json"
    )
    
    # Try with explicit custom pattern path
    pattern_manager = SitePatternManager(enable_built_in=False, custom_pattern_path=built_in_path)
    
    # Verify patterns are loaded
    pattern_count = pattern_manager.get_pattern_count()
    loaded_files = pattern_manager.get_loaded_files()
    
    # We should have patterns
    assert pattern_count > 0, f"No patterns loaded from {built_in_path}"
    assert built_in_path in loaded_files, f"Built-in pattern file not loaded: {built_in_path}"
    
    # Test some known patterns exist
    test_urls = [
        "https://artstation.com/artwork/123456",
        "https://twitter.com/username/status/123456789",
        "https://www.reddit.com/r/pics/comments/abcdef",
        "https://imgur.com/gallery/abcdef",
    ]
    
    for url in test_urls:
        patterns = pattern_manager.get_patterns_for_url(url)
        assert len(patterns) > 0, f"No patterns matched for URL: {url}"


def test_url_transformation():
    """Test that URL transformations work correctly"""
    pattern_manager = SitePatternManager(enable_built_in=True, custom_pattern_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "patterns",
        "site_patterns.json"
    ))
    
    # Test cases with source URL and thumbnail URL to transform
    test_cases = [
        (
            "https://example.com/images/thumb/image123_thumb.jpg", 
            "https://example.com/gallery",
            "https://example.com/images/image123.jpg"
        ),
    ]
    
    for thumbnail_url, source_url, expected_result in test_cases:
        # TST-3: assert the exact expected candidate (was only "!= original")
        transformed_url = pattern_manager.transform_image_url(thumbnail_url, source_url)
        assert transformed_url != thumbnail_url, f"URL was not transformed: {thumbnail_url}"
        assert transformed_url[0] == expected_result, \
            f"Expected {expected_result}, got {transformed_url}"


def test_sanitize_imagus_target_js_semantics():
    r"""PAT-3/PAT-5: $& → \g<0>; $nn clamped by group count (JS semantics)."""
    pm = SitePatternManager(enable_built_in=False)
    # PAT-3: $& (whole match) must convert, not stay literal
    t = pm._sanitize_imagus_target('#$1_fullsize\n$1\n$&')
    assert '\\g<1>_fullsize' in t
    assert '\\g<0>' in t
    assert '$&' not in t and '$1' not in t
    # PAT-5: Deezer '$11200x1200...' with 2 groups → group(1) + literal rest
    assert pm._sanitize_imagus_target('$11200x1200-000000-95-0-0.jpg', 2) == '\\g<1>1200x1200-000000-95-0-0.jpg'
    assert pm._sanitize_imagus_target('$2', 2) == '\\g<2>'
    assert pm._sanitize_imagus_target('$10', 2) == '\\g<1>0'
    # Escaped $1 stays literal
    assert pm._sanitize_imagus_target(r'\$1', 2) == r'\$1'


def test_native_dollar_target_sanitized():
    """PAT-4: native patterns with JS $n targets apply correctly."""
    pm = SitePatternManager(enable_built_in=False)
    new_url, changed = pm._apply_replace_pattern(
        r'_(\d+)\.(jpe?g)$', '_1280.$2', 'https://tumblr.com/xyz_42.jpg'
    )
    assert changed
    assert new_url == 'https://tumblr.com/xyz_1280.jpg'


def test_default_loader_finds_sieve_and_dedups():
    """PAT-1/2/9: default dev load picks up Imagus sieves from the project
    root, dedups rule names across files, and loads site_patterns.json once."""
    pm = SitePatternManager()  # defaults, dev layout
    total = len(pm.imagus_global_rules) + sum(len(r) for r in pm.imagus_rules.values())
    assert total > 0, "No Imagus sieve rules loaded with defaults (PAT-1)"
    # PAT-2: 849 + 823 = 1672 without dedup; the deduped total is the number
    # of unique rule names, well under 1000.
    assert total < 1000, f"Sieve dedup failed: {total} rules loaded"
    # PAT-9: built-in site_patterns.json loaded exactly once
    spm = [f for f in pm.loaded_files if f.endswith('site_patterns.json')]
    assert len(spm) == 1, f"site_patterns.json loaded {len(spm)} times (PAT-9)"


def test_google_images_root_pattern_loaded(tmp_path):
    """PAT-6: root-level google_images/yandex_images entries load as patterns."""
    import json
    data = {
        "version": "1",
        "patterns": [{"site": "dummy", "domains": ["dummy.com"], "url_patterns": [r"dummy\.com/"]}],
        "google_images": {
            "site": "google_images", "domains": ["google.com"],
            "url_patterns": [r"google\.com/imgres"],
        },
        "global_settings": {},
    }
    p = tmp_path / "site_patterns.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    pm = SitePatternManager(enable_built_in=False, custom_pattern_path=str(p))
    assert 'google_images' in pm.patterns
    assert len(pm.get_patterns_for_url("https://www.google.com/imgres?imgurl=x.jpg")) > 0


def test_nsfwalbum_dict_by_host_transformations():
    """PAT-6: image_transformations keyed by thumbnail host (nsfwalbum) apply."""
    pm = SitePatternManager(enable_built_in=False, custom_pattern_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources", "patterns", "site_patterns.json"
    ))
    results = pm.transform_image_url(
        "https://t.pixhost.to/thumbs/123/abc.jpg",
        "https://nsfwalbum.com/gallery/xyz",
    )
    assert any('/images/' in r for r in results)


def test_tumblr_video_transform_applied():
    """PAT-6: tumblr video_transform (list of {source, target}) is consumed."""
    pm = SitePatternManager(enable_built_in=False, custom_pattern_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources", "patterns", "site_patterns.json"
    ))
    results = pm.transform_image_url(
        "https://va.media.tumblr.com/tumblr_abc_frame1.jpg",
        "https://tumblr.com/post/123",
    )
    assert any(r.endswith('.mp4') for r in results)


def test_frozen_mode_loads_exe_patterns_and_scans_sieve(tmp_path, monkeypatch):
    """PAT-10/PAT-9: frozen build with exe-adjacent site_patterns.json loads it
    exactly once AND still scans for Imagus sieves next to the exe."""
    import json
    from src.parser import site_pattern_manager as spm_mod

    bundle = tmp_path / "bundle"
    exedir = tmp_path / "exe"
    (bundle / "resources" / "patterns").mkdir(parents=True)
    exedir.mkdir()

    def w(path, obj):
        path.write_text(json.dumps(obj), encoding="utf-8")

    # Bundle copy must NOT win in frozen mode
    w(bundle / "resources" / "patterns" / "site_patterns.json",
      {"version": "1", "patterns": [{"site": "bundlesite", "domains": ["bundle.com"], "url_patterns": [r"bundle\.com/"]}]})
    # Exe copy must win
    w(exedir / "site_patterns.json",
      {"version": "1", "patterns": [{"site": "exesite", "domains": ["exesite.com"], "url_patterns": [r"exesite\.com/"]}]})
    w(exedir / "Imagus_sieve_test.json", {"rule_x": {"img": r"test\.com/", "to": "$1"}})

    monkeypatch.setattr(spm_mod, "__file__", str(bundle / "src" / "parser" / "site_pattern_manager.py"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exedir / "app.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    pm = SitePatternManager()
    assert 'exesite' in pm.patterns
    assert 'bundlesite' not in pm.patterns
    assert pm.imagus_rules.get('test.com'), "sieve next to exe was not scanned (PAT-10)"
    spm_loaded = [f for f in pm.loaded_files if f.endswith('site_patterns.json')]
    assert len(spm_loaded) == 1, f"site_patterns.json loaded {len(spm_loaded)} times (PAT-9 frozen)"


def test_allowlist_candidates_include_resources():
    """PAT-8: dev allowlist lives in resources/ and is among the candidates."""
    from src.parser import junk_filter
    resources_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources", junk_filter.ALLOWLIST_FILENAME,
    )
    assert os.path.exists(resources_path), "resources/junk_allowlist.txt missing"
    candidates = junk_filter._allowlist_candidates()
    assert resources_path in candidates, "resources/ candidate missing (PAT-8)"


def test_imagus_off_rule_skipped(tmp_path):
    """C-3a: sieve rules with `off: 1` are disabled in the source and must not
    be loaded (Mod semantics). Name is NOT marked as loaded so a later file
    can supply an enabled variant.
    """
    import json
    sieve = tmp_path / "sieve.json"
    sieve.write_text(json.dumps({
        "Disabled_Rule": {"link": "^https?://off[.]example/", "to": "img", "off": 1},
        "Enabled_Rule": {"link": "^https?://on[.]example/", "to": "img"},
    }), encoding="utf-8")
    pm = SitePatternManager(enable_built_in=False, imagus_sieve_path=str(sieve))
    assert pm.get_link_rule("https://off.example/pic/1") is None
    rule, m = pm.get_link_rule("https://on.example/pic/1")
    assert rule is not None


def test_imagus_off_rule_shadowed_name_can_reappear(tmp_path):
    """C-3a: an off rule must not claim its name — a second file with an
    enabled variant of the same name still loads."""
    import json
    s1 = tmp_path / "s1.json"
    s1.write_text(json.dumps({
        "Same_Name": {"link": "^https?://a[.]example/", "to": "img", "off": 1},
    }), encoding="utf-8")
    s2 = tmp_path / "s2.json"
    s2.write_text(json.dumps({
        "Same_Name": {"link": "^https?://b[.]example/", "to": "img"},
    }), encoding="utf-8")
    pm = SitePatternManager(enable_built_in=False, imagus_sieve_path=str(s1))
    pm._load_imagus_file(str(s2))
    assert pm.get_link_rule("https://b.example/pic") is not None


def test_imagus_dc_rule_debug_logged(tmp_path, caplog):
    """C-3b: dc rules load but are flagged in debug logs (support deferred)."""
    import json
    import logging
    sieve = tmp_path / "sieve_dc.json"
    sieve.write_text(json.dumps({
        "DC_Rule": {"link": "^https?://dc[.]example/", "to": "img", "dc": 2},
    }), encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger="src.parser.site_pattern_manager"):
        pm = SitePatternManager(enable_built_in=False, imagus_sieve_path=str(sieve))
    assert any("dc" in r.message.lower() for r in caplog.records), "dc debug log missing"
    # Rule still loads (dc does not block loading)
    assert pm.get_link_rule("https://dc.example/pic/1") is not None


def _make_sieve_pm(tmp_path, rules=None):
    """Build a SitePatternManager with a writable sieve file."""
    import json
    if rules is None:
        rules = {"Base_Rule": {"link": "^https?://base[.]example/", "to": "img"}}
    p = tmp_path / "sieve.json"
    p.write_text(json.dumps(rules), encoding="utf-8")
    return SitePatternManager(enable_built_in=False, imagus_sieve_path=str(p))


class _FakeResp:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.content = content


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.closed = False
    def get(self, url, timeout=None):
        return self._resp
    def close(self):
        self.closed = True


def test_download_imagus_valid_replaces(tmp_path, monkeypatch):
    """C-1: a valid downloaded sieve atomically replaces the file and reloads."""
    import json
    pm = _make_sieve_pm(tmp_path)
    new_rules = {"New_Rule": {"link": "^https?://new[.]example/", "to": "img"}}
    payload = json.dumps(new_rules).encode("utf-8")
    from src.downloader import media_downloader as md_mod
    monkeypatch.setattr(md_mod, "create_shared_downloader_session",
                        lambda settings: _FakeSession(_FakeResp(content=payload)))

    ok, msg = pm.download_imagus_from_url("https://example.com/sieve.json")
    assert ok, msg
    # File replaced
    on_disk = json.load(open(tmp_path / "sieve.json", encoding="utf-8"))
    assert "New_Rule" in on_disk
    # Rules reloaded from the new file
    assert pm.get_link_rule("https://new.example/pic/1") is not None
    # No temp file left behind
    assert not (tmp_path / "sieve.json.tmp").exists()


def test_download_imagus_invalid_keeps_old(tmp_path, monkeypatch):
    """C-1: invalid payloads must never touch the working sieve."""
    import json
    pm = _make_sieve_pm(tmp_path)
    old_rule = pm.get_link_rule("https://base.example/pic/1")
    assert old_rule is not None

    from src.downloader import media_downloader as md_mod

    # Not JSON at all
    monkeypatch.setattr(md_mod, "create_shared_downloader_session",
                        lambda settings: _FakeSession(_FakeResp(content=b"<html>404 page</html>")))
    ok, msg = pm.download_imagus_from_url("https://example.com/sieve.json")
    assert not ok and "Invalid JSON" in msg

    # Valid JSON but zero usable rules (Mod's validRuleCount === 0)
    zero_rules_payload = b'{"a": {}, "b": {"x": 1}}'
    monkeypatch.setattr(md_mod, "create_shared_downloader_session",
                        lambda settings: _FakeSession(_FakeResp(content=zero_rules_payload)))
    ok, msg = pm.download_imagus_from_url("https://example.com/sieve.json")
    assert not ok and "no valid rules" in msg

    # HTTP error status
    monkeypatch.setattr(md_mod, "create_shared_downloader_session",
                        lambda settings: _FakeSession(_FakeResp(status=404, content=b"{}")))
    ok, msg = pm.download_imagus_from_url("https://example.com/sieve.json")
    assert not ok and "404" in msg

    # Old sieve untouched in all cases
    assert pm.get_link_rule("https://base.example/pic/1") is not None
    on_disk = json.load(open(tmp_path / "sieve.json", encoding="utf-8"))
    assert "Base_Rule" in on_disk


def test_jsdelivr_mirror_conversion():
    """C-1: raw.githubusercontent URL converts to jsDelivr CDN form."""
    pm = SitePatternManager(enable_built_in=False)
    url = "https://raw.githubusercontent.com/user/repo/master/data/sieve.json"
    assert pm.jsdelivr_mirror(url) == "https://cdn.jsdelivr.net/gh/user/repo@master/data/sieve.json"
    assert pm.jsdelivr_mirror("https://example.com/other.json") is None
    assert pm.jsdelivr_mirror("") is None


if __name__ == "__main__":
    test_site_pattern_manager_loading()
    test_url_transformation()
