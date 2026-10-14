# -*- coding: utf-8 -*-
"""R-C prototypes: json_parser (R-C-1/4/8), visibility (R-C-2), banner (R-C-7).

Nothing in the repo is modified: every candidate patch is applied to the source
text, compiled and exec'd into a throwaway module. The real classes/functions are
the control in every table.
"""
import json
import os
import re
import sys
import time
import types
import pathlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

from src import constants as K  # noqa: E402
from src.parser.json_parser import JSONWebpageParser as RealJSON  # noqa: E402
from src.parser.utils import is_banner_or_ad as real_banner  # noqa: E402

U = "https://cdn.example.com/pic.jpg"


def build(path, replacements, modname):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    for old, new in replacements:
        assert src.count(old) == 1, f"{path}: anchor count {src.count(old)} for {old[:60]!r}"
        src = src.replace(old, new)
    mod = types.ModuleType(modname)
    mod.__file__ = path
    exec(compile(src, modname + ".py", "exec"), mod.__dict__)
    return mod


# ---------------------------------------------------------------- json_parser
OLD_MEDIA_LIST = '''        media_key_patterns = [
            "url", "src", "source", "media", "image", "img", "photo", "picture",
            "thumbnail", "thumb", "icon", "avatar", "video", "file", "path",
            "href", "link", "content", "data", "original", "high_res", "hd",
        ]
'''
NEW_MEDIA_LIST = '''        media_key_patterns = K.JSON_MEDIA_KEY_PATTERNS  # R-C-8
'''

OLD_LIST_TAIL = '''                                self.media_files.append((media_type, abs_url, attrs))
                        else:
                            self.links.add(abs_url)
'''
NEW_LIST_TAIL = '''                                self.media_files.append((media_type, abs_url, attrs))
                        else:
                            self.links.add(abs_url)
                else:
                    # R-C-1: objects/lists without a "url" field were dropped
                    # silently; descend into them instead.
                    self._extract_media_from_json(item, path)
'''

OLD_DICT_BRANCH = '''    def _process_potential_media(self, value: Any, path: str) -> None:'''
NEW_DICT_BRANCH = '''    def _process_potential_media(self, value: Any, path: str) -> None:
        # R-C-1: a dict under a media-ish key ({"data": {...}}, {"media": {...}})
        # is not a URL container itself — descend so nested media is not lost.
        if isinstance(value, dict):
            self._extract_media_from_json(value, path)
            return
'''

OLD_LINKS = '''        if isinstance(data, dict):
            # Look for pagination or next page links
            for key in ["next", "next_page", "nextPage", "pagination", "paging", "links"]:
                if key in data and isinstance(data[key], str) and self._looks_like_url(data[key]):
                    abs_url = urljoin(self.url, data[key])
                    self.links.add(abs_url)
                    logger.debug(f"Found next page link: {abs_url}")
                elif key in data and isinstance(data[key], dict) and "url" in data[key]:
                    next_url = data[key]["url"]
                    if isinstance(next_url, str) and self._looks_like_url(next_url):
                        abs_url = urljoin(self.url, next_url)
                        self.links.add(abs_url)
                        logger.debug(f"Found next page link: {abs_url}")
'''
NEW_LINKS = '''        # R-C-4: search the whole payload (iterative, bounded) instead of only
        # the top level — pagination lives under data/meta on most APIs.
        stack = [(data, 0)]
        visited = 0
        while stack:
            node, depth = stack.pop()
            visited += 1
            if visited > 20000 or depth > 32:
                break
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in K.JSON_PAGINATION_KEY_PATTERNS:
                        if isinstance(value, str) and self._looks_like_url(value):
                            abs_url = urljoin(self.url, value)
                            self.links.add(abs_url)
                            logger.debug(f"Found next page link: {abs_url}")
                        elif isinstance(value, dict) and "url" in value:
                            next_url = value["url"]
                            if isinstance(next_url, str) and self._looks_like_url(next_url):
                                abs_url = urljoin(self.url, next_url)
                                self.links.add(abs_url)
                                logger.debug(f"Found next page link: {abs_url}")
                        # keep descending: {"meta": {"pagination": {"next": ...}}}
                    stack.append((value, depth + 1))
            elif isinstance(node, list):
                for item in node:
                    stack.append((item, depth + 1))
'''

jm = build("src/parser/json_parser.py",
           [(OLD_MEDIA_LIST, NEW_MEDIA_LIST),
            (OLD_LIST_TAIL, NEW_LIST_TAIL),
            (OLD_DICT_BRANCH, NEW_DICT_BRANCH),
            (OLD_LINKS, NEW_LINKS)], "patched_json_parser")
NewJSON = jm.JSONWebpageParser
print("json_parser patch built (adds K.JSON_PAGINATION_KEY_PATTERNS usage)")

K.JSON_PAGINATION_KEY_PATTERNS = ["next", "next_page", "nextpage", "pagination",
                                  "paging", "links"]  # proposed constant

