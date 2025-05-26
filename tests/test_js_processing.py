#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
from src.parser.webpage_parser import WebpageParser
import asyncio


@pytest.mark.asyncio
async def test_js_processing():
    """Test that JavaScript processing works correctly"""
    # Test URL with JavaScript content - using a site known to require JS
    test_url = "https://github.com"  # GitHub uses JS extensively
    settings = {
        "process_js": True,
        "page_timeout": 60,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36",
    }

    parser = WebpageParser(test_url, settings, process_js=True)
    links, media_files = await parser.parse()

    # If JS processing works, we should find multiple images and links
    assert len(links) > 0, "No links found - JS processing may have failed"
    assert len(media_files) > 0, "No media files found - JS processing may have failed"

    # Print results for inspection
    print(f"Found {len(links)} links and {len(media_files)} media files")
    print("\nSample media files:")
    for media_type, url, attrs in media_files[:5]:
        print(f"{media_type}: {url}")


if __name__ == "__main__":
    asyncio.run(test_js_processing())
