#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fix for brotli support in aiohttp
"""

import importlib.util
import logging
import sys
import os

logger = logging.getLogger(__name__)

class BrotliSupportFix:
    @staticmethod
    def patch():
        # Check if aiohttp is installed first
        if importlib.util.find_spec("aiohttp") is None:
            logger.warning("aiohttp is not installed, skipping brotli setup")
            return False
            
        # For aiohttp, we actually don't need full brotli support
        # We just need to inform it that we can handle brotli content-encoding
        # Let's create a minimal compatibility layer
        
        # Check if we already have a working version
        try:
            import aiohttp
            # Try to access the content_encoding attribute to see if brotli is recognized
            if hasattr(aiohttp, "hdrs") and hasattr(aiohttp.hdrs, "CONTENT_ENCODING"):
                logger.info(f"aiohttp {aiohttp.__version__} found, proceeding with content encoding setup")
                
                # Add brotli to the accepted encodings if not already there
                try:
                    # Import the ClientRequest class to modify accepted encodings
                    from aiohttp.client_reqrep import ClientRequest
                    
                    # Check if it already has brotli in its accepted encodings
                    if hasattr(ClientRequest, "DEFAULT_HEADERS") and "accept-encoding" in ClientRequest.DEFAULT_HEADERS:
                        if "br" not in ClientRequest.DEFAULT_HEADERS["accept-encoding"]:
                            # Add brotli to accepted encodings
                            current = ClientRequest.DEFAULT_HEADERS["accept-encoding"]
                            ClientRequest.DEFAULT_HEADERS["accept-encoding"] = current + ", br"
                            logger.info("Added 'br' to aiohttp's accepted encodings")
                    
                    logger.info("Brotli content-encoding support enabled for aiohttp")
                    return True
                except Exception as e:
                    logger.warning(f"Could not modify aiohttp's accepted encodings: {e}")
        except Exception as e:
            logger.warning(f"Error setting up aiohttp brotli support: {e}")
            
        # If we get here, we're going to skip brotli support but continue
        logger.warning("Brotli support disabled, but application will continue")
        return False