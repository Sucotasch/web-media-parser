#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HTTP API server for browser extension communication.

Runs on localhost:19876 (configurable). Accepts JSON commands from
the browser extension to add tasks to the queue and report status.
"""

import logging
import threading
from typing import Optional, Callable

from aiohttp import web

from src import constants as K

logger = logging.getLogger(__name__)

DEFAULT_PORT = K.EXTENSION_API_PORT  # single source: src/constants (GUI-8)

# C-04: header/cookie payloads come from a browser extension over loopback, but
# they still must be strings of sane length with no CR/LF (header injection) and
# no separators that a cookie jar would reject (`;` starts a new cookie).
MAX_SCALAR_LENGTH = 512


def _clean_header_value(value) -> str:
    """Return a safe single-line string for a header-ish payload, "" for junk."""
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\r", "").replace("\n", "").strip()
    return cleaned[:MAX_SCALAR_LENGTH]


def _clean_cookie_value(value) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\r", "").replace("\n", "").strip()
    # A cookie string carries `; ` between pairs — those are expected. Only
    # control characters are dropped (CR/LF above, everything else non-printable).
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() or ch == " ")
    return cleaned[:MAX_SCALAR_LENGTH]


class ExtensionServer:
    """Lightweight HTTP API server for browser extension communication."""

    # C-05/NF-6: aiohttp's default client_max_size is 1 MiB, and going over it
    # surfaced as a bare `Invalid JSON` 400 — a legitimate large batch (measured:
    # ~9 800 items reach 1 MiB at 107 B/item) was silently refused. 16 MiB covers
    # any real batch and still bounds what a hostile local client can allocate.
    MAX_REQUEST_BODY = 16 * 1024 * 1024

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._app = web.Application(client_max_size=self.MAX_REQUEST_BODY)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/queue", self._handle_queue)
        self._app.router.add_post("/api/tasks", self._handle_add_tasks)
        self._app.router.add_options("/api/tasks", self._handle_cors)  # CORS preflight
        # C-05: report an oversized body as 413 (the intended status) instead of
        # letting it surface as "Invalid JSON".
        self._app.middlewares.append(self._body_size_middleware)

        # Callbacks set by MainWindow
        self.add_tasks_callback: Optional[Callable] = None  # (urls: list[dict], one_shot: bool) -> None
        self.get_status_callback: Optional[Callable] = None  # () -> dict

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._thread: Optional[threading.Thread] = None
        # C-01: the last start failure, so the GUI can tell the user why the
        # extension is unreachable (None = started fine / not tried yet).
        self.start_error: Optional[str] = None

    @web.middleware
    async def _body_size_middleware(self, request, handler):
        """C-05: a body over MAX_REQUEST_BODY gets 413, not a confusing 400.

        Two entry points on purpose: a declared `Content-Length` is refused
        before a single body byte is read, and a body without one (chunked) is
        refused by aiohttp's own limit while it accumulates. The handler has to
        re-raise that exception — its catch-all `except Exception` around
        `request.json()` used to turn the size rejection into "Invalid JSON" 400.
        """
        declared = request.content_length
        if declared is not None and declared > self.MAX_REQUEST_BODY:
            return self._too_large(request, declared)
        try:
            return await handler(request)
        except web.HTTPRequestEntityTooLarge as e:
            return self._too_large(request, getattr(e, "actual_size", None))

    def _too_large(self, request, actual_size=None):
        logger.warning(
            f"Rejected oversized request body from {request.remote} "
            f"(actual {actual_size} bytes, limit {self.MAX_REQUEST_BODY})")
        return web.json_response(
            {"error": f"Request body too large (limit {self.MAX_REQUEST_BODY} bytes)"},
            status=413, headers=self._cors_headers(request),
        )

    def set_callbacks(self, add_tasks, get_status):
        """Set callbacks for task management (called from GUI thread)."""
        self.add_tasks_callback = add_tasks
        self.get_status_callback = get_status

    async def start(self):
        """Start the HTTP server (called from asyncio event loop).

        C-01: a port conflict (a second app instance, or any other listener on
        19876) raises here. That used to be swallowed one level up, so the
        window looked perfectly healthy while every extension request failed
        with "Desktop app not reachable". The failure is recorded in
        `start_error` and re-raised for the caller's UI path.
        """
        self.start_error = None
        try:
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
            await self._site.start()
        except Exception as e:
            self.start_error = f"{type(e).__name__}: {e}"
            logger.error(f"Extension API server failed to start on port {self.port}: {e}",
                         exc_info=True)
            raise
        logger.info(f"Extension API server started on http://localhost:{self.port}")

    async def stop(self):
        """Stop the HTTP server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Extension API server stopped")

    def _is_trusted_origin(self, request) -> bool:
        """Browser-originated requests must come from the Chrome extension.

        Requests without an Origin header (curl, scripts, tests) are treated as
        local clients and allowed. This blocks CSRF from arbitrary web pages,
        which previously could POST /api/tasks because CORS was `*`.
        """
        origin = request.headers.get("Origin", "")
        if not origin:
            return True
        if not origin.startswith("chrome-extension://"):
            return False
        # EXT-11: pin to a specific extension ID when configured (stable only
        # after Web Store publication); empty default = permissive compromise.
        if K.EXTENSION_ID:
            return origin == f"chrome-extension://{K.EXTENSION_ID}"
        return True

    def _cors_headers(self, request=None):
        origin = request.headers.get("Origin", "") if request is not None else ""
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
        # Echo the extension origin instead of `*` so browser responses stay readable
        if origin.startswith("chrome-extension://"):
            headers["Access-Control-Allow-Origin"] = origin
        return headers

    async def _handle_cors(self, request):
        return web.Response(status=204, headers=self._cors_headers(request))

    async def _handle_status(self, request):
        """GET /api/status — returns current parsing status."""
        status = {}
        if self.get_status_callback:
            try:
                status = self.get_status_callback()
            except Exception as e:
                logger.error(f"Error getting status: {e}")
                status = {"error": str(e)}
        return web.json_response(status, headers=self._cors_headers(request))

    async def _handle_queue(self, request):
        """GET /api/queue — returns current task queue."""
        status = {}
        if self.get_status_callback:
            try:
                status = self.get_status_callback()
            except Exception as e:
                logger.error(f"Error getting queue: {e}")
                status = {"error": str(e)}
        return web.json_response(status, headers=self._cors_headers(request))

    async def _handle_add_tasks(self, request):
        """POST /api/tasks — add media URLs to the queue.

        Expected JSON body:
        {
            "urls": [
                {"url": "...", "source": "...", "type": "image|video"},
                ...
            ],
            "one_shot": true,
            "settings": {}  // optional override
        }
        """
        if not self._is_trusted_origin(request):
            logger.warning(f"Rejected cross-origin request from {request.headers.get('Origin', '')}")
            return web.Response(status=403, text="Forbidden", headers=self._cors_headers(request))

        try:
            body = await request.json()
        except web.HTTPRequestEntityTooLarge:
            raise  # C-05: the size middleware answers 413; a 400 would misreport it
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400, headers=self._cors_headers(request))

        urls = body.get("urls", [])
        # EXT-4: validate items server-side — a corrupt payload (strings, nulls,
        # missing url) must not 500 the callback, which calls .get on each item.
        urls = [
            u for u in urls
            if isinstance(u, dict) and isinstance(u.get("url"), str) and u["url"].strip()
        ]
        # C-04: `urls` was validated item by item while the three scalars were
        # trusted. Their type is load-bearing: `{"user_agent": 5}` reached
        # aiohttp's header machinery as a TypeError, and a non-str cookie string
        # landed in the task snapshot / settings file as-is.
        one_shot = body.get("one_shot", False)
        if not isinstance(one_shot, bool):
            one_shot = bool(one_shot)
        user_agent = _clean_header_value(body.get("user_agent", ""))
        cookies = _clean_cookie_value(body.get("cookies", ""))

        if not urls:
            return web.json_response({"error": "No valid URLs provided"}, status=400, headers=self._cors_headers(request))

        if self.add_tasks_callback:
            try:
                result = self.add_tasks_callback(urls, one_shot, user_agent=user_agent, cookies=cookies)
                # X-1.4: the callback's own "added" count wins — the server-side
                # count was dead (and misleading after validation filtering).
                return web.json_response({"ok": True, **(result or {})}, headers=self._cors_headers(request))
            except Exception as e:
                logger.error(f"Error adding tasks from extension: {e}")
                return web.json_response({"error": str(e)}, status=500, headers=self._cors_headers(request))

        return web.json_response({"error": "No callback registered"}, status=503, headers=self._cors_headers(request))
