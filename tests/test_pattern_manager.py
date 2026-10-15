#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import re

from src.parser.site_pattern_manager import SitePatternManager, get_substitution


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


def test_substitution_follows_the_ecmascript_spec():
    r"""PAT-3/PAT-5 → A-10/A-12/NF-2/NF-7: the JS replacement semantics.

    The private `\g<n>`-rewriting helper that these assertions used to pin is
    gone: `get_substitution` is the ECMAScript GetSubstitution shared with the
    extension (extension/sieve.js), and the vectors in
    extension/tests/vectors/substitution.json cover it exhaustively — every
    expected value there is re-derived from V8's own String.replace.
    """
    # $& is the whole match (PAT-3), captures are positional
    m = re.search(r'#(\d+)_', '#42_x')
    assert get_substitution('$1_fullsize$&', m) == '42_fullsize#42_'
    # PAT-5 / NF-7: Deezer '$11200x1200…' with 2 groups → group(1) + literal
    two = re.search(r'^(\d)(\d)$', '12')
    assert get_substitution('$11200x1200-000000-95-0-0.jpg', two) \
        == '11200x1200-000000-95-0-0.jpg'
    assert get_substitution('$2', two) == '2'
    assert get_substitution('$10', two) == '10'
    # Out-of-range stays literal — $0 included (it used to be the whole match)
    assert get_substitution('$0', two) == '$0'
    assert get_substitution('$99', two) == '$99'
    # A backslash is literal in a replacement template; the reference resolves
    assert get_substitution(r'\$1', two) == r'\1'


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
    root, dedups rule names across files, and loads site_patterns.json once.

    E-02 (2026-10-15): the old `assert total < 1000` was vacuous — it held for
    the shipped snapshot (673 rules) whatever the loader did, and went red the
    moment a legitimate second sieve file appeared in the root. The dedup
    invariant is now explicit: the loaded rule count can never exceed the number
    of UNIQUE rule names in the files that were actually loaded.
    """
    import glob
    import json as _json
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    sieve_files = sorted(glob.glob(_os.path.join(root, "Imagus_sieve_*.json")))
    assert sieve_files, "no Imagus_sieve_*.json in the project root (TST-5)"
    unique_names = set()
    for f in sieve_files:
        with open(f, encoding="utf-8") as fh:
            unique_names.update(_json.load(fh).keys())

    pm = SitePatternManager()  # defaults, dev layout
    total = len(pm.imagus_global_rules) + sum(len(r) for r in pm.imagus_rules.values())
    assert total > 0, "No Imagus sieve rules loaded with defaults (PAT-1)"
    assert total <= len(unique_names), (
        f"Sieve dedup failed: {total} rules loaded from {len(sieve_files)} file(s) "
        f"holding {len(unique_names)} unique rule names (PAT-2)"
    )
    assert len(pm.loaded_files) >= len(sieve_files), "not every root sieve was loaded"
    # PAT-9: built-in site_patterns.json loaded exactly once
    spm = [f for f in pm.loaded_files if f.endswith('site_patterns.json')]
    assert len(spm) == 1, f"site_patterns.json loaded {len(spm)} times (PAT-9)"


def test_sieve_dedup_drops_duplicate_rule_names(tmp_path):
    """PAT-2: the dedup branch itself, exercised with two real files.

    E-02: no test ever loaded two sieves, so "a later file's duplicate rule name
    does not count twice" had zero coverage.
    """
    import json as _json

    first = tmp_path / "Imagus_sieve_test_A.json"
    second = tmp_path / "Imagus_sieve_test_B.json"
    first.write_text(_json.dumps({
        "Dup": {"link": r"dup\.example/", "img": r"dup\.example/(\w+)\.jpg", "to": "$1.jpg"},
        "OnlyA": {"link": r"a\.example/", "img": r"a\.example/(\w+)\.jpg", "to": "$1.jpg"},
    }), encoding="utf-8")
    second.write_text(_json.dumps({
        "Dup": {"link": r"dup2\.example/", "img": r"dup2\.example/(\w+)\.jpg", "to": "$1.jpg"},
        "OnlyB": {"link": r"b\.example/", "img": r"b\.example/(\w+)\.jpg", "to": "$1.jpg"},
    }), encoding="utf-8")

    pm = SitePatternManager(enable_built_in=False)
    pm.imagus_rules, pm.imagus_global_rules = {}, []
    pm._loaded_sieve_rule_names = set()
    pm.loaded_files = []

    def loaded():
        return len(pm.imagus_global_rules) + sum(len(r) for r in pm.imagus_rules.values())

    assert pm._load_imagus_file(str(first)) is True
    assert loaded() == 2, f"first file: {loaded()} rules loaded (expected 2)"
    assert pm._load_imagus_file(str(second)) is True
    assert loaded() == 3, (
        f"dedup failed: {loaded()} rules after the second file (expected 3 — "
        f"'Dup' must not be counted twice)"
    )


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


def test_protocol_relative_to_template_does_not_double_the_scheme(tmp_path):
    """A-06: a `to` template starting with `//` produced `https:////host/path`.

    Synthetic twin of the ~19 shipped rules that use a protocol-relative `to`
    (Imgflip-h-p, OfferUp-pp, MakeAGif-g-p, ...): the substitution prefixed the
    page scheme unconditionally, and `'://' not in '//host/...'` is true.
    """
    pm = _make_sieve_pm(tmp_path, {
        "Proto_Relative": {
            "img": r"^thumbs[.]example[.]com/(\w+)[.]jpg$",
            "to": "//cdn.example.com/$1.jpg",
        },
    })

    variants = pm.transform_image_url(
        "https://thumbs.example.com/abc.jpg", "https://thumbs.example.com/gallery")

    assert "https://cdn.example.com/abc.jpg" in variants
    assert not [v for v in variants if ":////" in v], variants


def test_protocol_relative_real_rule_imgflip():
    """A-06 with the shipped rule (Imgflip-h-p: `to = //i.$1$2.#jpg gif#`)."""
    from helpers import require_sieve

    pm = SitePatternManager(enable_built_in=False, imagus_sieve_path=require_sieve())

    variants = pm.transform_image_url(
        "https://i.imgflip.com/2/abc123.jpg", "https://imgflip.com/i/2abc")

    assert "https://i.imgflip.com/abc123.jpg" in variants, variants
    assert not [v for v in variants if ":////" in v], variants


def test_uppercase_host_still_matches_sieve_rules():
    """D-12: a host in upper case (legal in HTML, preserved by urljoin) used to
    find no sieve rule at all — `get_domain` returned the raw netloc while the
    rule index and the lookups are lowercased.

    Measured before the fix: 1 variant for `I.4CDN.ORG`, 6 for `i.4cdn.org`.
    """
    from helpers import require_sieve

    pm = SitePatternManager(enable_built_in=False, imagus_sieve_path=require_sieve())
    lower = pm.transform_image_url(
        "https://i.4cdn.org/g/12345s.jpg", "https://boards.4chan.org/g/")
    upper_host = pm.transform_image_url(
        "https://I.4CDN.ORG/g/12345s.jpg", "https://boards.4chan.org/g/")
    upper_page = pm.transform_image_url(
        "https://i.4cdn.org/g/12345s.jpg", "https://BOARDS.4CHAN.ORG/g/")

    assert len(lower) > 1, lower
    assert len(upper_host) == len(lower), upper_host
    assert len(upper_page) == len(lower), upper_page
    assert [u.lower() for u in upper_host] == lower


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


def test_resize_param_strip_colorsuper():
    """ERR-7 / Errors.txt #1: native [Resize] port — Shopify &width=500 URLs
    resolve to the original file on the desktop (the sieve's own [Resize]
    rule is JS with this.node, so it was skipped at load)."""
    pm = SitePatternManager(enable_built_in=False)
    src = "https://colorsuper.com/products/palette-triple-pink-brazilian-bikini"
    # width=500 preview -> original (trailing & preserved, Imagus parity)
    res = pm.transform_image_url(
        "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&width=500",
        src,
    )
    assert res[0] == "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&"
    # width=2048 og:image variant -> same original
    res = pm.transform_image_url(
        "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&width=2048",
        src,
    )
    assert res[0] == "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149&"
    # No width param -> untouched
    res = pm.transform_image_url(
        "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149",
        src,
    )
    assert res[0] == "https://colorsuper.com/cdn/shop/files/Colorsuper-Bikini-Palette-Tripple-Pink-Brazilian-3.jpg?v=1718457149"


def test_resize_param_strip_exclusions_and_generic():
    """ERR-7: excluded hosts (reddit) untouched; generic w/h/resize stripped."""
    pm = SitePatternManager(enable_built_in=False)
    # Excluded host never stripped
    res = pm.transform_image_url(
        "https://i.redd.it/abc123.jpg?width=500", "https://reddit.com/r/test")
    assert res[0] == "https://i.redd.it/abc123.jpg?width=500"
    # Generic CDN params (w/h/q) stripped
    res = pm.transform_image_url(
        "https://cdn.example.com/img.jpg?w=100&h=200&q=80", "https://example.com/page")
    assert res[0] == "https://cdn.example.com/img.jpg?"
    # Plain image path untouched
    res = pm.transform_image_url(
        "https://img10.reactor.cc/pics/post/full/abc.jpg", "https://pr.reactor.cc/post/1")
    assert res[0] == "https://img10.reactor.cc/pics/post/full/abc.jpg"


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
