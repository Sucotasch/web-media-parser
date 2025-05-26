import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from src.parser.shared_session import AsyncClientManager
from src import constants as K # For accessing default header constants

class TestAsyncClientManager(unittest.TestCase):

    def test_basic_session_acquisition(self):
        """Test that a session can be acquired via async with."""
        settings = {
            K.SETTING_PAGE_TIMEOUT: K.DEFAULT_PAGE_TIMEOUT,
            K.SETTING_USER_AGENT: K.DEFAULT_USER_AGENT,
            K.SETTING_ACCEPT_LANGUAGE: K.DEFAULT_ACCEPT_LANGUAGE,
        }
        manager = AsyncClientManager(settings=settings)

        async def _test():
            async with manager as session:
                self.assertIsNotNone(session)
                self.assertFalse(session.closed)
            # After exiting context, session should be closed by AsyncClientManager
            self.assertTrue(manager._session is None or manager._session.closed)

        asyncio.run(_test())

    @patch('src.parser.shared_session.aiohttp.ClientSession')
    def test_session_creation_with_default_headers(self, mock_aiohttp_session_class):
        """Test that ClientSession is called with default headers from K constants."""
        
        # Mock the session instance that will be created
        mock_session_instance = AsyncMock()
        mock_aiohttp_session_class.return_value = mock_session_instance
        
        settings = {
            K.SETTING_PAGE_TIMEOUT: 30, # Example value
            # K.SETTING_USER_AGENT is intentionally omitted to test fallback to K.DEFAULT_USER_AGENT
            # K.SETTING_ACCEPT_LANGUAGE is intentionally omitted for K.DEFAULT_ACCEPT_LANGUAGE
        }
        manager = AsyncClientManager(settings=settings)

        expected_headers = {
            "User-Agent": K.DEFAULT_USER_AGENT,
            "Accept-Language": K.DEFAULT_ACCEPT_LANGUAGE,
            "Accept": K.DEFAULT_ACCEPT_HEADER, # As defined in AsyncClientManager._get_default_headers
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
        }
        
        expected_timeout_config = aiohttp.ClientTimeout(
            total=settings[K.SETTING_PAGE_TIMEOUT],
            connect=K.DEFAULT_CONNECT_TIMEOUT, 
            sock_read=settings[K.SETTING_PAGE_TIMEOUT] 
        )


        async def _test():
            async with manager as session:
                self.assertIsNotNone(session)
            
            # Check that aiohttp.ClientSession was called correctly
            mock_aiohttp_session_class.assert_called_once()
            
            # Get the actual arguments passed to aiohttp.ClientSession constructor
            args, kwargs = mock_aiohttp_session_class.call_args
            
            # Check headers
            self.assertIn("headers", kwargs)
            self.assertEqual(kwargs["headers"], expected_headers)
            
            # Check timeout (aiohttp.ClientTimeout objects might not be directly comparable if defaults differ subtly)
            self.assertIn("timeout", kwargs)
            actual_timeout: aiohttp.ClientTimeout = kwargs["timeout"]
            self.assertEqual(actual_timeout.total, expected_timeout_config.total)
            self.assertEqual(actual_timeout.connect, expected_timeout_config.connect)
            self.assertEqual(actual_timeout.sock_read, expected_timeout_config.sock_read)


        # Need to import aiohttp for ClientTimeout comparison if not already done
        import aiohttp # Moved import here for clarity on where ClientTimeout comes from
        asyncio.run(_test())

    def test_session_recreation_if_closed(self):
        """Test that a new session is created if the previous one was closed."""
        settings = {}
        manager = AsyncClientManager(settings=settings)

        async def _test():
            session1_id = None
            session2_id = None
            
            async with manager as session1:
                self.assertIsNotNone(session1)
                session1_id = id(session1)
                # Manually close the session as if it were closed externally or due to an error
                await session1.close() 
            
            self.assertTrue(manager._session is None or manager._session.closed)

            async with manager as session2:
                self.assertIsNotNone(session2)
                session2_id = id(session2)
            
            self.assertNotEqual(session1_id, session2_id, "A new session object should have been created.")
            self.assertTrue(manager._session is None or manager._session.closed)


        asyncio.run(_test())

if __name__ == '__main__':
    unittest.main()
