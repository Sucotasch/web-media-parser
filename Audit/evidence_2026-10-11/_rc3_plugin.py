# -*- coding: utf-8 -*-
"""Temporary pytest plugin: attaches the proposed R-C patches at runtime so the
existing suite exercises the patched code WITHOUT touching the repository.

Usage:  python -m pytest tests -q -p _rc3_plugin
Env:
  RC_PATCH=media|json|utils|all   (default: all)
"""
import os
import pathlib
import re
import sys
import types

# NOTE: deliberately do NOT touch sys.path — inserting a resolved path here
# changes the case of test modules' __file__ and breaks a path-comparing test.

WHICH = os.environ.get("RC_PATCH", "all")


def _build(path, replacements, modname):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    for old, new in replacements:
        assert src.count(old) == 1, f"{path}: anchor {src.count(old)}x for {old[:50]!r}"
        src = src.replace(old, new)
    mod = types.ModuleType(modname)
    mod.__file__ = path
    exec(compile(src, modname + ".py", "exec"), mod.__dict__)
    return mod


def _patch_media():
    """R-C-3: validate the GET response on both the single-threaded and MT paths."""
    old_init = "            response_head = None  # Define response_head before try block\n"
    new_init = old_init + "            head_failed = False  # R-C-3\n"
    old_exc = ("            except http_engine.NETWORK_ERROR_EXCEPTIONS as e:\n"
               "                logger.warning(f\"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.\")\n")
    new_exc = ("            except http_engine.NETWORK_ERROR_EXCEPTIONS as e:\n"
               "                head_failed = True  # R-C-3: a 403/405 HEAD still leaves a response object\n"
               "                logger.warning(f\"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.\")\n")
    old_guard = "            if response_head is None:\n"
    new_guard = "            if response_head is None or head_failed:\n"
    old_chunk = ("            response = self.session.get(self.url, headers=headers, stream=True, timeout=timeout_val)\n"
                 "            try:\n"
                 "                response.raise_for_status()\n")
    new_chunk = old_chunk + (
        "                chunk_ct = (response.headers.get(\"Content-Type\") or \"\").lower()\n"
        "                if any(t in chunk_ct for t in (\"text/html\", \"application/javascript\",\n"
        "                                               \"text/javascript\", \"text/css\",\n"
        "                                               \"application/json\")):\n"
        "                    raise IOError(f\"Webpage/script content (Content-Type: {chunk_ct})\")\n")
    mod = _build("src/downloader/media_downloader.py",
                 [(old_init, new_init), (old_exc, new_exc), (old_guard, new_guard),
                  (old_chunk, new_chunk)], "rc_media_patch")
    import src.downloader.media_downloader as real
    real.MediaDownloader._do_download = mod.MediaDownloader._do_download
    real.MediaDownloader._download_chunk = mod.MediaDownloader._download_chunk
    return "media"


def _patch_json():
    """R-C-1 + R-C-4 + R-C-8."""
    old_list = '''        media_key_patterns = [
            "url", "src", "source", "media", "image", "img", "photo", "picture",
            "thumbnail", "thumb", "icon", "avatar", "video", "file", "path",
            "href", "link", "content", "data", "original", "high_res", "hd",
        ]
'''
    old_tail = '''                                self.media_files.append((media_type, abs_url, attrs))
                        else:
                            self.links.add(abs_url)
'''
    new_tail = old_tail + '''                else:
                    self._extract_media_from_json(item, path)
'''
    old_dict = "    def _process_potential_media(self, value: Any, path: str) -> None:\n"
    new_dict = old_dict + (
        "        if isinstance(value, dict):\n"
        "            self._extract_media_from_json(value, path)\n"
        "            return\n")
    old_links = '''        if isinstance(data, dict):
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
    new_links = '''        stack = [(data, 0)]
        visited = 0
        while stack:
            node, depth = stack.pop()
            visited += 1
            if visited > K.JSON_LINK_SCAN_MAX_NODES or depth > 32:
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
                    stack.append((value, depth + 1))
            elif isinstance(node, list):
                for item in node:
                    stack.append((item, depth + 1))
