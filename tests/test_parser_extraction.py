#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Stage 3 parser fixes (FIXPLAN_2026-10-15 §2.2) — extraction and content path.

B-03: encoding detection ignored the page's own `<meta charset>` — a
      windows-1251 page with an ASCII prefix was detected as ascii/utf-8 and its
      Cyrillic came back as mojibake. chardet's answer was also used unvalidated,
      so a codec Python does not ship reached `bytes.decode()`.
B-04: `parse()` closed the sync session only on the success path, so the two
      early returns (error status / no content) leaked a live session and its
      connection pool for every failed page.
B-05: the generic `<img>` pass re-collected the inner `<img>` of every
      `<picture>`, which the `<picture>` branch had already expanded — each URL
      was queued twice.
B-06: any attribute whose value merely contained an image extension became a
      fetchable candidate (`alt="sunset.jpg"`), i.e. text metadata was treated
      as a media URL.
B-07: an empty 200 body was treated as a network failure — a second request via
      the sync fallback, then NETWORK_ERROR, which penalised the domain health
      of a server that simply returned nothing.
"""

import asyncio
import codecs
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bs4 import BeautifulSoup

from src.parser.webpage_parser import WebpageParser
from src import constants as K


class FakeResp:
    """Minimal async-CM aiohttp response for `_get_content` tests."""

    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {"Content-Type": "text/html"}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self._body

    async def text(self):
        return self._body.decode("utf-8", "replace")


def make_parser(url="https://site.com/page", settings=None, session=None):
    return WebpageParser(
        url=url, settings=settings or {}, process_js=False,
        external_session=session if session is not None else MagicMock(),
    )


# Nothing may be filtered by size/dimension in the extraction tests: the point
# is which URLs get collected at all, not which pass the filters.
NO_MIN_FILTERS = {
    K.SETTING_MIN_IMG_WIDTH: 0,
    K.SETTING_MIN_IMG_HEIGHT: 0,
    K.SETTING_MIN_IMG_SIZE: 0,
}


class TestDeclaredCharset(unittest.TestCase):
    """B-03: the page's own declaration is used before statistical detection."""

    CYRILLIC = "Привет, мир"

    def _page(self, head: str, codec: str) -> bytes:
        return ("<html><head>%s</head><body>%s</body></html>" % (head, self.CYRILLIC)).encode(codec)

    def test_html5_meta_charset_windows_1251(self):
        content = self._page('<meta charset="windows-1251">', "cp1251")
        parser = make_parser()

        encoding = asyncio.run(parser._detect_encoding(content))
        self.assertEqual(codecs.lookup(encoding).name, "cp1251")
        self.assertIn(self.CYRILLIC, content.decode(encoding, errors="replace"),
                      "the declared 1251 page must not decode into mojibake")

    def test_declaration_outranks_a_wrong_statistical_answer(self):
        # chardet's answer is only the fallback: it reports ascii for a 1251
        # page whose Cyrillic sits past its detection window (the audit's
        # measured case). Pinned deterministically with a stand-in detector.
        content = self._page('<meta charset="windows-1251">', "cp1251")
        parser = make_parser()

        with patch("src.parser.webpage_parser.chardet.detect",
                   return_value={"encoding": "ascii"}):
            encoding = asyncio.run(parser._detect_encoding(content))

        self.assertEqual(codecs.lookup(encoding).name, "cp1251")
        self.assertIn(self.CYRILLIC, content.decode(encoding, errors="replace"))

    def test_long_ascii_prefix_page_still_decodes_as_cyrillic(self):
        # No patching: >16 KB of pure ASCII before the text, which is what made
        # the real page come back as mojibake.
        body = ('<html><head><meta charset="windows-1251"></head><body>'
                + "<div>" * 4000 + self.CYRILLIC + "</body></html>").encode("cp1251")

        encoding = asyncio.run(make_parser()._detect_encoding(body))

        self.assertIn(self.CYRILLIC, body.decode(encoding, errors="replace"))

    def test_legacy_http_equiv_content_type(self):
        content = self._page(
            '<meta http-equiv="Content-Type" content="text/html; charset=windows-1251">',
            "cp1251")
        encoding = asyncio.run(make_parser()._detect_encoding(content))
        self.assertEqual(codecs.lookup(encoding).name, "cp1251")

    def test_bom_outranks_a_declaration(self):
        content = b"\xef\xbb\xbf" + self._page('<meta charset="windows-1251">', "utf-8")
        encoding = asyncio.run(make_parser()._detect_encoding(content))
        self.assertEqual(encoding, "utf-8-sig")

    def test_unknown_declared_codec_falls_back_to_a_usable_one(self):
        content = self._page('<meta charset="totally-bogus-codec">', "utf-8")
        encoding = asyncio.run(make_parser()._detect_encoding(content))
        # Must be decodable — an unvalidated name would raise LookupError here.
        self.assertIn("html", content.decode(encoding, errors="replace"))

    def test_chardet_name_is_validated_before_use(self):
        parser = make_parser()
        with patch("src.parser.webpage_parser.chardet.detect",
                   return_value={"encoding": "MacRoman"}):
            encoding = asyncio.run(parser._detect_encoding(b"<html>plain</html>"))
        # Validated through codecs.lookup() (a name Python knows), not the raw
        # chardet string.
        self.assertEqual(codecs.lookup(encoding).name, "mac-roman")

    def test_chardet_garbage_name_becomes_utf8(self):
        parser = make_parser()
        with patch("src.parser.webpage_parser.chardet.detect",
                   return_value={"encoding": "NoSuchCodec-9000"}):
            encoding = asyncio.run(parser._detect_encoding(b"<html>plain</html>"))
        self.assertEqual(encoding, "utf-8")


