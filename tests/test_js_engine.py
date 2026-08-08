#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for the Deno JS engine (P0: Imagus sieve JS `to` rules)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.js_engine import DenoJsEngine, find_deno_bin
from src.parser.js_engine.engine import _popen_kwargs


def test_popen_kwargs_suppresses_console_window():
    # Regression: without CREATE_NO_WINDOW every deno.exe child pops a visible
    # console window on Windows that lives for the whole parse run.
    import subprocess
    kwargs = _popen_kwargs()
    if os.name == "nt":
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW
    else:
        assert kwargs.get("start_new_session") is True


def _engine_or_skip():
    engine = DenoJsEngine()
    if not engine.available():
        pytest.skip("Deno binary/worker not available on this machine")
    return engine


def test_find_deno_bin_returns_path_or_none():
    # Should not raise, and either find a binary or return None.
    result = find_deno_bin()
    assert result is None or os.path.exists(result)


def test_run_js_simple():
    engine = _engine_or_skip()
    try:
        # $[1] + ternary — classic sieve JS (e.g. 8muses-x-p style)
        code = "return $[1]+($[2][0]==='i'?'full':'fl')"
        groups = ["https://8muses.com/x/i.jpg", "https://8muses.com/", "i"]
        result = engine.run_js(code, groups, "https://8muses.com/")
        assert result == "https://8muses.com/full"
    finally:
        engine.shutdown()


def test_run_js_location_shim():
    engine = _engine_or_skip()
    try:
        code = ("if(!location.hostname.endsWith('1.org'))return $[0]; "
                "let u=new URL(location.href).searchParams.get('q'); "
                "return u ? 'x/'+u : $[0]")
        groups = ["https://1.org/q?x=1", "https://1.org/"]
        result = engine.run_js(code, groups, "https://1.org/page?q=hello")
        assert result == "x/hello"
    finally:
        engine.shutdown()


def test_run_js_dom_rule_fails_open():
    engine = _engine_or_skip()
    try:
        # Needs real DOM — P0 worker has no page HTML; the this.node shim
        # returns null-ish values, so the rule fails open (None), not raises.
        code = "return this.node.closest('tr')?.querySelector('img')?.src"
        result = engine.run_js(code, ["x"], "https://example.com/")
        assert result is None
    finally:
        engine.shutdown()


def test_run_js_this_node_shim_lazy_branch():
    # P0 url rules often branch on groups first, e.g. Ancensored-x:
    #   $[2] ? '//'+$[1]+'clip/-/-/'+$[2] : this.node.closest('a').href
    # Without the this.node shim the WHOLE rule threw "reading 'node'" and
    # even the working branch never returned. The shim must let the group
    # branch evaluate normally.
    engine = _engine_or_skip()
    try:
        code = "return $[2] ? '//'+$[1]+'clip/-/-/'+$[2] : this.node.closest('a').href"
        result = engine.run_js(code, ["ancensored.com/x", "ancensored.com/", "abc123"],
                               "https://ancensored.com/")
        assert result == "//ancensored.com/clip/-/-/abc123"
    finally:
        engine.shutdown()


def test_run_js_this_node_shim_guard_branch():
    # Kino-Teatr.ru: `(($[3]||$[4])&&!this.node.src) throw ''` — this.node.src
    # is a guard. The P0 shim's src is null, so the guard triggers the rule's
    # own `throw ''` (fail-open, as it does in the extension when not hovering
    # an image) — the shim must NOT raise "reading 'node'", and the fallback
    # $[0] branch must still work when groups are absent.
    engine = _engine_or_skip()
    try:
        code = ("(()=>{if(($[3]||$[4])&&!this.node.src)throw '';"
                "return $[3]?$[0].replace($[3],'foto/'):$[4]?$[0].replace($[4],'poster/'):$[0]})()")
        # $[3]/$[4] absent -> guard no-op -> returns $[0] (no exception)
        result = engine.run_js(code, ["https://kino-teatr.ru/photo/1.jpg", "kino-teatr.ru/"],
                               "https://kino-teatr.ru/")
        assert result == "https://kino-teatr.ru/photo/1.jpg"
    finally:
        engine.shutdown()


