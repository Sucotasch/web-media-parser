#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scrapling adapter for advanced JS rendering and anti-bot bypass.
Compatible with scrapling >= 0.4.x API.
"""

import logging
import asyncio
import os
import re
import tempfile
from typing import Dict, Any, Tuple, List, Optional
from urllib.parse import urlparse

try:
    from scrapling.fetchers import AsyncStealthySession
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from src import constants as K
from src.parser.webpage_parser import WebpageParser

logger = logging.getLogger(__name__)

# Sentinel returned when Scrapling times out — caller should fall back to static parser
SCRAPLING_TIMEOUT_FALLBACK = "SCRAPLING_TIMEOUT_FALLBACK"

# No longer use hardcoded profile locks, we use cookie passing in memory instead.


class ScraplingWebpageParser:
    """
    Adapter class for Scrapling to integrate with ParserManager.
    Uses a persistent browser profile to share cookies across all page loads.
    """

    def __init__(self, url: str, settings: Dict[str, Any], use_stealth: bool = False,
                 scrapling_cookies: Dict[str, List[Dict[str, Any]]] = None,
        shared_browser=None,
        pattern_manager=None,
        media_type_hint="image"
    ):
        self.url = url
        self.settings = settings
        self.use_stealth = use_stealth
        self._scrapling_cookies = scrapling_cookies if scrapling_cookies is not None else {}
        self.shared_browser = shared_browser
        self.pattern_manager = pattern_manager
        self.media_type_hint = media_type_hint

        self.links: Dict[str, Dict[str, Any]] = {}
        self.media_files: List[Tuple[str, str, Dict[str, Any]]] = []

    async def parse(self) -> Tuple[
        Dict[str, Dict[str, Any]],
        List[Tuple[str, str, Dict[str, Any]]],
        Optional[str],
        str,
        Optional[int]
    ]:
        """
        Parse the webpage using Scrapling and extract media files.

        Returns:
            Tuple of (links, media_files, error_status, error_message, http_status_code)
        """
        timeout_sec = self.settings.get(K.SETTING_PAGE_TIMEOUT, K.DEFAULT_PAGE_TIMEOUT)
        timeout_sec = min(15, timeout_sec)
        timeout_ms = int(timeout_sec * 1000)

        # When debug_show_browser=True in settings, browser window is visible for inspection
        debug_headless = not self.settings.get("debug_show_browser", False)

        # When debug_show_browser=True in settings, browser window is visible for inspection
        debug_headless = not self.settings.get("debug_show_browser", False)

        async def bypass_interstitials(page):
            """
            4-stage page_action (runs after page load):
              1. Click interstitial (I Agree / 18+ / Confirm popups) — searches main frame AND all iframes
              2. Scroll down to trigger Lazy Load / Infinite Scroll
              3. Remove invisible Honeypot links via JS
            """
            consent_keywords = ["agree", "confirm", "yes", "accept", "continue",
                                 "enter", "согласен", "да", "продолжить", "18+",
                                 "verify", "proceed"]

            patterns = [
                '.confirm-button', '#yes-button', '.adult-confirm', '.agree-button',
                '[data-action="agree"]', '[name="agree"]', '[id*="confirm"]', '[class*="adult"]'
            ]

            # FIX #1: Give the JS on the page time to actually load and render the iframe/button
            # Scrapling page_action runs immediately after start; without this sleep, query_selectors fail.
            try:
                await asyncio.sleep(2.5)
            except asyncio.CancelledError:
                raise

            clicked = False

            # Search in ALL frames (main + iframes) — overlays are often inside iframes
            frames_to_search = [page] + list(page.frames)

            for frame in frames_to_search:
                if clicked:
                    break
                # Stage 1a: Selector-based search
                try:
                    for selector in patterns:
                        try:
                            btn = await frame.query_selector(selector)
                            if btn and await btn.is_visible():
                                logger.info(f"Bypassing interstitial [{frame.url}] selector: {selector}")
                                await btn.click(force=True)
                                clicked = True
                                try:
                                    await page.wait_for_load_state("networkidle", timeout=5000)
                                except Exception:
                                    pass # Ignore networkidle timeouts, the click registered
                                break
                        except Exception:
                            continue
                except Exception as e:
                    logger.debug(f"Frame selector search error: {e}")

                # Stage 1b: Broad text scan fallback for this frame
                if not clicked:
                    try:
                        all_buttons = await frame.query_selector_all(
                            "button, a[role='button'], [type='button'], [type='submit'], input[type='button'], div[role='button']"
                        )
                        for btn in all_buttons:
                            try:
                                if not await btn.is_visible():
                                    continue
                                text = (await btn.inner_text()).strip().lower()
                                
                                has_consent = False
                                for kw in consent_keywords:
                                    if kw == "18+":
                                        if "18+" in text:
                                            has_consent = True
                                            break
                                        continue
                                    
                                    # Use regex boundaries to safely match whole words only.
                                    # e.g., \bда\b matches "Да," and "Да, мне 18" but NOT "создать"
                                    if re.search(r'\b' + re.escape(kw) + r'\b', text, flags=re.IGNORECASE):
                                        has_consent = True
                                        break
                                
                                if has_consent:
                                    logger.info(f"Bypassing interstitial via text scan in [{frame.url}]: '{text}'")
                                    await btn.click(force=True)
                                    clicked = True
                                    try:
                                        await page.wait_for_load_state("networkidle", timeout=5000)
                                    except Exception:
                                        pass
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        logger.debug(f"Broad button scan error in frame: {e}")

            # NEW Stage: Nuclear JS Overlay removal
            try:
                await page.evaluate("""
                    document.querySelectorAll('.overlay, .consent, .modal, #cookie-banner, .popup').forEach(el => el.remove());
                    document.body.style.overflow = 'auto';
                """)
            except Exception as e:
                pass

            # Stage 2: Scroll to trigger lazy-load scripts
            # Loop 2 times to catch lazy-loaded content or dynamic galleries
            for _ in range(2):
                try:
                    await page.mouse.wheel(delta_x=0, delta_y=2000)
                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.debug(f"Scroll trigger error: {e}")

            # Specific wait for video DOM element if hinted
            if self.media_type_hint == "video":
                try:
                    logger.info(f"Waiting specifically for video elements on {self.url}...")
                    # Give it up to 5 seconds to load the video element
                    await page.wait_for_selector('video, source, iframe', timeout=5000)
                    await asyncio.sleep(1.0) # Let the player initialize
                except Exception:
                    pass

            # Stage 3: Remove invisible honeypot links via JS
            try:
                await page.evaluate("""
                    document.querySelectorAll('a').forEach(a => {
                        const s = window.getComputedStyle(a);
                        if (s.display === 'none' || s.visibility === 'hidden' ||
                            s.opacity === '0' || parseInt(s.width) === 0) {
                            a.remove();
                        }
                    });
                """)
            except Exception as e:
                logger.debug(f"Honeypot removal error: {e}")

        html_content = ""
        try:
            proxy_server = self.settings.get(K.SETTING_PROXY, "")
            scrapling_proxy = f"http://{proxy_server}" if proxy_server else None
            
            domain = urlparse(self.url).netloc
            if self.use_stealth and HAS_STEALTH:
                logger.debug(f"Using Scrapling AsyncStealthySession for {self.url}")
                # For stealth, we rely on Scrapling's internal context handling
                async with AsyncStealthySession(
                    headless=debug_headless,
                    solve_cloudflare=True,
                    allow_webgl=True,
                    useragent=K.DEFAULT_USER_AGENT,
                    timeout=timeout_ms,
                    proxy=scrapling_proxy
                ) as session:
                    response = await session.fetch(
                        self.url,
                        page_action=bypass_interstitials,
                        wait_until="commit"
                    )
                    html_content = str(response.html_content) if hasattr(response, "html_content") else ""
            elif self.use_stealth and not HAS_STEALTH:
                logger.warning("Stealth requested but AsyncStealthySession not available. Falling back to standard browser.")
                # Fall through to standard Playwright logic
                # We need to set use_stealth to False for the logic below to handle it
                self.use_stealth = False
            
            if not html_content:
                logger.debug(f"Using Playwright Ephemeral Context for {self.url}")
                
                async def _run_in_context(browser):
                    context = await browser.new_context(
                        user_agent=K.DEFAULT_USER_AGENT
                    )
                    try:
                        # Restore cookies if we have them for this domain
                        domain_cookies = self._scrapling_cookies.get(domain, [])
                        if domain_cookies:
                            await context.add_cookies(domain_cookies)
                            
                        page = await context.new_page()
                        page.set_default_timeout(timeout_ms)
                        response = await page.goto(self.url, wait_until="commit", timeout=timeout_ms)
                        await bypass_interstitials(page)
                        
                        # Save new cookies
                        cookies = await context.cookies()
                        if cookies:
                            # Update reference in place so ParserManager sees it
                            self._scrapling_cookies[domain] = cookies
                            
                        return await page.content()
                    finally:
                        await context.close()

                if self.shared_browser:
                    html_content = await _run_in_context(self.shared_browser)
                else:
                    playwright_proxy = {"server": scrapling_proxy} if scrapling_proxy else None
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(
                            headless=debug_headless,
                            args=["--disable-gpu", "--no-sandbox"],
                            proxy=playwright_proxy
                        )
                        html_content = await _run_in_context(browser)
                        await browser.close()

            if not html_content:
                return {}, [], K.PARSER_UNKNOWN_ERROR, "Browser returned empty content", None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_str = str(e)
            error_msg = f"Browser fetch error for {self.url}: {error_str}"

            if "executable doesn't exist" in error_str.lower() or "playwright install" in error_str.lower():
                logger.warning(f"Browser binary issue detected for {self.url}: {error_str}")
                return {}, [], "BROWSER_UNAVAILABLE", error_msg, None

            if "target" in error_str.lower() and "closed" in error_str.lower():
                logger.warning(f"Browser target closed for {self.url} — returning fallback.")
                return {}, [], SCRAPLING_TIMEOUT_FALLBACK, error_msg, None

            if "timeout" in error_str.lower() and "exceeded" in error_str.lower():
                logger.warning(f"Browser timed out for {self.url} ({timeout_sec}s) — returning fallback.")
                await asyncio.sleep(0.5)
                return {}, [], SCRAPLING_TIMEOUT_FALLBACK, error_msg, None

            if "profile" in error_str.lower() and "in use" in error_str.lower():
                logger.warning(f"Browser profile conflict for {self.url}, retrying without profile persistence...")
                try:
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(
                            headless=debug_headless,
                            args=["--disable-gpu", "--no-sandbox"]
                        )
                        context = await browser.new_context(user_agent=K.DEFAULT_USER_AGENT)
                        try:
                            page = await context.new_page()
                            page.set_default_timeout(timeout_ms)
                            await page.goto(self.url, wait_until="networkidle", timeout=timeout_ms)
                            await bypass_interstitials(page)
                            html_content = await page.content()
                        finally:
                            await browser.close()
                except Exception as e2:
                    return {}, [], K.PARSER_NETWORK_ERROR, f"Browser fallback failed: {e2}", None
                if not html_content:
                    return {}, [], K.PARSER_UNKNOWN_ERROR, "Browser fallback empty content", None
            else:
                logger.error(f"Unexpected Browser error for {self.url}: {error_str}", exc_info=True)
                return {}, [], K.PARSER_NETWORK_ERROR, error_msg, None

        # Reuse WebpageParser extraction logic on the rendered DOM
        try:
            dummy_parser = WebpageParser(
                url=self.url,
                settings=self.settings,
                process_js=True,
                external_session=None,
                pattern_manager=self.pattern_manager
            )

            soup = BeautifulSoup(html_content, "html.parser")
            await dummy_parser._extract_images(soup)
            await dummy_parser._extract_videos(soup)
            await dummy_parser._extract_links(soup)
            await dummy_parser._handle_dynamic_content(soup)

            self.links = dummy_parser.links
            self.media_files = dummy_parser.media_files

            return (
                self.links,
                self.media_files,
                K.PARSER_SUCCESS,
                "Successfully parsed with Scrapling.",
                200,
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_msg = f"Scrapling extraction error for {self.url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {}, [], K.PARSER_UNKNOWN_ERROR, error_msg, None
