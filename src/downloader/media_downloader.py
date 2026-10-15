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

# Paths already claimed by an in-flight download (DL-2). The unique final name is
# reserved under _filename_lock so a concurrent download to the same sanitized
# target gets a distinct name instead of both writing the same temp file.
_reserved_paths: set = set()

# B-11: what a page shell actually starts with. The Content-Type checks trust the
# server's header, so a photo host that serves its login/interstitial page as
# `text/plain` (or with no Content-Type at all) used to leave that HTML on disk
# under a media name with a `success` result.
_PAGE_SNIFF_PREFIXES = (b"<!doctype", b"<html", b"<head", b"<body", b"{")


def body_looks_like_page(prefix, content_type: str = "") -> bool:
    """True when the leading bytes are an HTML/XML/JSON shell rather than media.

    `<?xml` is a rejection signal only when the server did not declare an image
    type: a user-enabled `image/svg+xml` legitimately starts with an XML prolog
    (FIXPLAN_2026-10-15 §1.2/5).
    """
    head = bytes(prefix or b"").lstrip()[:64].lower()
    if not head:
        return False
    if head.startswith(_PAGE_SNIFF_PREFIXES):
        return True
    return head.startswith(b"<?xml") and "image/" not in (content_type or "").lower()


def _has_dotdot_segment(filepath) -> bool:
    """B-08: True when any RAW path segment is `..`.

    `os.path.normpath` cannot be used for this check: it resolves the segments
    away, so `a/../b` looks clean (measured: a `..` destination written to the
    tmp root, and the guard never fired). Both separators are treated as
    separators because Windows paths reach here with either one.
    """
    parts = str(filepath).replace("/", os.sep).replace("\\", os.sep).split(os.sep)
    return ".." in parts


def _chunked_framing(response) -> bool:
    """B-12/NF-8: does this response carry `Transfer-Encoding: chunked`?

    A chunked body frames itself, so its end is verifiable (requests raises
    ChunkedEncodingError on a truncated one); a close-delimited body has no such
    signal.
    """
    return "chunked" in (response.headers.get("Transfer-Encoding") or "").lower()