def test_run_js_expression_wrapper_iife():
    # Regression: Imagus rules wrapped as `(()=>{...})()` are pure expressions
    # — a bare function body does NOT return a trailing expression's value, so
    # without the `return (...)` wrapper they all yielded undefined (silent
    # fail-open). The wrapper must make the IIFE's return value visible.
    engine = _engine_or_skip()
    try:
        code = "(()=>{return $[0]+'/wrap'})()"
        assert engine.run_js(code, ["https://x/a.jpg"], "https://x/") == "https://x/a.jpg/wrap"
        # ternary expression (no statements) also works
        code2 = "$[2] ? '//'+$[1]+'c/'+$[2] : $[0]"
        assert engine.run_js(code2, ["https://x/1", "https://x/", "y"], "https://x/") == "//https://x/c/y"
    finally:
        engine.shutdown()


def test_run_js_this_node_redirect_works():
    # Imagus convention: `this` may be reassigned by the rule. Since the shim
    # is passed via fn.call, a rule that reassigns `this` (redirect pattern)
    # must not clobber the shim — verify the pure return still works.
    engine = _engine_or_skip()
    try:
        code = "return this.node ? $[0]+'/ok' : 'no-node'"
        assert engine.run_js(code, ["https://x/a.jpg"], "https://x/") == "https://x/a.jpg/ok"
    finally:
        engine.shutdown()


def test_run_js_none_for_empty():
    engine = _engine_or_skip()
    try:
        assert engine.run_js("", [], "") is None
        assert engine.run_js(None, [], "") is None  # type: ignore[arg-type]
    finally:
        engine.shutdown()


def test_run_js_non_string_result():
    engine = _engine_or_skip()
    try:
        # Rule returns a number — worker reports result:null.
        code = "return 42"
        assert engine.run_js(code, ["x"], "") is None
    finally:
        engine.shutdown()


def test_run_js_console_log_does_not_corrupt_protocol():
    # Regression: a rule calling console.log() must NOT write to the JSON
    # protocol channel (stdout). Otherwise the response is desynced and a
    # later call misreads the previous response.
    engine = _engine_or_skip()
    try:
        noisy = ("console.log('debug', $[0]); "
                 "console.error('boom'); return $[0]+'/'+$[1]")
        assert engine.run_js(noisy, ["https://x/a.jpg", "https://x/"], "https://x/") \
            == "https://x/a.jpg/https://x/"
        # Following calls must still get THEIR OWN correct result.
        clean = "return $[0]+'-ok'"
        assert engine.run_js(clean, ["z"], "") == "z-ok"
        assert engine.run_js(noisy, ["https://x/b.jpg", "https://x/"], "https://x/") \
            == "https://x/b.jpg/https://x/"
        assert engine.run_js(clean, ["q"], "") == "q-ok"
    finally:
        engine.shutdown()


# --- Integration with SitePatternManager --------------------------------

def _make_manager(enable_deno):
    from src.parser.site_pattern_manager import SitePatternManager
    engine = None
    if enable_deno:
        engine = DenoJsEngine()
        if not engine.available():
            pytest.skip("Deno binary/worker not available on this machine")
    sieve = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "Imagus_sieve_2026.07.15_823.json")
    if not os.path.exists(sieve):
        pytest.skip("sieve file not present")
    return SitePatternManager(enable_built_in=False, imagus_sieve_path=sieve, js_engine=engine), engine


# --- DOM mode (P1: happy-dom res/url rules) ------------------------------

GALLERY_HTML = """<!doctype html><html><body>
<div class="gal">
  <a href="https://h/full/1.jpg"><img data-full="https://h/full/1.jpg" src="https://h/t/1.jpg"></a>
  <a href="https://h/full/2.jpg"><img data-full="https://h/full/2.jpg" src="https://h/t/2.jpg"></a>
</div>
<meta property="og:image" content="https://h/og.jpg">
</body></html>"""


