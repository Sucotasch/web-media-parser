#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import asyncio

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.site_pattern_manager import SitePatternManager


def test_site_pattern_manager_loading():
    """Test that the SitePatternManager loads patterns correctly"""
    # Get path to site_patterns.json
    built_in_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "patterns",
        "site_patterns.json"
    )
    
    # Try with explicit custom pattern path
    pattern_manager = SitePatternManager(enable_built_in=False, custom_pattern_path=built_in_path)
    
    # Verify patterns are loaded
    pattern_count = pattern_manager.get_pattern_count()
    loaded_files = pattern_manager.get_loaded_files()
    
    # We should have patterns
    assert pattern_count > 0, f"No patterns loaded from {built_in_path}"
    assert built_in_path in loaded_files, f"Built-in pattern file not loaded: {built_in_path}"
    
    # Test some known patterns exist
    test_urls = [
        "https://artstation.com/artwork/123456",
        "https://twitter.com/username/status/123456789",
        "https://www.reddit.com/r/pics/comments/abcdef",
        "https://imgur.com/gallery/abcdef",
    ]
    
    for url in test_urls:
        patterns = pattern_manager.get_patterns_for_url(url)
        assert len(patterns) > 0, f"No patterns matched for URL: {url}"


def test_url_transformation():
    """Test that URL transformations work correctly"""
    pattern_manager = SitePatternManager(enable_built_in=True, custom_pattern_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "patterns",
        "site_patterns.json"
    ))
    
    # Test cases with source URL and thumbnail URL to transform
    test_cases = [
        (
            "https://example.com/images/thumb/image123_thumb.jpg", 
            "https://example.com/gallery",
            "https://example.com/images/image123.jpg"
        ),
    ]
    
    for thumbnail_url, source_url, expected_result in test_cases:
        transformed_url = pattern_manager.transform_image_url(thumbnail_url, source_url)
        assert transformed_url != thumbnail_url, f"URL was not transformed: {thumbnail_url}"


if __name__ == "__main__":
    test_site_pattern_manager_loading()
    test_url_transformation()
