#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fix for brotli support in aiohttp
"""

import importlib.util
import logging

logger = logging.getLogger(__name__)

class BrotliSupportFix:
    @staticmethod
    def patch():
        """Report whether aiohttp can actually decode Brotli responses.

        DL-6: aiohttp 3.13+ builds its own Accept-Encoding header
        (_gen_default_accept_encoding) announcing exactly the encodings it can
        decode — "br" is added only when the `brotli` module is importable. The
        old code force-appended ", br" to aiohttp's defaults when brotli was
        missing, which made servers send br payloads that aiohttp then failed to
        decode (ContentEncodingError on every such page). Never mutate aiohttp;
        just report real capability.
        """
        if importlib.util.find_spec("aiohttp") is None:
            logger.warning("aiohttp is not installed, skipping brotli setup")
            return False
        if importlib.util.find_spec("brotli") is None:
            logger.info("brotli module not installed - 'br' is NOT announced (avoids undecodable br responses).")
            return False
        logger.info("brotli module available - aiohttp announces/decodes 'br' itself.")
        return True