class TestSyncSessionClosedOnEarlyReturns(unittest.TestCase):
    """B-04: every exit path of `parse()` releases the sync session."""

    def _parser_with_live_session(self):
        parser = make_parser(session=MagicMock())
        parser._sync_session = MagicMock()
        return parser, parser._sync_session

    def test_error_status_closes_the_session(self):
        parser, session = self._parser_with_live_session()
        parser._get_content = AsyncMock(
            return_value=(None, K.PARSER_HTTP_ERROR_4XX, "Client HTTP error 404", 404))

        result = asyncio.run(parser.parse())

        self.assertEqual(result[2], K.PARSER_HTTP_ERROR_4XX)
        session.close.assert_called_once()
        self.assertIsNone(parser._sync_session, "the leaked session was not released")

    def test_no_content_closes_the_session(self):
        parser, session = self._parser_with_live_session()
        parser._get_content = AsyncMock(return_value=(None, None, "Success", 200))

        result = asyncio.run(parser.parse())

        self.assertEqual(result[2], K.PARSER_UNKNOWN_ERROR)
        session.close.assert_called_once()
        self.assertIsNone(parser._sync_session)

    def test_a_broken_close_does_not_break_the_parse(self):
        parser, session = self._parser_with_live_session()
        session.close.side_effect = RuntimeError("already closed")
        parser._get_content = AsyncMock(return_value=(None, None, "Success", 200))

        result = asyncio.run(parser.parse())
        self.assertEqual(result[2], K.PARSER_UNKNOWN_ERROR)
        self.assertIsNone(parser._sync_session)


class TestPictureAndAttributeExtraction(unittest.TestCase):
    """B-05 / B-06: what gets collected, and only once."""

    def _urls(self, html):
        parser = make_parser(settings=NO_MIN_FILTERS)
        soup = BeautifulSoup(html, "lxml")
        asyncio.run(parser._extract_images(soup))
        return [url for _type, url, _attrs in parser.media_files]

    def test_picture_inner_img_is_collected_once(self):
        html = """
        <html><body><picture>
          <source srcset="https://site.com/a-400.jpg 400w, https://site.com/a-800.jpg 800w">
          <img src="https://site.com/a.jpg" srcset="https://site.com/a-1600.jpg 1600w">
        </picture></body></html>
        """
        urls = self._urls(html)

        self.assertEqual(sorted(urls), sorted(set(urls)),
                         f"a URL was collected twice: {urls}")
        # The picture branch expands the inner <img> fully, so nothing is lost
        # by skipping it in the generic pass.
        self.assertIn("https://site.com/a-1600.jpg", urls)
        self.assertIn("https://site.com/a-400.jpg", urls)
        self.assertIn("https://site.com/a-800.jpg", urls)

    def test_alt_text_is_not_a_media_url(self):
        # The audit's input: an <img> whose only extension-carrying attribute is
        # alt text. It used to be collected as a fetchable URL.
        urls = self._urls('<html><body><img alt="sunset.jpg"></body></html>')
        self.assertEqual(urls, [], f"an alt attribute became a media URL: {urls}")

    def test_other_text_attributes_are_not_urls(self):
        urls = self._urls(
            '<html><body><img title="holiday.png" aria-label="x.gif" '
            'class="thumb.webp" id="hero.avif"></body></html>')
        for leaked in ("holiday.png", "x.gif", "thumb.webp", "hero.avif"):
            self.assertFalse([u for u in urls if leaked in u],
                             f"{leaked} leaked from a text attribute: {urls}")

    def test_a_real_src_still_wins_over_the_alt_text(self):
        urls = self._urls(
            '<html><body><img alt="sunset.jpg" '
            'src="https://site.com/real/photo.jpg"></body></html>')
        self.assertEqual(urls, ["https://site.com/real/photo.jpg"])

    def test_an_unrelated_attribute_is_not_a_url(self):
        # Not in the text-attribute list, not a data-* attribute: it is only
        # accepted when it actually looks like a URL.
        urls = self._urls(
            '<html><body><img custom-thing="sunset.jpg"></body></html>')
        self.assertEqual(urls, [], f"a non-URL attribute was collected: {urls}")

    def test_free_text_in_a_data_attribute_is_not_a_url(self):
        urls = self._urls(
            '<html><body><img data-caption="see sunset.jpg here"></body></html>')
        self.assertEqual(urls, [], f"free text became a URL: {urls}")

    def test_custom_data_attribute_with_a_relative_path_still_works(self):
        # data-* attributes are how lazy loaders carry relative paths; the
        # URL-shape requirement must not apply to them.
        urls = self._urls(
            '<html><body><img data-custom-src="images/lazy.jpg"></body></html>')
        self.assertTrue([u for u in urls if u.endswith("/images/lazy.jpg")],
                        f"a relative data-* path was dropped: {urls}")


