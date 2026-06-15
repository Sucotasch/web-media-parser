#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced parser manager that coordinates parsing and downloading process
"""

import os
import sys
import re
import time
# import queue # Not used directly, asyncio.Queue is used
import pickle
import asyncio
import logging
import hashlib

import aiofiles
import threading
import traceback
from typing import Dict, Any, Set, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

from PySide6.QtCore import QObject, Signal
# from bs4 import BeautifulSoup # Not used directly in ParserManager

from src.parser.webpage_parser import WebpageParser
from src.parser.json_parser import JSONWebpageParser
from src.parser.priority_url_queue import PriorityURLQueue
from src.parser.site_pattern_manager import SitePatternManager
from src.downloader.media_downloader import MediaDownloader, create_shared_downloader_session
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


class ParserManager(QObject):
    """Enhanced parser manager with async support"""

    total_progress_updated = Signal(int)
    current_progress_updated = Signal(int)
    parsing_finished = Signal()
    status_updated = Signal(str)
    task_ended = Signal(str)  # "completed" | "stopped" | "failed"

    def __init__(
        self, url: str, download_path: str, settings: Dict[str, Any], log_handler,
        task_id: str = None, one_shot: bool = False, pending_downloads: list = None,
    ):
        super().__init__()
        self.start_url = url
        self.download_path = download_path
        self.settings = settings
        self.log = log_handler
        self.task_id = task_id  # Used for isolated session state paths
        self.one_shot = one_shot  # True = page only, no link following
        self.pending_downloads = pending_downloads or []  # Pre-populated download items

        self.is_running = False
        self.is_paused = False
        # NOTE: _pause_event, _stop_event, download_queue, quarantine_queue are
        # intentionally NOT created here. They are asyncio primitives and must be
        # created inside the asyncio event loop thread to be bound to the correct
        # loop. They are created in start_parsing() before the loop starts.
        self._pause_event = None
        self._stop_event = None

        self.max_depth = self.settings.get(K.SETTING_SEARCH_DEPTH, K.DEFAULT_SEARCH_DEPTH)
        self.page_limit = self.settings.get("page_limit", 1000)
        
        self.domain_health = {}
        self.quarantined_domains = set()
        self.quarantine_queue = None  # Created in start_parsing on the correct loop
        
        self.pattern_manager = None
        if self.settings.get(K.SETTING_USE_PATTERNS, K.DEFAULT_USE_PATTERNS):
            custom_pattern_path = self.settings.get(K.SETTING_CUSTOM_PATTERN_PATH, K.DEFAULT_SETTINGS_VALUES[K.SETTING_CUSTOM_PATTERN_PATH])
            imagus_sieve_path = self.settings.get(K.SETTING_IMAGUS_SIEVE_PATH, K.DEFAULT_SETTINGS_VALUES[K.SETTING_IMAGUS_SIEVE_PATH])
            self.pattern_manager = SitePatternManager(
                enable_built_in=True, 
                custom_pattern_path=custom_pattern_path,
                imagus_sieve_path=imagus_sieve_path
            )
            logger.info("Using SitePatternManager for pattern transformations")

        self.url_queue = PriorityURLQueue(settings=self.settings)
        self.download_queue = None  # Created in start_parsing on the correct loop
        self.processed_urls = set()
        self.downloaded_files = set() # Stores URLs of media marked for download to avoid re-processing

        self.stats = {
            "pages_processed": 0, "images_found": 0, "videos_found": 0,
            "files_downloaded": 0, "files_skipped": 0,
        }
        self._completed_naturally = False  # True only when parsing finished by itself, not user Stop
        self._had_critical_error = False
        self._last_activity_time: float = time.time()  # Updated on each parsed page/download for idle detection
        self._active_tasks = 0
        self._active_tasks_lock = threading.Lock()
        
        self.loop = asyncio.new_event_loop()
        self.async_client_manager: AsyncClientManager = AsyncClientManager(self.settings)
        self._shared_downloader_session = None  # Created in _main_task, closed in _main_task.finally
        
        self.parser_tasks = []
        self.downloader_tasks = []
        self.blocked_domains: Set[str] = self._load_domain_blocklist()

    def reset(self) -> None:
        """Reset the manager state for a new parsing task"""
        # Ensure we're in the right event loop if called during start
        self.url_queue = PriorityURLQueue(settings=self.settings)
        self.url_queue.reset_async_primitives()
        self.download_queue = asyncio.Queue()
        self.quarantine_queue = asyncio.Queue()
        self._active_tasks = 0
        self.processed_urls.clear()
        self.downloaded_files.clear()
        self.stats = {
            "images_found": 0, "videos_found": 0, "files_downloaded": 0,
            "files_skipped": 0, "pages_processed": 0, "bytes_downloaded": 0
        }
        self.domain_health.clear()
        self.quarantined_domains.clear()
        self._stop_event.clear()
        self._pause_event.set()
        self.is_running = False
        self.is_paused = False
        self._completed_naturally = False
        self._had_critical_error = False
        self._last_activity_time = time.time()

    def _load_domain_blocklist(self, blocklist_file_name: str = K.DOMAIN_BLOCKLIST_FILENAME) -> Set[str]:
        blocked_domains: Set[str] = set()
        path_to_load = None  # Must be initialized before any conditional assignment

        # 1. Try PyInstaller bundle resources first
        if getattr(sys, 'frozen', False):
            base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            bundled_path = os.path.join(base_dir, "resources", blocklist_file_name)
            if os.path.exists(bundled_path):
                path_to_load = bundled_path
        
        # 2. Try the actual executable directory (user-editable blocklist)
        if not path_to_load and getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            exe_path = os.path.join(exe_dir, blocklist_file_name)
            if os.path.exists(exe_path):
                path_to_load = exe_path

        # 3. Try relative to the script directory (development)
        if not path_to_load:
            script_dir_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), blocklist_file_name)
            if os.path.exists(script_dir_path):
                path_to_load = script_dir_path
        
        # 4. Try current working directory or provided path
        if not path_to_load and os.path.exists(blocklist_file_name):
            path_to_load = blocklist_file_name
        
        # 5. Try resources folder in development root
        if not path_to_load:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            dev_res_path = os.path.join(root_dir, "resources", blocklist_file_name)
            if os.path.exists(dev_res_path):
                path_to_load = dev_res_path
        
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
            logger.warning(
                f"Domain blocklist file '{blocklist_file_name}' not found in any expected location. "
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

    def start_parsing(self):
        self.is_running = True
        # Create the event loop first so asyncio primitives are bound to it
        if not self.loop or self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # (Re)create asyncio primitives on THIS loop to avoid cross-loop errors
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Unpaused by default
        self.url_queue = PriorityURLQueue(settings=self.settings)
        self.download_queue = asyncio.Queue()
        self.quarantine_queue = asyncio.Queue()
        self._active_tasks = 0
        self._active_tasks_lock = threading.Lock()
        self._processed_lock = asyncio.Lock()  # Initialize lock for thread-safe registry access
        self._completed_naturally = False
        self._last_activity_time = time.time()
        self._domain_semaphores: Dict[str, asyncio.Semaphore] = {}

        # Reset the url_queue's asyncio primitives (Lock and Event).
        # This cannot be done here (GUI/QThread context) safely — it's done
        # inside _main_task which runs in the event loop thread.

        self.loop_thread = threading.Thread(target=self._run_event_loop, name="AsyncEventLoopThread")
        self.loop_thread.daemon = True
        self.loop_thread.start()
        self.monitor_thread = threading.Thread(target=self._monitor_progress, name="ProgressMonitorThread")
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main_task())
        except Exception as e:
            logger.error(f"Error in event loop: {str(e)}", exc_info=True)
        finally:
            if self.async_client_manager:
                try:
                    if self.loop.is_running(): # Should ideally be closed via _main_task's async with
                        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.async_client_manager.close()))
                    elif not self.loop.is_closed():
                         self.loop.run_until_complete(self.async_client_manager.close())
                    logger.info("AsyncClientManager session closed during event loop shutdown.")
                except Exception as e:
                    logger.error(f"Error closing AsyncClientManager session in _run_event_loop: {e}", exc_info=True)
            if self.loop and not self.loop.is_closed():
                self.loop.close()
                logger.info("Asyncio event loop closed.")

    async def _main_task(self):
        # Reset PriorityURLQueue's asyncio primitives now that the loop is running.
        # This is the only safe place to do so, as asyncio.Lock/Event must be
        # created inside a running event loop context.
        self.url_queue.reset_async_primitives()

        # Load saved state (processed_urls, downloaded_files, queue items)
        # This runs in the correct event loop thread, unlike the dead
        # run_coroutine_threadsafe(self.loop) call that was previously in
        # MainWindow._launch_parser_for_task.
        await self.load_state(self.download_path)

        # If we have pre-populated download items (from extension one-shot),
        # add them to download_queue — extension did full scanning, no parsing needed
        if self.pending_downloads:
            for item in self.pending_downloads:
                await self.download_queue.put(item)
                self.stats["images_found"] += 1
            # Set pages_processed so completion monitor can trigger
            self.stats["pages_processed"] = 1
            logger.info(f"One-shot: {len(self.pending_downloads)} items added to download_queue")
            self.pending_downloads = []
            # Don't seed start_url — items are already in download_queue
            # Skip to waiting for downloads to complete
        elif len(self.processed_urls) == 0:
            # Seed the initial URL ONLY for fresh sessions (no state loaded, no pending downloads)
            await self.url_queue.put(
                self.start_url, 0, self.start_url,
                {"is_start_url": True, "start_url": self.start_url}
            )

        # Create the shared requests.Session here so its lifecycle is entirely
        # within _main_task: created before workers start, closed in finally
        # after all workers are cancelled+gathered. A local reference prevents
        # race conditions if start_parsing() is called again before this task ends.
        shared_dl_session = create_shared_downloader_session(self.settings)
        self._shared_downloader_session = shared_dl_session
        try:
            async with self.async_client_manager as session:
                parser_count = self.settings.get(K.SETTING_PARSER_THREADS, K.DEFAULT_PARSER_THREADS)
                downloader_count = self.settings.get(K.SETTING_DOWNLOADER_THREADS, K.DEFAULT_DOWNLOADER_THREADS)
                logger.info(f"Main task started. Parser threads: {parser_count}, Downloader threads: {downloader_count}")

                self.parser_tasks = [asyncio.create_task(self._parser_worker(session), name=f"parser_{i}") for i in range(parser_count)]
                self.downloader_tasks = [asyncio.create_task(self._downloader_worker(), name=f"downloader_{i}") for i in range(downloader_count)]

                completion_task = asyncio.create_task(self._completion_monitor(), name="CompletionMonitor")
                all_tasks = self.parser_tasks + self.downloader_tasks + [completion_task]
                stop_waiter = asyncio.create_task(self._stop_event.wait(), name="StopEventWaiter")
                
                # Wait for the stop event (set by user OR by _completion_monitor on natural finish)
                await stop_waiter
                logger.info("Stop event received, cancelling tasks.")
                
                # Cancel all worker and monitor tasks
                for task in all_tasks:
                    task.cancel()
                
                # Wait for all tasks to finish cancellation
                if all_tasks:
                    await asyncio.gather(*all_tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Critical error in main task: {str(e)}", exc_info=True)
            self._had_critical_error = True
        finally:
            logger.info("_main_task finished.")
            # Close shared session AFTER gather — all workers are stopped at this point
            self._shared_downloader_session = None
            try:
                shared_dl_session.close()
                logger.info("Shared downloader session closed.")
            except Exception as e:
                logger.error(f"Error closing shared downloader session: {e}", exc_info=True)
            # Emit parsing_finished for backwards compatibility (only natural completion)
            if self._completed_naturally:
                self.parsing_finished.emit()
            # Always emit task_ended so the queue manager knows the task finished
            if getattr(self, "_had_critical_error", False):
                self.task_ended.emit("failed")
            elif self._completed_naturally:
                self.task_ended.emit("completed")
            else:
                self.task_ended.emit("stopped")

    async def _completion_monitor(self) -> None:
        """Monitors queue emptiness and triggers natural completion when all work is done.

        Polls every second. When all three queues are empty AND pages_processed > 0
        AND no activity has occurred for IDLE_COMPLETION_TIMEOUT_SECONDS, sets the
        _stop_event via _completed_naturally=True, causing _main_task.finally to emit
        parsing_finished.
        """
        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            if self._stop_event.is_set():
                break
            if self.is_paused:
                continue
            if self.stats["pages_processed"] == 0:
                continue  # Parsing hasn't started yet, no work to complete
            all_empty = (
                self.url_queue.empty() and
                self.download_queue.empty() and
                self.quarantine_queue.empty()
            )
            if all_empty:
                # Check if any tasks are still in-flight
                with self._active_tasks_lock:
                    active = self._active_tasks
                
                if active > 0:
                    self._last_activity_time = time.time() # Reset idle timer if things are in-flight
                    continue

                idle_duration = time.time() - self._last_activity_time
                if idle_duration >= K.IDLE_COMPLETION_TIMEOUT_SECONDS:
                    logger.info(
                        f"All queues idle for {idle_duration:.1f}s — triggering natural completion."
                    )
                    self.status_updated.emit("Parsing complete.")
                    self._completed_naturally = True
                    self._stop_event.set()
                    return
        logger.info("Completion monitor finished.")

    async def _handle_empty_queues_and_quarantine(self) -> bool:
        if not (self.download_queue.empty() and self.url_queue.empty() and self.stats["pages_processed"] > 0):
            return True
        quarantine_size = self.quarantine_queue.qsize()
        if quarantine_size > 0:
            logger.info(f"Main queues empty. Processing {quarantine_size} URLs from quarantine.")
            self.status_updated.emit(f"Processing {quarantine_size} URLs from quarantined domains...")
            items_to_process = min(quarantine_size, K.QUARANTINE_BATCH_PROCESS_SIZE)
            for _ in range(items_to_process):
                try:
                    item = await self.quarantine_queue.get()
                    # Drop items that have exhausted their retry budget (prevents infinite loop)
                    if item.get("quarantine_retries", 0) >= K.QUARANTINE_MAX_ITEM_RETRIES:
                        logger.debug(f"Dropping quarantined URL after max retries: {item['url']}")
                        self.stats["files_skipped"] += 1  # Correctly increment skip counter for progress calculation
                        self.quarantine_queue.task_done()
                        continue
                    item["quarantine_retries"] = item.get("quarantine_retries", 0) + 1
                    domain = urlparse(item["url"]).netloc
                    if domain in self.quarantined_domains:
                        self.quarantined_domains.discard(domain)
                        if domain in self.domain_health:
                            self.domain_health[domain]["failures"] = 0
                    await self.download_queue.put(item)
                    self.quarantine_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            return True
        logger.debug("All queues empty, waiting for completion signal.")
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
        links_found: Any = set() 
        media_files_found: List[Tuple[str, str, Dict[str, Any]]] = []
        if is_json_api:
            logger.debug(f"Using JSONWebpageParser for {url}")
            async with JSONWebpageParser(url=url, settings=self.settings, external_session=session) as p:
                links_found, media_files_found = await p.parse()
        else:
            logger.debug(f"Using WebpageParser for {url}")
            p = WebpageParser(
                url=url, settings=self.settings,
                process_js=self.settings.get(K.SETTING_PROCESS_JS, K.DEFAULT_PROCESS_JS),
                external_session=session, pattern_manager=self.pattern_manager,
                context=context
            )
            parse_result = await p.parse()
            links_found = parse_result[0]
            media_files_found = parse_result[1]
            
            # Extract and sync cookies to the shared downloader session
            cookies = parse_result[5]
            if cookies and self._shared_downloader_session:
                logger.debug(f"Syncing {len(cookies)} cookies from parser to shared downloader session")
                for name, value in cookies.items():
                    self._shared_downloader_session.cookies.set(name, value)

            if parse_result[2] != K.PARSER_SUCCESS:
                logger.error(f"Error parsing {url}: {parse_result[3]}")
        return links_found, media_files_found

    async def _process_parser_results(self, url: str, depth: int, 
                                      links_data: Any, media_files: List[Tuple[str, str, Dict[str, Any]]],
                                      original_url_context: Dict[str, Any]):
        await self._process_media_files(media_files, url)
        await self._update_queue_priorities(url, media_files)

        # In one-shot mode, skip link discovery — only process media from this page
        if self.one_shot:
            self.stats["pages_processed"] += 1
            self._last_activity_time = time.time()
            return

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
        self._last_activity_time = time.time()  # Track last activity for idle detection
        self.status_updated.emit(f"Processed: {url}")

    def _get_domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        """Get or create a semaphore limiting concurrent requests per domain."""
        if domain not in self._domain_semaphores:
            self._domain_semaphores[domain] = asyncio.Semaphore(K.DOMAIN_CONCURRENCY_LIMIT)
        return self._domain_semaphores[domain]

    async def _parser_worker(self, session):
        while self.is_running and not self._stop_event.is_set():
            if self.is_paused:
                await self._pause_event.wait()
                if self._stop_event.is_set(): break
                continue
            # Page limit: stop when enough files downloaded (not just parsed)
            if self.page_limit > 0 and self.stats["files_downloaded"] >= self.page_limit:
                logger.info(f"Page limit reached: {self.stats['files_downloaded']} files downloaded >= {self.page_limit}")
                self._completed_naturally = True
                self._stop_event.set()
                break
            continue_processing = await self._handle_empty_queues_and_quarantine()
            if not continue_processing:
                # All work is done, but keep the worker alive until stop is requested
                await asyncio.sleep(1.0)
                continue
            url_data = await self._get_next_url_to_parse()
            if not url_data:
                await asyncio.sleep(0.1) 
                continue
            
            with self._active_tasks_lock:
                self._active_tasks += 1

            current_url, depth, source_page_url, context = url_data
            logger.debug(f"Parser worker got URL: {current_url} (depth={depth})")
            try:
                # Check before expensive network call
                if not self.is_running:
                    self.url_queue.task_done(); break
                if not current_url.startswith(("http://", "https://")):
                    current_url = urljoin(source_page_url or self.start_url, current_url)
                current_url = normalize_url(current_url)
                async with self._processed_lock:
                    if current_url in self.processed_urls:
                        self.url_queue.task_done(); continue
                    self.processed_urls.add(current_url)
                # Per-domain concurrency limit
                domain = get_domain(current_url)
                if domain:
                    sem = self._get_domain_semaphore(domain)
                    await sem.acquire()
                try:
                    is_json = self._determine_parser_type(current_url)
                    links_found, media_files_found = await self._invoke_parser(current_url, session, is_json, context)
                    await self._process_parser_results(current_url, depth, links_found, media_files_found, context)
                finally:
                    if domain:
                        sem.release()
            except Exception as e:
                logger.error(f"Error processing URL {current_url}: {str(e)}", exc_info=True)
                if current_url not in self.processed_urls: self.processed_urls.add(current_url)
            finally:
                with self._active_tasks_lock:
                    self._active_tasks -= 1
                self.url_queue.task_done()
        logger.info(f"Parser worker {threading.get_ident()} finished.")

    async def _process_media_files(self, media_files: List[Tuple[str, str, Dict[str, Any]]], source_url: str) -> None:
        if not media_files: return
        await self._process_media_batch(media_files, source_url)

    async def _process_media_batch(self, media_files: List[Tuple[str, str, Dict[str, Any]]], source_url: str) -> None:
        sorted_media = sorted(media_files, key=lambda x: self._get_media_priority(x, source_url), reverse=True)
        for media_type, url, attrs in sorted_media:
            try:
                abs_url = urljoin(source_url, url) if not (url.startswith("http://") or url.startswith("https://")) else url
                abs_url = normalize_url(abs_url)
                async with self._processed_lock:
                    if abs_url in self.downloaded_files: continue 
                    self.downloaded_files.add(abs_url) 
                    
                if is_webpage_url(abs_url) and not is_media_url(abs_url):
                    logger.debug(f"Treating as webpage rather than media: {abs_url}")
                    assumed_depth_for_media_webpage = 1 
                    if assumed_depth_for_media_webpage < self.max_depth:
                        ctx = {"source_url": source_url, "start_url": self.start_url, "from_media_item": True, "media_context": attrs, "priority": 5.0}
                        await self.url_queue.put(abs_url, assumed_depth_for_media_webpage, self.start_url, ctx)
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

    async def _downloader_worker(self) -> None:
        while self.is_running and not self._stop_event.is_set():
            try:
                if self.is_paused:
                    await self._pause_event.wait()
                    if self._stop_event.is_set(): break
                    continue
                media_item = await asyncio.wait_for(self.download_queue.get(), timeout=0.5)
                logger.debug(f"Downloader worker got media item: {media_item['url']}")
            except asyncio.TimeoutError: continue
            if not media_item:
                self.download_queue.task_done(); continue
            
            with self._active_tasks_lock:
                self._active_tasks += 1

            try:
                # Check before expensive download
                if not self.is_running:
                    self.download_queue.task_done(); break
                url = media_item["url"]
                filepath_from_queue = media_item["filepath"] 
                domain = urlparse(url).netloc
                if domain in self.quarantined_domains:
                    await self.quarantine_queue.put(media_item)
                    continue  # task_done() is safely handled by the `finally` block
                if domain not in self.domain_health: self.domain_health[domain] = {"failures": 0, "total": 0}
                domain_state = self.domain_health[domain]
                is_probation = domain_state["failures"] > 0
                
                timeout_val = K.DEFAULT_DOMAIN_PROBATION_TIMEOUT if is_probation else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
                retries_val = K.DEFAULT_DOMAIN_PROBATION_RETRIES if is_probation else self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT)
                
                # Per-domain concurrency limit
                sem = self._get_domain_semaphore(domain)
                await sem.acquire()
                try:
                    downloader = MediaDownloader(
                        url=url, filepath=filepath_from_queue, settings=self.settings,
                        media_type=media_item["media_type"], source_url=media_item["source_url"],
                        shared_session=self._shared_downloader_session,
                        stop_event=self._stop_event
                    )
                    downloader.set_progress_callback(self._update_current_progress)
                    base_filename_for_status = os.path.basename(filepath_from_queue)
                    self.status_updated.emit(f"Downloading: {base_filename_for_status}")
                    
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: downloader.download(timeout=timeout_val, retries=retries_val)
                    )
                finally:
                    sem.release()
                
                domain_state["total"] += 1
                self._last_activity_time = time.time()  # Track last activity for idle detection
                if result["success"]:
                    self.stats["files_downloaded"] += 1
                    if domain_state["failures"] > 0: domain_state["failures"] = max(0, domain_state["failures"] - 1)
                else:
                    self.stats["files_skipped"] += 1
                    err_msg = str(result.get('error', ''))
                    logger.warning(f"Failed to download file: {url} - {err_msg}")
                    
                    # 1. Feedback Loop: If we expected media but got HTML, feed it back to WebpageParser (limit 1 attempt)
                    if "webpage/script content" in err_msg.lower():
                        attrs = media_item.get("attrs", {})
                        if not attrs.get("interstitial_retry"):
                            logger.info(f"Targeting photo-host landing page for discovery: {url} (Retrying as webpage)")
                            # Track that this URL is being retried as a potential interstitial landing page
                            ctx = {
                                "source_url": media_item.get("source_url"),
                                "start_url": self.start_url,
                                "interstitial_retry": True, # Prevents infinite loops
                                "original_media_type": media_item.get("media_type", "image"),
                                "original_target_name": os.path.basename(media_item.get("filepath", "")),
                                "priority": 15.0 # High priority to resolve quickly
                            }
                            # Send back to url_queue at depth 1 to ensure it gets processed
                            await self.url_queue.put(url, 1, self.start_url, ctx)
                        else:
                            logger.warning(f"Interstitial recovery failed to find binary media for: {url}")
                    
                    # 2. Domain Health: Only penalize health for genuine network/server errors
                    is_content_skip = any(skip_reason in err_msg.lower() for skip_reason in [
                        "file too small", "already exists", "invalid extension", "unsupported", 
                        "webpage/script content", "trash media"
                    ])
                    
                    if not is_content_skip:
                        domain_state["failures"] += 1
                        if domain_state["failures"] >= K.DEFAULT_QUARANTINE_FAILURE_THRESHOLD:
                            # Race Condition Protection: Only log and add if not already quarantined
                            if domain not in self.quarantined_domains:
                                self.quarantined_domains.add(domain)
                                logger.warning(f"Domain {domain} quarantined after {domain_state['failures']} network failures.")
            except Exception as err:
                logger.error(f"Error in downloader_worker for {media_item.get('url', 'Unknown URL')}: {str(err)}", exc_info=True)
            finally:
                with self._active_tasks_lock:
                    self._active_tasks -= 1
                self.download_queue.task_done()
        logger.info(f"Downloader worker {threading.get_ident()} finished.")

    def pause_parsing(self) -> None:
        self.is_paused = True
        logger.info("Parsing paused")
        if self._pause_event is None:
            return
        # Schedule _pause_event.clear() in the correct event loop thread
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._pause_event.clear)
        else:
            self._pause_event.clear()

    def resume_parsing(self) -> None:
        self.is_paused = False
        logger.info("Parsing resumed")
        if self._pause_event is None:
            return
        # Schedule _pause_event.set() in the correct event loop thread
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._pause_event.set)
        else:
            self._pause_event.set()

    def _drain_queues(self) -> None:
        """Drain asyncio queues. Must run in the event loop thread."""
        try:
            while not self.download_queue.empty():
                self.download_queue.get_nowait()
                self.download_queue.task_done()
        except Exception:
            pass
        try:
            while not self.quarantine_queue.empty():
                self.quarantine_queue.get_nowait()
                self.quarantine_queue.task_done()
        except Exception:
            pass
        if hasattr(self.url_queue, '_queue'):
            self.url_queue._queue.clear()
        if hasattr(self.url_queue, '_not_empty'):
            self.url_queue._not_empty.set()  # Wake blocked workers so they can exit

    def stop_parsing(self) -> None:
        logger.info("Attempting to stop parsing...")
        self.is_running = False
        # Thread-safe signaling to the event loop thread
        if self._stop_event is not None and self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._stop_event.set)
            self.loop.call_soon_threadsafe(self._pause_event.set)
            self.loop.call_soon_threadsafe(self._drain_queues)
        else:
            if self._stop_event is not None:
                self._stop_event.set()
            if self._pause_event is not None:
                self._pause_event.set()
            if self.download_queue is not None:
                self._drain_queues()
        logger.info("Parsing stop procedure initiated.")

    def _monitor_progress(self) -> None:
        while self.is_running and not self._stop_event.is_set():
            try:
                if self.is_paused and not self._pause_event.is_set(): time.sleep(0.2); continue
                total_found = self.stats["images_found"] + self.stats["videos_found"]
                total_proc = self.stats["files_downloaded"] + self.stats["files_skipped"]
                if total_found > 0: self.total_progress_updated.emit(int((total_proc / total_found) * 100))
                else: self.total_progress_updated.emit(0) 
                time.sleep(0.2) 
            except Exception as e: logger.error(f"Error in progress monitor: {str(e)}", exc_info=True); time.sleep(1)
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
        # Wait for in-flight tasks to finish (max 5 sec timeout to prevent deadlock)
        timeout = 0
        while self._active_tasks > 0 and timeout < 50:
            await asyncio.sleep(0.1)
            timeout += 1
        if self._active_tasks > 0:
            logger.warning(f"save_state: {self._active_tasks} in-flight tasks did not finish within 5s")

        url_queue_items = []
        if hasattr(self.url_queue, '_queue'):
            for item in self.url_queue._queue:
                if hasattr(item, 'url'):
                    url_queue_items.append(
                        (item.url, item.depth, item.source_url, item.context)
                    )
                elif isinstance(item, tuple) and len(item) >= 4:
                    url_queue_items.append(item)

        download_queue_items = []
        if hasattr(self.download_queue, '_queue'):
            download_queue_items = list(self.download_queue._queue)

        quarantine_queue_items = []
        if hasattr(self.quarantine_queue, '_queue'):
            quarantine_queue_items = list(self.quarantine_queue._queue)

        async with self._processed_lock:
            state = {
                "url_queue_items": url_queue_items,
                "download_queue_items": download_queue_items,
                "quarantine_queue_items": quarantine_queue_items,
                "processed_urls": list(self.processed_urls),
                "downloaded_files": list(self.downloaded_files),
                "stats": self.stats, "settings": self.settings, "start_url": self.start_url,
                "download_path": self.download_path,
                "domain_health": self.domain_health, "quarantined_domains": list(self.quarantined_domains)
            }

        logger.info(f"save_state: {len(url_queue_items)} URL items, {len(download_queue_items)} download items, {len(self.downloaded_files)} downloaded files")

        # Run pickle.dumps in a thread pool so it doesn't block the event loop.
        loop = asyncio.get_running_loop()
        pickled = await loop.run_in_executor(None, pickle.dumps, state)

        session_dir = os.path.join(task_download_path, K.SESSION_STATE_SUBDIR)
        if self.task_id:
            session_dir = os.path.join(session_dir, self.task_id)
        os.makedirs(session_dir, exist_ok=True)
        full_state_path = os.path.join(session_dir, K.SESSION_STATE_FILENAME)
        tmp_path = full_state_path + ".tmp"

        # Atomic write: write to temp file → fsync → rename
        # Prevents corruption if process is killed mid-write
        try:
            async with aiofiles.open(tmp_path, "wb") as f:
                await f.write(pickled)
                await f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, full_state_path)  # atomic on Windows/NTFS
            logger.info(f"Session state saved to {full_state_path}")
        except Exception as e:
            logger.error(f"Error saving state: {e}")
            # Cleanup temp file on failure
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    async def load_state(self, task_download_path: str) -> None:
        session_dir = os.path.join(task_download_path, K.SESSION_STATE_SUBDIR)
        if self.task_id:
            session_dir = os.path.join(session_dir, self.task_id)
        session_file_path = os.path.join(session_dir, K.SESSION_STATE_FILENAME)
        if not os.path.exists(session_file_path):
            logger.info(f"No session state file found at {session_file_path}. Starting fresh.")
            return

        try:
            async with aiofiles.open(session_file_path, "rb") as f: data = await f.read(); state = pickle.loads(data)
            
            # Restore sets FIRST so we can filter download_queue
            self.processed_urls = set(state.get("processed_urls", []))
            self.downloaded_files = set(state.get("downloaded_files", []))
            self.stats = state.get("stats", self.stats)
            self.start_url = state.get("start_url", self.start_url)
            self.domain_health = state.get("domain_health", {})
            self.quarantined_domains = set(state.get("quarantined_domains", []))

            for item_tuple in state.get("url_queue_items", []):
                if len(item_tuple) == 4:
                     # IMPORTANT: Use bypass_checks=True during restoration to ensure
                     # all previously valid URLs are re-queued correctly.
                     await self.url_queue.put(item_tuple[0], item_tuple[1], item_tuple[2], item_tuple[3], bypass_checks=True)

            # Restore download_queue, but skip items whose URL was already downloaded.
            # This prevents re-downloading files that completed before the previous pause.
            skipped_downloads = 0
            for item in state.get("download_queue_items", []):
                if item.get("url") not in self.downloaded_files:
                    await self.download_queue.put(item)
                else:
                    skipped_downloads += 1
            if skipped_downloads:
                logger.info(f"load_state: skipped {skipped_downloads} already-downloaded items from download_queue")

            for item in state.get("quarantine_queue_items", []):
                await self.quarantine_queue.put(item)

            logger.info(f"Successfully loaded state from {session_file_path}")
        except Exception as err:
            logger.error(f"Error loading state from {session_file_path}: {str(err)}", exc_info=True)
