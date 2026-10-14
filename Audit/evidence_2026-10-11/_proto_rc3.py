# -*- coding: utf-8 -*-
"""R-C-3 prototype: fix the dead GET Content-Type guard, verifed in memory.

The repo is NOT modified: the patch is applied to the source text, compiled and
exec'd into a throwaway module. The unpatched class from the real module is used
as the control, so both versions run against identical fakes.
"""
import os
import shutil
import sys
import tempfile
import types
import pathlib
from unittest.mock import MagicMock

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

from src.parser import http_engine  # noqa: E402
from src.downloader.media_downloader import MediaDownloader as RealDownloader  # noqa: E402

SRC_PATH = pathlib.Path("src/downloader/media_downloader.py")
src = SRC_PATH.read_text(encoding="utf-8")

# --- the proposed patch -----------------------------------------------------
OLD_HEAD_EXCEPT = (
    "            except http_engine.NETWORK_ERROR_EXCEPTIONS as e:\n"
    "                logger.warning(f\"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.\")\n"
)
NEW_HEAD_EXCEPT = (
    "            except http_engine.NETWORK_ERROR_EXCEPTIONS as e:\n"
    "                # R-C-3: a HEAD that came back 403/405 still leaves a non-None\n"
    "                # response object, which used to disable the GET-side content\n"
    "                # validation entirely. Remember the failure explicitly.\n"
    "                head_failed = True\n"
    "                logger.warning(f\"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.\")\n"
)
OLD_HEAD_INIT = "            response_head = None  # Define response_head before try block\n"
NEW_HEAD_INIT = ("            response_head = None  # Define response_head before try block\n"
                 "            head_failed = False  # R-C-3\n")
OLD_GUARD = "            if response_head is None:\n"
NEW_GUARD = "            if response_head is None or head_failed:\n"

for needle in (OLD_HEAD_EXCEPT, OLD_HEAD_INIT, OLD_GUARD):
    assert src.count(needle) == 1, f"anchor not unique: {needle!r} -> {src.count(needle)}"

patched_src = (src.replace(OLD_HEAD_INIT, NEW_HEAD_INIT)
                  .replace(OLD_HEAD_EXCEPT, NEW_HEAD_EXCEPT)
                  .replace(OLD_GUARD, NEW_GUARD))

mod = types.ModuleType("patched_media_downloader")
mod.__file__ = str(SRC_PATH)
exec(compile(patched_src, "patched_media_downloader.py", "exec"), mod.__dict__)
PatchedDownloader = mod.MediaDownloader

# --- variant B: validate the GET response unconditionally -------------------
# (covers HEAD lying about the type, HEAD 200 without Content-Type, etc.)
NEW_GUARD_B = "            if True:\n"
assert src.count(OLD_GUARD) == 1
src_b = (src.replace(OLD_HEAD_INIT, NEW_HEAD_INIT)
            .replace(OLD_HEAD_EXCEPT, NEW_HEAD_EXCEPT)
            .replace(OLD_GUARD, NEW_GUARD_B))
mod_b = types.ModuleType("patched_b_media_downloader")
mod_b.__file__ = str(SRC_PATH)
exec(compile(src_b, "patched_b_media_downloader.py", "exec"), mod_b.__dict__)
VariantB = mod_b.MediaDownloader

# --- variant C = B + the same content-type check inside each MT chunk -------
OLD_CHUNK_RAISE = ("            response = self.session.get(self.url, headers=headers, stream=True, timeout=timeout_val)\n"
                   "            try:\n"
                   "                response.raise_for_status()\n")
NEW_CHUNK_RAISE = """            response = self.session.get(self.url, headers=headers, stream=True, timeout=timeout_val)
            try:
                response.raise_for_status()
                # R-C-3c: the MT path assembles ranged GETs and never reached the
                # single-threaded GET guard — validate the chunk type here too.
                chunk_ct = (response.headers.get("Content-Type") or "").lower()
                if any(t in chunk_ct for t in ("text/html", "application/javascript",
                                               "text/javascript", "text/css",
                                               "application/json")):
                    raise IOError(f"Webpage/script content (Content-Type: {chunk_ct})")
"""
assert src_b.count(OLD_CHUNK_RAISE) == 1, src_b.count(OLD_CHUNK_RAISE)
src_c = src_b.replace(OLD_CHUNK_RAISE, NEW_CHUNK_RAISE)
mod_c = types.ModuleType("patched_c_media_downloader")
mod_c.__file__ = str(SRC_PATH)
exec(compile(src_c, "patched_c_media_downloader.py", "exec"), mod_c.__dict__)
VariantC = mod_c.MediaDownloader
print("patched modules built; classes differ:",
      PatchedDownloader is not RealDownloader, VariantB is not RealDownloader)