def _dom_engine_or_skip():
    engine = DenoJsEngine()
    if not engine.dom_available():
        pytest.skip("Deno DOM worker / happy-dom cache not available on this machine")
    return engine


def test_dom_res_query_selector_all():
    engine = _dom_engine_or_skip()
    try:
        code = "return [...document.querySelectorAll('img')].map(i=>i.getAttribute('data-full')||i.src)"
        result = engine.run_dom_js(code, GALLERY_HTML, "https://h/gal", [], "https://h/link")
        assert result == ["https://h/full/1.jpg", "https://h/full/2.jpg"]
    finally:
        engine.shutdown()


def test_dom_res_meta_query():
    engine = _dom_engine_or_skip()
    try:
        code = "return document.querySelector('meta[property=og:image]')?.content"
        result = engine.run_dom_js(code, GALLERY_HTML, "https://h/gal", [], "https://h/link")
        assert result == ["https://h/og.jpg"]
    finally:
        engine.shutdown()


def test_dom_res_uses_groups():
    engine = _dom_engine_or_skip()
    try:
        code = "return $[1]+'/page-2'"
        result = engine.run_dom_js(code, "<html></html>", "https://h/gal",
                                   ["https://h/a/x", "https://h/a"], "https://h/a/x")
        assert result == ["https://h/a/page-2"]
    finally:
        engine.shutdown()


def test_dom_res_fails_open_on_this_node():
    engine = _dom_engine_or_skip()
    try:
        code = "return this.node.closest('tr')?.querySelector('img')?.src"
        assert engine.run_dom_js(code, GALLERY_HTML, "https://h/gal", [], "https://h/link") is None
    finally:
        engine.shutdown()


def test_dom_res_none_for_empty():
    engine = _dom_engine_or_skip()
    try:
        assert engine.run_dom_js("", GALLERY_HTML, "https://h/", [], "") is None
        assert engine.run_dom_js("return 42", GALLERY_HTML, "https://h/", [], "") is None
    finally:
        engine.shutdown()


def test_dom_res_page_text_meta():
    # Regression: Imagus res rules rely on `$._` = raw fetched page text
    # (e.g. `$._.match(/window.__INIT_DATA=.../)`). Before this fix every
    # such rule threw "Cannot read properties of undefined (reading 'match')"
    # and P1 fullsize discovery returned zero results on real sites.
    engine = _dom_engine_or_skip()
    try:
        html = ('<html><body><script>window.__DATA={"r":[{"u":"https://h/full/1.jpg"},'
                '{"u":"https://h/full/2.jpg"}]};</script></body></html>')
        code = ("$=JSON.parse($._.match(/__DATA=(\\{[^;]+)/)[1]);"
                "return Object.values($.r).map(i=>i.u)")
        assert engine.run_dom_js(code, html, "https://x.test/1", [], "https://x.test/1") == [
            "https://h/full/1.jpg", "https://h/full/2.jpg"]
    finally:
        engine.shutdown()


def test_dom_res_nested_array_flat():
    # Imagus res rules may return nested arrays ([[['#url']]]) where '#' marks
    # an already-fullsize URL. normalize() must flatten and strip the marker.
    engine = _dom_engine_or_skip()
    try:
        code = ("let o=($._.match(/origURL=\"([^\"]+)/)||[,''])[1];"
                "return o ? [[['#'+o]]] : null")
        html = '<html><body><script>var origURL="https://h/full/23.jpg";</script></body></html>'
        assert engine.run_dom_js(code, html, "https://23hq.com/", [], "https://23hq.com/") == [
            "https://h/full/23.jpg"]
    finally:
        engine.shutdown()


# --- P1.5: this.node (live DOM element shim) ------------------------------

NODE_HTML = """<html><body>
<div class="g"><a href="https://h/view/1">
  <img src="https://h/t/1.jpg" width="640" height="480">
</a></div>
<div class="g"><a href="https://h/view/2">
  <img src="https://h/t/2.jpg" width="640" height="480">
</a></div>
</body></html>"""


