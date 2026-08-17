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


if __name__ == "__main__":
    test_site_pattern_manager_loading()
    test_url_transformation()
