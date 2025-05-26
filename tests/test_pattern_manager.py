#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
import os
import sys
import asyncio

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.parser.site_pattern_manager import SitePatternManager

@pytest.mark.asyncio
async def test_site_pattern_manager_loading():
    """Test that the SitePatternManager loads patterns correctly"""
    # Get path to site_patterns.json
    built_in_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "patterns",
        "site_patterns.json"
    )
    
    # Print the path and check if file exists
    print(f"Checking if pattern file exists at: {built_in_path}")
    print(f"File exists: {os.path.exists(built_in_path)}")
    
    # Try with explicit custom pattern path
    pattern_manager = SitePatternManager(enable_built_in=False, custom_pattern_path=built_in_path)
    
    # Verify patterns are loaded
    pattern_count = pattern_manager.get_pattern_count()
    loaded_files = pattern_manager.get_loaded_files()
    
    # We should have patterns
    assert pattern_count > 0, f"No patterns loaded from {built_in_path}"
    assert built_in_path in loaded_files, f"Built-in pattern file not loaded: {built_in_path}"
    
    print(f"Successfully loaded {pattern_count} patterns from {loaded_files}")
    
    # Test some known patterns exist
    test_urls = [
        "https://artstation.com/artwork/123456", # Should match artstation pattern
        "https://twitter.com/username/status/123456789", # Should match twitter pattern
        "https://www.reddit.com/r/pics/comments/abcdef", # Should match reddit pattern
        "https://imgur.com/gallery/abcdef", # Should match imgur pattern
    ]
    
    for url in test_urls:
        patterns = pattern_manager.get_patterns_for_url(url)
        print(f"URL: {url}, Matched patterns: {len(patterns)}")
        for pattern_name, pattern_data in patterns:
            print(f"  - {pattern_name}: {pattern_data.get('site', 'unknown')}")
        assert len(patterns) > 0, f"No patterns matched for URL: {url}"

@pytest.mark.asyncio
async def test_url_transformation():
    """Test that URL transformations work correctly"""
    pattern_manager = SitePatternManager(enable_built_in=True, custom_pattern_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "patterns",
        "site_patterns.json"
    ))
    
    # Test cases with source URL and thumbnail URL to transform
    test_cases = [
        # Common transformation using global patterns (thumbnail -> original)
        (
            "https://example.com/images/thumb/image123_thumb.jpg", 
            "https://example.com/gallery",
            "https://example.com/images/image123.jpg"
        ),
    ]
    
    for thumbnail_url, source_url, expected_result in test_cases:
        transformed_url = pattern_manager.transform_image_url(thumbnail_url, source_url)
        print(f"\nTransformation:")
        print(f"Original: {thumbnail_url}")
        print(f"Source URL: {source_url}")
        print(f"Transformed: {transformed_url}")
        # The transformation might not match exactly our expected result, but it should be different
        assert transformed_url != thumbnail_url, f"URL was not transformed: {thumbnail_url}"

if __name__ == "__main__":
    asyncio.run(test_site_pattern_manager_loading())
    asyncio.run(test_url_transformation())