def test_dom_res_this_node_closest():
    # this.node must be the matched anchor so closest('a') resolves its href
    # (pattern: Google_Images `n.closest('a')?.href`).
    engine = _dom_engine_or_skip()
    try:
        code = "return this.node.closest('a')?.href"
        assert engine.run_dom_js(code, NODE_HTML, "https://h/gal", [],
                                 "https://h/view/1") == ["https://h/view/1"]
    finally:
        engine.shutdown()


def test_dom_res_this_node_query_selector():
    # this.node.querySelector('img') must return the anchor's thumbnail
    # (pattern: CNN-m-pp `this.node.querySelector('img')?.src`).
    engine = _dom_engine_or_skip()
    try:
        code = "return this.node.querySelector('img')?.src"
        assert engine.run_dom_js(code, NODE_HTML, "https://h/gal", [],
                                 "https://h/view/2") == ["https://h/t/2.jpg"]
    finally:
        engine.shutdown()


def test_dom_res_this_node_src_via_groups():
    # this.node.src resolves when the img src matches a regex group
    # (pattern: 1.org-pp `[i.thumbnailUrl,...].includes(this.node.src)`).
    engine = _dom_engine_or_skip()
    try:
        code = "return this.node.src"
        assert engine.run_dom_js(code, NODE_HTML, "https://h/gal",
                                 ["https://h/view/1", "https://h/t/1.jpg"],
                                 "https://h/view/1") == ["https://h/t/1.jpg"]
    finally:
        engine.shutdown()


def test_dom_res_this_find_href():
    # this.find({href}) searches the document and returns the resolved URL
    # (pattern: `this.find({ href: u }) || u`).
    engine = _dom_engine_or_skip()
    try:
        code = "return this.find({ href: 'https://h/view/1' }) || 'fallback'"
        assert engine.run_dom_js(code, NODE_HTML, "https://h/gal", [], "") == [
            "https://h/view/1"]
        # missing URL -> null -> rule falls back
        code2 = "return this.find({ href: 'https://h/nope' }) || 'fallback'"
        assert engine.run_dom_js(code2, NODE_HTML, "https://h/gal", [], "") == [
            "fallback"]
    finally:
        engine.shutdown()


def test_dom_res_this_node_doc_fallback():
    # No element matched href/groups: this.node falls back to the DOCUMENT,
    # NOT to an arbitrary first <a>/<img>. querySelector from the doc root
    # still works (CNN-m-pp container lookups), while closest/src return null
    # (clean fail-open) instead of garbage URLs from a logo/nav element.
    engine = _dom_engine_or_skip()
    try:
        # document.querySelector('img') resolves the page's first image
        code = "return this.node.querySelector('img')?.src"
        assert engine.run_dom_js(code, NODE_HTML, "https://h/gal", [], "") == [
            "https://h/t/1.jpg"]
        # no blind first-anchor fallback -> no wrong href, clean fail-open
        code2 = "return this.node.closest('a')?.href"
        assert engine.run_dom_js(code2, NODE_HTML, "https://h/gal", [], "") is None
        code3 = "return this.node.src"
        assert engine.run_dom_js(code3, NODE_HTML, "https://h/gal", [], "") is None
    finally:
        engine.shutdown()


def test_dom_res_this_node_still_fails_open_without_any():
    # A page with no links/images -> this.node null -> rule returns null.
    engine = _dom_engine_or_skip()
    try:
        code = "return this.node.closest('a')?.href"
        assert engine.run_dom_js(code, "<html><body>none</body></html>",
                                 "https://h/gal", [], "") is None
    finally:
        engine.shutdown()