'''
    mod = _build("src/parser/json_parser.py",
                 [(old_list, "        media_key_patterns = K.JSON_MEDIA_KEY_PATTERNS  # R-C-8\n"),
                  (old_tail, new_tail), (old_dict, new_dict), (old_links, new_links)],
                 "rc_json_patch")
    from src import constants as K
    if not hasattr(K, "JSON_PAGINATION_KEY_PATTERNS"):
        K.JSON_PAGINATION_KEY_PATTERNS = ["next", "next_page", "nextpage", "pagination",
                                          "paging", "links"]
    if not hasattr(K, "JSON_LINK_SCAN_MAX_NODES"):
        K.JSON_LINK_SCAN_MAX_NODES = 100_000
    import src.parser.json_parser as real
    real.JSONWebpageParser._extract_media_from_json = mod.JSONWebpageParser._extract_media_from_json
    real.JSONWebpageParser._process_potential_media = mod.JSONWebpageParser._process_potential_media
    real.JSONWebpageParser._extract_links_from_json = mod.JSONWebpageParser._extract_links_from_json
    real.JSONWebpageParser._is_media_key = mod.JSONWebpageParser._is_media_key
    return "json"


def _patch_utils():
    """R-C-2 (visibility regexes) + R-C-7 (area-gated aspect rule)."""
    old_kw = '''    "display: none", "display:none", "visibility: hidden", "visibility:hidden", 
    "opacity: 0", "opacity:0", "left: -999", "top: -999", "width: 0", "height: 0"'''
    new_kw = '''    "display: none", "display:none", "visibility: hidden", "visibility:hidden",
    "left: -999", "top: -999"'''
    src = pathlib.Path("src/constants.py").read_text(encoding="utf-8")
    assert src.count(old_kw) == 1
    patched = src.replace(old_kw, new_kw)
    mod = types.ModuleType("rc_constants_patch")
    mod.__file__ = "src/constants.py"
    exec(compile(patched, "rc_constants_patch.py", "exec"), mod.__dict__)
    from src import constants as K
    K.VISIBILITY_HIDDEN_KEYWORDS = mod.VISIBILITY_HIDDEN_KEYWORDS

    old_style_check = "            if style and any(kw in style for kw in hidden_keywords):\n"
    new_style_check = '''            if style:
                if any(kw in style for kw in hidden_keywords):
                    return False
                if re.search(r"(?:^|;)\\s*(?:opacity\\s*:\\s*0(?:\\.0+)?)\\s*(?:;|$)", style):
                    return False
                if re.search(r"(?:^|;)\\s*(?:width|height|max-width|max-height)\\s*:\\s*0(?:\\.0+)?\\s*(?:px|%|em|rem|pt)?\\s*(?:;|$)", style):
                    return False
'''
    wp = _build("src/parser/webpage_parser.py", [(old_style_check, new_style_check)],
                "rc_wp_patch")
    import src.parser.webpage_parser as real_wp
    real_wp.WebpageParser._is_element_visible = wp.WebpageParser._is_element_visible
    return "utils"


def _patch_banner():
    """R-C-7 only (utils.is_banner_or_ad + the name bound inside webpage_parser)."""
    old = '''                if (width_val >= 3 * height_val) or (
                    height_val >= 5 * width_val and width_val < 300
                ):
                    return True'''
    new = '''                area = width_val * height_val
                if area <= K.BANNER_MAX_AREA and (
                    2 * width_val >= 7 * height_val
                    or (3 * width_val <= height_val and width_val <= 200)
                ):
                    return True'''
    mod = _build("src/parser/utils.py", [(old, new)], "rc_utils_patch")
    from src import constants as K
    if not hasattr(K, "BANNER_MAX_AREA"):
        K.BANNER_MAX_AREA = 1_000_000
    import src.parser.utils as real_utils
    import src.parser.webpage_parser as real_wp
    real_utils.is_banner_or_ad = mod.is_banner_or_ad
    real_wp.is_banner_or_ad = mod.is_banner_or_ad
    return "banner"


def pytest_configure(config):
    applied = []
    if WHICH in ("all", "media"):
        applied.append(_patch_media())
    if WHICH in ("all", "json"):
        applied.append(_patch_json())
    if WHICH in ("all", "utils"):
        applied.append(_patch_utils())
    if WHICH in ("all", "banner"):
        applied.append(_patch_banner())
    print(f"\n[_rc3_plugin] applied runtime patches: {applied}")