def _http_failure_kind(code) -> str:
    """R-C-5/R-C-6: map an HTTP status onto a machine-readable failure kind.

    A dead link (4xx) is not a host-health fault; a 5xx or 429 is.
    """
    if code == 429:
        return K.FAILURE_HTTP_429
    if isinstance(code, int) and code >= 500:
        return K.FAILURE_HTTP_5XX
    return K.FAILURE_HTTP_4XX


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
        # B-09: the download session must follow the same TLS setting as the page
        # fetch. Only the escalation GET used to read it, so with verify_tls off
        # (the app default) pages loaded fine and every media download failed the
        # certificate check. legacy_default=False keeps verification ON for
        # installs that never touched the setting.
        session.verify = http_engine.tls_verify(settings, legacy_default=False)
        logger.info(
            "Shared downloader session created (curl_cffi impersonate=%s, verify=%s, "
            "0 internal retries).",
            http_engine.impersonate_profile(settings),
            session.verify,
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
    # B-09: the download session follows the same TLS decision as the page fetch
    # (see the curl_cffi branch). This used to sit inside the `if proxy_url:`
    # block, so a session without a proxy fell through and was never returned.
    session.verify = http_engine.tls_verify(settings, legacy_default=False)
    logger.info(
        "Shared downloader session created with 0 internal retries "
        "(Immediate-Stop enabled, verify=%s).", session.verify
    )
    return session


class _ContentPageError(IOError):
    """B-11: a ranged chunk turned out to be an HTML/JSON page instead of media."""


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
        # A-07: hard cap on a single file, 0 = no limit. The settings dict ships
        # 2 GiB (K.DEFAULT_SETTINGS_VALUES) so a fresh profile has a backstop;
        # the code fallback stays 0 so a hand-built dict is never capped.
        self.max_bytes = int(self.settings.get(K.SETTING_MAX_FILE_SIZE, K.DEFAULT_MAX_FILE_SIZE) or 0) * 1024
        # B-12 strict mode (opt-in): a body with no Content-Length and no chunked
        # framing has no integrity signal at all, so refuse it instead of keeping
        # a possibly-truncated file.
        self.strict_length = bool(self.settings.get(
            K.SETTING_REQUIRE_VERIFIED_LENGTH, K.DEFAULT_REQUIRE_VERIFIED_LENGTH))
        # Use K.MAX_THREADS_PER_FILE_CAP as a hard upper limit
        self.threads_per_file = min(
            self.settings.get(K.SETTING_THREADS_PER_FILE, K.DEFAULT_THREADS_PER_FILE),
            K.MAX_THREADS_PER_FILE_CAP
        )

    def _create_session(self):
        # B-09: a downloader that builds its own session follows the TLS setting
        # too (see create_shared_downloader_session).
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
            # B-09: follow the TLS setting here too (own-session path).
            session.verify = http_engine.tls_verify(self.settings, legacy_default=False)
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
        # B-09: follow the TLS setting here too (own-session path).
        session.verify = http_engine.tls_verify(self.settings, legacy_default=False)
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
            # A-6 (revised): this path historically did NOT pass verify →
            # curl_cffi defaulted to verify=True. legacy_default=False keeps
            # verification ON unless the user explicitly disables it — a
            # verify=False default here would silently weaken TLS.
            resp = session.get(
                self.url, headers=headers, timeout=timeout,
                stream=True, allow_redirects=True,
                verify=http_engine.tls_verify(self.settings, legacy_default=False),
            )
            if resp.status_code >= 400:
                resp.close()
                session.close()
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

    def _max_size_error(self):
        """A-07: the result for a download that crossed Max File Size.

        Local policy, not a host fault: no `failure_kind`, so the domain-health
        counters in ParserManager (FAILURE_PENALISING) stay untouched.
        """
        if not self.max_bytes:
            return None
        return {"success": False,
                "error": f"File exceeds Max File Size ({self.max_bytes // 1024} KB)"}

    def _cleanup_partial(self):
        """Remove the current download's half-written .partial file (DL-5).

        Mid-stream network/disk errors must not leave permanent junk behind once
        retries are exhausted; only the Stop path and the explicit disk-error
        returns remove the file themselves.
        """
        temp_path = getattr(self, "_temp_path", None)
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass

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
        try:
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
                # B-13: wait out the backoff in small steps while watching
                # stop_event. A bare time.sleep(0.5 * attempt) ignored Stop for
                # the whole pause (measured: Stop at 5.2 s still exited after
                # 2.36 s), which is exactly the responsiveness Retry(total=0)
                # exists to protect.
                deadline = time.time() + 0.5 * attempt
                while time.time() < deadline:
                    if self.stop_event and self.stop_event.is_set():
                        return {"success": False, "error": "Download manually aborted"}
                    time.sleep(0.1)
        finally:
            # DL-2: release the reserved final name once the file exists on disk
            # (success) or the download is abandoned (failure). os.path.exists then
            # guards the name for any later downloader.
            reserved = getattr(self, "_reserved_path", None)
            if reserved:
                _reserved_paths.discard(reserved)

    def _ensure_unique_filepath_at_destination(self, current_filepath: str) -> str:
        # B-08: never reserve a name above the destination tree. The subdirectory
        # is built from the page URL and urlparse does not normalise dot segments,
        # so a page path carrying '/../../..' reached the filesystem verbatim and
        # wrote files next to (or above) the chosen download folder.
        if _has_dotdot_segment(current_filepath):
            raise ValueError(f"Path traversal rejected: {current_filepath}")
        with _filename_lock:
            # Release the previous reservation (if any) so a retry of the same
            # downloader re-reserves cleanly instead of leaking stale entries.
            prev_reserved = getattr(self, "_reserved_path", None)
            if prev_reserved:
                _reserved_paths.discard(prev_reserved)
            candidate = current_filepath
            if os.path.exists(candidate) or os.path.normcase(candidate) in _reserved_paths:
                dir_path, original_basename = os.path.split(candidate)
                base_name, ext = os.path.splitext(original_basename)
                counter = 1
                candidate = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
                while os.path.exists(candidate) or os.path.normcase(candidate) in _reserved_paths:
                    counter += 1
                    candidate = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
                logger.debug(f"Adjusted filepath from {current_filepath} to {candidate} due to existing/reserved file.")
            _reserved_paths.add(os.path.normcase(candidate))
            self._reserved_path = os.path.normcase(candidate)
            return candidate

    def _do_download(self, custom_timeout=None):
        # B-08: a rejected path is a local problem, not a host fault — refuse it
        # before the unique-name reservation so it never reaches the generic
        # handler (which would tag it FAILURE_UNEXPECTED and penalise the domain).
        if _has_dotdot_segment(self.filepath):
            logger.warning(f"Refusing path outside the download root: {self.filepath}")
            return {"success": False, "error": f"Path traversal rejected: {self.filepath}"}
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
            head_encoding = ""  # DL-3: set when the HEAD response carries Content-Encoding
            # Per-request headers built once and reused for HEAD, GET, and chunks
            per_req_hdrs = self._get_per_request_headers()

            try:
                # MUST follow redirects: photo hosts (imx.to etc.) 302 image URLs to
                # a CDN host. requests.head() defaults to allow_redirects=False, so
                # without this a valid image was misdetected as an HTML shell page
                # ("Webpage/script content") and the item was failed + re-parsed.
                response_head = self.session.head(self.url, headers=per_req_hdrs, timeout=timeout_to_use, allow_redirects=True)
                response_head.raise_for_status()
                try:
                    content_length = int(response_head.headers.get("Content-Length", 0))
                except (TypeError, ValueError):
                    content_length = 0
                head_encoding = (response_head.headers.get("Content-Encoding") or "").strip().lower()
                content_type = response_head.headers.get("Content-Type", "").lower()
                if any(t in content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                    return {"success": False,
                            "error": f"Webpage/script content (Content-Type: {content_type})",
                            "failure_kind": K.FAILURE_CONTENT_PAGE}
                
                min_img_size_kb = self.settings.get(K.SETTING_MIN_IMG_SIZE, K.DEFAULT_MIN_IMAGE_SIZE_KB)
                min_vid_size_kb = self.settings.get(K.SETTING_MIN_VID_SIZE, K.DEFAULT_MIN_VIDEO_SIZE_KB)

                if content_length > 0:
                    size_kb = content_length / 1024
                    min_size_for_type = min_img_size_kb if self.media_type == "image" else min_vid_size_kb
                    if min_size_for_type > 0 and size_kb < min_size_for_type:
                        return {"success": False, "error": f"File too small ({size_kb:.2f}KB < {min_size_for_type}KB)"}
                    # A-07: a declared body that is already over the cap is refused
                    # before a single byte is written (and before the MT decision,
                    # so an enormous file never spawns chunk threads).
                    if self.max_bytes and content_length > self.max_bytes:
                        response_head.close()
                        return self._max_size_error()
            except http_engine.NETWORK_ERROR_EXCEPTIONS as e:
                logger.warning(f"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.")

            mode = "wb"
            # DL-3 edge: never range-download an encoded body — byte ranges are
            # served for the compressed representation, requests decompresses each
            # part, and the per-chunk size check fails (or worse, corrupts).
            can_multi_thread = (self.threads_per_file > 1 and 
                                content_length > 0 and 
                                content_length > K.WRITE_BUFFER_SIZE * self.threads_per_file and 
                                response_head and response_head.headers.get("Accept-Ranges") == "bytes" and
                                not head_encoding)

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
                    # A-07 / B-11: a size cap or a non-media body is a verdict about
                    # the file itself — falling back would download it all over
                    # again only to reach the same conclusion.
                    if result.get("size_limited") or result.get("failure_kind") == K.FAILURE_CONTENT_PAGE:
                        return result
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
                    return {"success": False, "error": f"HTTP error: {code}",
                            "failure_kind": _http_failure_kind(code)}
                if not self._try_escalate_get(per_req_hdrs, timeout_to_use):
                    return {"success": False, "error": f"HTTP error: {code}",
                            "failure_kind": _http_failure_kind(code)}
                response_get = self._escalated_response

            # R-C-3: the GET body is the only thing we write to disk, so its
            # Content-Type must be validated unconditionally. The old guard was
            # `if response_head is None`, which silently disabled this check
            # whenever HEAD returned a response with a 4xx status (a 403/405 HEAD
            # still leaves a non-None object behind) — an HTML error/login page
            # was then saved under a media extension. A HEAD that answers 200
            # with a wrong or missing Content-Type left the same hole.
            get_content_type = (response_get.headers.get("Content-Type") or "").lower()
            if any(t in get_content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                response_get.close()
                self._close_escalation_session()
                return {"success": False,
                        "error": f"Webpage/script content (Content-Type: {get_content_type})",
                        "failure_kind": K.FAILURE_CONTENT_PAGE}

            if content_length == 0:
                try:
                    content_length = int(response_get.headers.get("Content-Length", 0))
                except (TypeError, ValueError):
                    content_length = 0

            # A-07: the same cap on the GET's own length — the HEAD may have been
            # refused (403/405), in which case only the GET declares the size.
            if self.max_bytes and content_length > self.max_bytes:
                response_get.close()
                self._close_escalation_session()
                return self._max_size_error()

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
            temp_path = f"{self.filepath}.{threading.get_ident():x}.partial"
            self._temp_path = temp_path  # DL-5: for cleanup on mid-stream failures
            # A-07 / B-11 / B-12 / F-14: one abort point for the stream loop. The
            # result is returned AFTER the file handle is closed: os.remove() on an
            # open file is refused on Windows (the OSError was swallowed), so every
            # Stop used to leave its own `.partial` behind for the next run to trip
            # over. Keeping a single exit also means the size cap, the body sniff
            # and a disk error cannot disagree about what was written.
            abort_result = None
            unverified_length = False
            try:
                with open(temp_path, mode) as f:
                    start_time = time.time()
                    network_chunk_size = 8192  
                    downloaded_bytes = 0
                    sniffed = False
                    for chunk in response_get.iter_content(chunk_size=network_chunk_size):
                        if self.stop_event and self.stop_event.is_set():
                            abort_result = {"success": False, "error": "Download manually aborted"}
                            break
                        
                        if chunk:
                            write_buffer.extend(chunk)
                            downloaded_bytes += len(chunk)
                            # A-07: nothing past the cap reaches the disk. The HEAD
                            # and GET checks above only cover a declared length; this
                            # one also stops a host that answers 200 with a length
                            # and then streams without end.
                            if self.max_bytes and downloaded_bytes > self.max_bytes:
                                abort_result = self._max_size_error()
                                break
                            # B-11: sniff the leading bytes before the first write.
                            # Re-checked on every chunk until the window is full, so a
                            # 51-byte login page served as text/plain is caught too.
                            if not sniffed and body_looks_like_page(write_buffer, get_content_type):
                                abort_result = {"success": False,
                                                "error": "Body is not media (sniffed page content)",
                                                "failure_kind": K.FAILURE_CONTENT_PAGE}
                                break
                            if len(write_buffer) >= K.SNIFF_PREFIX_BYTES:
                                sniffed = True
                            if self.progress_callback:
                                prog = min(100, int((downloaded_bytes / content_length) * 100)) if content_length > 0 else -1
                                self.progress_callback(prog)
                            if len(write_buffer) >= K.WRITE_BUFFER_SIZE:
                                try: f.write(write_buffer); write_buffer.clear()
                                except Exception as e:
                                    abort_result = {"success": False, "error": f"Disk write error: {e}"}
                                    break
                            if self.rate_limit > 0:
                                elapsed = time.time() - start_time
                                expected_time = downloaded_bytes / (self.rate_limit * 1024)
                                if elapsed < expected_time: time.sleep(expected_time - elapsed)
                    if write_buffer and abort_result is None:
                        try:
                            f.write(write_buffer)
                        except Exception as e:
                            abort_result = {"success": False, "error": f"Disk write error: {e}"}

                # The handle is closed here, so the temp file can really be removed.
                if abort_result is not None:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    return abort_result

                # DL-3: Content-Length is the COMPRESSED size when the server
                # answers with Content-Encoding (gzip/br/deflate); iter_content
                # yields decompressed bytes, so a byte-exact comparison would
                # delete valid files. Skip the check for encoded responses.
                # Runs AFTER the with-block closes so getsize sees flushed data.
                encoding = (response_get.headers.get("Content-Encoding") or "").strip().lower()
                actual_size = os.path.getsize(temp_path)
                if actual_size == 0:
                    # B-12: an empty body is never a media file, known length or not.
                    abort_result = {"success": False, "error": "Empty body (no data received)"}
                elif content_length > 0 and not encoding:
                    if actual_size != content_length:
                        abort_result = {"success": False,
                                        "error": f"Size mismatch: expected {content_length}, got {actual_size}"}
                elif content_length <= 0:
                    # B-12 / NF-8: without Content-Length the only integrity signal is
                    # the framing. A chunked body carries its own and requests raises
                    # ChunkedEncodingError on a truncated one (bounded by the read
                    # timeout); a close-delimited body simply ends when the socket
                    # closes, so an early end is indistinguishable from a complete
                    # one — reported as `unverified_length` rather than silently
                    # trusted. Strict mode (opt-in setting) refuses it instead.
                    if _chunked_framing(response_get):
                        pass
                    elif self.strict_length:
                        abort_result = {"success": False,
                                        "error": "No Content-Length and no chunked framing "
                                                 "(require_verified_length)"}
                    else:
                        unverified_length = True

                if abort_result is not None:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    return abort_result

                # Atomic rename from .partial to final path
                os.replace(temp_path, self.filepath)
                if self.progress_callback: self.progress_callback(100)
                if unverified_length:
                    logger.warning(
                        "Downloaded without a verifiable length (no Content-Length): "
                        f"{os.path.basename(self.filepath)}"
                    )
                logger.info(f"Download completed: {os.path.basename(self.filepath)}")
                result = {"success": True, "message": "File downloaded successfully"}
                if unverified_length:
                    result["unverified_length"] = True
                return result
            finally:
                # Close an escalation curl session (if any) after the stream is
                # consumed or the download aborted — never leak the session.
                self._close_escalation_session()

        except http_engine.HTTP_ERROR_EXCEPTIONS as e:
            status = getattr(e, "response", None)
            code = getattr(status, "status_code", None)
            return {"success": False, "error": f"HTTP error: {code if code else 'Unknown'}",
                    "failure_kind": _http_failure_kind(code)}
        except http_engine.NETWORK_ERROR_EXCEPTIONS as e:
            self._cleanup_partial()
            return {"success": False, "error": f"Network error: {e}",
                    "failure_kind": K.FAILURE_NETWORK}
        except Exception as e:
            self._cleanup_partial()
            logger.error(f"Generic download error for {self.url}: {str(e)}", exc_info=True)
            # Unknown faults keep today's conservatism: they did count as host faults.
            return {"success": False, "error": str(e), "failure_kind": K.FAILURE_UNEXPECTED}

    def _download_with_threads(self, total_size, custom_timeout=None):
        temp_files, threads = [], []
        progress_lock = threading.Lock()
        progress_dict = {"total": 0, "success": True, "errors": []}
        if self.rate_limit > 0:
            # DL-12: shared rate limiter — all chunks of this download throttle
            # against ONE byte counter (otherwise N threads would each run at
            # the configured limit and the Max Download Speed setting would be
            # silently multiplied by the thread count).
            progress_dict["_rate"] = {"start": time.time(), "bytes": 0}
        timeout_to_use = custom_timeout if custom_timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
        
        num_threads = min(self.threads_per_file, max(1, total_size // K.MIN_CHUNK_SIZE_PER_THREAD_MT), K.MAX_THREADS_PER_FILE_CAP)
        if num_threads <= 1: return {"success": False, "error": "Not enough parts for multi-thread based on min chunk size"}

        chunk_size_for_threads = total_size // num_threads
        for i in range(num_threads):
            start = i * chunk_size_for_threads
            end = (i + 1) * chunk_size_for_threads - 1 if i < num_threads - 1 else total_size - 1
            temp_file = f"{self.filepath}.{threading.get_ident():x}.part{i}"
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
            if progress_dict.get("size_exceeded"):
                result = self._max_size_error() or {"success": False,
                                                   "error": "File exceeds Max File Size"}
                result["size_limited"] = True
                return result
            if progress_dict.get("content_page"):
                return {"success": False, "error": "Webpage/script content in body",
                        "failure_kind": K.FAILURE_CONTENT_PAGE}
            return {"success": False, "error": f"Chunk failure(s): {progress_dict['errors']}",
                    "failure_kind": K.FAILURE_NETWORK}
        # B-10 / NF-3: assemble into '{filepath}.mt.partial' and rename. The old
        # code wrote the final name directly, so an interrupted assembly left a
        # truncated file under a plausible media name; the next run then saw that
        # name as taken and picked 'name_1.jpg', preserving the truncated file
        # forever (and breaking resume).
        asm_path = f"{self.filepath}.mt.partial"
        self._temp_path = asm_path  # DL-5 / NF-3: swept on Stop, removed on error
        try:
            with open(asm_path, "wb") as outfile:
                for temp_file in temp_files:
                    if not os.path.exists(temp_file): raise IOError(f"Missing part: {temp_file}")
                    with open(temp_file, "rb") as infile: outfile.write(infile.read())
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass  # Clean up successful part
            if os.path.getsize(asm_path) != total_size:
                try:
                    os.remove(asm_path)
                except OSError:
                    pass
                return {"success": False, "error": "Combined file size mismatch"}
            os.replace(asm_path, self.filepath)
            if self.progress_callback: self.progress_callback(100)
            return {"success": True, "message": "Multi-threaded download success"}
        except Exception as e:
            if os.path.exists(asm_path):
                try:
                    os.remove(asm_path)
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
            try:
                response.raise_for_status()
                # R-C-3: the multi-threaded path assembles ranged GETs and returns
                # before the single-threaded GET guard runs — validate the chunk
                # type here too. On mismatch the whole MT attempt fails, the
                # downloader falls back to single-threaded, and the guard there
                # rejects the body instead of writing an error page.
                chunk_ct = (response.headers.get("Content-Type") or "").lower()
                if any(t in chunk_ct for t in ("text/html", "application/javascript",
                                               "text/javascript", "text/css",
                                               "application/json")):
                    raise IOError(f"Webpage/script content (Content-Type: {chunk_ct})")
                write_buffer_chunk = bytearray()
                downloaded_this_chunk = 0 # For this specific chunk part
                sniffed = False
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
                            # A-07: same cap as the single-threaded loop. Chunk sizes
                            # are bounded by the requested range, so this only fires
                            # when a server ignores Range and streams more than asked.
                            if self.max_bytes and downloaded_this_chunk > self.max_bytes:
                                with progress_lock:
                                    progress_dict["success"] = False
                                    progress_dict["size_exceeded"] = True
                                    progress_dict["errors"].append("File exceeds Max File Size")
                                return
                            # B-11: only the part that starts at offset 0 sees the real
                            # beginning of the file — a ranged chunk of a valid image
                            # legitimately starts mid-binary and must not be sniffed.
                            if start == 0 and not sniffed:
                                if body_looks_like_page(write_buffer_chunk, chunk_ct):
                                    raise _ContentPageError("Body is not media (sniffed page content)")
                                if len(write_buffer_chunk) >= K.SNIFF_PREFIX_BYTES:
                                    sniffed = True
                            with progress_lock:
                                progress_dict["total"] += len(chunk_data) # Update overall progress
                                if self.progress_callback:
                                    prog = min(99, int((progress_dict["total"] / total_size) * 100))
                                    self.progress_callback(prog)
                            # DL-12: shared rate limiter — compute needed sleep under
                            # the lock, then sleep outside it so one slow chunk never
                            # blocks the others' progress updates.
                            if self.rate_limit > 0 and "_rate" in progress_dict:
                                with progress_lock:
                                    rate = progress_dict["_rate"]
                                    rate["bytes"] += len(chunk_data)
                                    elapsed = time.time() - rate["start"]
                                    expected_time = rate["bytes"] / (self.rate_limit * 1024)
                                    sleep_needed = max(0.0, expected_time - elapsed)
                                if sleep_needed > 0:
                                    time.sleep(sleep_needed)
                            if len(write_buffer_chunk) >= K.WRITE_BUFFER_SIZE:
                                f.write(write_buffer_chunk); write_buffer_chunk.clear()
                    # A-1: flush the residual buffer INSIDE the with-block —
                    # writing it after the file was closed raised ValueError
                    # and silently failed every MT download to single-thread.
                    if write_buffer_chunk: f.write(write_buffer_chunk)
            finally:
                # DL-12: always release the connection, including on errors —
                # otherwise it hangs in the pool until GC.
                response.close()
            
            if os.path.getsize(filename) != (end - start + 1): # Verify this chunk's size
                raise IOError(f"Chunk size mismatch: expected {end - start + 1}, got {os.path.getsize(filename)}")
        except Exception as e:
            error_msg = f"Chunk {filename} failed: {type(e).__name__} - {e}"
            logger.error(error_msg)
            with progress_lock:
                progress_dict["success"] = False
                progress_dict["errors"].append(error_msg)
                if isinstance(e, _ContentPageError):
                    progress_dict["content_page"] = True