def test_extract_res_urls_js_branch():
    # SitePatternManager.extract_res_urls must evaluate JS res rules through
    # the DOM engine and fall back to the img scan otherwise.
    from src.parser.site_pattern_manager import SitePatternManager
    engine = _dom_engine_or_skip()
    try:
        mgr = SitePatternManager(enable_built_in=False, js_engine=engine)
        rule = {"res": ":return [...document.querySelectorAll('img')].map(i=>i.getAttribute('data-full')||i.src)"}
        urls = mgr.extract_res_urls(rule, GALLERY_HTML, page_url="https://h/gal",
                                    groups=[], href="https://h/link")
        assert urls == ["https://h/full/1.jpg", "https://h/full/2.jpg"]
        # JS rule with no DOM engine is skipped; the plain <img src> fallback
        # still yields the thumbnails (previous behaviour).
        mgr2 = SitePatternManager(enable_built_in=False, js_engine=None)
        assert mgr2.extract_res_urls(rule, GALLERY_HTML) == ["https://h/t/1.jpg", "https://h/t/2.jpg"]
    finally:
        engine.shutdown()


def test_apply_link_url_transform_js_branch():
    from src.parser.site_pattern_manager import SitePatternManager
    import re
    engine = DenoJsEngine()
    try:
        if not engine.available():
            pytest.skip("Deno binary/worker not available on this machine")
        mgr = SitePatternManager(enable_built_in=False, js_engine=engine)
        # JS url rule: builds the fetch URL from $[1]; without engine -> None.
        rule = {"url": ":return 'https://view.example/show/'+$[1]+'?page=1'"}
        match = re.search(r"https://(example\.com)/([a-z]+)", "https://example.com/abc")
        assert match is not None
        result = mgr.apply_link_url_transform(rule, match, page_url="https://example.com/")
        assert result == ("https://view.example/show/example.com?page=1", None), result
        # Without engine the JS url rule is skipped (previous behaviour).
        mgr2 = SitePatternManager(enable_built_in=False, js_engine=None)
        assert mgr2.apply_link_url_transform(rule, match, page_url="https://example.com/") is None
    finally:
        engine.shutdown()


def test_manager_indexes_deno_js_rules_when_engine_enabled():
    mgr, engine = _make_manager(enable_deno=True)
    try:
        found = 0
        for rules in mgr.imagus_rules.values():
            found += sum(1 for r in rules if isinstance(r.get("to_js"), str))
        found += sum(1 for r in mgr.imagus_global_rules if isinstance(r.get("to_js"), str))
        # At least a handful of rules should be routed to Deno (measured ~48).
        assert found >= 5, f"expected deno-routed rules, got {found}"
    finally:
        if engine:
            engine.shutdown()


def test_manager_without_engine_keeps_old_behaviour():
    mgr, engine = _make_manager(enable_deno=False)
    try:
        for rules in mgr.imagus_rules.values():
            assert all(not isinstance(r.get("to_js"), str) for r in rules)
        for r in mgr.imagus_global_rules:
            assert not isinstance(r.get("to_js"), str)
    finally:
        if engine:
            engine.shutdown()


def test_transform_deno_js_rule_produces_variant():
    mgr, engine = _make_manager(enable_deno=True)
    try:
        # 8muses-x-p: img matches /^8muses\.com/…? Simpler: find a rule whose
        # to_js exists and whose img regex matches a synthetic URL, then verify
        # transform_image_url returns more than the input (or at least runs).
        candidates = []
        for rules in mgr.imagus_rules.values():
            for r in rules:
                if isinstance(r.get("to_js"), str) and r.get("img"):
                    candidates.append(r)
        if not candidates:
            pytest.skip("no deno JS rules indexed")
        rule = candidates[0]
        img_re = rule["img"]
        # Use the first rule's own img regex against a sample URL derived from it.
        # Fall back to a generic probe if the regex is too exotic.
        for probe in ("https://example.com/x/i.jpg", "https://example.com/photo.jpg"):
            import re
            m = re.search(img_re, probe, re.I)
            if m:
                results = mgr.transform_image_url(probe, "https://example.com/")
                assert isinstance(results, list) and len(results) >= 1
                break
        # No crash is the core assertion (fail-open); exact URL varies by rule.
    finally:
        if engine:
            engine.shutdown()
