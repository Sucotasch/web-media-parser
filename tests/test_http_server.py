#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EXT-4 / X-1: POST /api/tasks validation, origin trust, callback semantics."""

import os
import sys
import asyncio
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.server.http_server import ExtensionServer


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


if __name__ == "__main__":
    unittest.main()
