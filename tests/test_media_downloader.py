#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test the MediaDownloader class
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.downloader.media_downloader import MediaDownloader


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
    assert "Webpage files should not be downloaded" in result["error"]


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
    assert "Webpage files should not be downloaded" in result["error"]


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
    assert "Webpage/script content should not be downloaded" in result["error"]