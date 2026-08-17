import asyncio
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


if __name__ == '__main__':
    unittest.main()
