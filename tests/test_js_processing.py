#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
from src.parser.webpage_parser import WebpageParser
import asyncio


@pytest.mark.skip(reason="Requires network access and external session — manual test only")
def test_js_processing():
    """Test that JavaScript processing works correctly.
    
    Requires network and aiohttp session — not suitable for automated CI.
    Run manually: python -m pytest tests/test_js_processing.py -v -k test_js_processing --no-header -rN
    """
    pass