HTML = (b"<!DOCTYPE html><html><head><title>403 Forbidden</title></head>"
        b"<body><h1>Access denied</h1></body></html>")
JPEG = b"\xff\xd8\xff\xe0" + b"J" * 119996  # exactly 120000 bytes


class FakeResp:
    def __init__(self, status=200, headers=None, body=b"", url="https://cdn.example.com/photo.jpg"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error", response=self)

    def iter_content(self, chunk_size=8192):
        yield self._body

    def close(self):
        pass


def head(status, ct, length):
    def _f(*a, **kw):
        return FakeResp(status, {"Content-Type": ct, "Content-Length": str(length)})
    return _f


def head_raises(exc):
    def _f(*a, **kw):
        raise exc
    return _f


def resp(status, ct, body):
    return FakeResp(status, {"Content-Type": ct, "Content-Length": str(len(body))}, body)


def run(cls, name, head_factory, get_resp):
    tmp = tempfile.mkdtemp(prefix="rc3p_")
    try:
        d = cls(url="https://cdn.example.com/photo.jpg",
                filepath=os.path.join(tmp, "photo.jpg"), settings={}, media_type="image")
        s = MagicMock()
        s.head.side_effect = head_factory
        s.get.return_value = get_resp
        d.session = s
        res = d.download()
        files = [f for f in sorted(os.listdir(tmp)) if not f.endswith(".partial")]
        blob = b""
        if files:
            blob = open(os.path.join(tmp, files[0]), "rb").read()
        is_html = blob.lstrip().startswith(b"<!DOCTYPE") or b"<html" in blob[:200]
        return {"success": res.get("success"), "error": res.get("error"),
                "files": files, "html_on_disk": is_html}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def head_no_ct(*a, **kw):
    r = FakeResp(200, {"Content-Length": str(len(JPEG))})
    return r


def resp_no_ct(status, body):
    return FakeResp(status, {"Content-Length": str(len(body))}, body)


CASES = [
    ("HEAD 403 -> GET text/html", head(403, "text/html", len(HTML)),
     resp(200, "text/html", HTML)),
    ("HEAD 405 -> GET text/html", head(405, "text/html", len(HTML)),
     resp(200, "text/html", HTML)),
    ("HEAD connection error -> GET text/html", head_raises(requests.exceptions.ConnectionError("x")),
     resp(200, "text/html", HTML)),
    ("HEAD 200 text/html", head(200, "text/html", len(HTML)), resp(200, "text/html", HTML)),
    ("HEAD 403 -> GET image/jpeg (HEAD refused)", head(403, "text/html", 0),
     resp(200, "image/jpeg", JPEG)),
    ("HEAD connection error -> GET image/jpeg", head_raises(requests.exceptions.ConnectionError("x")),
     resp(200, "image/jpeg", JPEG)),
    ("HEAD 200 image/jpeg", head(200, "image/jpeg", len(JPEG)), resp(200, "image/jpeg", JPEG)),
    ("HEAD 403 -> GET application/json", head(403, "text/html", 0),
     resp(200, "application/json", b'{"error":"forbidden"}')),
    ("HEAD 200 image/jpeg but GET text/html (HEAD lies)", head(200, "image/jpeg", len(HTML)),
     resp(200, "text/html", HTML)),
    ("HEAD 200 without Content-Type, GET text/html", head_no_ct, resp(200, "text/html", HTML)),
    ("HEAD 200 image/jpeg, GET octet-stream (legit)", head(200, "image/jpeg", len(JPEG)),
     resp(200, "application/octet-stream", JPEG)),
    ("HEAD 200 image/jpeg, GET no Content-Type", head(200, "image/jpeg", len(JPEG)),
     resp_no_ct(200, JPEG)),
]


def brief(r):
    return f"{'OK ' if r['success'] else 'ERR'} {r['error'] or '-'} html={r['html_on_disk']}"


print(f"\n{'case':48} {'BEFORE':30} {'A(minimal)':30} {'B(always)'}")
print("-" * 140)
diffs = []
for name, hf, gr in CASES:
    a = run(RealDownloader, name, hf, gr)
    b = run(PatchedDownloader, name, hf, gr)
    c = run(VariantB, name, hf, gr)
    print(f"{name:48} {brief(a)[:29]:30} {brief(b)[:29]:30} {brief(c)}")
    if a != b or b != c:
        diffs.append((name, a, b, c))

print("\nDIFFERENCES vs the current code:")
for n, a, b, c in diffs:
    print(f"  {n}")
    print(f"    now     : {a}")
    print(f"    variant A: {b}")
    print(f"    variant B: {c}")

# Regression assertions for the intended semantics.
ok = True


def expect(name, cond):
    global ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    ok = ok and cond


print("\nASSERTIONS (post-patch semantics):")
r = run(PatchedDownloader, "x", head(403, "text/html", len(HTML)), resp(200, "text/html", HTML))
expect("HEAD 403 + HTML GET is rejected", r["success"] is False and "Webpage/script content" in r["error"])
expect("no HTML file left on disk", not r["files"])
r = run(PatchedDownloader, "x", head(403, "text/html", 0), resp(200, "image/jpeg", JPEG))
expect("HEAD 403 + JPEG GET still downloads (no functional loss)",
       r["success"] is True and r["files"] and not r["html_on_disk"])
r = run(PatchedDownloader, "x", head_raises(requests.exceptions.ConnectionError("x")),
        resp(200, "image/jpeg", JPEG))
expect("HEAD without response + JPEG GET still downloads", r["success"] is True)
r = run(PatchedDownloader, "x", head(200, "image/jpeg", len(JPEG)), resp(200, "image/jpeg", JPEG))
expect("healthy path unchanged (HEAD 200 + JPEG)", r["success"] is True)
r = run(PatchedDownloader, "x", head(403, "text/html", 0), resp(200, "application/json", b"{}"))
expect("HEAD 403 + JSON GET rejected", r["success"] is False and "Webpage/script content" in r["error"])

print("\nASSERTIONS (variant B):")
r = run(VariantB, "x", head(200, "image/jpeg", len(HTML)), resp(200, "text/html", HTML))
expect("VariantB rejects HTML when HEAD lied", r["success"] is False and not r["files"])
r = run(VariantB, "x", head_no_ct, resp(200, "text/html", HTML))
expect("VariantB rejects HTML when HEAD had no Content-Type", r["success"] is False)
r = run(VariantB, "x", head(200, "image/jpeg", len(JPEG)), resp(200, "application/octet-stream", JPEG))
expect("VariantB still accepts octet-stream (legit CDN label)", r["success"] is True)
r = run(VariantB, "x", head(200, "image/jpeg", len(JPEG)), resp_no_ct(200, JPEG))
expect("VariantB accepts a GET without Content-Type", r["success"] is True)
r = run(VariantB, "x", head(403, "text/html", 0), resp(200, "image/jpeg", JPEG))
expect("VariantB accepts JPEG behind a HEAD-refusing host", r["success"] is True)
print("\nRESULT:", "ALL EXPECTATIONS MET" if ok else "SOME EXPECTATIONS FAILED")

# ---------------------------------------------------------------------------
# Residual-gap checks: a HEAD that LIES (200 + image/jpeg) and the MT path,
# which never validates the GET body at all.
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("RESIDUAL GAPS: lying HEAD and the multi-threaded path")
print("=" * 78)

BIG_HTML = b"<!DOCTYPE html><html><body>" + b"x" * 60000 + b"</body></html>"


class RangedSession:
    """Honors Range requests so the MT path can assemble a body."""

    def __init__(self, body, head_ct, get_ct, total):
        self.body, self.head_ct, self.get_ct, self.total = body, head_ct, get_ct, total

    def head(self, url, **kw):
        return FakeResp(200, {"Content-Type": self.head_ct,
                              "Content-Length": str(self.total),
                              "Accept-Ranges": "bytes"})

    def get(self, url, headers=None, **kw):
        rng = (headers or {}).get("Range")
        if rng:
            start, end = (int(x) for x in rng.replace("bytes=", "").split("-"))
            return FakeResp(206, {"Content-Type": self.get_ct,
                                  "Content-Length": str(end - start + 1)},
                            self.body[start:end + 1])
        return FakeResp(200, {"Content-Type": self.get_ct,
                              "Content-Length": str(len(self.body))}, self.body)

    def close(self):
        pass


from src import constants as K  # noqa: E402


def run_with_session(cls, session, name, settings=None):
    tmp = tempfile.mkdtemp(prefix="rc3r_")
    try:
        d = cls(url="https://cdn.example.com/photo.jpg",
                filepath=os.path.join(tmp, "photo.jpg"), settings=settings or {},
                media_type="image")
        d.session = session
        res = d.download()
        files = [f for f in sorted(os.listdir(tmp)) if not f.endswith(".partial")]
        blob = open(os.path.join(tmp, files[0]), "rb").read() if files else b""
        return {"success": res.get("success"), "error": res.get("error"),
                "files": files, "html_on_disk": b"<html" in blob[:400]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# 1) HEAD 200 image/jpeg, body is HTML big enough to pass the size floor
lying = RangedSession(BIG_HTML, "image/jpeg", "text/html", len(BIG_HTML))
print(f"\n1) HEAD says image/jpeg ({len(BIG_HTML)} B), GET says text/html")
for label, cls in (("now", RealDownloader), ("A", PatchedDownloader), ("B", VariantB)):
    print(f"   {label:4} -> {run_with_session(cls, lying, 'x')}")

# 2) HEAD 200 with no Content-Type, body HTML
noct = RangedSession(BIG_HTML, "", "text/html", len(BIG_HTML))
print(f"\n2) HEAD 200 without Content-Type, GET text/html")
for label, cls in (("now", RealDownloader), ("A", PatchedDownloader), ("B", VariantB)):
    print(f"   {label:4} -> {run_with_session(cls, noct, 'x')}")

# 3) multi-threaded path: ranged GETs assemble an HTML body
mt_body = b"<!DOCTYPE html><html><body>" + b"y" * 2_500_000 + b"</body></html>"
mt = RangedSession(mt_body, "image/jpeg", "text/html", len(mt_body))
mt_settings = {K.SETTING_THREADS_PER_FILE: 2}
print(f"\n3) MT path: HEAD image/jpeg, ranged GETs return text/html "
      f"({len(mt_body)} B, threads=2)")
for label, cls in (("now", RealDownloader), ("A", PatchedDownloader),
                   ("B", VariantB), ("C", VariantC)):
    print(f"   {label:4} -> {run_with_session(cls, mt, 'x', mt_settings)}")

# 4) control: a legitimate large image must still download multi-threaded
jpeg_big = b"\xff\xd8\xff\xe0" + b"J" * 2_500_000
mt_ok = RangedSession(jpeg_big, "image/jpeg", "image/jpeg", len(jpeg_big))
print(f"\n4) control: MT path with a real JPEG ({len(jpeg_big)} B, threads=2)")
for label, cls in (("now", RealDownloader), ("A", PatchedDownloader),
                   ("B", VariantB), ("C", VariantC)):
    r = run_with_session(cls, mt_ok, 'x', mt_settings)
    print(f"   {label:4} -> success={r['success']} error={r['error']}")

print("\nEXPECTED: C rejects the HTML in cases 1-3 and keeps case 4 working")
for label, cls in (("A", PatchedDownloader), ("B", VariantB), ("C", VariantC)):
    r1 = run_with_session(cls, lying, 'x')
    r3 = run_with_session(cls, mt, 'x', mt_settings)
    r4 = run_with_session(cls, mt_ok, 'x', mt_settings)
    verdict = ("html still saved" if r1["html_on_disk"] else "html rejected") + ", " \
              + ("MT html saved" if r3["html_on_disk"] else "MT html rejected") + ", " \
              + ("legit MT ok" if r4["success"] else "legit MT BROKEN")
    print(f"   {label}: {verdict}")
