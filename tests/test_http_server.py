#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EXT-4 / X-1 / C-04 / C-05: POST /api/tasks validation, origin trust, callback
semantics, scalar cleaning, and the real oversized-body (413) wire path."""

import os
import sys
import json
import socket
import asyncio
import unittest

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.server.http_server import ExtensionServer, MAX_SCALAR_LENGTH


class FakeRequest:
    """Minimal aiohttp Request stand-in for handler unit tests."""

    def __init__(self, body, origin=""):
        self._body = body
        self.headers = {"Origin": origin} if origin else {}

    async def json(self):
        if not isinstance(self._body, dict):
            raise ValueError("invalid json")
        return self._body


class TestAddTasksValidation(unittest.TestCase):
    def _server(self):
        s = ExtensionServer()
        s.received = []
        s.add_tasks_callback = lambda urls, one_shot, **kw: (
            s.received.append((urls, one_shot)), {"added": len(urls)}
        )[1]
        return s

    def test_non_dict_and_empty_urls_filtered(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest(
            {"urls": [{"url": "https://a.example/1.jpg"},
                      "just-a-string",
                      None,
                      {"url": "   "},
                      {"url": "https://b.example/2.jpg"}],
             "one_shot": True})))
        self.assertEqual(resp.status, 200)
        urls, one_shot = s.received[0]
        self.assertEqual([u["url"] for u in urls],
                         ["https://a.example/1.jpg", "https://b.example/2.jpg"])
        self.assertTrue(one_shot)

    def test_no_valid_urls_returns_400(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest(
            {"urls": ["junk", {"url": "  "}, 42]})))
        self.assertEqual(resp.status, 400)
        self.assertFalse(s.received)

    def test_missing_urls_key_returns_400(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest({"one_shot": True})))
        self.assertEqual(resp.status, 400)

    def test_invalid_json_returns_400(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest("not json")))
        self.assertEqual(resp.status, 400)


class TestOriginTrust(unittest.TestCase):
    def _server(self):
        s = ExtensionServer()
        s.add_tasks_callback = lambda urls, one_shot, **kw: {"added": len(urls)}
        return s

    def test_chrome_extension_origin_allowed(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest(
            {"urls": [{"url": "https://a.example/1.jpg"}]},
            origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop")))
        self.assertEqual(resp.status, 200)

    def test_foreign_origin_rejected(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest(
            {"urls": [{"url": "https://a.example/1.jpg"}]},
            origin="https://evil.example")))
        self.assertEqual(resp.status, 403)

    def test_no_origin_is_local_client_allowed(self):
        s = self._server()
        resp = asyncio.run(s._handle_add_tasks(FakeRequest(
            {"urls": [{"url": "https://a.example/1.jpg"}]})))
        self.assertEqual(resp.status, 200)


class TestScalarCleaning(unittest.TestCase):
    """C-04 — the three scalars in the body are typed and cleaned, not trusted.

    Their type is load-bearing: `{"user_agent": 5}` reached aiohttp's header
    machinery as a TypeError, and a non-string cookie string was stored in the
    task snapshot as-is.
    """

    def _post(self, body):
        s = ExtensionServer()
        s.seen = {}

        def cb(urls, one_shot, **kw):
            s.seen = {"one_shot": one_shot, **kw}
            return {"added": len(urls)}

        s.add_tasks_callback = cb
        resp = asyncio.run(s._handle_add_tasks(FakeRequest(body)))
        return s, resp

    def test_non_string_scalars_never_reach_aiohttp(self):
        s, resp = self._post({"urls": [{"url": "https://a.example/1.jpg"}],
                              "user_agent": 5, "cookies": {"a": "b"}})
        self.assertEqual(resp.status, 200)          # not a 500 TypeError
        self.assertEqual(s.seen["user_agent"], "")
        self.assertEqual(s.seen["cookies"], "")

    def test_one_shot_is_coerced_to_a_real_bool(self):
        s, resp = self._post({"urls": [{"url": "https://a.example/1.jpg"}],
                              "one_shot": "yes"})
        self.assertEqual(resp.status, 200)
        self.assertIs(s.seen["one_shot"], True)

    def test_crlf_cannot_be_injected_through_a_header_value(self):
        s, _ = self._post({"urls": [{"url": "https://a.example/1.jpg"}],
                           "user_agent": "Mozilla/5.0\r\nX-Injected: 1"})
        self.assertNotIn("\n", s.seen["user_agent"])
        self.assertNotIn("\r", s.seen["user_agent"])

    def test_scalars_are_truncated_to_the_cap(self):
        s, _ = self._post({"urls": [{"url": "https://a.example/1.jpg"}],
                           "user_agent": "U" * 2000,
                           "cookies": "c=1; " * 500})
        self.assertEqual(len(s.seen["user_agent"]), MAX_SCALAR_LENGTH)
        self.assertEqual(len(s.seen["cookies"]), MAX_SCALAR_LENGTH)


class _SizeRequest:
    """Request double for the middleware branch that carries no Content-Length."""

    def __init__(self, content_length=None):
        self.content_length = content_length
        self.headers = {}
        self.remote = "127.0.0.1"


class TestRequestBodySize(unittest.TestCase):
    """C-05 / NF-6 — a real client against a real listening socket, not mocks.

    The defect was wire-level: aiohttp's 1 MiB default refused a legitimate
    ~1.1 MiB batch, and the handler's catch-all `except Exception` rewrote the
    size rejection as "Invalid JSON" 400, so the extension showed nonsense.
    """

    def _server(self):
        s = ExtensionServer(port=0)
        s.batches = []
        s.add_tasks_callback = lambda urls, one_shot, **kw: (
            s.batches.append(len(urls)), {"added": len(urls)})[1]
        return s

    def test_11mib_batch_accepted(self):
        s = self._server()
        count = 12000
        payload = json.dumps({
            "urls": [
                {"url": f"https://a.example/gallery/{i:05d}.jpg",
                 "source": "https://a.example/post/12345",
                 "type": "image",
                 "title": f"Post image {i:05d}"}
                for i in range(count)
            ],
            "one_shot": True,
        }).encode()
        self.assertGreater(len(payload), 1100 * 1024)  # over aiohttp's 1 MiB default

        async def run():
            await s.start()
            try:
                port = s._site._server.sockets[0].getsockname()[1]
                async with aiohttp.ClientSession() as cli:
                    async with cli.post(f"http://127.0.0.1:{port}/api/tasks",
                                        data=payload,
                                        headers={"Content-Type": "application/json"}) as r:
                        return r.status, await r.json()
            finally:
                await s.stop()

        status, body = asyncio.run(run())
        self.assertEqual(status, 200)
        self.assertEqual(body["added"], count)
        self.assertEqual(s.batches, [count])

    def test_17mib_body_rejected_with_413(self):
        s = self._server()
        status, body = asyncio.run(self._oversized_roundtrip(s, 17 * 1024 * 1024))
        self.assertEqual(status, 413)
        self.assertIn("too large", body["error"])
        self.assertEqual(s.batches, [])  # nothing reached the queue

    @staticmethod
    async def _oversized_roundtrip(s, declared):
        """Send headers only, declaring `declared` body bytes.

        Pushing 17 MiB through the loop would race the server's early refusal
        (it answers before the body is read, so the client's send may fail with
        the response already in its receive buffer). Declaring the size without
        sending it exercises exactly the same refusal path, deterministically.
        """
        await s.start()
        try:
            port = s._site._server.sockets[0].getsockname()[1]
            loop = asyncio.get_running_loop()
            sock = socket.socket()
            sock.setblocking(False)
            raw = b""
            try:
                await loop.sock_connect(sock, ("127.0.0.1", port))
                await loop.sock_sendall(sock, (
                    "POST /api/tasks HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {declared}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode())
                while b"\r\n\r\n" not in raw or not raw.endswith(b"}\n"):
                    try:
                        chunk = await asyncio.wait_for(loop.sock_recv(sock, 65536), timeout=5)
                    except asyncio.TimeoutError:
                        break
                    if not chunk:
                        break  # server closed after the response
                    raw += chunk
            finally:
                sock.close()
        finally:
            await s.stop()
        head, _, rest = raw.partition(b"\r\n\r\n")
        return int(head.split(b"\r\n", 1)[0].split()[1]), json.loads(rest)

    def test_chunked_body_over_limit_becomes_413(self):
        """No Content-Length (chunked): aiohttp trips its limit while reading,
        and the middleware — not the handler — must be the one to answer."""
        s = ExtensionServer()

        async def handler(request):
            raise web.HTTPRequestEntityTooLarge(s.MAX_REQUEST_BODY,
                                                s.MAX_REQUEST_BODY + 1)

        resp = asyncio.run(s._body_size_middleware(_SizeRequest(), handler))
        self.assertEqual(resp.status, 413)

    def test_declared_length_refused_before_any_body_is_read(self):
        s = ExtensionServer()
        handler_calls = []

        async def handler(request):
            handler_calls.append(1)
            return web.Response(text="ok")

        resp = asyncio.run(s._body_size_middleware(
            _SizeRequest(s.MAX_REQUEST_BODY + 1), handler))
        self.assertEqual(resp.status, 413)
        self.assertEqual(handler_calls, [])


if __name__ == "__main__":
    unittest.main()
