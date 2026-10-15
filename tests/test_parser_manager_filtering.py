import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from src.parser.parser_manager import ParserManager
from src import constants as K

from helpers import MockGUILogHandler


class TestParserManagerFiltering(unittest.TestCase):

    def setUp(self):
        # Basic settings for ParserManager initialization
        self.settings = {
            K.SETTING_SEARCH_DEPTH: K.DEFAULT_SEARCH_DEPTH,
            K.SETTING_STAY_IN_DOMAIN: False,  # Disable to test blocked_domains independently
            K.SETTING_STOP_WORDS: [],
            # Add other minimal required settings if ParserManager constructor needs them
        }
        self.mock_log_handler = MockGUILogHandler()
        
        # Patch the _load_domain_blocklist to avoid actual file I/O during these tests
        # and to control the blocked domains directly.
        self.patcher = patch('src.parser.parser_manager.ParserManager._load_domain_blocklist')
        self.mock_load_blocklist = self.patcher.start()
        
        self.base_download_path = "test_downloads" # Dummy path

    def tearDown(self):
        self.patcher.stop()

    @patch('src.parser.parser_manager.logger') # Mock the logger used in ParserManager
    def test_domain_filtering_in_process_parser_results(self, mock_pm_logger):
        """
        Test that _process_parser_results correctly filters URLs from blocked domains.
        """
        # Set specific blocked domains for this test via the mocked _load_domain_blocklist
        blocked_domains_set = {'blockeddomain.com', 'anotherblock.org'}
        self.mock_load_blocklist.return_value = blocked_domains_set

        # Initialize ParserManager, it will use the mocked _load_domain_blocklist
        # It's important that AsyncClientManager does not try to make real connections in unit tests
        # Assuming AsyncClientManager is robust to not needing a running loop for basic init, or mock it too if needed.
        # For this test, we are focusing on the filtering logic, so we mock url_queue.put
        with patch('src.parser.parser_manager.AsyncClientManager', MagicMock()): # Mock AsyncClientManager
            parser_manager = ParserManager(
                url="http://startdomain.com", 
                download_path=self.base_download_path, 
                settings=self.settings, 
                log_handler=self.mock_log_handler
            )
        
        # Ensure blocked_domains is set as expected
        self.assertEqual(parser_manager.blocked_domains, blocked_domains_set)

        # Mock the url_queue.put method to track calls
        parser_manager.url_queue.put = AsyncMock()

        # Test URLs
        current_parsed_url = "http://startdomain.com/currentpage"
        depth = 1
        original_url_context = {"start_url": "http://startdomain.com"}

        links_data_from_parser = {
            "http://gooddomain.com/page1": {"text": "Good Link 1"},
            "https://blockeddomain.com/badpage": {"text": "Blocked Link 1"},
            "http://anotherblock.org/resource.jpg": {"text": "Blocked Resource Link"},
            "http://gooddomain.com/anotherpage": {"text": "Good Link 2"},
            "ftp://blockeddomain.com/file": {"text": "Blocked FTP (should also be skipped by domain)"}
        }
        
        # Simulate calling _process_parser_results
        # This method processes media files and then links. We are interested in the link processing part.
        # For simplicity, we pass empty media_files.
        async def _run_test():
            await parser_manager._process_parser_results(
                url=current_parsed_url,
                depth=depth,
                links_data=links_data_from_parser,
                media_files=[], # No media files for this specific test focus
                original_url_context=original_url_context
            )

        asyncio.run(_run_test())

        # Assertions
        # Check which URLs were attempted to be put into the queue
        put_calls = parser_manager.url_queue.put.call_args_list
        
        actual_queued_urls = [call[0][0] for call in put_calls] # First arg of first call tuple is the URL

        self.assertIn("http://gooddomain.com/page1", actual_queued_urls)
        self.assertIn("http://gooddomain.com/anotherpage", actual_queued_urls)
        
        self.assertNotIn("https://blockeddomain.com/badpage", actual_queued_urls)
        self.assertNotIn("http://anotherblock.org/resource.jpg", actual_queued_urls)
        self.assertNotIn("ftp://blockeddomain.com/file", actual_queued_urls) 

        # TST-3: behavior is asserted above (blocked URLs absent from the queue);
        # the per-line log-message assertions were removed — they were fragile
        # to message rephrasing and duplicated the assertNotIn checks.

        # Ensure it tried to queue exactly two URLs
        self.assertEqual(parser_manager.url_queue.put.call_count, 2)


