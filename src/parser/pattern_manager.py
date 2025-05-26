#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This file has been deprecated and is kept only for compatibility.
Please use site_pattern_manager.py instead.
"""

import logging
from src.parser.site_pattern_manager import SitePatternManager

logger = logging.getLogger(__name__)

# For backward compatibility
PatternManager = SitePatternManager