SHAPES = {
    "control flat": {"images": [U]},
    "control dict+url": {"images": [{"url": U, "width": 1200}]},
    "data wrapper": {"data": {"images": [U]}},
    "media wrapper": {"media": {"url": U}},
    "dict with src": {"images": [{"src": U}]},
    "metadata (substring 'data')": {"metadata": {"url": U}},
    "realistic API": {"data": {"items": [{"images": [{"url": U, "width": 800}]}]}},
    "wp-style gallery": {"data": {"media": [{"src": U}]}},
    "asset key": {"asset": U},
    "assets list": {"assets": [U]},
}


def media_of(cls, payload):
    p = cls(url="https://api.example.com/feed", settings={}, external_session=object())
    p._extract_media_from_json(payload)
    return p.media_files


print(f"\n{'shape':32} {'now':>5} {'patched':>8}  detail")
print("-" * 78)
for name, payload in SHAPES.items():
    a = media_of(RealJSON, payload)
    b = media_of(NewJSON, payload)
    det = [u for _, u, _ in b]
    print(f"{name:32} {len(a):>5} {len(b):>8}  {det}")
    # duplicates check on the patched path
    urls = [u for _, u, _ in b]
    assert len(urls) == len(set(urls)), f"DUPLICATE media from {name}: {urls}"

print("\nattrs preserved for list-of-dict-with-url:",
      [a for _, _, a in media_of(NewJSON, {"images": [{"url": U, "width": 1200}]})])

PAG = {
    "control top-level": {"next": "https://api.example.com/feed?page=2"},
    "nested in data": {"data": {"next": "https://api.example.com/feed?page=2"}},
    "nested meta.pagination": {"meta": {"pagination": {"next": "https://api.example.com/feed?page=2"}}},
    "links.url form": {"links": {"url": "https://api.example.com/feed?page=2"}},
    "generic url key (must NOT become a link)": {"data": {"url": "https://example.com/other-page"}},
    "href key (must NOT become a link)": {"item": {"href": "https://example.com/somewhere"}},
}
print(f"\n{'pagination shape':44} {'now':>4} {'patched':>8}")
print("-" * 78)
for name, payload in PAG.items():
    def links_of(cls):
        p = cls(url="https://api.example.com/feed", settings={}, external_session=object())
        p._extract_links_from_json(payload)
        return p.links
    a, b = links_of(RealJSON), links_of(NewJSON)
    print(f"{name:44} {len(a):>4} {len(b):>8}  {sorted(b)}")

# --- performance -------------------------------------------------------------
ITEMS = [{"id": i, "title": f"t{i}", "images":
          [{"url": f"https://cdn.example.com/{i}.jpg", "width": 800, "height": 600}],
          "meta": {"next": "https://api.example.com/feed?page=2"}} for i in range(4000)]
# fair payload: both versions traverse it and find the same 4000 media
fair = {"items": ITEMS}
# wrapped payload: the current code finds nothing at all
wrapped = {"data": {"items": ITEMS}}
print(f"\nfair payload: {len(json.dumps(fair))/1e6:.1f} MB, wrapped: "
      f"{len(json.dumps(wrapped))/1e6:.1f} MB, {len(ITEMS)} items")


def timed(cls, fn, payload, runs=5):
    best, out = 1e9, None
    for _ in range(runs):
        p = cls(url="https://api.example.com/feed", settings={}, external_session=object())
        t0 = time.perf_counter()
        getattr(p, fn)(payload)
        dt = time.perf_counter() - t0
        best, out = min(best, dt), p
    return best, out


for fn in ("_extract_media_from_json", "_extract_links_from_json"):
    ta, pa = timed(RealJSON, fn, fair)
    tb, pb = timed(NewJSON, fn, fair)
    tc, pc = timed(NewJSON, fn, wrapped)
    print(f"  {fn:28} fair: now={ta*1000:6.1f} ms  patched={tb*1000:6.1f} ms "
          f"(overhead x{tb/ta if ta else float('nan'):.2f})  "
          f"wrapped(patched)={tc*1000:6.1f} ms  media {len(pa.media_files)}->{len(pb.media_files)}"
          f"/{len(pc.media_files)}  links {len(pa.links)}->{len(pb.links)}/{len(pc.links)}")

# --- visibility (R-C-2) ------------------------------------------------------
OLD_KW = '''    "display: none", "display:none", "visibility: hidden", "visibility:hidden", 
    "opacity: 0", "opacity:0", "left: -999", "top: -999", "width: 0", "height: 0"'''
NEW_KW = '''    "display: none", "display:none", "visibility: hidden", "visibility:hidden",
    "left: -999", "top: -999"'''
# plus a value-aware regex list used by _is_element_visible


def hidden_old(style, keywords=K.VISIBILITY_HIDDEN_KEYWORDS):
    return any(kw in style.lower() for kw in keywords)


ZERO_VALUE_RE = re.compile(r"^(?:width|height|max-width|max-height|font-size)\s*:\s*0\s*(?:px|%|em|rem|pt)?\s*$")
OPACITY_RE = re.compile(r"^opacity\s*:\s*0(?:\.0+)?\s*$")


