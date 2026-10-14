# -*- coding: utf-8 -*-
"""R-C final verification: banner rule v2, typed failures (AST), MT timing."""
import ast
import copy
import os
import shutil
import sys
import tempfile
import time
import types
import pathlib
from unittest.mock import MagicMock

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

from src import constants as K  # noqa: E402
from src.downloader.media_downloader import MediaDownloader  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Banner rule: compare three candidate policies on real-world sizes
# ---------------------------------------------------------------------------
AREA_MAX = 1_000_000


def rule(attrs, ratio, area_max, tall_ratio=None, tall_w_max=None):
    dims = attrs
    w, h = int(dims["width"]), int(dims["height"])
    if (w <= 5 and h <= 5) or (w == 1 and h == 1):
        return True
    if w * h > area_max:
        return False
    if 2 * w >= ratio * h:                     # width/height >= ratio/2
        return True
    if tall_ratio and tall_w_max and tall_ratio * w <= h and w <= tall_w_max:
        return True
    return False


STANDARD_ADS = [("728x90", 728, 90), ("970x90", 970, 90), ("468x60", 468, 60),
                ("234x60", 234, 60), ("320x50", 320, 50), ("970x250", 970, 250),
                ("160x600", 160, 600), ("120x600", 120, 600), ("300x250", 300, 250),
                ("336x280", 336, 280)]
LEGIT = [("1200x800", 1200, 800), ("3000x1000", 3000, 1000), ("1920x640", 1920, 640),
         ("4096x1024", 4096, 1024), ("8000x2000", 8000, 2000), ("1500x500", 1500, 500),
         ("600x200", 600, 200), ("1400x400", 1400, 400), ("1920x1080", 1920, 1080),
         ("400x900", 400, 900), ("600x900", 600, 900), ("2560x1440", 2560, 1440),
         ("1000x250 crop", 1000, 250)]

POLICIES = [
    ("now (3:1, no area gate)", lambda d: None),
    ("A: 3:1 + area<=1M", lambda d: rule(d, 6, AREA_MAX)),
    ("B: 3.5:1 + area + tall3", lambda d: rule(d, 7, AREA_MAX, tall_ratio=3, tall_w_max=200)),
    ("C: 4:1 + area + tall3", lambda d: rule(d, 8, AREA_MAX, tall_ratio=3, tall_w_max=200)),
]

import src.parser.utils as _u  # noqa: E402


def now_rule(d):
    return _u.is_banner_or_ad("https://cdn.example.com/p.jpg", {"dimensions": d})


print("### R-C-7 banner policy comparison (True = dropped as ad/banner)")
print(f"{'size':18} {'kind':7} " + " ".join(f"{n[:22]:>22}" for n, _ in POLICIES))
rows = [(n, w, h, "ad") for n, w, h in STANDARD_ADS] + [(n, w, h, "legit") for n, w, h in LEGIT]
bad = {n: 0 for n, _ in POLICIES}
for name, w, h, kind in rows:
    d = {"width": w, "height": h}
    marks = []
    for pname, fn in POLICIES:
        v = now_rule(d) if fn(d) is None else fn(d)
        correct = (v is True) if kind == "ad" else (v is False)
        if not correct:
            bad[pname] += 1
        marks.append(("OK " if correct else "BAD") + f" {str(v):5}")
    print(f"{name:18} {kind:7} " + " ".join(f"{m:>22}" for m in marks))
print(f"\nwrong decisions: " + ", ".join(f"{n}={bad[n]}/{len(rows)}" for n, _ in POLICIES))

print("\nstandard ad sizes caught per policy:")
for pname, fn in POLICIES:
    caught = [n for n, w, h, kind in rows if kind == "ad"
              and (now_rule({"width": w, "height": h}) if fn({"width": w, "height": h}) is None
                   else fn({"width": w, "height": h}))]
    print(f"  {pname:28} {len(caught)}/{len(STANDARD_ADS)}  missed: "
          f"{[n for n, *_ in STANDARD_ADS if n not in caught]}")
print("legit sizes wrongly dropped per policy:")
for pname, fn in POLICIES:
    lost = [n for n, w, h, kind in rows if kind == "legit"
            and (now_rule({"width": w, "height": h}) if fn({"width": w, "height": h}) is None
                 else fn({"width": w, "height": h}))]
    print(f"  {pname:28} {len(lost)}/{len(LEGIT)}  lost: {lost}")

# ---------------------------------------------------------------------------
# 2. Typed failures: enumerate every failure return in the downloader (AST)
# ---------------------------------------------------------------------------
src_text = pathlib.Path("src/downloader/media_downloader.py").read_text(encoding="utf-8")
tree = ast.parse(src_text)
returns = []
for node in ast.walk(tree):
    if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.value.keys]
        if "error" in keys:
            err = node.value.values[keys.index("error")]
            kind = "f-string" if isinstance(err, ast.JoinedStr) else (
                err.value if isinstance(err, ast.Constant) else type(err).__name__)
            returns.append((node.lineno, kind, "failure_kind" in keys))
print(f"\n### R-C-5/6: failure returns in media_downloader.py = {len(returns)}")
for lineno, kind, has_kind in returns:
    print(f"  line {lineno:4}  kind-present={has_kind!s:5}  error={str(kind)[:58]!r}")
print("all returns tagged:", all(h for _, _, h in returns))