class TestMediaTypeRoutes(unittest.TestCase):
    """D-11: the HTML discovery route types media with the shared classifier.

    It used to run four inline `"video" if .mp4/.webm in url else "image"`
    copies, so a .mov / .mkv / .mp3 found on an HTML page was typed "image" —
    and therefore checked against the IMAGE format allowlist.
    """

    def _types(self, attr_name, url):
        parser = make_parser(settings=NO_MIN_FILTERS)
        soup = BeautifulSoup(
            '<html><body><div %s="%s"></div></body></html>' % (attr_name, url),
            "lxml")
        parser._process_data_attribute(soup.find("div"), attr_name)
        return {media_type for media_type, _url, _attrs in parser.media_files}

    def test_mov_in_a_data_attribute_is_video(self):
        self.assertEqual(self._types("data-video", "https://cdn.site.com/clip.mov"),
                         {"video"})

    def test_mp4_in_a_data_attribute_is_video(self):
        self.assertEqual(self._types("data-video", "https://cdn.site.com/clip.mp4"),
                         {"video"})

    def test_mp3_in_a_data_attribute_is_audio(self):
        self.assertEqual(self._types("data-audio", "https://cdn.site.com/song.mp3"),
                         {"audio"})

    def test_jpg_in_a_data_attribute_is_image(self):
        self.assertEqual(self._types("data-src", "https://cdn.site.com/photo.jpg"),
                         {"image"})


class TestEmptyBodyHandling(unittest.TestCase):
    """B-07: an empty 2xx body is an empty page, not a failure."""

    def _get_content(self, status, body):
        session = MagicMock()
        session.get.return_value = FakeResp(status=status, body=body)
        parser = make_parser(
            settings={K.SETTING_RETRY_COUNT: 0, K.SETTING_PAGE_TIMEOUT: 5},
            session=session)
        result = asyncio.run(parser._get_content())
        return parser, result

    def test_empty_200_is_a_clean_success_without_a_second_request(self):
        parser, (content, error_status, message, status) = self._get_content(200, b"")

        self.assertEqual(content, "")
        self.assertEqual(error_status, K.PARSER_SUCCESS)
        self.assertEqual(status, 200)
        self.assertIsNone(parser._sync_session,
                          "the sync fallback made a second request for an empty 200")

    def test_empty_404_stays_an_http_error(self):
        parser, (content, error_status, message, status) = self._get_content(404, b"")

        self.assertIsNone(content)
        self.assertEqual(error_status, K.PARSER_HTTP_ERROR_4XX)
        self.assertEqual(status, 404)
        self.assertIsNone(parser._sync_session)


class TestJfifJifSupport(unittest.TestCase):
    """Verify .jfif and .jif image formats are recognized and handled as images."""

    def test_is_image_url_recognizes_jfif_and_jif(self):
        from src.parser.utils import is_image_url, is_format_allowed, guess_media_type
        for url in ("https://example.com/photo.jfif", "https://example.com/gallery/img.jif",
                    "https://example.com/asset.JFIF?w=800", "https://example.com/pic.JIF#hash"):
            self.assertTrue(is_image_url(url), f"is_image_url failed for {url}")
            self.assertTrue(is_format_allowed(url, "image", {}), f"is_format_allowed failed for {url}")
            self.assertEqual(guess_media_type(url), "image", f"guess_media_type failed for {url}")

    def test_anchor_image_extracted(self):
        from bs4 import BeautifulSoup
        from src.parser.utils import is_image_url
        soup = BeautifulSoup('<a href="https://example.com/full.jif"><img src="https://example.com/thumb.jpg"></a>', "html.parser")
        a = soup.find("a")
        self.assertTrue(is_image_url(a["href"]))


if __name__ == "__main__":
    unittest.main()