def hidden_new(style):
    style = (style or "").lower()
    if any(kw in style for kw in NEW_KW_list):
        return True
    for decl in re.split(r"[;]", style):
        decl = decl.strip()
        if not decl:
            continue
        if ZERO_VALUE_RE.match(decl) or OPACITY_RE.match(decl):
            return True
        # off-screen tricks keep the substring form (number-agnostic)
        if "left:" in decl and "-999" in decl:
            return True
        if "top:" in decl and "-999" in decl:
            return True
    return False


NEW_KW_list = ["display: none", "display:none", "visibility: hidden",
               "visibility:hidden", "left: -999", "top: -999"]
print("\nR-C-2 visibility: proposed keyword list =", NEW_KW_list)

STYLES = [
    ("opacity: 0.9", False), ("width: 0.5px", False), ("height: 0.5em", False),
    ("max-width: 0.5%", False), ("font-size: 0.9em", False),
    ("opacity: 0", True), ("opacity:0", True), ("width: 0", True), ("width:0px", True),
    ("height: 0", True), ("height:0%", True), ("display: none", True),
    ("display:none", True), ("visibility: hidden", True), ("left: -9999px", True),
    ("top: -9999px", True), ("width: 100%", False), ("opacity: 1", False),
    ("color: #c00; width: 0.5em", False), ("width: 0.5em; height: 12px", False),
    ("border-width: 0.5px", False), ("opacity: 0.05", False),
]
print(f"\n{'style':34} {'expected':>8} {'now':>6} {'new':>6}  verdict")
bad = 0
for style, expected in STYLES:
    o, n = hidden_old(style), hidden_new(style)
    mark = "ok" if n == expected else "NEW WRONG"
    if n != expected:
        bad += 1
    if n != expected or True:
        print(f"{style:34} {str(expected):>8} {str(o):>6} {str(n):>6}  {mark}")
print(f"  new-rule mismatches: {bad}  (now-rule mismatches: "
      f"{sum(1 for s, e in STYLES if hidden_old(s) != e)})")

# --- banner / ad aspect rule (R-C-7) ----------------------------------------
BANNER_AREA_MAX = 1_000_000
OLD_TALL = '''                if (width_val >= 3 * height_val) or (
                    height_val >= 5 * width_val and width_val < 300
                ):
                    return True'''
NEW_TALL = '''                # R-C-7: the aspect test only fires for physically small
                # creatives — a 3:1 panorama (3000x1000) is real content.
                area = width_val * height_val
                if area <= K.BANNER_AREA_MAX and (
                    width_val >= 3 * height_val
                    or (height_val >= 5 * width_val and width_val < 300)
                ):
                    return True'''


def banner_new(url, attrs, area_max=BANNER_AREA_MAX):
    """Reference implementation of the proposed rule (same inputs as the real one)."""
    import src.parser.utils as _u
    dims = attrs.get("dimensions") if isinstance(attrs.get("dimensions"), dict) else {}
    w = dims.get("width", attrs.get("width", ""))
    h = dims.get("height", attrs.get("height", ""))
    # delegate the non-dimension logic to the real function by passing no dims
    base = real_banner(url, {})
    if base:
        return True
    try:
        if w and h:
            wv, hv = int(w), int(h)
            if (wv <= 5 and hv <= 5) or (wv == 1 and hv == 1):
                return True
            if wv * hv <= area_max and (wv >= 3 * hv or (hv >= 5 * wv and wv < 300)):
                return True
    except (ValueError, TypeError):
        pass
    return False


SIZES = [
    ("1x1 tracker", 1, 1, True), ("728x90 leaderboard", 728, 90, True),
    ("300x250 MPU", 300, 250, False), ("970x250 billboard", 970, 250, True),
    ("160x600 skyscraper", 160, 600, True), ("1200x800 photo", 1200, 800, False),
    ("3000x1000 panorama", 3000, 1000, False), ("1920x640 wallpaper", 1920, 640, False),
    ("4096x1024 pano", 4096, 1024, False), ("8000x2000 pano", 8000, 2000, False),
    ("1920x1080 video", 1920, 1080, False), ("600x200 thumb", 600, 200, False),
    ("4000x200 extreme", 4000, 200, True), ("100x600 tall", 100, 600, True),
    ("400x900 tall legit", 400, 900, False),
]
print(f"\n{'size':26} {'desired':>8} {'now':>6} {'new':>6}  verdict")
mism = 0
for name, w, h, desired in SIZES:
    attrs = {"dimensions": {"width": w, "height": h}}
    o = real_banner("https://cdn.example.com/p.jpg", attrs)
    n = banner_new("https://cdn.example.com/p.jpg", attrs)
    ok = (n == desired)
    if not ok:
        mism += 1
    print(f"{name:26} {str(desired):>8} {str(o):>6} {str(n):>6}  {'ok' if ok else 'MISMATCH'}")
print(f"  mismatches vs desired: {mism}; existing test cases preserved:",
      real_banner("https://example.com/img/pixel.png", {"dimensions": {"width": 1, "height": 1}}) is True,
      real_banner("https://example.com/img/photo.jpg", {"dimensions": {"width": 1200, "height": 800}}) is False,
      banner_new("https://example.com/img/banner.png", {"dimensions": {"width": 728, "height": 90}}) is True)
