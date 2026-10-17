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
import aiohttp
import threading
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
    format_proxy_url,
    registrable_domain,
    is_format_allowed,
    should_skip_crawl_url,
    # is_image_url, # Not used directly
    # is_video_url # Not used directly
)
from concurrent.futures import ThreadPoolExecutor

from src.parser.shared_session import AsyncClientManager
from src.parser import junk_filter
from src.app_paths import task_state_path
from src import constants as K # Import constants

logger = logging.getLogger(__name__)


class ParserManager(QObject):
    """Enhanced parser manager with async support"""

    total_progress_updated = Signal(int)
    current_progress_updated = Signal(int)
    parsing_finished = Signal()
    status_updated = Signal(str)
    # GUI-2: (task_id, reason) — the queue/GUI must be able to tell a late
    # signal from a previous task apart from the current one.
    task_ended = Signal(str, str)  # (task_id, "completed"|"stopped"|"failed")

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
        # P4: chunked synchronisation of excavation with download throughput.
        # `backlog_hwm` = pause starting new pages at this many queued downloads,
        # `backlog_lwm` = resume once it has drained to this many. 0 = gate off.
        self.backlog_hwm = self._read_int_setting(K.SETTING_CRAWL_BACKLOG_HWM, K.DEFAULT_CRAWL_BACKLOG_HWM)
        self.backlog_lwm = self._read_int_setting(K.SETTING_CRAWL_BACKLOG_LWM, K.DEFAULT_CRAWL_BACKLOG_LWM)
        if self.backlog_hwm > 0 and self.backlog_lwm >= self.backlog_hwm:
            # A low watermark at/above the high one would make the gate release
            # itself the instant it engages (resume <= pause), i.e. no throttling
            # at all. Fall back to a fifth of the high watermark.
            logger.warning(
                f"crawl_backlog_lwm ({self.backlog_lwm}) >= crawl_backlog_hwm "
                f"({self.backlog_hwm}) — using {max(1, self.backlog_hwm // 5)} as the resume level"
            )
            self.backlog_lwm = max(1, self.backlog_hwm // 5)
        self.backlog_pauses = 0            # how many times excavation was gated
        self.backlog_pause_seconds = 0.0   # total time spent gated (stats/logs)
        
        self.domain_health = {}
        self.quarantined_domains = set()
        self.quarantine_queue = None  # Created in start_parsing on the correct loop
        
        self.pattern_manager = None
        if self.settings.get(K.SETTING_USE_PATTERNS, K.DEFAULT_USE_PATTERNS):
            custom_pattern_path = self.settings.get(K.SETTING_CUSTOM_PATTERN_PATH, K.DEFAULT_SETTINGS_VALUES[K.SETTING_CUSTOM_PATTERN_PATH])
            imagus_sieve_path = self.settings.get(K.SETTING_IMAGUS_SIEVE_PATH, K.DEFAULT_SETTINGS_VALUES[K.SETTING_IMAGUS_SIEVE_PATH])
            js_engine = None
            if self.settings.get(K.SETTING_JS_ENGINE, "static") == "deno":
                try:
                    from src.parser.js_engine import DenoJsEngine
                    js_engine = DenoJsEngine()
                    if js_engine.available():
                        logger.info(f"Deno JS engine enabled (bin: {js_engine.bin_path()})")
                    else:
                        logger.warning(
                            "Deno JS engine requested but no deno binary/worker found — "
                            "falling back to static pattern parsing"
                        )
                        js_engine = None
                except Exception as e:
                    logger.warning(f"Failed to init Deno JS engine: {e}")
                    js_engine = None
            self.pattern_manager = SitePatternManager(
                enable_built_in=True, 
                custom_pattern_path=custom_pattern_path,
                imagus_sieve_path=imagus_sieve_path,
                js_engine=js_engine
            )
            logger.info("Using SitePatternManager for pattern transformations")

        self.url_queue = PriorityURLQueue(settings=self.settings)
        self.download_queue = None  # Created in start_parsing on the correct loop
        self.processed_urls = set()
        self.downloaded_files = set() # Stores URLs of media marked for download to avoid re-processing
        # F4 crawler fix: source pages that produced at least one successful
        # download. page_limit counts THESE, not raw file counts — a 50-file
        # gallery is one source page out of the configured allowance.
        self._pages_with_downloads = set()

        self.stats = {
            "pages_processed": 0, "images_found": 0, "videos_found": 0,
            "files_downloaded": 0, "files_skipped": 0,
        }
        # P4: per-domain consent cookies learned from successful gateway
        # bypasses. Injected into every page of that domain for the rest of
        # the run — a gateway is bypassed once per domain, not per page.
        self._consent_cookies: Dict[str, Dict[str, str]] = {}
        self._completed_naturally = False  # True only when parsing finished by itself, not user Stop
        self._had_critical_error = False
        self._last_activity_time: float = time.time()  # Updated on each parsed page/download for idle detection
        self._active_tasks = 0
        self._active_tasks_lock = threading.Lock()
        # A-03: items taken out of a queue but not finished yet. They live in no
        # queue's own storage, so a snapshot without them silently loses the
        # page or the download that was in flight when the user hit Pause.
        self._inflight_url_item = None
        self._inflight_media = None
        # B-02/A-16: created in _main_task (the event-loop thread) and always
        # present so callers can pass them to run_in_executor without a
        # getattr dance; None means "use the default executor" (tests, scripts).
        self._dl_executor = None
        self._html_executor = None
        self._js_executor = None
        
        self.loop = asyncio.new_event_loop()
        # DL-7: start_url is passed so browser-extension cookies are scoped to the
        # start domain instead of leaking to every crawled host.
        self.async_client_manager: AsyncClientManager = AsyncClientManager(self.settings, start_url=self.start_url)
        self._shared_downloader_session = None  # Created in _main_task, closed in _main_task.finally
        
        self.parser_tasks = []
        self.downloader_tasks = []
        self.blocked_domains: Set[str] = self._load_domain_blocklist()

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
                            # A-04: store the entry lowercased/normalized plus its
                            # registrable form, so "www.example.com" in the file
                            # also blocks "example.com" and the other way round.
                            # IPs keep their literal form (deriving a base domain
                            # from "1.2.3.4" would yield the nonsense "3.4").
                            entry = domain.strip().lower().lstrip("*.").rstrip(".")
                            if not entry:
                                continue
                            blocked_domains.add(entry)
                            if not all(part.isdigit() for part in entry.split(".")):
                                base = registrable_domain(entry)
                                if base:
                                    blocked_domains.add(base)
                logger.info(f"Loaded {len(blocked_domains)} domains into the blocklist from {path_to_load}.")
            except Exception as e:
                logger.error(f"Error loading domain blocklist from {path_to_load}: {e}", exc_info=True)
        else:
            logger.warning(
                f"Domain blocklist file '{blocklist_file_name}' not found in any expected location. "
                "Proceeding with an empty blocklist."
            )
        return blocked_domains

    def _is_domain_blocked(self, host: str) -> bool:
        """A-04: is this host blocklisted, subdomains included?

        The old check compared the raw netloc against the file entries, so a
        blocklist entry "example.com" never matched "img.example.com" (and a
        host with a port matched nothing at all). Suffix matching on the
        registrable-name boundary keeps "notexample.com" out.
        """
        if not host:
            return False
        bare = host.strip().lower()
        if "@" in bare:  # strip credentials
            bare = bare.split("@")[-1]
        if bare.count(":") == 1:  # strip a port, but not an IPv6 literal
            bare = bare.split(":")[0]
        bare = bare.rstrip(".")
        if not bare:
            return False
        if bare in self.blocked_domains:
            return True
        return any(bare.endswith("." + entry) for entry in self.blocked_domains if entry)

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
        # threading.Event for cross-thread signaling (download threads, progress monitor)
        self._thread_stop_event = threading.Event()
        self.url_queue = PriorityURLQueue(settings=self.settings)
        self.download_queue = asyncio.Queue()
        self.quarantine_queue = asyncio.Queue()
        self._active_tasks = 0
        self._active_tasks_lock = threading.Lock()
        # A-03: items taken out of a queue but not finished yet. They live in no
        # queue's own storage, so a snapshot without them silently loses the
        # page or the download that was in flight when the user hit Pause.
        self._inflight_url_item = None
        self._inflight_media = None
        # B-02/A-16: created in _main_task (the event-loop thread) and always
        # present so callers can pass them to run_in_executor without a
        # getattr dance; None means "use the default executor" (tests, scripts).
        self._dl_executor = None
        self._html_executor = None
        self._js_executor = None
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

        # CORE-11: the pre-worker section (state load, seeding, shared session
        # creation) must also emit task_ended on failure. Previously any
        # exception here escaped _main_task without emitting anything, so the
        # GUI kept showing a zombie "running" task forever. The shared session
        # is created as the LAST step of this block: if it succeeded, nothing
        # after it within the block can fail, so the early-return path never
        # leaks an open session (and a raised create has nothing to close).
        try:
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

            # B-02/A-16: dedicated executors instead of the default pool. A
            # download lives for the whole file transfer and used to occupy a
            # slot in the pool that BeautifulSoup and the sync fallbacks also
            # use: with downloader_threads=8 + parser_threads=4 against a
            # default pool of min(32, cpu+4)=8, the configured thread counts and
            # the per-domain semaphore were fiction. The JS pool is separate
            # again, because a hanging sieve rule must not consume the slots
            # that downloads and HTML parsing need (and vice versa).
            self._dl_executor = ThreadPoolExecutor(
                max_workers=max(1, int(self.settings.get(
                    K.SETTING_DOWNLOADER_THREADS, K.DEFAULT_DOWNLOADER_THREADS))),
                thread_name_prefix="media_dl",
            )
            self._html_executor = ThreadPoolExecutor(
                max_workers=max(1, int(self.settings.get(
                    K.SETTING_PARSER_THREADS, K.DEFAULT_PARSER_THREADS))),
                thread_name_prefix="html_parse",
            )
            self._js_executor = ThreadPoolExecutor(
                max_workers=K.JS_ENGINE_POOL_WORKERS, thread_name_prefix="js_engine",
            )
        except Exception as e:
            logger.error(f"Critical error before parser workers started: {e}", exc_info=True)
            self._had_critical_error = True
            self.task_ended.emit(self.task_id or "", "failed")
            # MONITOR-LEAK: this early-return path bypasses the finally of the
            # main try below, so stop the progress monitor thread here too —
            # otherwise it loops forever after a pre-worker crash.
            self.is_running = False
            thread_stop = getattr(self, "_thread_stop_event", None)
            if thread_stop is not None:
                thread_stop.set()
            return
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
            # B-02/A-16: retire the dedicated pools. `wait=False` keeps a stuck
            # JS rule from holding the task's teardown (the rule's worker is
            # killed separately).
            for _executor in (getattr(self, "_dl_executor", None),
                              getattr(self, "_html_executor", None),
                              getattr(self, "_js_executor", None)):
                if _executor is not None:
                    try:
                        _executor.shutdown(wait=False)
                    except Exception as e:
                        logger.debug(f"Executor shutdown failed: {e}")
            self._dl_executor = None
            self._html_executor = None
            self._js_executor = None
            # MONITOR-LEAK: stop the progress monitor thread even on NATURAL
            # completion or an early crash — stop_parsing() is the only other
            # place that clears these, so without this the daemon monitor
            # looped forever emitting progress signals on a finished task.
            self.is_running = False
            thread_stop = getattr(self, "_thread_stop_event", None)
            if thread_stop is not None:
                thread_stop.set()
            # Shut down the Deno JS worker (if any) — terminates the subprocess.
            if self.pattern_manager is not None and self.pattern_manager.js_engine is not None:
                try:
                    self.pattern_manager.js_engine.shutdown()
                except Exception as e:
                    logger.error(f"Error shutting down Deno JS engine: {e}")
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
                self.task_ended.emit(self.task_id or "", "failed")
            elif self._completed_naturally:
                self.task_ended.emit(self.task_id or "", "completed")
            else:
                self.task_ended.emit(self.task_id or "", "stopped")

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

    async def _await_download_capacity(self) -> None:
        """P4: hold off excavation while the download backlog is full.

        Excavating a page is what creates MORE work (links to follow, media to
        download), while downloads drain at a fixed rate — measured on reactor.cc
        and on the fixture: media are found 2-6x faster than they are downloaded,
        so the backlog, the requests behind it and RSS all grew monotonically for
        as long as the run lasted. This gate chunks the two: above HWM the parser
        stops *starting* pages until the downloaders have drained the backlog to
        LWM.

        Deliberately gated on the DOWNLOAD queue, never on the URL frontier:
        pending URLs only shrink when a parser worker pops one, and those workers
        are exactly what a frontier watermark would suspend — the crawl would sit
        frozen with nothing able to reduce the count. Downloader workers drain the
        download queue on their own, so this gate always releases.
        """
        hwm = self.backlog_hwm
        if hwm <= 0 or self.download_queue is None:
            return
        if self.download_queue.qsize() < hwm:
            return

        logger.info(
            f"Download backlog {self.download_queue.qsize()} >= {hwm}: pausing page "
            f"excavation until it drains to {self.backlog_lwm}"
        )
        self.backlog_pauses += 1
        waited = 0.0
        last_depth = self.download_queue.qsize()
        unchanged_for = 0.0
        while not self._stop_event.is_set():
            await asyncio.sleep(K.CRAWL_BACKLOG_POLL_SECONDS)
            waited += K.CRAWL_BACKLOG_POLL_SECONDS
            depth = self.download_queue.qsize()
            if depth != last_depth:
                last_depth = depth
                unchanged_for = 0.0
            else:
                unchanged_for += K.CRAWL_BACKLOG_POLL_SECONDS
            if depth <= self.backlog_lwm:
                self.backlog_pause_seconds += waited
                logger.info(
                    f"Download backlog drained to {depth}: resuming page excavation "
                    f"(paused {waited:.0f}s)"
                )
                return
            if unchanged_for >= K.CRAWL_BACKLOG_STALL_TIMEOUT:
                # Safety valve: a jammed downloader (hung connection, dead host)
                # must not freeze the crawl for the rest of the task.
                self.backlog_pause_seconds += waited
                logger.warning(
                    f"Download backlog stuck at {depth} for {unchanged_for:.0f}s — "
                    f"resuming excavation anyway (nothing to download might still be runnable)"
                )
                return
        self.backlog_pause_seconds += waited

    def _read_int_setting(self, key: str, default: int) -> int:
        """Tolerant int read for settings handed in as JSON (None/"abc" -> default)."""
        raw = self.settings.get(key, default)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning(f"Invalid integer setting {key}={raw!r} — using {default}")
            return default

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
            # CORE-1: JSONWebpageParser manages its session externally (the
            # AsyncClientManager passed as external_session) and deliberately
            # does NOT implement __aenter__/__aexit__ — `async with` raised
            # TypeError on every JSON-classified URL, silently killing the
            # JSON API parser in production (each URL was logged as an error
            # and no media was ever extracted). Call parse() directly.
            p = JSONWebpageParser(url=url, settings=self.settings, external_session=session)
            links_found, media_files_found = await p.parse()
        else:
            logger.debug(f"Using WebpageParser for {url}")
            # P4: inject per-domain consent cookies learned from earlier
            # gateway bypasses so a gate is crossed once per domain, not per
            # page. Copy the context dict (queue items are shared).
            parser_context = dict(context or {})
            consent = self._consent_cookies.get(get_domain(url))
            if consent:
                parser_context["consent_cookies"] = consent
            # A-3: also mirror the source page into settings via a copy so the
            # Referer logic (which reads `_source_url`) works even for code
            # paths that only look at settings, never the context.
            settings_for_parser = dict(self.settings)
            src_page = parser_context.get("source_url")
            if src_page:
                settings_for_parser["_source_url"] = src_page
            p = WebpageParser(
                url=url, settings=settings_for_parser,
                process_js=self.settings.get(K.SETTING_PROCESS_JS, K.DEFAULT_PROCESS_JS),
                external_session=session, pattern_manager=self.pattern_manager,
                context=parser_context,
                # A-16/B-02: the blocking work this parser does (JS rules,
                # BeautifulSoup, sync fallbacks) runs in the dedicated pools.
                js_executor=self._js_executor, html_executor=self._html_executor,
            )
            parse_result = await p.parse()
            links_found = parse_result[0]
            media_files_found = parse_result[1]
            
            # Extract and sync cookies to the shared downloader session (domain-scoped).
            # The shared requests.Session is used concurrently by downloader threads;
            # guard cookie-jar writes with the session lock attached at creation.
            cookies = parse_result[5]
            if cookies:
                # P4: remember them per-domain for subsequent pages of this site
                self._consent_cookies.setdefault(get_domain(url), {}).update(cookies)
            if cookies and self._shared_downloader_session:
                logger.debug(f"Syncing {len(cookies)} cookies from parser to shared downloader session")
                domain = urlparse(url).hostname
                cookie_lock = getattr(self._shared_downloader_session, "_cookie_lock", None)
                if cookie_lock:
                    cookie_lock.acquire()
                try:
                    for name, value in cookies.items():
                        self._shared_downloader_session.cookies.set(name, value, domain=domain, path="/")
                finally:
                    if cookie_lock:
                        cookie_lock.release()

            if parse_result[2] != K.PARSER_SUCCESS:
                logger.error(f"Error parsing {url}: {parse_result[3]}")
        return links_found, media_files_found

    async def _discover_linked_fullsize(self, links, page_url, session):
        """Sieve link->url->res fullsize discovery for thumbnail-transition links.

        Mirrors the extension's discoverFullsize: for each from_image link that
        matches a sieve `link` rule, apply the rule's `url` template (string
        transform; content after " :" is POSTed form data, e.g. imx.to
        'imgContinue='), fetch the page, and extract fullsize URLs via the
        rule's `res` regex(es) with an <img src> fallback.

        Returns (discovered_media, resolved_thumbnails, consumed_links):
          - discovered_media: (media_type, url, attrs) items for the download queue
          - resolved_thumbnails: thumbnail URLs whose fullsize was found
            (drop them from the page's media batch)
          - consumed_links: transition links handled here (do NOT queue them
            for crawling — they are lookups, not content)
        """
        if self.pattern_manager is None or not links:
            return [], set(), set()
        discovered = []
        resolved_thumbnails = set()
        consumed = set()
        sem = asyncio.Semaphore(K.FULLSIZE_DISCOVER_CONCURRENCY)
        timeout = aiohttp.ClientTimeout(total=K.FULLSIZE_DISCOVER_TIMEOUT)
        proxy_url = format_proxy_url(self.settings.get(K.SETTING_PROXY))
        # Resource guard, not a content cap: all from_image links are probed
        # (a gallery with 500 thumbnails resolves all 500), but a page whose
        # hosts are dead/slow cannot hold the worker forever.
        deadline = time.monotonic() + K.FULLSIZE_DISCOVER_TIME_BUDGET

        async def probe(link_url, ctx):
            rule_match = self.pattern_manager.get_link_rule(link_url)
            if not rule_match:
                return  # No sieve rule for this host — leave to the normal crawl path
            rule, match = rule_match
            link_groups = [match.group(0)]
            link_groups += [match.group(i) for i in range(1, match.re.groups + 1)]
            # A-16: this call can execute a sieve JS rule in the Deno worker and
            # blocks up to the rule timeout; on the event loop it froze the
            # completion monitor, the progress signals and Stop itself.
            probe_loop = asyncio.get_running_loop()
            transformed = await probe_loop.run_in_executor(
                self._js_executor,
                lambda: self.pattern_manager.apply_link_url_transform(rule, match, page_url),
            )

            def _res_usable():
                # A res field is usable if it's a plain regex OR a JS
                # expression and the DOM engine is available (P1).
                res_field = rule.get('res')
                pats = res_field if isinstance(res_field, list) else [res_field]
                for p in pats:
                    if not isinstance(p, str) or not p.strip():
                        continue
                    if not p.lstrip().startswith(':'):
                        return True
                    if (self.pattern_manager.js_engine is not None
                            and self.pattern_manager.js_engine.dom_available()):
                        return True
                return False

            if not transformed and not _res_usable():
                # Catch-all rules with unusable JS url+res only (e.g.
                # [MediaGrabber]) match every URL but offer no usable lookup —
                # leave the link to the normal crawl path instead of
                # consuming it blindly.
                return
            consumed.add(link_url)
            # A-5/BUG-5 note: only the ORIGINAL link_url is ever added to
            # `consumed` (intermediate loop hops are transient fetch targets,
            # not crawled links), so the exception handler below releasing
            # exactly link_url is correct for every hop.
            original_link_url = link_url
            if transformed:
                fetch_url, post_data = transformed
            else:
                # No url transform, but the rule has a usable res: probe the
                # linked page itself (extension behaviour).
                fetch_url, post_data = link_url, None
            thumb = ctx.get("thumbnail_url")
            try:
                # C-2 (loop): Mod semantics (src/includes/content.js:1481/4478) —
                # a rule with `loop & 1` whose res yields a single URL matching
                # another sieve rule re-enters the engine with that URL (recurs-
                # ive re-resolution), up to SIEVE_LOOP_MAX_HOPS, self-reference
                # aborts. Implemented as a bounded chain: each iteration fetches
                # the current hop's page; if the result is exactly one URL that
                # matches another link-rule (and the current rule allows loop),
                # the chain continues; otherwise all accumulated URLs are media.
                seen_fetch_urls = {fetch_url}
                loop_guard = 0
                while True:
                    # ERR-1 (desktop twin): the viewer/fullsize page is fetched
                    # on behalf of a thumbnail on `page_url` — send it as
                    # Referer, else hotlink-protected hosts 302 the probe to an
                    # HTML page and fullsize discovery silently degrades
                    # (mirrors extension discoverFullsize.processLink).
                    headers = {"Accept": "text/html", "Referer": page_url}
                    if post_data:
                        headers["Content-Type"] = "application/x-www-form-urlencoded"

                    async def _fetch():
                        if post_data:
                            return await session.post(fetch_url, data=post_data, headers=headers,
                                                      timeout=timeout, proxy=proxy_url)
                        return await session.get(fetch_url, headers=headers,
                                                 timeout=timeout, proxy=proxy_url)

                    # CORE-2: `async with` guarantees the pooled connection is
                    # released even on the early returns below (image/video content,
                    # non-HTML) — without it the unread body pinned the connection
                    # until the server's keepalive timeout.
                    async with await _fetch() as resp:
                        content_type = resp.headers.get("Content-Type", "") or ""
                        if "image/" in content_type or "video/" in content_type:
                            final_url = str(resp.url)
                            media_type = "video" if "video/" in content_type else "image"
                            discovered.append((media_type, final_url, {"source": "link-direct"}))
                            if thumb:
                                resolved_thumbnails.add(thumb)
                            return
                        if "text/html" not in content_type:
                            return
                        html = await resp.text()

                    res_urls = [u for u in await probe_loop.run_in_executor(
                        self._js_executor,
                        lambda: self.pattern_manager.extract_res_urls(
                            rule, html, page_url=page_url, groups=link_groups, href=link_url))
                        if is_format_allowed(u, "image", self.settings)]

                    # Loop continuation check: rule opted in via `loop & 1`,
                    # exactly ONE res-URL, that URL matches another rule, and we
                    # have hops/time left and haven't seen this fetch before.
                    next_link = None
                    if (res_urls and len(res_urls) == 1
                            and rule.get('loop') and (rule.get('loop') & 1)
                            and loop_guard < K.SIEVE_LOOP_MAX_HOPS
                            and time.monotonic() < deadline):
                        candidate = res_urls[0]
                        cand_match = self.pattern_manager.get_link_rule(candidate)
                        if cand_match is not None and candidate not in seen_fetch_urls:
                            cand_rule, cand_m = cand_match
                            cand_transformed = await probe_loop.run_in_executor(
                                self._js_executor,
                                lambda: self.pattern_manager.apply_link_url_transform(
                                    cand_rule, cand_m, page_url),
                            )
                            cand_fetch = cand_transformed[0] if cand_transformed else candidate
                            if cand_fetch not in seen_fetch_urls:
                                # Re-resolve: promote candidate to the next hop.
                                next_link = candidate
                                seen_fetch_urls.add(cand_fetch)
                                rule = cand_rule
                                match = cand_m
                                link_groups = [cand_m.group(0)]
                                link_groups += [cand_m.group(i) for i in range(1, cand_m.re.groups + 1)]
                                link_url = candidate
                                transformed = cand_transformed
                                if transformed:
                                    fetch_url, post_data = transformed
                                else:
                                    fetch_url, post_data = candidate, None
                                loop_guard += 1

                    if next_link is None:
                        # Chain ended — everything found on this hop is media.
                        for u in res_urls:
                            discovered.append(("image", u, {"source": "sieve-res", "thumbnail_url": thumb}))
                            if thumb:
                                resolved_thumbnails.add(thumb)
                        break
            except Exception as e:
                logger.debug(f"Fullsize discovery failed for {link_url}: {e}")
                # CORE-20: the linked page was NOT resolved — release it back
                # to the normal crawl path instead of silently discarding it.
                # consumed.add() happens before the fetch; without the discard,
                # a transient network error excluded the viewer page from the
                # crawl and its remaining content was lost.
                # BUG-5: release the ORIGINAL link (link_url mutates across
                # loop hops) so a mid-chain failure never loses the source.
                consumed.discard(original_link_url)

        async def limited(link_url, ctx):
            async with sem:
                if time.monotonic() >= deadline:
                    # Budget exhausted — remaining links fall back to their
                    # thumbnails; nothing is lost, just not upgraded.
                    logger.debug("Fullsize discovery time budget exhausted; remaining links fall back to thumbnails")
                    return
                await probe(link_url, ctx)

        await asyncio.gather(*[limited(u, c) for u, c in links], return_exceptions=True)
        if discovered:
            logger.info(f"Fullsize discovery: {len(discovered)} media from {len(links)} linked pages on {page_url}")
        return discovered, resolved_thumbnails, consumed

    async def _process_parser_results(self, url: str, depth: int, 
                                      links_data: Any, media_files: List[Tuple[str, str, Dict[str, Any]]],
                                      original_url_context: Dict[str, Any], session=None):
        # Fullsize discovery: resolve thumbnail-transition links (from_image)
        # via the sieve link->url->res chain BEFORE queueing media, so external
        # image hosts (imx.to etc.) yield fullsize originals instead of being
        # dropped by stay-in-domain checks. Resolved thumbnails are removed
        # from the media batch (fullsize wins); consumed links are not crawled.
        consumed_links = set()
        if (self.pattern_manager is not None and not self.one_shot
                and session is not None and isinstance(links_data, dict)):
            from_image_links = [
                (u, c) for u, c in links_data.items()
                if c.get("from_image") and u.startswith(("http://", "https://"))
                # Only external transitions (image-host viewer pages) go through
                # the sieve lookup. Same-domain thumbnail links keep the normal
                # crawl path: WebpageParser handles lazy/data-src galleries far
                # better than the res/img-scan fallback.
                and not is_same_domain(u, self.start_url)
            ]
            if from_image_links:
                discovered, resolved_thumbs, consumed_links = await self._discover_linked_fullsize(
                    from_image_links, url, session
                )
                if resolved_thumbs:
                    media_files = [m for m in media_files if m[1] not in resolved_thumbs]
                if discovered:
                    media_files = list(media_files) + discovered

        await self._process_media_files(media_files, url)
        await self._update_queue_priorities(url, media_files)

        # In one-shot mode, skip link discovery — only process media from this page
        if self.one_shot:
            self.stats["pages_processed"] += 1
            self._last_activity_time = time.time()
            return

        # Depth gate: excavation (following page links to find new content)
        # stops at max_depth (or continues indefinitely if max_depth < 0, e.g. -1).
        # Media lookups (direct media URLs + from_image thumbnail→fullsize
        # transitions) are never depth-limited — the same exemption philosophy
        # as stay-in-domain: a gallery reached deep must still yield its viewer
        # pages and fullsize images instead of dying as a silent dead end.
        can_follow_excavation = (self.max_depth < 0) or (depth < self.max_depth)
        urls_to_queue = []
        if isinstance(links_data, dict): # From WebpageParser
            for disc_url, link_ctx in links_data.items(): urls_to_queue.append((disc_url, link_ctx))
        elif isinstance(links_data, set): # From JSONWebpageParser
            for disc_url in links_data: urls_to_queue.append((disc_url, {}))

        # Cap per-page links — "excavation" only. from_image links are
        # media lookups (thumbnail->fullsize transitions) and are never
        # content-capped: a gallery with >max_links thumbnails must not
        # lose any of them. Unconsumed lookups (no sieve rule) are still
        # bounded downstream by the queue's pathological-URL guards,
        # processed_urls dedup, search depth and the page limit.
        max_links = self.settings.get("max_links_per_page", K.DEFAULT_MAX_LINKS_PER_PAGE)
        if max_links > 0:
            excavation = [(u, c) for u, c in urls_to_queue if not c.get("from_image")]
            lookups = [(u, c) for u, c in urls_to_queue if c.get("from_image")]
            if len(excavation) > max_links:
                excavation.sort(
                    key=lambda x: float(x[1].get("priority", 0)),
                    reverse=True
                )
                excavation = excavation[:max_links]
                logger.debug(f"Capped excavation links per page to {max_links} (had {len(excavation) + len(lookups)} total; {len(lookups)} media lookups kept)")
            urls_to_queue = excavation + lookups

        for disc_url_str, link_spec_ctx in urls_to_queue:
            if disc_url_str in consumed_links:
                continue
            abs_disc_url = disc_url_str
            if not abs_disc_url.startswith(("http://", "https://")):
                abs_disc_url = urljoin(url, abs_disc_url)
            
            disc_domain = get_domain(abs_disc_url)
            if self._is_domain_blocked(disc_domain):
                logger.debug(f"Skipping blocked domain for URL {abs_disc_url} (Domain: {disc_domain})")
                continue
            
            # stay-in-domain restricts "excavation" (following page links to
            # find new content) only. Media URLs and thumbnail transitions
            # (from_image) are media lookups and are never domain-restricted.
            is_media_lookup = link_spec_ctx.get("from_image") or is_media_url(abs_disc_url)
            if self.settings.get(K.SETTING_STAY_IN_DOMAIN, K.DEFAULT_STAY_IN_DOMAIN) and \
               not is_media_lookup and not is_same_domain(abs_disc_url, self.start_url): 
                logger.debug(f"Skipping out-of-domain link: {abs_disc_url} (Original start: {self.start_url})")
                continue
            
            stop_words_list = self.settings.get(K.SETTING_STOP_WORDS, K.DEFAULT_STOP_WORDS)
            if should_skip_crawl_url(abs_disc_url, stop_words_list):
                logger.debug(f"Skipping non-content link: {abs_disc_url}")
                continue

            # Depth gate: excavation stops at max_depth; media lookups
            # (direct media URLs + from_image transitions) continue — they
            # are lookups, not content excavation.
            if not can_follow_excavation and not is_media_lookup:
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
            # Page limit: stop after N source pages produced at least one
            # successful download (matches the settings tooltip — a 50-file
            # gallery counts as ONE source page, not 50 files).
            if self.page_limit > 0 and len(self._pages_with_downloads) >= self.page_limit:
                logger.info(f"Page limit reached: {len(self._pages_with_downloads)} source pages with downloads >= {self.page_limit}")
                self._completed_naturally = True
                self._stop_event.set()
                break
            continue_processing = await self._handle_empty_queues_and_quarantine()
            if not continue_processing:
                # All work is done, but keep the worker alive until stop is requested
                await asyncio.sleep(1.0)
                continue
            # P4: don't excavate new pages while the downloader is behind.
            await self._await_download_capacity()
            if self._stop_event.is_set():
                break
            url_data = await self._get_next_url_to_parse()
            if not url_data:
                await asyncio.sleep(0.1) 
                continue
            
            with self._active_tasks_lock:
                self._active_tasks += 1

            current_url, depth, source_page_url, context = url_data
            self._inflight_url_item = url_data  # A-03
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
                finally:
                    if domain:
                        sem.release()
                # Fullsize discovery + media queueing probe OTHER domains (image
                # hosts) — intentionally outside the source-domain semaphore so
                # a page with many linked pages cannot stall parsing of the
                # source domain.
                await self._process_parser_results(current_url, depth, links_found, media_files_found, context, session)
            except Exception as e:
                logger.error(f"Error processing URL {current_url}: {str(e)}", exc_info=True)
                # CORE-10: count a failed page as processed so the completion
                # monitor (which requires pages_processed > 0) can still
                # trigger natural finish. Without this, an exception on the
                # only seeded URL left the task hanging forever — queues
                # empty, pages_processed == 0, monitor kept skipping.
                if current_url not in self.processed_urls:
                    self.processed_urls.add(current_url)
                self.stats["pages_processed"] += 1
                self._last_activity_time = time.time()
            finally:
                self._inflight_url_item = None  # A-03
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
                # P2-lite final gate: drop ad/tracker media URLs that slipped
                # past the HTML-level checks (e.g. sieve fullsize discovery
                # results pointing at ad CDNs). Allowlist-aware; the item is
                # skipped before stats are touched so progress stays correct.
                if (self.settings.get(K.SETTING_FILTER_JUNK, True)
                        and junk_filter.is_ad_url(abs_url, source_url)):
                    logger.debug(f"Filtered ad/junk media URL: {abs_url}")
                    continue
                async with self._processed_lock:
                    if abs_url in self.downloaded_files: continue 
                    self.downloaded_files.add(abs_url) 
                    
                if is_webpage_url(abs_url) and not is_media_url(abs_url):
                    logger.debug(f"Treating as webpage rather than media: {abs_url}")
                    assumed_depth_for_media_webpage = 1 
                    if self.max_depth < 0 or assumed_depth_for_media_webpage < self.max_depth:
                        ctx = {"source_url": source_url, "start_url": self.start_url, "from_media_item": True, "media_context": attrs, "priority": 5.0}
                        await self.url_queue.put(abs_url, assumed_depth_for_media_webpage, self.start_url, ctx)
                    continue
                
                base_filename = self._get_filename_from_url(abs_url, media_type)
                target_dir_path_final = self.download_path 
                page_domain_for_subdir = get_domain(source_url)
                
                # B-08: '.'/'..' as a host cannot form a directory name.
                if page_domain_for_subdir and page_domain_for_subdir not in (".", ".."):
                    sane_domain = re.sub(r'[<>:"\/\\|?*]', '_', page_domain_for_subdir)
                    parsed_source_page = urlparse(source_url)
                    source_page_path_components = parsed_source_page.path.strip('/').split('/')
                    # B-08: drop '.' and '..' here — the sanitizer below strips path
                    # separators but kept the dots, and urlparse does not normalise dot
                    # segments, so a page URL could build a subdirectory above the root.
                    sane_path_parts = [
                        re.sub(r'[<>:"\/\\|?*]', '_', part)[:K.MAX_SUBDIR_COMPONENT_LENGTH]
                        for part in source_page_path_components
                        if part and part not in (".", "..")
                    ][:K.MAX_PATH_COMPONENTS_FOR_SUBDIR]
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
            self._inflight_media = media_item  # A-03

            try:
                # Check before expensive download
                if not self.is_running:
                    break  # finally block handles task_done()
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
                        stop_event=self._thread_stop_event
                    )
                    downloader.set_progress_callback(self._update_current_progress)
                    base_filename_for_status = os.path.basename(filepath_from_queue)
                    self.status_updated.emit(f"Downloading: {base_filename_for_status}")
                    
                    result = await asyncio.get_event_loop().run_in_executor(
                        self._dl_executor,
                        lambda: downloader.download(timeout=timeout_val, retries=retries_val)
                    )
                finally:
                    sem.release()
                
                domain_state["total"] += 1
                self._last_activity_time = time.time()  # Track last activity for idle detection
                if result["success"]:
                    self.stats["files_downloaded"] += 1
                    src = media_item.get("source_url")
                    if src:
                        self._pages_with_downloads.add(src)
                    if domain_state["failures"] > 0: domain_state["failures"] = max(0, domain_state["failures"] - 1)
                else:
                    self.stats["files_skipped"] += 1
                    err_msg = str(result.get('error', ''))
                    logger.warning(f"Failed to download file: {url} - {err_msg}")
                    
                    # 1. Feedback Loop: If we expected media but got HTML, feed it back to WebpageParser (limit 1 attempt)
                    if result.get("failure_kind") == K.FAILURE_CONTENT_PAGE:
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
                    
                    # 2. Domain Health: Only penalize health for genuine network/server errors.
                    # R-C-5/R-C-6: decided by the downloader's machine-readable
                    # failure kind — the old substring list both punished local
                    # faults (disk, size, user abort) and listed messages that no
                    # code path ever produced. Fail-open: an untyped failure is
                    # never a domain fault.
                    is_content_skip = result.get("failure_kind") not in K.FAILURE_PENALISING
                    
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
                self._inflight_media = None  # A-03
                with self._active_tasks_lock:
                    self._active_tasks -= 1
                self.download_queue.task_done()
        logger.info(f"Downloader worker {threading.get_ident()} finished.")

    def _schedule_on_loop(self, callback) -> None:
        """Run `callback` on the event-loop thread; fall back to calling it
        directly when the loop is closed/unavailable.

        CORE-12: call_soon_threadsafe can raise RuntimeError if the loop closes
        between the is_closed() check and the call (race from a GUI thread) —
        never let that escape.
        """
        try:
            if self.loop and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(callback)
                return
        except RuntimeError:
            pass
        callback()

    def pause_parsing(self) -> None:
        self.is_paused = True
        logger.info("Parsing paused")
        if self._pause_event is None:
            return
        # Schedule _pause_event.clear() in the correct event loop thread
        self._schedule_on_loop(self._pause_event.clear)

    def resume_parsing(self) -> None:
        self.is_paused = False
        logger.info("Parsing resumed")
        if self._pause_event is None:
            return
        # Schedule _pause_event.set() in the correct event loop thread
        self._schedule_on_loop(self._pause_event.set)

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
        if self._stop_event is not None:
            self._schedule_on_loop(self._stop_event.set)
            self._schedule_on_loop(self._pause_event.set)
            if self.download_queue is not None:
                self._schedule_on_loop(self._drain_queues)
        # Signal cross-thread stop for download threads and progress monitor
        if self._thread_stop_event is not None:
            self._thread_stop_event.set()
        logger.info("Parsing stop procedure initiated.")

    def _monitor_progress(self) -> None:
        while self.is_running and not self._thread_stop_event.is_set():
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
        # R-P-2: a query/tracking tail survived sanitising inside the extension
        # ("photo.jpeg?w=100" -> ".jpeg_w"), so files were written with an
        # unusable extension. Recover the real one from the known lists instead.
        known_extensions = sorted(K.IMAGE_EXTENSIONS + K.VIDEO_EXTENSIONS + K.AUDIO_EXTENSIONS,
                                  key=len, reverse=True)
        ext_lower = ext_part.lower()
        known_match = next((e for e in known_extensions if ext_lower.startswith(e)), None)
        if known_match:
            ext_part = ext_part[:len(known_match)]
        elif len(ext_part) > 7:
            ext_part = ext_part[:7] 
        
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
        if "fullsize" in source_type or "original" in source_type or source_type in ("sieve-res", "link-direct"):
            priority *= 3.0
        elif "parent-link" in source_type: priority *= 2.5
        if any(p in url.lower() for p in ["/full/", "/large/", "/original/", "fullsize", "highres"]): priority *= 2.0
        if source_url == self.start_url: priority *= 3.0 
        if "dimensions" in attrs:
            w, h = attrs["dimensions"].get("width",0), attrs["dimensions"].get("height",0)
            if w > 0 and h > 0: priority *= min(1.0 + ((w * h) / 1000000), 3.0) 
        # Soft thumbnail hint (WP-2.1): deprioritize, never drop — the fullsize
        # transform or the parent-link page should be preferred when available.
        if attrs.get("likely_thumbnail"):
            priority *= 0.5
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
                "pages_with_downloads": list(self._pages_with_downloads),
                # B-01: snapshot the two containers the workers keep mutating.
                # They used to be stored by reference and pickled later, in an
                # executor thread, after this lock was already released — a page
                # finishing in that window either raised "dictionary changed size
                # during iteration" (state not saved at all) or wrote a
                # half-applied snapshot.
                "stats": dict(self.stats),
                "settings": self.settings, "start_url": self.start_url,
                "download_path": self.download_path,
                "domain_health": {d: dict(v) if isinstance(v, dict) else v
                                  for d, v in self.domain_health.items()},
                "quarantined_domains": list(self.quarantined_domains),
                # A-03: the page/download that is being worked on right now.
                "inflight_url_item": self._inflight_url_item,
                "inflight_media": self._inflight_media,
            }

        logger.info(f"save_state: {len(url_queue_items)} URL items, {len(download_queue_items)} download items, {len(self.downloaded_files)} downloaded files")

        # Run pickle.dumps in a thread pool so it doesn't block the event loop.
        loop = asyncio.get_running_loop()
        pickled = await loop.run_in_executor(None, pickle.dumps, state)

        full_state_path = task_state_path(task_download_path, self.task_id)
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
        session_file_path = task_state_path(task_download_path, self.task_id)
        if not os.path.exists(session_file_path):
            logger.info(f"No session state file found at {session_file_path}. Starting fresh.")
            return

        try:
            async with aiofiles.open(session_file_path, "rb") as f: data = await f.read(); state = pickle.loads(data)
            
            # Restore sets FIRST so we can filter download_queue
            self.processed_urls = set(state.get("processed_urls", []))
            self.downloaded_files = set(state.get("downloaded_files", []))
            self._pages_with_downloads = set(state.get("pages_with_downloads", []))
            self.stats = state.get("stats", self.stats)
            self.start_url = state.get("start_url", self.start_url)
            self.domain_health = state.get("domain_health", {})
            self.quarantined_domains = set(state.get("quarantined_domains", []))

            for item_tuple in state.get("url_queue_items", []):
                if len(item_tuple) == 4:
                     # IMPORTANT: Use bypass_checks=True during restoration to ensure
                     # all previously valid URLs are re-queued correctly.
                     await self.url_queue.put(item_tuple[0], item_tuple[1], item_tuple[2], item_tuple[3], bypass_checks=True)

            # Restore ALL pending download_queue items.
            # downloaded_files is populated at enqueue time (not completion time),
            # so filtering by it would incorrectly drop items that were queued but never downloaded.
            restored = 0
            for item in state.get("download_queue_items", []):
                await self.download_queue.put(item)
                restored += 1
            if restored:
                logger.info(f"load_state: restored {restored} pending download items")

            for item in state.get("quarantine_queue_items", []):
                await self.quarantine_queue.put(item)

            # A-03: put the interrupted page/download back at the front of its
            # queue. They were already consumed from the queue, so restoring
            # them cannot duplicate anything.
            inflight_url = state.get("inflight_url_item")
            if inflight_url and len(inflight_url) == 4:
                await self.url_queue.put(
                    inflight_url[0], inflight_url[1], inflight_url[2], inflight_url[3],
                    bypass_checks=True,
                )
                logger.info(f"load_state: re-queued in-flight page {inflight_url[0]}")
            inflight_media = state.get("inflight_media")
            if inflight_media:
                await self.download_queue.put(inflight_media)
                logger.info(f"load_state: re-queued in-flight download {inflight_media.get('url')}")

            logger.info(f"Successfully loaded state from {session_file_path}")
        except Exception as err:
            logger.error(f"Error loading state from {session_file_path}: {str(err)}", exc_info=True)
