#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Этап 2 (FIXPLAN_2026-10-15): the media write path — one pass.

Covers the seven findings that all live in `MediaDownloader._do_download` /
`_download_chunk` / `_download_with_threads` and had to change together:

A-07 — no ceiling on a single file (disk fill).
B-10 — the MT assembly wrote the FINAL name, so an interruption left a
       truncated file under a plausible media name (and the next run then picked
       `name_1.jpg`, preserving it forever).
B-11 — the Content-Type substring checks trusted the header; an HTML login page
       served as `text/plain` was written under a media name with `success`.
B-12 — no Content-Length disabled the only integrity check (NF-8: a truncated
       chunked body surfaces as a read-timeout ConnectionError, a close-delimited
       one ended silently as "success" with half a file).
B-13 — the retry backoff slept through Stop (measured: 2.36 s after the click).
NF-3 — a Stop left its own `.partial` behind: the abort branch removed the temp
       file while its handle was still open, which Windows refuses.
"""

import asyncio
import os
import sys
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import MockGUILogHandler

from src import constants as K
from src.downloader.media_downloader import MediaDownloader, create_shared_downloader_session
from src.parser.parser_manager import ParserManager

# Disable the too-small gate so tiny mock bodies are accepted.
ZERO_MIN_SIZE = {K.SETTING_MIN_IMG_SIZE: 0, K.SETTING_MIN_VID_SIZE: 0}

_JPEG = b"\xff\xd8\xff\xe0" + b"J" * 1000
_LOGIN_PAGE = (b"<!DOCTYPE html><html><head><title>Please sign in</title></head>"
               b"<body>Login</body></html>")


class _Response:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class _Session:
    """HEAD/GET double with full control over length, framing and ranges.

    `head_cl` / `get_cl`: None = advertise len(body), False = omit the header.
    `transfer_encoding` adds `Transfer-Encoding: chunked`.
    """

    def __init__(self, body=b"", *, head_ct="image/jpeg", get_ct="image/jpeg",
                 head_status=200, get_status=200, accept_ranges=False,
                 head_cl=None, get_cl=None, transfer_encoding=None,
                 ignore_range=False):
        self.body = body
        self.head_ct, self.get_ct = head_ct, get_ct
        self.head_status, self.get_status = head_status, get_status
        self.accept_ranges = accept_ranges
        self.head_cl, self.get_cl = head_cl, get_cl
        self.transfer_encoding = transfer_encoding
        self.ignore_range = ignore_range
        self.head_calls = 0
        self.get_calls = 0

    def head(self, url, **kwargs):
        self.head_calls += 1
        headers = {"Content-Type": self.head_ct}
        cl = len(self.body) if self.head_cl is None else self.head_cl
        if cl is not False:
            headers["Content-Length"] = str(cl)
        if self.accept_ranges:
            headers["Accept-Ranges"] = "bytes"
        return _Response(self.head_status, headers)

    def get(self, url, headers=None, **kwargs):
        self.get_calls += 1
        rng = (headers or {}).get("Range")
        if rng and not self.ignore_range:
            start, end = (int(x) for x in rng.replace("bytes=", "").split("-"))
            body = self.body[start:end + 1]
            return _Response(206, {"Content-Type": self.get_ct,
                                   "Content-Length": str(len(body))}, body)
        resp_headers = {"Content-Type": self.get_ct}
        cl = len(self.body) if self.get_cl is None else self.get_cl
        if cl is not False:
            resp_headers["Content-Length"] = str(cl)
        if self.transfer_encoding:
            resp_headers["Transfer-Encoding"] = self.transfer_encoding
        return _Response(self.get_status, resp_headers, self.body)


def _make(tmp_path, session, settings=None, threads=1, name="photo.jpg", stop=None):
    downloader = MediaDownloader(
        url="https://cdn.example.com/photo.jpg",
        filepath=str(tmp_path / name),
        settings=dict(ZERO_MIN_SIZE, **{K.SETTING_THREADS_PER_FILE: threads,
                                        **(settings or {})}),
        media_type="image",
        stop_event=stop,
    )
    downloader.session = session
    return downloader


# --- A-07: the size ceiling -------------------------------------------------

def test_declared_length_over_the_cap_is_refused_before_the_get(tmp_path):
    """The HEAD already knows the size — no GET, no file, and not a host fault."""
    session = _Session(_JPEG + b"x" * 200_000)
    result = _make(tmp_path, session, {K.SETTING_MAX_FILE_SIZE: 10}).download()

    assert result["success"] is False
    assert "Max File Size" in result["error"], result
    assert "failure_kind" not in result, "a local size policy must not penalise the host"
    assert session.get_calls == 0, "the body was requested despite the declared size"
    assert os.listdir(tmp_path) == []


def test_stream_crossing_the_cap_stops_before_the_write(tmp_path):
    """A host that declares little and streams a lot is stopped mid-stream."""
    body = _JPEG + b"x" * 200_000
    # HEAD lies about the length; only the stream reveals the real size.
    session = _Session(body, head_cl="100")
    result = _make(tmp_path, session, {K.SETTING_MAX_FILE_SIZE: 10}).download()

    assert result["success"] is False
    assert "Max File Size" in result["error"], result
    assert os.listdir(tmp_path) == [], "a partial file was left behind"


def test_multi_threaded_chunks_respect_the_cap(tmp_path):
    """A server that ignores Range streams the whole body into every chunk."""
    body = _JPEG + b"M" * 6_000_000
    # Declared 3 MB keeps the MT path eligible (HEAD gate sees 3 MB <= 4 MB cap),
    # the real body is 6 MB and arrives in full for each range.
    session = _Session(body, accept_ranges=True, ignore_range=True, head_cl="3000000")
    result = _make(tmp_path, session, {K.SETTING_MAX_FILE_SIZE: 4 * 1024},
                   threads=2, name="big.jpg").download()

    assert result["success"] is False
    assert "Max File Size" in result["error"], result
    assert os.listdir(tmp_path) == [], os.listdir(tmp_path)


# --- B-11: body sniffing ----------------------------------------------------

def test_login_page_served_as_text_plain_is_refused(tmp_path):
    """Measured before the fix: `success` with a 51-byte HTML page named .jpg."""
    session = _Session(_LOGIN_PAGE, get_ct="text/plain")
    result = _make(tmp_path, session).download()

    assert result["success"] is False
    assert result["failure_kind"] == K.FAILURE_CONTENT_PAGE
    assert "not media" in result["error"], result
    assert os.listdir(tmp_path) == []


def test_sniff_keeps_a_user_enabled_svg(tmp_path):
    """`<?xml` is only a rejection signal when the server did not say image/*."""
    svg = b'<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg"/>'
    session = _Session(svg, head_ct="image/svg+xml", get_ct="image/svg+xml")
    result = _make(tmp_path, session).download()

    assert result["success"] is True, result
    assert open(tmp_path / "photo.jpg", "rb").read() == svg


def test_xml_from_a_non_image_type_is_refused(tmp_path):
    body = b'<?xml version="1.0"?><rss><channel><item>index</item></channel></rss>'
    session = _Session(body, get_ct="application/octet-stream")
    result = _make(tmp_path, session).download()

    assert result["success"] is False
    assert result["failure_kind"] == K.FAILURE_CONTENT_PAGE
    assert os.listdir(tmp_path) == []


# --- B-12 / NF-8: bodies without a verifiable length ------------------------

def test_close_delimited_body_is_reported_as_unverified(tmp_path):
    """No Content-Length anywhere: keep the file, admit the length is unknown."""
    body = b"x" * 1000
    session = _Session(body, head_cl=False, get_cl=False)
    result = _make(tmp_path, session).download()

    assert result["success"] is True, result
    assert result.get("unverified_length") is True, result
    assert os.path.getsize(tmp_path / "photo.jpg") == len(body)


def test_chunked_framing_needs_no_unverified_flag(tmp_path):
    """A chunked body frames itself, so its end is verifiable after all."""
    session = _Session(b"x" * 1000, head_cl=False, get_cl=False,
                       transfer_encoding="chunked")
    result = _make(tmp_path, session).download()

    assert result["success"] is True, result
    assert "unverified_length" not in result, result


def test_strict_mode_refuses_a_close_delimited_body(tmp_path):
    """Opt-in: `require_verified_length` turns the unverifiable case into a refusal."""
    session = _Session(b"x" * 1000, head_cl=False, get_cl=False)
    result = _make(tmp_path, session,
                   {K.SETTING_REQUIRE_VERIFIED_LENGTH: True}).download()

    assert result["success"] is False
    assert "require_verified_length" in result["error"], result
    assert os.listdir(tmp_path) == []


def test_empty_body_is_not_a_success(tmp_path):
    """Nothing arrived: not a zero-byte "media file" under a downloaded name."""
    session = _Session(b"", head_cl=False, get_cl="0")
    result = _make(tmp_path, session).download()

    assert result["success"] is False
    assert "Empty body" in result["error"], result
    assert os.listdir(tmp_path) == []


# --- B-13: Stop must not wait for the backoff -------------------------------

def test_stop_during_backoff_exits_immediately(tmp_path):
    """Measured before the fix: Stop was ignored for 2.36 s (one full backoff)."""
    session = _Session(b"")           # never used: HEAD/GET both fail below
    session.head = lambda url, **kw: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("boom"))
    session.get = session.head
    stop = threading.Event()
    stopped_at = []

    def _stop():
        stopped_at.append(time.time())
        stop.set()

    downloader = _make(tmp_path, session,
                       {K.SETTING_RETRY_COUNT: 5}, stop=stop)
    # retry_count=5 → backoffs 0.5/1.0/1.5/2.0/2.5 s; 3.5 s lands inside the 2.0 s one.
    timer = threading.Timer(3.5, _stop)
    timer.start()
    try:
        result = downloader.download()
    finally:
        timer.cancel()

    assert stopped_at, "the stop never fired — the test did not exercise the backoff"
    assert result["success"] is False
    assert result["error"] == "Download manually aborted"
    delay = time.time() - stopped_at[0]
    assert delay < 0.5, f"Stop waited out the backoff ({delay:.2f}s)"


# --- B-10 / NF-3: atomic multi-threaded assembly ---------------------------

def _spy_on_writes():
    """Record the basenames opened for writing (the old code opened the FINAL name)."""
    import builtins

    written = []
    real_open = builtins.open

    def _open(file, mode="r", *args, **kwargs):
        if isinstance(file, str) and "w" in mode:
            written.append(os.path.basename(file))
        return real_open(file, mode, *args, **kwargs)

    return written, patch("builtins.open", _open)


def test_mt_assembly_publishes_atomically(tmp_path):
    """The final name appears only through one os.replace from `*.mt.partial`.

    Measured before the fix: the assembly opened and wrote the final name
    directly, so an interruption anywhere in it left a truncated file that the
    next run treated as an existing download.
    """
    from src.downloader import media_downloader as md_module

    body = _JPEG + b"M" * 2_500_000
    downloader = _make(tmp_path, _Session(body, accept_ranges=True), threads=2,
                       name="big.jpg")
    real_replace = os.replace
    written, spy = _spy_on_writes()
    with spy, patch.object(md_module.os, "replace", wraps=real_replace) as replace:
        result = downloader._download_with_threads(len(body))

    assert result["success"] is True, result
    assert replace.call_count == 1, replace.call_args_list
    src_path, dst_path = replace.call_args[0]
    assert src_path.endswith(".mt.partial"), src_path
    assert dst_path == str(tmp_path / "big.jpg")
    assert "big.jpg" not in written, f"the final name was written directly: {written}"
    assert "big.jpg.mt.partial" in written, written
    assert os.path.getsize(tmp_path / "big.jpg") == len(body)
    assert os.listdir(tmp_path) == ["big.jpg"], os.listdir(tmp_path)


def test_mt_failure_leaves_nothing_at_the_final_name(tmp_path):
    """Before the fix the truncated assembly WAS the file at the final name."""
    body = _JPEG + b"M" * 2_500_000
    downloader = _make(tmp_path, _Session(body, accept_ranges=True), threads=2,
                       name="big.jpg")

    written = []

    def _failing_chunk(self, start, end, filename, total_size, progress_dict,
                       progress_lock, timeout_val):
        with open(filename, "wb") as f:
            f.write(b"p" * (end - start + 1))
        written.append(filename)
        if filename.endswith("part1"):
            with progress_lock:
                progress_dict["success"] = False
                progress_dict["errors"].append("boom")

    with patch.object(MediaDownloader, "_download_chunk", _failing_chunk):
        result = downloader._download_with_threads(len(body))

    assert len(written) == 2, f"the MT phase did not run: {written}"
    assert result["success"] is False, result
    assert "Chunk failure" in result["error"], result
    assert os.listdir(tmp_path) == [], os.listdir(tmp_path)
    assert not os.path.exists(tmp_path / "big.jpg")


def test_stop_during_mt_leaves_no_temp_residue(tmp_path):
    """NF-3: the Stop sweep used to fail on Windows (temp file still open)."""
    body = _JPEG + b"M" * 2_500_000
    stop = threading.Event()

    class _StoppingSession(_Session):
        def get(self, url, headers=None, **kwargs):
            if (headers or {}).get("Range"):
                stop.set()  # Stop lands mid-chunk, not before the attempt
            return super().get(url, headers=headers, **kwargs)

    session = _StoppingSession(body, accept_ranges=True)
    downloader = _make(tmp_path, session, threads=2, name="big.jpg", stop=stop)
    result = downloader.download()

    assert result["success"] is False
    assert result["error"] == "Download manually aborted"
    assert os.listdir(tmp_path) == [], os.listdir(tmp_path)


# --- B-08: nothing is written outside the download root ---------------------

def test_path_outside_the_download_root_is_refused(tmp_path):
    """B-08: a '..' destination is refused before the name is reserved."""
    escaped = str(tmp_path / ".." / "escaped.jpg")
    downloader = _make(tmp_path, _Session(_JPEG))
    downloader.filepath = escaped
    result = downloader._do_download()

    assert result["success"] is False
    assert "Path traversal rejected" in result["error"], result
    assert "failure_kind" not in result, "our own bad path is not a host fault"
    assert not os.path.exists(escaped)


def test_media_subdirectory_cannot_escape_the_download_root():
    """B-08: dot segments in the page URL must not build '..' subdirectories."""
    with patch("src.parser.parser_manager.AsyncClientManager", MagicMock()), \
         patch.object(ParserManager, "_load_domain_blocklist", return_value=set()):
        pm = ParserManager(
            url="http://startdomain.com/", download_path="test_downloads",
            settings={K.SETTING_STAY_IN_DOMAIN: False, K.SETTING_STOP_WORDS: [],
                      K.SETTING_ENABLED_IMAGE_FORMATS: K.DEFAULT_ENABLED_IMAGE_FORMATS},
            log_handler=MockGUILogHandler(),
        )
    put = AsyncMock()
    # The queue is created in _main_task; the direct call to _process_media_files
    # below only needs something awaitable.
    pm.download_queue = MagicMock()
    pm.download_queue.put = put

    async def _run():
        # asyncio primitives belong to the loop (create it here, as start_parsing does).
        pm._processed_lock = asyncio.Lock()
        await pm._process_media_files(
            [("image", "http://cdn.example.com/a.jpg", {})],
            "http://startdomain.com/../../../../evil/gallery",
        )

    asyncio.run(_run())

    item = put.call_args[0][0]
    filepath = item["filepath"]
    assert ".." not in filepath.split(os.sep), filepath
    assert os.path.abspath(filepath).startswith(os.path.abspath(pm.download_path) + os.sep), filepath


# --- B-09: the download session follows verify_tls -------------------------

def test_shared_session_follows_the_tls_setting():
    """B-09: the shared session reads verify_tls, and is returned at all.

    The narrow defect this pins: the assignment (and its `return session`) used
    to sit inside the `if proxy_url:` block, so a session without a proxy fell
    through and every download with a shared session raised on `None`.
    """
    assert isinstance(create_shared_downloader_session({}), requests.Session)
    assert create_shared_downloader_session({}).verify is True
    assert create_shared_downloader_session(
        {K.SETTING_VERIFY_TLS: False}).verify is False
    assert create_shared_downloader_session(
        {K.SETTING_VERIFY_TLS: True}).verify is True


def test_own_session_follows_the_tls_setting():
    """B-09: a downloader that builds its own session reads verify_tls too."""
    for flag in (True, False):
        with patch("src.downloader.media_downloader.requests.Session") as session_cls:
            session_cls.return_value.headers = {}
            downloader = MediaDownloader.__new__(MediaDownloader)
            downloader.settings = {K.SETTING_VERIFY_TLS: flag}
            downloader.media_type = "image"
            downloader.source_url = None
            session = downloader._create_session()
        assert session.verify is flag, f"verify_tls={flag} -> {session.verify}"


if __name__ == "__main__":
    unittest.main()
