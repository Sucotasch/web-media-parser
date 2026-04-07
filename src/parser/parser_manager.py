#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced parser manager that coordinates parsing and downloading process
"""

import os
import re
import time
# import queue # Not used directly, asyncio.Queue is used
import pickle
import asyncio
import logging
import hashlib
from asyncio import Lock, Semaphore

import aiofiles
import threading
import traceback
from typing import Dict, Any, Set, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

from src.parser.scrapling_adapter import ScraplingWebpageParser, SCRAPLING_TIMEOUT_FALLBACK

from PySide6.QtCore import QObject, Signal
# from bs4 import BeautifulSoup # Not used directly in ParserManager

from src.parser.webpage_parser import WebpageParser
from src.parser.json_parser import JSONWebpageParser
from src.parser.priority_url_queue import PriorityURLQueue
from src.parser.site_pattern_manager import SitePatternManager
from src.downloader.media_downloader import MediaDownloader
from src.parser.utils import (
    # is_valid_url, # Not used
    get_domain,
    is_media_url,
    is_webpage_url,
    is_same_domain,
    normalize_url,
    # is_image_url, # Not used directly
    # is_video_url # Not used directly
)
from src.parser.shared_session import AsyncClientManager
from src import constants as K # Import constants

logger = logging.getLogger(__name__)

PARSER_DONE_SENTINEL = None


class ParserManager(QObject):
    """Enhanced parser manager with async support"""

    total_progress_updated = Signal(int)
    current_progress_updated = Signal(int)
    parsing_finished = Signal()
    status_updated = Signal(str)

    def __init__(
        self, url: str, download_path: str, settings: Dict[str, Any], log_handler
    ):
        super().__init__()
        self.start_url = url
        self.download_path = download_path 
        self.settings = settings 
        self.log = log_handler

        self.is_running = False
        self.is_paused = False
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self.url_queue = PriorityURLQueue(self.settings)
        self.download_queue = asyncio.Queue()

        self.max_depth = self.settings.get(K.SETTING_SEARCH_DEPTH, K.DEFAULT_SEARCH_DEPTH)
        
        self.domain_health = {}
        self.quarantined_domains = set()
        self.quarantine_queue = asyncio.Queue() # Max size can be a constant if needed
        
        self.pattern_manager = None
        if self.settings.get(K.SETTING_USE_PATTERNS, K.DEFAULT_USE_PATTERNS):
            custom_pattern_path = self.settings.get(K.SETTING_CUSTOM_PATTERN_PATH, K.DEFAULT_SETTINGS_VALUES[K.SETTING_CUSTOM_PATTERN_PATH])
            self.pattern_manager = SitePatternManager(
                enable_built_in=True, custom_pattern_path=custom_pattern_path
            )
            logger.info("Using SitePatternManager for pattern transformations")

        self.url_queue = PriorityURLQueue()
        self.download_queue = asyncio.Queue()
        self._url_lock = asyncio.Lock()
        self.processed_urls = set()
        self.downloaded_files = set() 
        
        # Limit concurrent browser instances to avoid RAM exhaustion
        max_browsers = self.settings.get(K.SETTING_MAX_BROWSER_INSTANCES, 2)
        self._browser_semaphore = asyncio.Semaphore(max_browsers)
        
        self.async_client_manager = AsyncClientManager(self.settings)
        self.session = None

        self.stats = {
            "pages_processed": 0, "images_found": 0, "videos_found": 0,
            "files_downloaded": 0, "files_skipped": 0,
        }
        
        self.async_client_manager: AsyncClientManager = AsyncClientManager(self.settings)
        
        self.parser_tasks = []
        self.downloader_tasks = []
        self.blocked_domains: Set[str] = self._load_domain_blocklist()
        # Handle to the top-level asyncio task — used for cancellation on restart
        self._main_asyncio_task: Optional[asyncio.Task] = None
        # Track how many download jobs are currently in-flight in run_in_executor
        # Parser workers must NOT exit while this is > 0, as migrations may re-populate url_queue
        self._active_downloads: int = 0
        # All currently active MediaDownloader instances (for graceful abort on stop)
        self._active_downloader_sessions: List[Any] = []
        # Shared cookie store for Scrapling: {domain: [cookie_dicts]}
        # Populated after first I-Agree click, injected into subsequent sessions for same domain
        self._scrapling_domain_cookies: Dict[str, list] = {}

    def _load_domain_blocklist(self, blocklist_file_name: str = K.DOMAIN_BLOCKLIST_FILENAME) -> Set[str]:
        blocked_domains: Set[str] = set()
        # Path relative to the current script directory
        script_dir_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), blocklist_file_name)
        
        # Path as provided (could be relative to CWD or absolute)
        provided_path = blocklist_file_name

        path_to_load = None
        if os.path.exists(script_dir_path):
            path_to_load = script_dir_path
        elif os.path.exists(provided_path):
            path_to_load = provided_path
        
        if path_to_load:
            try:
                with open(path_to_load, "r", encoding="utf-8") as f:
                    for line in f:
                        domain = line.strip()
                        if domain and not domain.startswith("#"):
                            blocked_domains.add(domain)
                logger.info(f"Loaded {len(blocked_domains)} domains into the blocklist from {path_to_load}.")
            except Exception as e:
                logger.error(f"Error loading domain blocklist from {path_to_load}: {e}", exc_info=True)
        else:
            logger.debug(
                f"Domain blocklist file not found. Tried: '{script_dir_path}' and '{provided_path}'. "
                "Proceeding with an empty blocklist."
            )
        return blocked_domains

    async def _update_queue_priorities(
        self, url: str, media_files: List[Tuple[str, str, Dict[str, Any]]]
    ):
        media_count = len(media_files)
        if media_count > 0:
            self.url_queue.update_domain_score(url, media_count)
            self.url_queue.update_url_pattern(url, True)
        else:
            self.url_queue.update_url_pattern(url, False)

    def _reset_session_state(self) -> None:
        """
        Full reset of all per-run mutable state.
        MUST be called at the start of every new parsing session to avoid
        leftover data from a previous run causing queue conflicts or duplicate skips.
        """
        self.url_queue = PriorityURLQueue()
        self.download_queue = asyncio.Queue()
        self._url_lock = asyncio.Lock()
        self.processed_urls = set()
        self.downloaded_files = set()
        self.domain_health = {}
        self.quarantined_domains = set()
        self.quarantine_queue = asyncio.Queue()
        self.parser_tasks = []
        self.downloader_tasks = []
        self._active_downloads = 0
        self._active_downloader_sessions = []
        self._scrapling_domain_cookies = {}
        self._main_asyncio_task = None
        self.is_paused = False
        self._pause_event.set()  # Ensure not stuck in paused state
        self.stats = {
            "pages_processed": 0, "images_found": 0, "videos_found": 0,
            "files_downloaded": 0, "files_skipped": 0,
        }
        logger.info("Session state fully reset.")

    def start_parsing(self):
        """Schedule the main parsing task on the running qasync event loop."""
        # Reset ALL state from any previous run before starting clean
        self._reset_session_state()
        
        # Apply proxy to environment for aiohttp trust_env=True
        proxy_server = self.settings.get(K.SETTING_PROXY, "")
        if proxy_server:
            os.environ["HTTP_PROXY"] = f"http://{proxy_server}"
            os.environ["HTTPS_PROXY"] = f"http://{proxy_server}"
            logger.info(f"Global proxy set to: {proxy_server}")
        else:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)

        self.is_running = True
        self._stop_event.clear()
        # Store the task handle so we can cancel it on stop/restart
        self._main_asyncio_task = asyncio.ensure_future(self._run_async_parsing())

    async def _run_async_parsing(self):
        """Top-level async entry point. Seeds the URL queue, starts _main_task,
        and runs a progress monitor as a concurrent task."""
        # FIX #4: Allow older ParserManager processes to fully cancel and release network/browser resources
        await asyncio.sleep(0.8)

        # Seed with empty source_url so _is_downward_url returns True unconditionally
        # (passing start_url as source causes priority=0 since url==source_url at root path)
        await self.url_queue.put(
            self.start_url, 0, "",
            {"is_start_url": True, "start_url": self.start_url, "priority": 1000.0}
        )
        logger.info(f"Seeded URL queue with start URL: {self.start_url}")
        monitor_task = asyncio.create_task(self._async_monitor_progress())
        try:
            await self._main_task()
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

    async def _main_task(self):
        """
        Main orchestration task.
        Sentinels are always dispatched in finally so downloaders never deadlock.
        """
        from playwright.async_api import async_playwright
        
        num_downloaders = self.settings.get(K.SETTING_DOWNLOADER_THREADS, K.DEFAULT_DOWNLOADER_THREADS)
        downloader_tasks = []
        try:
            async with self.async_client_manager as session:
                self.session = session

                num_parsers = self.settings.get(K.SETTING_PARSER_THREADS, K.DEFAULT_PARSER_THREADS)

                logger.info("Initializing shared Playwright browser instance...")
                
                proxy_server = self.settings.get(K.SETTING_PROXY, "")
                playwright_proxy = {"server": f"http://{proxy_server}"} if proxy_server else None
                
                async with async_playwright() as p:
                    self.shared_browser = await p.chromium.launch(
                        headless=not self.settings.get("debug_show_browser", False),
                        args=["--disable-gpu", "--no-sandbox"],
                        proxy=playwright_proxy
                    )

                    logger.info(f"Starting {num_parsers} parser workers and {num_downloaders} downloader workers...")

                    parser_tasks = [asyncio.create_task(self._parser_worker(i)) for i in range(num_parsers)]
                    downloader_tasks = [asyncio.create_task(self._downloader_worker(i)) for i in range(num_downloaders)]

                    # Wait for parsers — they exit when queue empties or stop_event fires
                    await asyncio.gather(*parser_tasks, return_exceptions=True)
                    logger.info("All parser workers have finished.")

                    # Wait for downloaders (sentinels sent in finally below)
                    await asyncio.gather(*downloader_tasks, return_exceptions=True)
                    logger.info("All downloader workers have finished.")
                    
                    await self.shared_browser.close()

        except asyncio.CancelledError:
            logger.info("Main parsing task cancelled.")
            # Cancel all workers immediately
            for t in downloader_tasks:
                if not t.done(): t.cancel()
            for t in parser_tasks:
                if not t.done(): t.cancel()
            raise
        except Exception as e:
            logger.error(f"Error in main task: {str(e)}", exc_info=True)
        finally:
            # Send sentinels to gracefully unblock downloaders, but ONLY if we weren't abruptly cancelled by stop()
            if not self._stop_event.is_set():
                for _ in range(num_downloaders):
                    try:
                        self.download_queue.put_nowait(PARSER_DONE_SENTINEL)
                    except Exception:
                        pass
            self.is_running = False
            self.parsing_finished.emit()
            logger.info("Parsing process completed.")

    async def _handle_empty_queues_and_quarantine(self) -> bool:
        """
        Returns True if there is still work to do (parsers should keep running).
        Returns False only when BOTH url_queue is empty AND no downloads are actively
        in-flight (meaning no Migrator re-injections can happen).
        """
        if not self.url_queue.empty():
            return True
        # Stay alive if downloads are running — they may trigger Migrator re-queues
        if self._active_downloads > 0:
            await asyncio.sleep(0.2)  # Brief yield to allow download completion
            return True
        return False

    async def _get_next_url_to_parse(self) -> Optional[Tuple[str, int, str, Dict[str, Any]]]:
        try:
            return await self.url_queue.get(timeout=0.5) 
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    def _determine_parser_type(self, url: str) -> bool:
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()
        query = parsed_url.query.lower()
        return ("/api/" in path or "/json/" in path or path.endswith(".json") or 
                "format=json" in query or "output=json" in query or "callback=" in query)

    async def _invoke_parser(self, url: str, session, is_json_api: bool, context: Dict[str, Any]):
        """Select and invoke the appropriate parser for the given URL."""
        try:
            # Check if domain is in the quarantined set (tracked separately from asyncio.Queue)
            domain = urlparse(url).netloc
            is_protected = domain in self.quarantined_domains

            if is_json_api:
                p = JSONWebpageParser(url, self.settings, self.session)
                parse_result = await p.parse()
            else:
                # Tier 1: Fast Static Parser (aiohttp + BeautifulSoup)
                p = WebpageParser(url, self.settings, False, self.session, self.pattern_manager)
                parse_result = await p.parse()
                
                # Links, media, status, msg, code = parse_result
                # Tier 2: Conditional Scrapling upgrade if enabled and static found NO media
                # We also force Scrapling for the START URL if it looks empty, to bypass portals.
                depth = context.get("depth", 0)
                is_media_container = context.get("is_media_container", False)
                media_type_hint = context.get("media_type_hint", "image")
                is_js_enabled = bool(self.settings.get(K.SETTING_PROCESS_JS, False))
                # 0 items is our strict trigger for JS-heavy or interstitial pages
                low_media_found = (not parse_result[1] or len(parse_result[1]) == 0)
                
                if (low_media_found or depth == 0) and is_js_enabled and not self._stop_event.is_set():
                    # Log based on why we are upgrading
                    if depth == 0:
                        reason = "start page"
                    else:
                        reason = "0 valid media found by static parser"
                    
                    if is_media_container:
                        reason += f" on media container ({media_type_hint})"
                    
                    logger.info(f"Targeting Scrapling for {url} ({reason}).")
                    try:
                        async with self._browser_semaphore:
                            if self._stop_event.is_set():
                                return parse_result

                            sp = ScraplingWebpageParser(
                                url, self.settings,
                                use_stealth=is_protected,
                                scrapling_cookies=self._scrapling_domain_cookies,
                                shared_browser=getattr(self, "shared_browser", None),
                                pattern_manager=self.pattern_manager,
                                media_type_hint=media_type_hint
                            )
                            scrapling_result = await sp.parse()
                            
                            # If Scrapling found MORE media than static, OR static was empty, use Scrapling
                            s_media_count = len(scrapling_result[1]) if scrapling_result[1] else 0
                            p_media_count = len(parse_result[1]) if parse_result[1] else 0
                            
                            if s_media_count > p_media_count or p_media_count == 0:
                                # We throw away the static result (including its junk links)
                                parse_result = scrapling_result
                                logger.info(f"Scrapling upgrade successful: {s_media_count} items (discarded static 'junk' links).")
                            elif scrapling_result[2] == K.PARSER_SUCCESS:
                                # Even if 0, Scrapling might have 'better' internal links after the bypass click
                                parse_result = scrapling_result
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"Scrapling upgrade failed for {url}: {str(e)}")

            links, media, status, msg, code = parse_result
            return links, media, status, msg, code

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in _invoke_parser for {url}: {str(e)}", exc_info=True)
            return {}, [], K.PARSER_UNKNOWN_ERROR, str(e), None

    async def _process_parser_results(self, url: str, depth: int, 
                                      links_data: Any, media_files: List[Tuple[str, str, Dict[str, Any]]],
                                      original_url_context: Dict[str, Any]):
        await self._process_media_files(media_files, url)
        await self._update_queue_priorities(url, media_files)

        if depth < self.max_depth:
            urls_to_queue = []
            if isinstance(links_data, dict): # From WebpageParser
                for disc_url, link_ctx in links_data.items(): urls_to_queue.append((disc_url, link_ctx))
            elif isinstance(links_data, set): # From JSONWebpageParser
                for disc_url in links_data: urls_to_queue.append((disc_url, {})) 

            for disc_url_str, link_spec_ctx in urls_to_queue:
                abs_disc_url = disc_url_str
                if not abs_disc_url.startswith(("http://", "https://")):
                    abs_disc_url = urljoin(url, abs_disc_url)
                
                disc_domain = get_domain(abs_disc_url)
                if disc_domain in self.blocked_domains:
                    logger.debug(f"Skipping blocked domain for URL {abs_disc_url} (Domain: {disc_domain})")
                    continue
                
                if self.settings.get(K.SETTING_STAY_IN_DOMAIN, K.DEFAULT_STAY_IN_DOMAIN) and \
                   not is_same_domain(abs_disc_url, self.start_url): 
                    logger.debug(f"Skipping out-of-domain link: {abs_disc_url} (Original start: {self.start_url})")
                    continue
                
                stop_words_list = self.settings.get(K.SETTING_STOP_WORDS, K.DEFAULT_STOP_WORDS)
                if any(stop_word.lower() in abs_disc_url.lower() for stop_word in stop_words_list):
                    logger.debug(f"Skipping link with stop word: {abs_disc_url}")
                    continue

                new_ctx = {"source_url": url, "start_url": self.start_url, **link_spec_ctx}
                await self.url_queue.put(abs_disc_url, depth + 1, url, new_ctx)
        
        self.stats["pages_processed"] += 1
        self.status_updated.emit(f"Processed: {url}")

    # (old stub removed)
    async def _parser_worker(self, worker_id: int):
        """
        Worker task for parsing webpages
        """
        logger.info(f"Parser worker {worker_id} started.")
        try:
            while not self._stop_event.is_set():
                # Check if we should pause
                await self._pause_event.wait()
                
                # Try to get next URL from queue
                try:
                    url_data = await self.url_queue.get(timeout=1.0)
                except asyncio.QueueEmpty:
                    # Queue is empty, check if we should finish
                    if not await self._handle_empty_queues_and_quarantine():
                        break
                    continue
                
                if url_data is None: # Should not happen with PriorityURLQueue but for safety
                    break
                    
                current_url, depth, source_page_url, context = url_data
                
                try:
                    # Normalize and validate URL
                    if not current_url.startswith(("http://", "https://")):
                        current_url = urljoin(source_page_url or self.start_url, current_url)
                    current_url = normalize_url(current_url)
                    
                    # Check if URL was already processed (protected by lock)
                    async with self._url_lock:
                        if current_url in self.processed_urls:
                            self.url_queue.task_done()
                            continue
                        self.processed_urls.add(current_url)
                    
                    is_json = self._determine_parser_type(current_url)
                    links_found, media_files_found, status, msg, code = await self._invoke_parser(current_url, self.session, is_json, context)
                    
                    if status != K.PARSER_SUCCESS:
                        logger.warning(f"Parser returned {status} for {current_url}: {msg}")
                    
                    await self._process_parser_results(current_url, depth, links_found, media_files_found, context)
                    
                except Exception as e:
                    logger.error(f"Error processing URL {current_url}: {str(e)}", exc_info=True)
                finally:
                    self.url_queue.task_done()
                    
        except asyncio.CancelledError:
            logger.info(f"Parser worker {worker_id} cancelled.")
        except Exception as e:
            logger.error(f"Error in parser worker {worker_id}: {str(e)}", exc_info=True)
        finally:
            logger.info(f"Parser worker {worker_id} exiting.")

    async def _process_media_files(self, media_files: List[Tuple[str, str, Dict[str, Any]]], source_url: str) -> None:
        if not media_files: return
        await self._process_media_batch(media_files, source_url)

    async def _process_media_batch(self, media_files: List[Tuple[str, str, Dict[str, Any]]], source_url: str) -> None:
        sorted_media = sorted(media_files, key=lambda x: self._get_media_priority(x, source_url), reverse=True)
        for media_type, url, attrs in sorted_media:
            try:
                abs_url = urljoin(source_url, url) if not (url.startswith("http://") or url.startswith("https://")) else url
                abs_url = normalize_url(abs_url)
                if abs_url in self.downloaded_files: continue 
                
                # Check Blocklist for media domain
                media_domain = get_domain(abs_url)
                if media_domain in self.blocked_domains:
                    logger.debug(f"Skipping media from blocked domain: {abs_url} (Domain: {media_domain})")
                    continue
                
                # Check Stop Words for media URL
                stop_words_list = self.settings.get(K.SETTING_STOP_WORDS, K.DEFAULT_STOP_WORDS)
                if any(stop_word.lower() in abs_url.lower() for stop_word in stop_words_list):
                    logger.debug(f"Skipping media with stop word: {abs_url}")
                    continue
                    
                # Strict check: if it looks like a webpage but NOT a media file, skip putting in download queue
                if (is_webpage_url(abs_url) or abs_url.rstrip("/").lower().endswith((".html", ".htm", ".php"))) and not is_media_url(abs_url):
                    logger.debug(f"Skipping potential webpage/HTML link from media list: {abs_url}")
                    continue
                    
                base_filename = self._get_filename_from_url(abs_url, media_type)
                target_dir_path_final = self.download_path 
                page_domain_for_subdir = get_domain(source_url)
                
                if page_domain_for_subdir:
                    sane_domain = re.sub(r'[<>:"\/\\|?*]', '_', page_domain_for_subdir)
                    parsed_source_page = urlparse(source_url)
                    source_page_path_components = parsed_source_page.path.strip('/').split('/')
                    sane_path_parts = [re.sub(r'[<>:"\/\\|?*]', '_', part)[:K.MAX_SUBDIR_COMPONENT_LENGTH] for part in source_page_path_components if part][:K.MAX_PATH_COMPONENTS_FOR_SUBDIR]
                    path_subdir = os.path.join(*sane_path_parts) if sane_path_parts else ""
                    subdirname = os.path.join(sane_domain, path_subdir) if path_subdir else sane_domain
                    target_dir_path_final = os.path.join(self.download_path, subdirname)

                full_filepath_for_downloader = os.path.join(target_dir_path_final, base_filename)
                self.downloaded_files.add(abs_url) 

                media_item = {
                    "url": abs_url, "source_url": source_url, "media_type": media_type,
                    "attrs": attrs, "filepath": full_filepath_for_downloader,
                }
                await self.download_queue.put(media_item)
                if media_type == "image": self.stats["images_found"] += 1
                elif media_type == "video": self.stats["videos_found"] += 1
                logger.debug(f"Added {media_type} to download queue: {abs_url} (queue size: {self.download_queue.qsize()})")
            except Exception as err:
                logger.error(f"Error processing media file {url}: {str(err)}", exc_info=True)

    async def _process_download(self, media_item: Dict[str, Any]):
        """
        Process a single media download task
        """
        url = media_item["url"]
        filepath = media_item["filepath"]
        media_type = media_item["media_type"]
        source_url = media_item.get("source_url")
        
        domain = urlparse(url).netloc
        if domain in self.quarantined_domains:
            # Silently skip quarantined domain files but log once per worker/domain if needed
            self.stats["files_skipped"] += 1
            return
            
        if domain not in self.domain_health:
            self.domain_health[domain] = {"failures": 0, "total": 0}
        
        domain_state = self.domain_health[domain]
        is_probation = domain_state["failures"] > 0
        
        timeout_val = K.DEFAULT_DOMAIN_PROBATION_TIMEOUT if is_probation else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
        retries_val = K.DEFAULT_DOMAIN_PROBATION_RETRIES if is_probation else self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT)
        
        # Create a new downloader instance for this specific file
        downloader = MediaDownloader(
            url=url, 
            filepath=filepath, 
            settings=self.settings, 
            media_type=media_type, 
            source_url=source_url
        )
        downloader.set_progress_callback(self._update_current_progress)
        downloader.set_stop_event(self._stop_event)  # V7: enable graceful abort
        
        self.status_updated.emit(f"Downloading: {os.path.basename(filepath)}")
        
        try:
            # MediaDownloader.download will be a pure async function
            # _active_downloads stays high until AFTER Migrator logic to prevent
            # parser workers exiting before the re-queued URL is visible in url_queue.
            self._active_downloads += 1
            self._active_downloader_sessions.append(downloader)
            try:
                # Provide the aiohttp ClientSession from ParserManager for cookie/state pooling
                result = await downloader.download(session=self.session, timeout=timeout_val, retries=retries_val)

                
                domain_state["total"] += 1
                error_msg = result.get("error", "")
                if result.get("success"):
                    self.stats["files_downloaded"] += 1
                    if domain_state["failures"] > 0:
                        domain_state["failures"] = max(0, domain_state["failures"] - 1)
                else:
                    self.stats["files_skipped"] += 1
                    is_filter_reject = any(msg in error_msg for msg in ["too small", "Non-media", "Size mismatch", "Webpage/script", "Aborted:"])
                    
                    if is_filter_reject or any(msg in error_msg for msg in ["timeout", "403 Client Error"]):
                        if is_filter_reject:
                            logger.debug(f"File filtered out for {url}: {error_msg}")
                        
                        # --- Universal Migrator (Image Proxy Bypass) ---
                        # If the proxy times out HEAD requests or returns HTML instead of binary,
                        # re-parse to find the real direct image URL embedded inside.
                        # FIX #3: Catch Timeouts as image hosts block bot queries
                        is_timeout_or_block = any(msg in error_msg for msg in ["timeout", "403 Client Error"])
                        should_migrate = "Webpage/script received" in error_msg or is_timeout_or_block

                        if should_migrate:
                            async with self._url_lock:
                                if url not in self.processed_urls:
                                    logger.warning(f"[MIGRATOR] Re-queuing as webpage (HTML/block detected from supposed image): {url}")
                                    await self.url_queue.put(url, 0, source_url, {
                                        "is_webpage": True,
                                        "priority": 500.0,
                                        "is_media_container": True,
                                        "is_migrated": True
                                    })
                                else:
                                    logger.warning(f"[MIGRATOR] Skipping (already processed as page): {url}")
                        else:
                            logger.debug(f"[MIGRATOR] Not triggering — error_msg: {error_msg!r}")
                    else:
                        domain_state["failures"] += 1
                        logger.warning(f"Failed to download {url}: {error_msg}")
                    
                    # Check for quarantine threshold (only for real failures)
                    if not is_filter_reject and domain_state["failures"] >= K.DEFAULT_QUARANTINE_FAILURE_THRESHOLD:
                        self.quarantined_domains.add(domain)
                        logger.warning(f"Domain {domain} quarantined after {domain_state['failures']} real failures.")
            finally:
                # Decrement AFTER all Migrator logic so parser workers wait correctly
                self._active_downloads -= 1
                try:
                    self._active_downloader_sessions.remove(downloader)
                except ValueError:
                    pass
                    
        except Exception as e:
            logger.error(f"Execution error in _process_download for {url}: {str(e)}")
            self.stats["files_skipped"] += 1

    async def _downloader_worker(self, worker_id: int):
        """
        Worker task for downloading media files
        """
        logger.info(f"Downloader worker {worker_id} started.")
        
        try:
            while True:
                # Check if we should stop (but first process what's in queue)
                if self._stop_event.is_set() and self.download_queue.empty():
                    break
                    
                # Check for pause
                await self._pause_event.wait()
                
                # IMPORTANT: Get item OUTSIDE the try/finally that calls task_done()
                try:
                    item = await self.download_queue.get()
                except asyncio.CancelledError:
                    break
                    
                if item is PARSER_DONE_SENTINEL:
                    self.download_queue.task_done()
                    break
                    
                try:
                    # Process download with a small random jitter to mimic human timing
                    import random
                    await asyncio.sleep(random.uniform(0.2, 0.7))
                    await self._process_download(item)
                    
                except Exception as e:
                    logger.error(f"Error in downloader worker {worker_id}: {str(e)}", exc_info=True)
                finally:
                    # Called ONLY if get() was successful to prevent 'called too many times' ValueError
                    self.download_queue.task_done()
                    
        except asyncio.CancelledError:
            logger.info(f"Downloader worker {worker_id} cancelled.")
        finally:
            logger.info(f"Downloader worker {worker_id} exiting.")

    def pause_parsing(self) -> None:
        self.is_paused = True; self._pause_event.clear(); logger.info("Parsing paused")

    def resume_parsing(self) -> None:
        self.is_paused = False; self._pause_event.set(); logger.info("Parsing resumed")

    def stop_parsing(self) -> None:
        logger.info("Attempting to stop parsing...")
        self.is_running = False
        self._stop_event.set()
        self._pause_event.set()  # Unblock paused workers
        # Cancel the top-level asyncio task — this propagates CancelledError
        # into all nested awaits (including blocked Playwright fetches)
        self.cancel_all_tasks()
        # Abort all in-flight HTTP download sessions immediately (they run in thread executor)
        for dl in list(self._active_downloader_sessions):
            try:
                dl.session.close()
            except Exception:
                pass
        self._active_downloader_sessions.clear()
        try:
            while not self.download_queue.empty():
                try:
                    self.download_queue.get_nowait()
                    self.download_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            while not self.quarantine_queue.empty():
                try:
                    self.quarantine_queue.get_nowait()
                    self.quarantine_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            self.url_queue = PriorityURLQueue(self.settings)
            logger.info("Queues cleared.")
        except Exception as e:
            logger.error(f"Error clearing queues: {str(e)}", exc_info=True)
        logger.info("Parsing stop procedure initiated. Tasks will shut down.")

    def cancel_all_tasks(self) -> None:
        """Cancel the top-level asyncio task and all its children."""
        if self._main_asyncio_task and not self._main_asyncio_task.done():
            self._main_asyncio_task.cancel()
            logger.info("Main asyncio task cancellation requested.")

    async def _async_monitor_progress(self) -> None:
        """Async coroutine that replaces the old blocking thread monitor."""
        while not self._stop_event.is_set():
            try:
                total_found = self.stats["images_found"] + self.stats["videos_found"]
                total_proc = self.stats["files_downloaded"] + self.stats["files_skipped"]
                if total_found > 0:
                    self.total_progress_updated.emit(int((total_proc / total_found) * 100))
                else:
                    self.total_progress_updated.emit(0)
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in progress monitor: {str(e)}", exc_info=True)
                await asyncio.sleep(1)
        logger.info("Progress monitor finished.")

    def _update_current_progress(self, progress: int) -> None: self.current_progress_updated.emit(progress)

    def _get_filename_from_url(self, url: str, media_type: str) -> str:
        parsed_url = urlparse(url)
        path = parsed_url.path.strip("/")
        basename = os.path.basename(path)
        if not basename or "." not in basename:
            name_part = basename if basename and "." not in basename else hashlib.md5(url.encode()).hexdigest()[:K.DEFAULT_FILENAME_HASH_LENGTH]
            extension = ""
            if "." in path:
                potential_ext = os.path.splitext(path)[1]
                if potential_ext and 1 < len(potential_ext) <= 5: extension = potential_ext.lower() # Max 5 char extension like .jpeg
            if not extension: extension = K.DEFAULT_IMAGE_EXTENSION if media_type == "image" else K.DEFAULT_VIDEO_EXTENSION
            basename = f"{name_part}{extension}"
        return self._sanitize_filename(basename)

    def _sanitize_filename(self, filename: str) -> str:
        invalid_os_chars = r'<>:"/\\|?*' 
        control_chars = ''.join(map(chr, range(32)))
        sanitized_name = re.sub(f'[{re.escape(invalid_os_chars + control_chars)}]+', '_', filename)
        sanitized_name = re.sub(r'\s+', '_', sanitized_name) 
        sanitized_name = re.sub(r'__+', '_', sanitized_name).strip('_')
        name_part, ext_part = os.path.splitext(sanitized_name)
        if len(ext_part) > 7: ext_part = ext_part[:7] 
        
        # K.MAX_FILENAME_LENGTH from constants.py is for the name_part only
        if len(name_part) > K.MAX_FILENAME_LENGTH: name_part = name_part[:K.MAX_FILENAME_LENGTH]
        
        final_filename = f"{name_part}{ext_part}"
        if not name_part: 
             final_filename = f"{hashlib.md5(filename.encode()).hexdigest()[:K.DEFAULT_FILENAME_HASH_LENGTH]}{ext_part or K.DEFAULT_IMAGE_EXTENSION}"
        return final_filename
    
    def _get_media_priority(self, media_item: Tuple[str, str, Dict[str, Any]], source_url: str) -> float:
        media_type, url, attrs = media_item; priority = 1.0
        if media_type == "image": priority *= 2.0
        elif media_type == "video": priority *= 3.0
        source_type = attrs.get("source", "")
        if "fullsize" in source_type or "original" in source_type: priority *= 3.0
        elif "parent-link" in source_type: priority *= 2.5
        if any(p in url.lower() for p in ["/full/", "/large/", "/original/", "fullsize", "highres"]): priority *= 2.0
        if source_url == self.start_url: priority *= 3.0 
        if "dimensions" in attrs:
            w, h = attrs["dimensions"].get("width",0), attrs["dimensions"].get("height",0)
            if w > 0 and h > 0: priority *= min(1.0 + ((w * h) / 1000000), 3.0) 
        return priority
    
    def get_stats(self) -> Dict[str, int]: return self.stats.copy()

    async def save_state(self, task_download_path: str) -> None:
        url_queue_items = []
        if hasattr(self.url_queue, '_queue'):
             url_queue_items = [item_tuple_with_priority[1] for item_tuple_with_priority in self.url_queue._queue]
        
        download_queue_items = []
        temp_dq_holder = [] 
        while not self.download_queue.empty():
            try: item = self.download_queue.get_nowait(); temp_dq_holder.append(item)
            except asyncio.QueueEmpty: break
        download_queue_items.extend(temp_dq_holder) 
        for item in temp_dq_holder: await self.download_queue.put(item) 

        state = {
            "url_queue_items": url_queue_items, "download_queue_items": download_queue_items,
            "processed_urls": list(self.processed_urls), "downloaded_files": list(self.downloaded_files),
            "stats": self.stats, "settings": self.settings, "start_url": self.start_url,
            "download_path": self.download_path, 
            "domain_health": self.domain_health, "quarantined_domains": list(self.quarantined_domains)
        }
        
        session_dir = os.path.join(task_download_path, K.SESSION_STATE_SUBDIR) 
        os.makedirs(session_dir, exist_ok=True)
        full_state_path = os.path.join(session_dir, K.SESSION_STATE_FILENAME)

        async with aiofiles.open(full_state_path, "wb") as f: await f.write(pickle.dumps(state))
        logger.info(f"Session state saved to {full_state_path}")

    async def load_state(self, task_download_path: str) -> None: 
        session_file_path = os.path.join(task_download_path, K.SESSION_STATE_SUBDIR, K.SESSION_STATE_FILENAME)
        if not os.path.exists(session_file_path):
            logger.info(f"No session state file found at {session_file_path}. Starting fresh.")
            return

        try:
            async with aiofiles.open(session_file_path, "rb") as f: data = await f.read(); state = pickle.loads(data)
            
            for item_tuple in state.get("url_queue_items", []):
                if len(item_tuple) == 4: # url, depth, source_url, context
                     await self.url_queue.put(item_tuple[0], item_tuple[1], item_tuple[2], item_tuple[3])
            for item in state.get("download_queue_items", []): await self.download_queue.put(item)

            self.processed_urls = set(state.get("processed_urls", []))
            self.downloaded_files = set(state.get("downloaded_files", [])) 
            self.stats = state.get("stats", self.stats)
            self.start_url = state.get("start_url", self.start_url)
            self.domain_health = state.get("domain_health", {})
            self.quarantined_domains = set(state.get("quarantined_domains", []))
            logger.info(f"Successfully loaded state from {session_file_path}")
        except Exception as err:
            logger.error(f"Error loading state from {session_file_path}: {str(err)}", exc_info=True)
