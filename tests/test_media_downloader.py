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
    """DL-4: the escalation GET must not disable TLS verification."""
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
    assert "verify" not in kwargs, "escalation GET must not pass verify=False"
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