class TestDomainBlocklistCoversSubdomains(unittest.TestCase):
    """A-04: a blocklist entry blocks the whole site, not just the exact netloc.

    The check compared the raw netloc against the file entries, so
    "blockeddomain.com" in the blocklist never stopped "img.blockeddomain.com"
    (measured: 6 subdomains crawled) and a host carrying a port matched nothing.
    """

    def _manager(self, blocked, start_url="http://startdomain.com"):
        with patch('src.parser.parser_manager.ParserManager._load_domain_blocklist',
                   return_value=set(blocked)):
            with patch('src.parser.parser_manager.AsyncClientManager', MagicMock()):
                pm = ParserManager(
                    url=start_url, download_path="test_downloads",
                    settings={
                        K.SETTING_SEARCH_DEPTH: 5,  # deep enough to queue depth-2 links
                        K.SETTING_STAY_IN_DOMAIN: False,
                        K.SETTING_STOP_WORDS: [],
                    },
                    log_handler=MockGUILogHandler(),
                )
        pm.url_queue.put = AsyncMock()
        return pm

    def test_subdomains_of_a_blocked_domain_are_not_queued(self):
        pm = self._manager({'blockeddomain.com'})
        links = {
            "https://www.blockeddomain.com/page": {},
            "https://img.blockeddomain.com/photo.jpg": {},
            "https://a.b.blockeddomain.com/page": {},
            "https://notblockeddomain.com/page": {},
            "https://blockeddomain.com.evil.com/page": {},
        }

        asyncio.run(pm._process_parser_results(
            url="http://startdomain.com/current", depth=1, links_data=links,
            media_files=[], original_url_context={"start_url": "http://startdomain.com"},
        ))

        queued = [call[0][0] for call in pm.url_queue.put.call_args_list]
        self.assertNotIn("https://www.blockeddomain.com/page", queued)
        self.assertNotIn("https://img.blockeddomain.com/photo.jpg", queued)
        self.assertNotIn("https://a.b.blockeddomain.com/page", queued)
        # A look-alike host that merely CONTAINS the blocked name is a different
        # site and must still be crawled (suffix matching on the name boundary).
        self.assertIn("https://notblockeddomain.com/page", queued)
        self.assertIn("https://blockeddomain.com.evil.com/page", queued)
        self.assertEqual(len(queued), 2, queued)

    def test_matching_details(self):
        pm = self._manager({'blocked.com', '1.2.3.4'})
        self.assertTrue(pm._is_domain_blocked('blocked.com'))
        self.assertTrue(pm._is_domain_blocked('img.blocked.com'))
        self.assertTrue(pm._is_domain_blocked('IMG.Blocked.COM:8080'))
        self.assertTrue(pm._is_domain_blocked('user:pw@img.blocked.com'))
        self.assertTrue(pm._is_domain_blocked('1.2.3.4'))
        self.assertFalse(pm._is_domain_blocked('notblocked.com'))
        self.assertFalse(pm._is_domain_blocked('blocked.com.evil.com'))
        self.assertFalse(pm._is_domain_blocked(''))

    def test_loader_normalizes_entries_and_keeps_ips_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'blocklist.txt')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('# comment\n\nWWW.Blocked.com\n*.wild.com\n1.2.3.4\n')

            with patch('src.parser.parser_manager.AsyncClientManager', MagicMock()):
                pm = ParserManager(
                    url="http://startdomain.com", download_path="test_downloads",
                    settings={}, log_handler=MockGUILogHandler(),
                )
            blocked = pm._load_domain_blocklist(blocklist_file_name=path)

        self.assertIn('blocked.com', blocked)
        self.assertIn('wild.com', blocked)
        self.assertIn('1.2.3.4', blocked)
        # Deriving a base domain from an IP would produce the nonsense "3.4",
        # which would block unrelated hosts ending in .3.4.
        self.assertNotIn('3.4', blocked)


if __name__ == '__main__':
    unittest.main()