# The punitive set proposed for parser_manager: only genuine network/server faults.
PROPOSED = {
    "network": "penalize", "http_5xx": "penalize", "http_429": "penalize",
    "http_4xx": "forgive (dead link, not a host fault)",
    "content_skip": "forgive", "size": "forgive",
    "local_io": "forgive (disk)", "aborted": "forgive (user pressed Stop)",
}
print("\nproposed kinds:", PROPOSED)

# Current skip list vs the error strings it is meant to catch.
skip_list = ["file too small", "already exists", "invalid extension", "unsupported",
             "webpage/script content", "trash media"]
current_errors = ["Non-media file based on URL extension", "Size mismatch: expected 1, got 2",
                  "HTTP error: 404", "HTTP error: 403", "HTTP error: 410", "HTTP error: 500",
                  "HTTP error: 429", "Network error: boom", "Disk write error: x",
                  "Could not create directory: x", "Download manually aborted",
                  "Chunk failure(s): []", "Combined file size mismatch",
                  "Combining/verifying error: x", "Not enough parts for multi-thread based on min chunk size",
                  "Webpage/script content (Content-Type: text/html)", "File too small (1KB < 40KB)",
                  "Filtered as trash media (GIF/ICO/SVG)"]
print(f"\n{'error':56} {'now penalises?':>14}  desired")
for e in current_errors:
    now = not any(s in e.lower() for s in skip_list)
    low = e.lower()
    if low.startswith("network error") or "http error: 5" in low or "http error: 429" in low:
        desired = "penalize"
    elif e.startswith("Non-media") or e.startswith("Size mismatch") or e.startswith("Combined") \
            or e.startswith("Chunk failure") or e.startswith("Combining") \
            or e.startswith("Not enough parts") or e.startswith("Disk write") \
            or e.startswith("Could not create") or e.startswith("Download manually aborted") \
            or e.startswith("HTTP error: 4"):
        desired = "forgive"
    else:
        desired = "forgive"
    flag = "ok" if (("penalize" if now else "forgive") == desired) else "MISMATCH"
    print(f"{e[:56]:56} {str(now):>14}  {desired}  {flag}")

# ---------------------------------------------------------------------------
# 3. MT chunk check: does it cost anything on a legitimate big file?
# ---------------------------------------------------------------------------
SRC = pathlib.Path("src/downloader/media_downloader.py").read_text(encoding="utf-8")
OLD_CHUNK = ("            response = self.session.get(self.url, headers=headers, stream=True, timeout=timeout_val)\n"
             "            try:\n"
             "                response.raise_for_status()\n")
NEW_CHUNK = OLD_CHUNK + (
    "                chunk_ct = (response.headers.get(\"Content-Type\") or \"\").lower()\n"
    "                if any(t in chunk_ct for t in (\"text/html\", \"application/javascript\",\n"
    "                                               \"text/javascript\", \"text/css\",\n"
    "                                               \"application/json\")):\n"
    "                    raise IOError(f\"Webpage/script content (Content-Type: {chunk_ct})\")\n")
assert SRC.count(OLD_CHUNK) == 1
mod = types.ModuleType("patched_chunk_md")
mod.__file__ = "patched_chunk_md"
exec(compile(SRC.replace(OLD_CHUNK, NEW_CHUNK), "patched_chunk_md.py", "exec"), mod.__dict__)
PatchedMD = mod.MediaDownloader


class FakeResp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code, self.headers, self._body = status, headers or {}, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError("x", response=self)

    def iter_content(self, chunk_size=8192):
        yield self._body

    def close(self):
        pass


class RangedSession:
    def __init__(self, body, ct, total):
        self.body, self.ct, self.total = body, ct, total

    def head(self, url, **kw):
        return FakeResp(200, {"Content-Type": self.ct, "Content-Length": str(self.total),
                              "Accept-Ranges": "bytes"})

    def get(self, url, headers=None, **kw):
        rng = (headers or {}).get("Range")
        if rng:
            s, e = (int(x) for x in rng.replace("bytes=", "").split("-"))
            return FakeResp(206, {"Content-Type": self.ct, "Content-Length": str(e - s + 1)},
                            self.body[s:e + 1])
        return FakeResp(200, {"Content-Type": self.ct, "Content-Length": str(len(self.body))}, self.body)


jpeg = b"\xff\xd8\xff\xe0" + b"J" * 2_500_000
sess = RangedSession(jpeg, "image/jpeg", len(jpeg))
settings = {K.SETTING_THREADS_PER_FILE: 2}


def mt_download(cls, runs=3):
    best = 1e9
    ok = None
    for _ in range(runs):
        tmp = tempfile.mkdtemp(prefix="mt_")
        try:
            d = cls(url="https://cdn.example.com/big.jpg",
                    filepath=os.path.join(tmp, "big.jpg"), settings=settings, media_type="image")
            d.session = sess
            t0 = time.perf_counter()
            r = d.download()
            best = min(best, time.perf_counter() - t0)
            ok = r["success"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return best, ok


print("\n### MT chunk check cost (2 threads, 2.5 MB legit JPEG)")
t_now, ok_now = mt_download(MediaDownloader)
t_patched, ok_patched = mt_download(PatchedMD)
print(f"  now    : {t_now*1000:7.1f} ms  success={ok_now}")
print(f"  patched: {t_patched*1000:7.1f} ms  success={ok_patched}")
print(f"  delta  : {(t_patched-t_now)*1000:+7.1f} ms  (header lookup per chunk)")
