#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Media downloader class for downloading media files
"""

import os
import time
import threading
from urllib.parse import urlparse
import logging
import requests
from src.parser.utils import format_proxy_url, is_format_allowed
from src.parser import http_engine
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src import constants as K  # Import constants

logger = logging.getLogger(__name__)

# WRITE_BUFFER_SIZE is now in K.WRITE_BUFFER_SIZE

# Module-level lock to prevent race conditions when multiple downloaders
# simultaneously check and reserve unique filenames.
_filename_lock = threading.Lock()


def create_shared_downloader_session(settings: dict) -> requests.Session:
    """Create a single shared download session for all MediaDownloader instances.

    Honors the http_engine setting (P3): when "curl_cffi" (and curl_cffi is
    installed) the session impersonates a real browser TLS fingerprint
    (impersonate="chrome") — CDNs that block on JA3/JA4 then serve media
    instead of refusing. When "aiohttp" (default) a plain requests.Session is
    built exactly as before.

    One session enables TCP/TLS keep-alive connection reuse across file downloads.
    INTERNAL RETRIES ARE DISABLED (total=0) to ensure the application Stop button
    works immediately by preventing urllib3 from hanging in long retry loops.
    """
    if http_engine.engine_uses_curl(settings):
        session = http_engine.create_sync_session(settings)
        http_engine.attach_cookie_lock(session)
        # curl_cffi sets browser-consistent headers (UA/Sec-CH-UA/Accept) from
        # the impersonation profile — overriding User-Agent would defeat the
        # fingerprint. Proxy is applied as on the requests path.
        proxy_url = format_proxy_url(settings.get(K.SETTING_PROXY))
        if proxy_url:
            session.proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
            logger.info(f"Shared downloader session (curl_cffi) configured with proxy: {proxy_url}")
        logger.info(
            "Shared downloader session created (curl_cffi impersonate=%s, 0 internal retries).",
            http_engine.impersonate_profile(settings),
        )
        return session

    session = requests.Session()
    # Use standard cookie jar instead of _NullCookieJar to allow site-specific cookies (e.g. Age Verification)
    # Standard CookieJar handles domain scoping automatically.
    # Disable internal retries to allow immediate shutdown per worker check
    retry = Retry(
        total=0,
        connect=None,
        read=None,
        redirect=None,
        status=None
    )
    # Larger pool to support many concurrent download workers
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=50)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    # The session is shared across downloader threads while the parser thread
    # may write cookies to the jar. Guard cookie-jar writes with this lock.
    session._cookie_lock = threading.Lock()
    # Only stable, file-invariant headers are set at session level.
    session.headers.update({
        "User-Agent": settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT),
        "Accept-Language": settings.get(K.SETTING_ACCEPT_LANGUAGE, K.DEFAULT_ACCEPT_LANGUAGE),
    })
    
    # Configure proxy if specified
    proxy_url = format_proxy_url(settings.get(K.SETTING_PROXY))
    if proxy_url:
        session.proxies = {
            "http": proxy_url,
            "https": proxy_url
        }
        logger.info(f"Shared downloader session configured with proxy: {proxy_url}")
    logger.info(
        "Shared downloader session created with 0 internal retries (Immediate-Stop enabled)."
    )
    return session


class MediaDownloader:
    """
    Media downloader class for downloading media files
    """

    def __init__(self, url, filepath, settings, media_type="image", source_url=None, shared_session=None, stop_event=None):
        """
        Initializes MediaDownloader.
        filepath: Full path including desired subdirectories, before final uniqueness.
        shared_session: Optional pre-created requests.Session from ParserManager.
                        When provided, connection reuse and retry are already configured.
                        Per-file headers (Accept, Referer) are sent per-request instead.
        stop_event: Optional threading.Event to monitor for aborting downloads.
        """
        self.url = url
        self.filepath = filepath
        self.settings = settings  # Settings from ParserManager, which got them from SettingsDialog
        self.media_type = media_type
        self.source_url = source_url
        self.stop_event = stop_event
        self.progress_callback = None
        # P3 auto-escalation state (bounded single attempt per download).
        self._escalation_tried = False
        self._escalated_response = None
        self._escalation_session = None
        # Use provided shared session (preferred) or fall back to a local session
        if shared_session is not None:
            self.session = shared_session
            self._owns_session = False  # Don't close a session we didn't create
        else:
            self.session = self._create_session()
            self._owns_session = True
        self.rate_limit = self.settings.get(K.SETTING_MAX_DOWNLOAD_SPEED, 0)  # 0 for unlimited
        # Use K.MAX_THREADS_PER_FILE_CAP as a hard upper limit
        self.threads_per_file = min(
            self.settings.get(K.SETTING_THREADS_PER_FILE, K.DEFAULT_THREADS_PER_FILE),
            K.MAX_THREADS_PER_FILE_CAP
        )

    def _create_session(self):
        # P3: when http_engine=curl_cffi the local session impersonates a real
        # browser TLS fingerprint. urllib3 retry mounting is requests-only; the
        # app-level retry loop in download() covers retries for both engines
        # (and the shared session used in real runs keeps total=0 anyway).
        if http_engine.engine_uses_curl(self.settings):
            session = http_engine.create_sync_session(self.settings)
            headers = {}
            if self.media_type == "image":
                headers["Accept"] = K.DEFAULT_ACCEPT_IMAGE_HEADER
            elif self.media_type == "video":
                headers["Accept"] = K.DEFAULT_ACCEPT_VIDEO_HEADER
            else:
                headers["Accept"] = K.DEFAULT_ACCEPT_HEADER
            if self.source_url:
                referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
                if referrer_policy == "origin":
                    parsed_source = urlparse(self.source_url)
                    headers["Referer"] = f"{parsed_source.scheme}://{parsed_source.netloc}"
                elif referrer_policy == "auto":
                    headers["Referer"] = self.source_url
            session.headers.update(headers)
            return session

        session = requests.Session()
        retry_strategy = Retry(
            total=self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT),
            backoff_factor=0.5, # This could also be a constant
            status_forcelist=[429, 500, 502, 503, 504], # HTTP status codes to retry on
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        headers = {
            "User-Agent": self.settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT),
            "Accept-Language": self.settings.get(K.SETTING_ACCEPT_LANGUAGE, K.DEFAULT_ACCEPT_LANGUAGE),
        }

        if self.media_type == "image": 
            headers["Accept"] = K.DEFAULT_ACCEPT_IMAGE_HEADER
        elif self.media_type == "video": 
            headers["Accept"] = K.DEFAULT_ACCEPT_VIDEO_HEADER
        else: 
            headers["Accept"] = K.DEFAULT_ACCEPT_HEADER # Generic accept

        if self.source_url:
            referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto") # Default to "auto"
            if referrer_policy == "origin":
                parsed_source = urlparse(self.source_url)
                headers["Referer"] = f"{parsed_source.scheme}://{parsed_source.netloc}"
            elif referrer_policy == "auto": # "auto" means send full source_url as referrer
                headers["Referer"] = self.source_url
            # If "none", no Referer header is added.
        
        session.headers.update(headers)
        return session

    def set_progress_callback(self, callback): self.progress_callback = callback

    # P3 auto-escalation helpers (bounded: at most ONE curl_cffi attempt per
    # download; the escalation session is created, used once, and closed).
    def _should_escalate(self, code) -> bool:
        """True when an HTTP status is an explicit-block signal worth a curl_cffi
        retry (single source of truth: http_engine.should_escalate)."""
        return http_engine.should_escalate(self.settings, code)

    def _try_escalate_get(self, headers, timeout) -> bool:
        """Perform ONE curl_cffi browser-TLS GET and keep the streaming response
        in self._escalated_response on success. Returns True on success, False
        otherwise. Bounded by construction: called at most once per download
        (guarded by self._escalation_tried)."""
        if getattr(self, "_escalation_tried", False):
            return False
        self._escalation_tried = True
        session = http_engine.create_escalation_session(self.settings)
        if session is None:
            return False
        try:
            # curl_cffi carries the browser UA/headers from its profile; only
            # per-request media headers (Accept, Referer) are merged on top.
            resp = session.get(
                self.url, headers=headers, timeout=timeout,
                verify=False, stream=True, allow_redirects=True,
            )
            if resp.status_code >= 400:
                resp.close()
                logger.debug(f"Escalation GET returned HTTP {resp.status_code} for {self.url}")
                return False
            self._escalated_response = resp
            self._escalation_session = session  # keep alive until stream consumed
            logger.info(f"Escalation succeeded for {self.url} (HTTP {resp.status_code})")
            return True
        except Exception as e:
            logger.debug(f"Escalation GET failed for {self.url}: {e}")
            try:
                session.close()
            except Exception:
                pass
            return False

    def _close_escalation_session(self):
        """Close the escalation curl session (if any) after the stream is done."""
        session = getattr(self, "_escalation_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._escalation_session = None

    def _get_per_request_headers(self) -> dict:
        """Build headers that vary per file and must be sent per-request.

        Accept and Referer differ between image/video files and between source
        pages, so they cannot be set once on a shared session without
        contaminating unrelated downloads.
        """
        headers: dict = {}
        # Media-type-specific Accept header
        if self.media_type == "image":
            headers["Accept"] = K.DEFAULT_ACCEPT_IMAGE_HEADER
        elif self.media_type == "video":
            headers["Accept"] = K.DEFAULT_ACCEPT_VIDEO_HEADER
        else:
            headers["Accept"] = K.DEFAULT_ACCEPT_HEADER
        # Referer based on referrer policy setting
        if self.source_url:
            referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
            if referrer_policy == "origin":
                parsed_source = urlparse(self.source_url)
                headers["Referer"] = f"{parsed_source.scheme}://{parsed_source.netloc}"
            elif referrer_policy == "auto":
                headers["Referer"] = self.source_url
            # If "none": no Referer header added
        return headers

    # Error substrings that indicate a transient network/server problem worth
    # retrying. Everything else (content filters, size checks, manual abort) is final.
    _RETRYABLE_ERROR_HINTS = (
        "network error",
        "timed out",
        "connection",
        "http error: 5",
        "http error: 429",
    )

    def download(self, timeout=None, retries=None):
        """Download with a bounded, user-configurable retry loop.

        The shared session keeps urllib3 Retry(total=0) so the app's Stop button
        stays responsive; transient failures are retried here instead, honoring
        the Retry Count setting (and 0 = no retries, e.g. probation domains).
        """
        current_timeout = timeout if timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
        retries_left = retries if retries is not None else self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT)
        attempt = 0
        while True:
            if self.stop_event and self.stop_event.is_set():
                return {"success": False, "error": "Download manually aborted"}
            try:
                result = self._do_download(custom_timeout=current_timeout)
            except Exception as e:
                logger.error(f"Download failed for {self.filepath}: {str(e)}", exc_info=True)
                result = {"success": False, "error": str(e)}
            if result["success"] or retries_left <= 0:
                return result
            err_lower = (result.get("error") or "").lower()
            if not any(hint in err_lower for hint in self._RETRYABLE_ERROR_HINTS):
                return result  # content filter / size / abort — final
            retries_left -= 1
            attempt += 1
            logger.info(f"Retrying download ({attempt}/{retries_left + attempt}) for {self.url}: {result.get('error')}")
            time.sleep(0.5 * attempt)

    def _ensure_unique_filepath_at_destination(self, current_filepath: str) -> str:
        with _filename_lock:
            if not os.path.exists(current_filepath):
                return current_filepath
            dir_path, original_basename = os.path.split(current_filepath)
            base_name, ext = os.path.splitext(original_basename)
            counter = 1
            unique_filepath = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
            while os.path.exists(unique_filepath):
                counter += 1
                unique_filepath = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
            logger.debug(f"Adjusted filepath from {current_filepath} to {unique_filepath} due to existing file.")
            return unique_filepath

    def _do_download(self, custom_timeout=None):
        try:
            self.filepath = self._ensure_unique_filepath_at_destination(self.filepath)
            
            # 1. Final Safety Check: filter out disabled media formats (settings-driven)
            if not is_format_allowed(self.url, self.media_type, self.settings):
                return {"success": False, "error": "Filtered as trash media (GIF/ICO/SVG)"}

            # 2. Blacklist check for non-media webpage extensions
            non_media_extensions = [ ".html", ".htm", ".php", ".asp", ".aspx", ".js", ".css", ".json", ".xml"]
            url_lower = self.url.lower()
            parsed_url = urlparse(url_lower)
            path = parsed_url.path
            
            if any(path.endswith(ext) for ext in non_media_extensions):
                return {"success": False, "error": "Non-media file based on URL extension"}
            
            timeout_to_use = custom_timeout if custom_timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
            content_length = 0
            response_head = None  # Define response_head before try block
            # Per-request headers built once and reused for HEAD, GET, and chunks
            per_req_hdrs = self._get_per_request_headers()

            try:
                # MUST follow redirects: photo hosts (imx.to etc.) 302 image URLs to
                # a CDN host. requests.head() defaults to allow_redirects=False, so
                # without this a valid image was misdetected as an HTML shell page
                # ("Webpage/script content") and the item was failed + re-parsed.
                response_head = self.session.head(self.url, headers=per_req_hdrs, timeout=timeout_to_use, allow_redirects=True)
                response_head.raise_for_status()
                content_length = int(response_head.headers.get("Content-Length", 0))
                content_type = response_head.headers.get("Content-Type", "").lower()
                if any(t in content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                    return {"success": False, "error": f"Webpage/script content (Content-Type: {content_type})"}
                
                min_img_size_kb = self.settings.get(K.SETTING_MIN_IMG_SIZE, K.DEFAULT_MIN_IMAGE_SIZE_KB)
                min_vid_size_kb = self.settings.get(K.SETTING_MIN_VID_SIZE, K.DEFAULT_MIN_VIDEO_SIZE_KB)

                if content_length > 0:
                    size_kb = content_length / 1024
                    min_size_for_type = min_img_size_kb if self.media_type == "image" else min_vid_size_kb
                    if min_size_for_type > 0 and size_kb < min_size_for_type:
                        return {"success": False, "error": f"File too small ({size_kb:.2f}KB < {min_size_for_type}KB)"}
            except http_engine.NETWORK_ERROR_EXCEPTIONS as e:
                logger.warning(f"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.")

            mode = "wb"
            can_multi_thread = (self.threads_per_file > 1 and 
                                content_length > 0 and 
                                content_length > K.WRITE_BUFFER_SIZE * self.threads_per_file and 
                                response_head and response_head.headers.get("Accept-Ranges") == "bytes")

            if can_multi_thread:
                try:
                    # Ensure the target directory exists before multi-threaded download
                    target_dir = os.path.dirname(self.filepath)
                    if target_dir:
                        try:
                            os.makedirs(target_dir, exist_ok=True)
                        except OSError as e:
                            logger.error(f"Could not create target directory {target_dir}: {e}", exc_info=True)
                            return {"success": False, "error": f"Could not create directory: {e}"}
                    
                    logger.info(f"Attempting multi-threaded download for {self.filepath}")
                    result = self._download_with_threads(content_length, custom_timeout=timeout_to_use)
                    if result["success"]: return result
                    logger.warning(f"Multi-threaded download failed for {self.filepath}, falling back to single-threaded.")
                except Exception as e:
                    logger.warning(f"Multi-threaded download for {self.filepath} raised {e}, falling back.", exc_info=True)
            
            logger.info(f"Starting single-threaded download: {os.path.basename(self.filepath)}")
            response_get = self.session.get(self.url, headers=per_req_hdrs, stream=True, timeout=timeout_to_use)
            try:
                response_get.raise_for_status()
            except http_engine.HTTP_ERROR_EXCEPTIONS as e:
                # P3 auto-escalation: HTTP 403/5xx on GET is the explicit-block
                # signal (bot-protection). Try ONE curl_cffi browser-TLS fetch
                # before failing. Bounded (single attempt, no retry loop) so the
                # domain-health/quarantine counters in ParserManager stay
                # unchanged: a successful escalation downloads the file (no
                # failure counted); a failed one returns the same HTTP error.
                code = getattr(getattr(e, "response", None), "status_code", None)
                response_get.close()
                if not self._should_escalate(code):
                    return {"success": False, "error": f"HTTP error: {code}"}
                if not self._try_escalate_get(per_req_hdrs, timeout_to_use):
                    return {"success": False, "error": f"HTTP error: {code}"}
                response_get = self._escalated_response

            # Some servers reject HEAD (405/403). When HEAD never succeeded,
            # validate the GET response too so an HTML error/login page is
            # never saved as media.
            if response_head is None:
                get_content_type = (response_get.headers.get("Content-Type") or "").lower()
                if any(t in get_content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                    response_get.close()
                    self._close_escalation_session()
                    return {"success": False, "error": f"Webpage/script content (Content-Type: {get_content_type})"}

            if content_length == 0:
                content_length = int(response_get.headers.get("Content-Length", 0))

            # Ensure the target directory exists only when we're about to download
            target_dir = os.path.dirname(self.filepath)
            if target_dir:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                except OSError as e:
                    logger.error(f"Could not create target directory {target_dir}: {e}", exc_info=True)
                    self._close_escalation_session()
                    return {"success": False, "error": f"Could not create directory: {e}"}

            write_buffer = bytearray()
            temp_path = self.filepath + ".partial"
            try:
                with open(temp_path, mode) as f:
                    start_time = time.time()
                    network_chunk_size = 8192  
                    downloaded_bytes = 0
                    for chunk in response_get.iter_content(chunk_size=network_chunk_size):
                        if self.stop_event and self.stop_event.is_set():
                            try: os.remove(temp_path)
                            except OSError: pass
                            return {"success": False, "error": "Download manually aborted"}
                        
                        if chunk:
                            write_buffer.extend(chunk)
                            downloaded_bytes += len(chunk)
                            if self.progress_callback:
                                prog = min(100, int((downloaded_bytes / content_length) * 100)) if content_length > 0 else -1
                                self.progress_callback(prog)
                            if len(write_buffer) >= K.WRITE_BUFFER_SIZE:
                                try: f.write(write_buffer); write_buffer.clear()
                                except Exception as e: return {"success": False, "error": f"Disk write error: {e}"}
                            if self.rate_limit > 0:
                                elapsed = time.time() - start_time
                                expected_time = downloaded_bytes / (self.rate_limit * 1024)
                                if elapsed < expected_time: time.sleep(expected_time - elapsed)
                    if write_buffer:
                        try:
                            f.write(write_buffer)
                        except Exception as e:
                            return {"success": False, "error": f"Disk write error: {e}"}

                if content_length > 0:
                    actual_size = os.path.getsize(temp_path)
                    if actual_size != content_length:
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
                        return {"success": False, "error": f"Size mismatch: expected {content_length}, got {actual_size}"}

                # Atomic rename from .partial to final path
                os.replace(temp_path, self.filepath)
                if self.progress_callback: self.progress_callback(100)
                logger.info(f"Download completed: {os.path.basename(self.filepath)}")
                return {"success": True, "message": "File downloaded successfully"}
            finally:
                # Close an escalation curl session (if any) after the stream is
                # consumed or the download aborted — never leak the session.
                self._close_escalation_session()

        except http_engine.HTTP_ERROR_EXCEPTIONS as e:
            status = getattr(e, "response", None)
            return {"success": False, "error": f"HTTP error: {getattr(status, 'status_code', 'Unknown')}"}

        except http_engine.HTTP_ERROR_EXCEPTIONS as e:
            status = getattr(e, "response", None)
            return {"success": False, "error": f"HTTP error: {getattr(status, 'status_code', 'Unknown')}"}
        except http_engine.NETWORK_ERROR_EXCEPTIONS as e:
            return {"success": False, "error": f"Network error: {e}"}
        except Exception as e:
            logger.error(f"Generic download error for {self.url}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _download_with_threads(self, total_size, custom_timeout=None):
        temp_files, threads = [], []
        progress_lock = threading.Lock()
        progress_dict = {"total": 0, "success": True, "errors": []}
        timeout_to_use = custom_timeout if custom_timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
        
        num_threads = min(self.threads_per_file, max(1, total_size // K.MIN_CHUNK_SIZE_PER_THREAD_MT), K.MAX_THREADS_PER_FILE_CAP)
        if num_threads <= 1: return {"success": False, "error": "Not enough parts for multi-thread based on min chunk size"}

        chunk_size_for_threads = total_size // num_threads
        for i in range(num_threads):
            start = i * chunk_size_for_threads
            end = (i + 1) * chunk_size_for_threads - 1 if i < num_threads - 1 else total_size - 1
            temp_file = f"{self.filepath}.part{i}"
            temp_files.append(temp_file)
            thread = threading.Thread(target=self._download_chunk, args=(start, end, temp_file, total_size, progress_dict, progress_lock, timeout_to_use))
            thread.daemon = True; thread.start(); threads.append(thread)
        for thread in threads: thread.join()

        if not progress_dict["success"]:
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass
            return {"success": False, "error": f"Chunk failure(s): {progress_dict['errors']}"}
        try:
            with open(self.filepath, "wb") as outfile:
                for temp_file in temp_files:
                    if not os.path.exists(temp_file): raise IOError(f"Missing part: {temp_file}")
                    with open(temp_file, "rb") as infile: outfile.write(infile.read())
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass  # Clean up successful part
            if os.path.getsize(self.filepath) != total_size:
                try:
                    os.remove(self.filepath)
                except OSError:
                    pass
                return {"success": False, "error": "Combined file size mismatch"}
            if self.progress_callback: self.progress_callback(100)
            return {"success": True, "message": "Multi-threaded download success"}
        except Exception as e:
            if os.path.exists(self.filepath):
                try:
                    os.remove(self.filepath)
                except OSError:
                    pass
            return {"success": False, "error": f"Combining/verifying error: {e}"}
        finally: # Ensure all temp files are attempted to be cleaned up
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass

    def _download_chunk(self, start, end, filename, total_size, progress_dict, progress_lock, timeout_val):
        # Merge per-file headers (Accept, Referer) with the byte-range header for this chunk
        per_req_hdrs = self._get_per_request_headers()
        headers = {**per_req_hdrs, "Range": f"bytes={start}-{end}"}
        network_chunk_size_thread = 8192
        try:
            # Guard: ensure the target directory exists before writing this chunk's temp file.
            # Must be inside try so failures are caught and reported via progress_dict.
            dir_name = os.path.dirname(filename)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            response = self.session.get(self.url, headers=headers, stream=True, timeout=timeout_val)
            response.raise_for_status()
            write_buffer_chunk = bytearray()
            downloaded_this_chunk = 0 # For this specific chunk part
            with open(filename, "wb") as f:
                for chunk_data in response.iter_content(chunk_size=network_chunk_size_thread):
                    if not progress_dict["success"]: return # Check if another thread failed
                    if self.stop_event and self.stop_event.is_set():
                        with progress_lock:
                            progress_dict["success"] = False
                            progress_dict["errors"].append("Download manually aborted")
                        return
                    if chunk_data:
                        write_buffer_chunk.extend(chunk_data)
                        downloaded_this_chunk += len(chunk_data)
                        with progress_lock:
                            progress_dict["total"] += len(chunk_data) # Update overall progress
                            if self.progress_callback:
                                prog = min(99, int((progress_dict["total"] / total_size) * 100))
                                self.progress_callback(prog)
                        if len(write_buffer_chunk) >= K.WRITE_BUFFER_SIZE:
                            f.write(write_buffer_chunk); write_buffer_chunk.clear()
                        # Simplified rate limiting for threaded chunks - focus on buffer primarily
                if write_buffer_chunk: f.write(write_buffer_chunk) 
            
            if os.path.getsize(filename) != (end - start + 1): # Verify this chunk's size
                raise IOError(f"Chunk size mismatch: expected {end - start + 1}, got {os.path.getsize(filename)}")
        except Exception as e:
            error_msg = f"Chunk {filename} failed: {type(e).__name__} - {e}"
            logger.error(error_msg)
            with progress_lock:
                progress_dict["success"] = False
                progress_dict["errors"].append(error_msg)
