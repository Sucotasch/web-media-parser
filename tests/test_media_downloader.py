#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test the MediaDownloader class
"""

import sys
import os
import threading
import requests
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.downloader.media_downloader import MediaDownloader


# Phase 3 (DL) helpers ------------------------------------------------------

# Disable the file-too-small gate so tiny mock bodies are accepted.
ZERO_MIN_SIZE = {"min_image_size": 0, "min_video_size": 0}


def _mock_ok_download(downloader, body, head_cl, get_cl, get_encoding=None):
    """Wire a fake session whose HEAD and GET answer a tiny image without network."""
    session = MagicMock()
    head = MagicMock()
    head.headers = {"Content-Type": "image/jpeg", "Content-Length": head_cl}
    session.head.return_value = head
    get = MagicMock()
    get_headers = {"Content-Type": "image/jpeg", "Content-Length": get_cl}
    if get_encoding:
        get_headers["Content-Encoding"] = get_encoding
    get.headers = get_headers
    get.iter_content.return_value = [body]
    session.get.return_value = get
    downloader.session = session
    return session


# DL-1: escalation curl session lifecycle -----------------------------------

def test_escalation_session_closed_on_http_error():
    """DL-1: a failed escalation (HTTP >= 400) must close the curl session too."""
    d = MediaDownloader(url="http://example.com/1.jpg", filepath="/tmp/x.jpg",
                        settings={}, media_type="image")
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_session.get.return_value = mock_resp
    with patch("src.downloader.media_downloader.http_engine.create_escalation_session",
               return_value=mock_session):
        assert d._try_escalate_get({}, 30) is False
    mock_resp.close.assert_called_once()
    mock_session.close.assert_called_once()
    assert d._escalation_session is None
    assert d._escalated_response is None


def test_escalation_session_kept_open_on_success():
    """DL-1 positive: success keeps the session alive until the stream is consumed."""
    d = MediaDownloader(url="http://example.com/1.jpg", filepath="/tmp/x.jpg",
                        settings={}, media_type="image")
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.get.return_value = mock_resp
    with patch("src.downloader.media_downloader.http_engine.create_escalation_session",
               return_value=mock_session):
        assert d._try_escalate_get({}, 30) is True
    mock_session.close.assert_not_called()
    assert d._escalation_session is mock_session
    assert d._escalated_response is mock_resp
    d._close_escalation_session()
    mock_session.close.assert_called_once()
    assert d._escalation_session is None


def test_escalation_get_keeps_tls_verification():
    """DL-4 (updated by A-6 revision): the escalation GET keeps verification ON
    by default (curl_cffi's historical default), off only when the user
    explicitly sets verify_tls=False."""
    d = MediaDownloader(url="http://example.com/1.jpg", filepath="/tmp/x.jpg",
                        settings={}, media_type="image")
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.get.return_value = mock_resp
    with patch("src.downloader.media_downloader.http_engine.create_escalation_session",
               return_value=mock_session):
        assert d._try_escalate_get({}, 30) is True
    _, kwargs = mock_session.get.call_args
    assert kwargs["verify"] is True, "escalation keeps verify=True (no silent weakening)"
    d._close_escalation_session()


# DL-2: filename reservation / temp-path uniqueness -------------------------

def test_reserved_paths_give_distinct_targets():
    """DL-2: two in-flight downloads targeting the same path reserve distinct names."""
    from src.downloader import media_downloader as md_module
    d1 = MediaDownloader(url="http://a/1.jpg", filepath="/tmp/z.jpg", settings={}, media_type="image")
    d2 = MediaDownloader(url="http://b/1.jpg", filepath="/tmp/z.jpg", settings={}, media_type="image")
    try:
        p1 = d1._ensure_unique_filepath_at_destination("/tmp/z.jpg")
        p2 = d2._ensure_unique_filepath_at_destination("/tmp/z.jpg")
        assert p1 == "/tmp/z.jpg"
        assert p2 == os.path.join("/tmp", "z_1.jpg")  # os.path.join uses OS separators
    finally:
        md_module._reserved_paths.clear()


def test_concurrent_downloads_same_target_use_distinct_temp(tmp_path):
    """DL-2: parallel downloads to one target path must not share temp files."""
    from src.downloader import media_downloader as md_module
    target = str(tmp_path / "same.jpg")
    results = {}

    def _run(d):
        results[d] = d._do_download()

    d1 = MediaDownloader(url="http://a/1.jpg", filepath=target, settings=ZERO_MIN_SIZE, media_type="image")
    d2 = MediaDownloader(url="http://b/1.jpg", filepath=target, settings=ZERO_MIN_SIZE, media_type="image")
    _mock_ok_download(d1, body=b"1234567890", head_cl="10", get_cl="10")
    _mock_ok_download(d2, body=b"abcdefghij", head_cl="10", get_cl="10")
    t1 = threading.Thread(target=_run, args=(d1,))
    t2 = threading.Thread(target=_run, args=(d2,))
    t1.start(); t2.start(); t1.join(); t2.join()
    try:
        assert results[d1]["success"] is True, results[d1]
        assert results[d2]["success"] is True, results[d2]
        assert len(os.listdir(tmp_path)) == 2, os.listdir(tmp_path)
    finally:
        md_module._reserved_paths.clear()


# DL-3: gzip Content-Length is compressed size ------------------------------

def test_gzip_response_skips_size_mismatch(tmp_path):
    """DL-3: decompressed body must not be rejected as 'Size mismatch'."""
    d = MediaDownloader(url="http://example.com/g.jpg", filepath=str(tmp_path / "g.jpg"),
                        settings=ZERO_MIN_SIZE, media_type="image")
    body = b"x" * 5000
    _mock_ok_download(d, body=body, head_cl="100", get_cl="100", get_encoding="gzip")
    result = d._do_download()
    assert result["success"] is True, result
    assert os.path.getsize(str(tmp_path / "g.jpg")) == len(body)


# DL-13: malformed Content-Length -------------------------------------------

def test_garbage_content_length_treated_as_zero(tmp_path):
    """DL-13: a malformed Content-Length header must not raise ValueError."""
    d = MediaDownloader(url="http://example.com/h.jpg", filepath=str(tmp_path / "h.jpg"),
                        settings=ZERO_MIN_SIZE, media_type="image")
    body = b"1234567890"
    _mock_ok_download(d, body=body, head_cl="abc", get_cl="10")
    result = d._do_download()
    assert result["success"] is True, result


# DL-5: no junk .partial left behind ----------------------------------------

def test_partial_removed_on_stream_error(tmp_path):
    """DL-5: a mid-stream network error must not leave a .partial file behind."""
    d = MediaDownloader(url="http://example.com/i.jpg", filepath=str(tmp_path / "i.jpg"),
                        settings=ZERO_MIN_SIZE, media_type="image")
    _mock_ok_download(d, body=b"x", head_cl="5000", get_cl="5000")

    def broken_stream():
        yield b"x" * 100
        raise requests.exceptions.ConnectionError("connection reset")

    d.session.get.return_value.iter_content.return_value = broken_stream()
    result = d._do_download()
    assert result["success"] is False
    assert "Network error" in result["error"]
    assert [f for f in os.listdir(tmp_path) if f.endswith(".partial")] == []


def test_partial_removed_on_disk_error(tmp_path):
    """DL-5: a disk write error must clean up the .partial file."""
    d = MediaDownloader(url="http://example.com/j.jpg", filepath=str(tmp_path / "j.jpg"),
                        settings=ZERO_MIN_SIZE, media_type="image")
    _mock_ok_download(d, body=b"x" * 100, head_cl="100", get_cl="100")
    with patch("src.downloader.media_downloader.os.remove") as mock_remove, \
         patch("builtins.open") as mock_open:
        fh = MagicMock()
        fh.write.side_effect = OSError("disk full")
        mock_open.return_value.__enter__.return_value = fh
        result = d._do_download()
    assert result["success"] is False
    assert "Disk write error" in result["error"]
    mock_remove.assert_called_once()


# DL-12: MT chunk response lifecycle ----------------------------------------

def test_mt_chunk_closes_response_on_error(tmp_path):
    """DL-12: a failing MT chunk must close its HTTP response."""
    d = MediaDownloader(url="http://example.com/1.jpg", filepath=str(tmp_path / "x.jpg"),
                        settings=ZERO_MIN_SIZE, media_type="image")
    d.session = MagicMock()
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/jpeg"}

    def broken_stream():
        raise requests.exceptions.ConnectionError("boom")
        yield  # pragma: no cover

    resp.iter_content.return_value = broken_stream()
    d.session.get.return_value = resp
    progress_dict = {"total": 0, "success": True, "errors": []}
    lock = threading.Lock()
    d._download_chunk(0, 99, str(tmp_path / "c0.part"), 100, progress_dict, lock, 30)
    resp.close.assert_called_once()
    assert progress_dict["success"] is False
    assert progress_dict["errors"]


def test_verify_tls_flag_passed_to_session():
    """A-6: the verify_tls setting must reach the sync session calls.
    Default (False) keeps legacy behavior; True must propagate verify=True.
    """
    from src import constants as K
    from src.parser import http_engine

    # tls_verify returns the flag and warns once on unverified use
    assert http_engine.tls_verify({}) is False
    assert http_engine.tls_verify({K.SETTING_VERIFY_TLS: True}) is True

    # Escalation path keeps curl_cffi's verify=True default unless explicitly
    # disabled (legacy_default=False — A-6 revision: don't weaken TLS silently).
    d = MediaDownloader(url="http://example.com/1.jpg", filepath="/tmp/x.jpg",
                        settings={K.SETTING_VERIFY_TLS: True}, media_type="image")
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.get.return_value = mock_resp
    with patch("src.downloader.media_downloader.http_engine.create_escalation_session",
               return_value=mock_session):
        assert d._try_escalate_get({}, 30) is True
    _, kwargs = mock_session.get.call_args
    assert kwargs["verify"] is True

    d2 = MediaDownloader(url="http://example.com/1.jpg", filepath="/tmp/x.jpg",
                         settings={}, media_type="image")
    with patch("src.downloader.media_downloader.http_engine.create_escalation_session",
               return_value=mock_session):
        assert d2._try_escalate_get({}, 30) is True
    _, kwargs2 = mock_session.get.call_args
    assert kwargs2["verify"] is True, "escalation must keep verify=True by default"
    d2._close_escalation_session()

    # Explicitly disabled: setting wins.
    d3 = MediaDownloader(url="http://example.com/1.jpg", filepath="/tmp/x.jpg",
                         settings={K.SETTING_VERIFY_TLS: False}, media_type="image")
    with patch("src.downloader.media_downloader.http_engine.create_escalation_session",
               return_value=mock_session):
        assert d3._try_escalate_get({}, 30) is True
    _, kwargs3 = mock_session.get.call_args
    assert kwargs3["verify"] is False
    d3._close_escalation_session()


def test_mt_chunk_success_writes_full_file(tmp_path):
    """A-1 regression: the residual (< WRITE_BUFFER_SIZE) buffer must be flushed
    INSIDE the open-file context. Before the fix it was written after the
    with-block closed → ValueError → every MT download fell back to single-thread.
    3 x 600KB chunks guarantee a residual buffer at stream end (1.8MB total,
    buffer threshold is 1MB → two flushes + residual).
    """
    d = MediaDownloader(url="http://example.com/1.jpg", filepath=str(tmp_path / "x.jpg"),
                        settings=ZERO_MIN_SIZE, media_type="image")
    d.session = MagicMock()
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/jpeg", "Content-Length": str(3 * 600 * 1024)}
    chunk = b"x" * (600 * 1024)
    resp.iter_content.return_value = iter([chunk, chunk, chunk])
    d.session.get.return_value = resp

    progress_dict = {"total": 0, "success": True, "errors": []}
    lock = threading.Lock()
    part_path = tmp_path / "c0.part"
    d._download_chunk(0, 3 * 600 * 1024 - 1, str(part_path), 3 * 600 * 1024,
                      progress_dict, lock, 30)

    assert progress_dict["success"] is True, progress_dict["errors"]
    assert progress_dict["errors"] == []
    assert part_path.exists()
    assert part_path.stat().st_size == 3 * 600 * 1024


def test_downloader_filters_webpage_files():
    """Test that the downloader filters out webpage files"""
    # Create a downloader instance with a webpage URL
    downloader = MediaDownloader(
        url="https://example.com/page.html",
        filepath="/tmp/test.html",
        settings={},
        media_type="image"
    )
    
    # Mock the session to avoid actual network requests
    downloader.session = MagicMock()
    
    # Test the download method
    result = downloader.download()
    
    # Verify the result
    assert result["success"] is False
    assert "Non-media file based on URL extension" in result["error"]


def test_downloader_filters_js_files():
    """Test that the downloader filters out JavaScript files"""
    # Create a downloader instance with a JavaScript URL
    downloader = MediaDownloader(
        url="https://example.com/script.js",
        filepath="/tmp/script.js",
        settings={},
        media_type="image"
    )
    
    # Mock the session to avoid actual network requests
    downloader.session = MagicMock()
    
    # Test the download method
    result = downloader.download()
    
    # Verify the result
    assert result["success"] is False
    assert "Non-media file based on URL extension" in result["error"]


def test_downloader_filters_by_content_type():
    """Test that the downloader filters files by content type"""
    # Create a downloader instance with a generic URL
    downloader = MediaDownloader(
        url="https://example.com/content",  # No extension
        filepath="/tmp/content",
        settings={},
        media_type="image"
    )
    
    # Mock the session and head response
    mock_response = MagicMock()
    mock_response.headers = {
        "Content-Type": "text/html",
        "Content-Length": "1000",
    }
    downloader.session = MagicMock()
    downloader.session.head.return_value = mock_response
    
    # Test the download method
    result = downloader._do_download()  # Call internal method to test content-type check
    
    # Verify the result
    assert result["success"] is False
    assert "Webpage/script content" in result["error"]


# R-C-3: an error/login page must never be written under a media extension.

_H_TML = (b"<!DOCTYPE html><html><head><title>403 Forbidden</title></head>"
          b"<body><h1>Access denied</h1></body></html>")
_JPEG = b"\xff\xd8\xff\xe0" + b"J" * 1000


class _FakeHttpResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size=8192):
        yield self._body

    def close(self):
        pass


class _HeadRefusingSession:
    """HEAD answers `head_status`; GET streams `body` as `get_ct`. Range-aware."""

    def __init__(self, head_status, head_ct, body, get_ct, accept_ranges=False,
                 get_status=200):
        self.head_status, self.head_ct = head_status, head_ct
        self.body, self.get_ct = body, get_ct
        self.accept_ranges = accept_ranges
        self.get_status = get_status

    def head(self, url, **kwargs):
        headers = {"Content-Type": self.head_ct, "Content-Length": str(len(self.body))}
        if self.accept_ranges:
            headers["Accept-Ranges"] = "bytes"
        return _FakeHttpResponse(self.head_status, headers)

    def get(self, url, headers=None, **kwargs):
        rng = (headers or {}).get("Range")
        if rng:
            start, end = (int(x) for x in rng.replace("bytes=", "").split("-"))
            return _FakeHttpResponse(206, {"Content-Type": self.get_ct,
                                           "Content-Length": str(end - start + 1)},
                                     self.body[start:end + 1])
        return _FakeHttpResponse(self.get_status, {"Content-Type": self.get_ct,
                                                   "Content-Length": str(len(self.body))},
                                 self.body)

    def close(self):
        pass


def _images_on_disk(tmp_path):
    return [f for f in sorted(os.listdir(tmp_path)) if not f.endswith(".partial")]


def _run_download(tmp_path, session, settings=None, name="photo.jpg"):
    downloader = MediaDownloader(
        url="https://cdn.example.com/photo.jpg",
        filepath=str(tmp_path / name),
        settings=dict(ZERO_MIN_SIZE, **(settings or {})),
        media_type="image",
    )
    downloader.session = session
    return downloader.download()


def test_head_403_reads_get_content_type_and_refuses_html(tmp_path):
    """A 403 HEAD still leaves a response object; the GET body is what matters."""
    session = _HeadRefusingSession(403, "text/html", _H_TML, "text/html")
    result = _run_download(tmp_path, session)
    assert result["success"] is False
    assert "Webpage/script content" in result["error"]
    assert _images_on_disk(tmp_path) == []


def test_head_405_refuses_json_body(tmp_path):
    session = _HeadRefusingSession(405, "text/html", b'{"error":"forbidden"}',
                                   "application/json")
    result = _run_download(tmp_path, session)
    assert result["success"] is False
    assert "Webpage/script content" in result["error"]
    assert _images_on_disk(tmp_path) == []


def test_lying_head_still_refuses_html(tmp_path):
    """HEAD says image/jpeg, GET returns an HTML page that is big enough to pass."""
    body = b"<!DOCTYPE html><html><body>" + b"x" * 60000 + b"</body></html>"
    session = _HeadRefusingSession(200, "image/jpeg", body, "text/html")
    result = _run_download(tmp_path, session)
    assert result["success"] is False
    assert "Webpage/script content" in result["error"]
    assert _images_on_disk(tmp_path) == []


def test_head_refusing_host_still_downloads_real_image(tmp_path):
    """No functional loss: hosts that reject HEAD keep working."""
    session = _HeadRefusingSession(403, "text/html", _JPEG, "image/jpeg")
    result = _run_download(tmp_path, session)
    assert result["success"] is True, result
    files = _images_on_disk(tmp_path)
    assert files and open(tmp_path / files[0], "rb").read() == _JPEG


# R-C-5/R-C-6: failure kinds — only these may quarantine a host.

def test_failure_kind_http_4xx_is_not_penalising(tmp_path):
    from src import constants as K
    body = b"not found"
    session = _HeadRefusingSession(404, "text/html", body, "text/html", get_status=404)
    result = _run_download(tmp_path, session)
    assert result["success"] is False
    assert result["failure_kind"] == K.FAILURE_HTTP_4XX
    assert result["failure_kind"] not in K.FAILURE_PENALISING


def test_failure_kind_http_5xx_is_penalising(tmp_path):
    from src import constants as K
    session = _HeadRefusingSession(503, "image/jpeg", b"err", "image/jpeg", get_status=503)
    result = _run_download(tmp_path, session)
    assert result["failure_kind"] == K.FAILURE_HTTP_5XX
    assert result["failure_kind"] in K.FAILURE_PENALISING


def test_failure_kind_429_is_penalising(tmp_path):
    from src import constants as K
    session = _HeadRefusingSession(429, "image/jpeg", b"slow down", "image/jpeg", get_status=429)
    result = _run_download(tmp_path, session)
    assert result["failure_kind"] == K.FAILURE_HTTP_429
    assert result["failure_kind"] in K.FAILURE_PENALISING


def test_failure_kind_content_page_is_not_penalising(tmp_path):
    from src import constants as K
    session = _HeadRefusingSession(200, "image/jpeg", _H_TML, "text/html")
    result = _run_download(tmp_path, session)
    assert result["failure_kind"] == K.FAILURE_CONTENT_PAGE
    assert result["failure_kind"] not in K.FAILURE_PENALISING


def test_failure_kind_network_is_penalising(tmp_path):
    from src import constants as K
    session = MagicMock()
    session.head.side_effect = requests.exceptions.ConnectionError("boom")
    session.get.side_effect = requests.exceptions.ConnectionError("boom")
    result = _run_download(tmp_path, session)
    assert result["failure_kind"] == K.FAILURE_NETWORK
    assert result["failure_kind"] in K.FAILURE_PENALISING


def test_content_filters_carry_no_kind(tmp_path):
    """Fail-open: local/content-side failures are forgiven by absence of a kind."""
    from src import constants as K
    session = _HeadRefusingSession(200, "image/jpeg", _JPEG, "image/jpeg")
    result = _run_download(tmp_path, session, {K.SETTING_MIN_IMG_SIZE: 10 ** 6})
    assert result["success"] is False
    assert "failure_kind" not in result
    assert result.get("failure_kind") not in K.FAILURE_PENALISING


def test_multi_thread_path_rejects_html_chunks(tmp_path):
    """The MT path returns before the single-threaded GET guard — it must
    validate its own chunks instead of assembling an error page."""
    from src import constants as K
    body = b"<!DOCTYPE html><html><body>" + b"y" * 2_500_000 + b"</body></html>"
    session = _HeadRefusingSession(200, "image/jpeg", body, "text/html",
                                   accept_ranges=True)
    result = _run_download(tmp_path, session, {K.SETTING_THREADS_PER_FILE: 2},
                           name="big.jpg")
    assert result["success"] is False
    assert "Webpage/script content" in result["error"]
    files = _images_on_disk(tmp_path)
    assert files == [] or b"<html" not in open(tmp_path / files[0], "rb").read()[:400]

# F-14 (2026-10-15): the Stop path, progress reporting and the multi-thread
# entry point had no coverage in this file at all (VERIFY_2026-10-15 §3.5/F-14:
# `stop_event` / `progress_callback` / `_download_with_threads` — 0 mentions).
# Every assertion below was measured on this machine before it was written; the
# `*.partial` residue under Stop is stage-2 work (FIXPLAN_2026-10-15, Этап 2).


def _stopped_event():
    """A threading.Event already set — i.e. the user pressed Stop."""
    ev = threading.Event()
    ev.set()
    return ev


def _make_downloader(tmp_path, session, stop=None, threads=1, cb=None,
                     name="photo.jpg", body_len=None):
    from src import constants as K

    settings = dict(ZERO_MIN_SIZE, **{K.SETTING_THREADS_PER_FILE: threads})
    downloader = MediaDownloader(
        url="https://cdn.example.com/photo.jpg",
        filepath=str(tmp_path / name),
        settings=settings,
        media_type="image",
        stop_event=stop,
    )
    downloader.session = session
    downloader.progress_callback = cb
    return downloader


def test_stop_before_attempt_issues_no_request(tmp_path):
    """Stop pressed while the file is still queued: no HEAD, no GET, no file."""
    session = MagicMock()
    downloader = _make_downloader(tmp_path, session, stop=_stopped_event())
    result = downloader.download()

    assert result["success"] is False
    assert result["error"] == "Download manually aborted"
    session.head.assert_not_called()
    session.get.assert_not_called()
    assert os.listdir(tmp_path) == []


def test_stop_mid_stream_does_not_publish_a_media_file(tmp_path):
    """Stop while bytes are flowing: abort, nothing under the media name.

    Measured: this path leaves a `*.partial` behind — the Stop branch calls
    os.remove() while its own file object is still open, which Windows refuses
    (the OSError is swallowed). That residue is fixed in Этап 2, so this test
    asserts only the contract that holds today: no file is published under the
    destination name and an operator Stop is not a host fault.
    """
    body = _JPEG + b"Z" * (4096 * 8)
    stop = threading.Event()

    class _StoppingSession(_HeadRefusingSession):
        def get(self, url, headers=None, **kwargs):
            stop.set()  # the operator hits Stop once the stream is open
            return super().get(url, headers=headers, **kwargs)

    downloader = _make_downloader(tmp_path, _StoppingSession(200, "image/jpeg", body,
                                                             "image/jpeg"),
                                  stop=stop)
    result = downloader.download()

    assert result["success"] is False
    assert result["error"] == "Download manually aborted"
    assert "failure_kind" not in result, "an operator Stop must not penalise the host"
    assert _images_on_disk(tmp_path) == []


def test_stop_during_multi_thread_chunks_aborts_both_phases(tmp_path):
    """Stop pressed while ranged chunk requests are streaming.

    Measured sequence: both chunk threads report "Download manually aborted" and
    the MT attempt fails, the downloader falls back to single-threaded — where
    the Stop check fires again and produces the final result. The fallback GET
    still goes out (Stop is checked per attempt, not per phase); Этап 2 narrows
    that. Asserted here: the abort result, no published file, no host penalty,
    and that the MT phase really ran (two range requests).
    """
    body = _JPEG + b"M" * 2_500_000
    stop = threading.Event()
    range_requests = []

    class _RangeCountingSession(_HeadRefusingSession):
        def get(self, url, headers=None, **kwargs):
            rng = (headers or {}).get("Range")
            if rng:
                range_requests.append(rng)
                stop.set()  # Stop lands mid-stream, not before the attempt
            return super().get(url, headers=headers, **kwargs)

    session = _RangeCountingSession(200, "image/jpeg", body, "image/jpeg",
                                    accept_ranges=True)
    downloader = _make_downloader(tmp_path, session, stop=stop, threads=2,
                                  name="big.jpg")
    result = downloader.download()

    assert len(range_requests) == 2, f"multi-thread phase did not run: {range_requests}"
    assert result["success"] is False
    assert result["error"] == "Download manually aborted"
    assert "failure_kind" not in result
    assert _images_on_disk(tmp_path) == []
    assert not os.path.exists(str(tmp_path / "big.jpg"))
    assert not any(f.endswith(".part0") or f.endswith(".part1")
                   for f in os.listdir(tmp_path)), os.listdir(tmp_path)


def test_download_with_threads_refuses_below_min_part_size(tmp_path):
    """_download_with_threads returns early when the size yields < 2 parts."""
    downloader = _make_downloader(tmp_path, MagicMock())
    result = downloader._download_with_threads(1024)
    assert result["success"] is False
    assert "Not enough parts" in result["error"]


def test_progress_callback_single_thread_ends_at_100(tmp_path):
    values = []
    downloader = _make_downloader(tmp_path, _HeadRefusingSession(200, "image/jpeg",
                                                                 _JPEG, "image/jpeg"),
                                  cb=values.append)
    result = downloader.download()

    assert result["success"] is True, result
    assert values[-1] == 100, f"progress never reached 100%: {values}"
    assert all(isinstance(v, int) and 0 <= v <= 100 for v in values), values
    assert values == sorted(values), f"progress went backwards: {values}"


def test_progress_callback_multi_thread_reports_intermediate_values(tmp_path):
    """The MT path must report intermediate progress, not only the final 100.

    Measured with 2 threads: [50, 100, 100] — one update per chunk plus the
    completion callback. The point of the assertion is the intermediate value:
    a bar that only ever jumps 0 → 100 makes the progress column useless.
    """
    body = _JPEG + b"M" * 2_500_000
    values = []
    downloader = _make_downloader(tmp_path, _HeadRefusingSession(200, "image/jpeg",
                                                                 body, "image/jpeg",
                                                                 accept_ranges=True),
                                  threads=2, cb=values.append, name="big.jpg")
    result = downloader.download()

    assert result["success"] is True, result
    assert values[-1] == 100, values
    assert min(values) < 100, f"no intermediate progress reported: {values}"
    assert values == sorted(values), f"progress went backwards: {values}"
    assert all(0 <= v <= 100 for v in values), values
