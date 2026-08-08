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

logger = logging.getLogger(__name__)

DEFAULT_PORT = 19876


class ExtensionServer:
    """Lightweight HTTP API server for browser extension communication."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._app = web.Application()
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/queue", self._handle_queue)
        self._app.router.add_post("/api/tasks", self._handle_add_tasks)
        self._app.router.add_options("/api/tasks", self._handle_cors)  # CORS preflight

        # Callbacks set by MainWindow
        self.add_tasks_callback: Optional[Callable] = None  # (urls: list[dict], one_shot: bool) -> None
        self.get_status_callback: Optional[Callable] = None  # () -> dict

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._thread: Optional[threading.Thread] = None

    def set_callbacks(self, add_tasks, get_status):
        """Set callbacks for task management (called from GUI thread)."""
        self.add_tasks_callback = add_tasks
        self.get_status_callback = get_status

    async def start(self):
        """Start the HTTP server (called from asyncio event loop)."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await self._site.start()
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
        return origin.startswith("chrome-extension://")

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
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400, headers=self._cors_headers(request))

        urls = body.get("urls", [])
        one_shot = body.get("one_shot", False)
        user_agent = body.get("user_agent", "")
        cookies = body.get("cookies", "")

        if not urls:
            return web.json_response({"error": "No URLs provided"}, status=400, headers=self._cors_headers(request))

        if self.add_tasks_callback:
            try:
                result = self.add_tasks_callback(urls, one_shot, user_agent=user_agent, cookies=cookies)
                return web.json_response({"ok": True, "added": len(urls), **(result or {})}, headers=self._cors_headers(request))
            except Exception as e:
                logger.error(f"Error adding tasks from extension: {e}")
                return web.json_response({"error": str(e)}, status=500, headers=self._cors_headers(request))

        return web.json_response({"error": "No callback registered"}, status=503, headers=self._cors_headers(request))
