#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Priority URL Queue implementation for intelligent URL processing
"""

import asyncio
import logging
from typing import Dict, Any, Tuple, Optional, Set
import re
from urllib.parse import urlparse
from heapq import heappush, heappop
from dataclasses import dataclass, field
from datetime import datetime
from src.parser.utils import is_media_url
from src import constants as K

logger = logging.getLogger(__name__)


@dataclass(order=True)
class PrioritizedURL:
    """A URL with priority information"""

    priority: float
    url: str = field(compare=False)
    depth: int = field(compare=False)
    timestamp: datetime = field(default_factory=datetime.now, compare=False)
    source_url: str = field(default="", compare=False)
    context: dict = field(default_factory=dict, compare=False)


class PriorityURLQueue:
    """Intelligent URL queue with priority-based processing"""

    def __init__(self, settings: Dict[str, Any] = None):
        self._queue = []
        self._url_scores: Dict[str, float] = {}
        self._domain_scores: Dict[str, float] = {}
        self._url_patterns: Dict[str, int] = {}
        self.settings = settings or {}
        # NOTE: _lock and _not_empty are asyncio primitives.
        # They are created lazily in reset() which is called from
        # ParserManager.start_parsing() after the event loop is running.
        self._lock = None
        self._not_empty = None
        self._waiters = []

    def reset_async_primitives(self):
        """(Re)create asyncio primitives. Must be called from within a running event loop."""
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        self._waiters = []
        self._queue.clear()

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL"""
        try:
            return urlparse(url).netloc
        except:
            return ""
            
    def _is_downward_url(self, url: str, source_url: str, context: dict = None) -> bool:
        """
        Check if target URL is downward/related from source URL.
        """
        context = context or {}
        # 0. Bypass: If this is an interstitial recovery task, ALWAYS allow it
        if context.get("interstitial_retry"):
            logger.debug(f"Allowing out-of-domain URL for interstitial recovery: {url}")
            return True
            
        # 1. Global Bypass: if STAY_IN_DOMAIN is disabled, allow everything
        stay_in_domain = self.settings.get(K.SETTING_STAY_IN_DOMAIN, K.DEFAULT_STAY_IN_DOMAIN)
        if not stay_in_domain:
            return True

        if not source_url:
            return True
        
        url_parsed = urlparse(url)
        source_parsed = urlparse(source_url)
        
        url_domain = url_parsed.netloc.lower()
        source_domain = source_parsed.netloc.lower()
        
        logger.debug(f"Comparing domains - Source: {source_domain}, Target: {url_domain}")
        
        # First check: exact domain match
        exact_domain_match = (url_domain == source_domain)
        if exact_domain_match:
            logger.debug(f"Exact domain match between {source_domain} and {url_domain}")
            # Exact domain match is always allowed to proceed to path checking
            pass
        else:
            # If not exact match, check subdomain relationships
            
            # Check if target URL is a parent domain of the source URL
            if source_domain.endswith('.' + url_domain):
                # Target is a parent domain of source - this is going UP, not allowed
                logger.debug(f"Rejected: Target {url_domain} is parent domain of source {source_domain}")
                return False
                
            # Allow subdomains of the original domain - these are considered "sideways" but valid
            # Extract base domains for comparison
            url_parts = url_domain.split('.')
            source_parts = source_domain.split('.')
            
            # Get the main domain (usually last 2 parts, e.g. livejournal.com)
            url_main_domain = '.'.join(url_parts[-2:]) if len(url_parts) >= 2 else url_domain
            source_main_domain = '.'.join(source_parts[-2:]) if len(source_parts) >= 2 else source_domain
            
            # If main domains match, allow it - this allows subdomains of the same parent domain
            if url_main_domain == source_main_domain:
                logger.debug(f"Related domains (same parent domain) - allowing: {source_domain} and {url_domain}")
                # Allow related domains to proceed to path checking
                # This lets us crawl within the same site but different subdomains
                # Example: from blog.example.com to images.example.com is OK
                pass
            else:
                # Completely different domains - reject
                logger.debug(f"Domains don't match and aren't related: {source_domain} vs {url_domain} - rejecting")
                return False
        
        # Get normalized paths
        url_path = url_parsed.path.lower().strip('/')
        source_path = source_parsed.path.lower().strip('/')
        
        logger.debug(f"Comparing paths - Source: /{source_path}, Target: /{url_path}")
        
        # If source path is empty (homepage), all paths on same domain are downward
        if not source_path:
            logger.debug(f"Source is homepage, target path is {'downward' if len(url_path) > 0 else 'not downward'}")
            return len(url_path) > 0
            
        # First, try exact path matching for subpaths
        if url_path.startswith(source_path):
            # If it's the same path with query/fragment differences, that's fine
            if url_path == source_path:
                logger.debug(f"Same path: /{url_path} - considering downward")
                return True
                
            # Check if it's actually a deeper path (not just a partial string match)
            if len(url_path) > len(source_path):
                # Either next char is '/' or source_path already ended with '/'
                if url_path[len(source_path)] == '/' or source_path.endswith('/'):
                    logger.debug(f"Deeper path: /{url_path} is a subpath of /{source_path} - considering downward")
                    return True
                else:
                    logger.debug(f"Partial match but not subpath: /{url_path} vs /{source_path} - still checking other criteria")
        
        # Check for common directories - if they share the first N path components
        source_components = [c for c in source_path.split('/') if c]
        url_components = [c for c in url_path.split('/') if c]
        
        # If at least one component matches, consider it related enough
        if len(source_components) > 0 and len(url_components) > 0 and source_components[0] == url_components[0]:
            logger.debug(f"URLs share common root directory: {source_components[0]} - considering related")
            return True
        
        # Special cases: some content-pattern URLs might be considered downward even if not subpaths
        
        # Allow sibling content - if both paths contain numeric IDs in the same position, consider them related
        source_id_match = re.search(r'/(\d+)(?:/|$)', source_path)
        url_id_match = re.search(r'/(\d+)(?:/|$)', url_path)
        
        if source_id_match and url_id_match:
            # Check if the parts before the IDs match (they're in the same section)
            source_prefix = source_path[:source_id_match.start(1) - 1]  # Path up to the ID
            url_prefix = url_path[:url_id_match.start(1) - 1]  # Path up to the ID
            
            if source_prefix == url_prefix:
                logger.debug(f"Sibling content pages with IDs - considering related: {source_path} and {url_path}")
                return True
        
        # Extract folder components from both paths
        source_components = [c for c in source_path.split('/') if c]
        url_components = [c for c in url_path.split('/') if c]
        
        # If they share at least the first component, consider them related
        if len(source_components) > 0 and len(url_components) > 0 and source_components[0] == url_components[0]:
            logger.debug(f"URLs share first path component: {source_components[0]} - considering related")
            return True
            
        # If they're both in blog or content sections, allow it
        content_patterns = ['post', 'article', 'blog', 'entry', 'content', 'page', 'story', 'news']
        if any(pattern in source_path for pattern in content_patterns) and any(pattern in url_path for pattern in content_patterns):
            logger.debug(f"Both URLs appear to be content pages - considering related")
            return True
            
        # Last chance check: both paths are quite short (both at root level or first level)
        if len(source_components) <= 1 and len(url_components) <= 2:
            logger.debug(f"Both URLs are at top-level directories - allowing exploration")
            return True
            
        logger.debug(f"No relationship found between {source_url} and {url} - rejecting")
        return False
            
    def _is_likely_content_page(self, url: str) -> bool:
        """
        Detect if a URL is likely to be a content page based on patterns
        """
        path = urlparse(url).path.lower()
        query = urlparse(url).query.lower()
        fragment = urlparse(url).fragment.lower()
        
        # Known content patterns
        content_patterns = [
            "view", "show", "gallery", "album", "photo", "image", "pic", "media", "full", 
            "display", "post", "entry", "article", "story", "video", "watch", "page", "item",
            "content", "viewer", "collection", "detail", "preview", "original", "fullsize", "large"
        ]
        
        # Check path for content indicators
        if any(pattern in path for pattern in content_patterns):
            logger.debug(f"Boosting likely content URL: {url}")
            return True
            
        # Check query and fragment for content indicators
        if any(pattern in query for pattern in content_patterns) or any(pattern in fragment for pattern in content_patterns):
            logger.debug(f"Boosting likely content URL (query/fragment): {url}")
            return True
            
        # Check for numeric IDs in URL which often indicate content pages
        if re.search(r'/\d+', path):
            logger.debug(f"Boosting numeric ID URL: {url}")
            return True
            
        # Check for date patterns in URL which often indicate content pages
        if re.search(r'/(19|20)\d{2}/(0[1-9]|1[0-2])/([0-2][0-9]|3[01])', path) or re.search(r'/(0[1-9]|1[0-2])/([0-2][0-9]|3[01])/(19|20)\d{2}', path):
            logger.debug(f"Boosting date pattern URL: {url}")
            return True
            
        return False

    def _calculate_url_priority(
        self, url: str, depth: int, source_url: str = "", context: dict = None
    ) -> float:
        """
        Calculate URL priority based on multiple factors:
        - Downward path enforcement (only follow links deeper than source URL)
        - Media context (higher priority for links from thumbnails/images)
        - Path similarity (higher priority for URLs in same section as starting URL)
        - URL patterns (prioritize URLs similar to successful ones)
        - Domain reputation (based on media count success)
        - Depth (modified to not overly penalize deeper URLs on same path)
        - Media first processing (prioritize media URLs over navigation)
        """
        # Check if this is a downward URL from the source
        # If not, give it zero priority which will effectively skip it
        if source_url and not self._is_downward_url(url, source_url, context):
            logger.debug(f"Skipping URL: {url} (not considered related to {source_url})")
            return 0.0
            
        # Add an explicit log for URLs that pass the downward check
        logger.debug(f"URL {url} passed the relationship check with {source_url} - calculating priority")
            
        base_priority = 1.0
        context = context or {}
        
        # Check if this is a direct media URL (image or video file) - highest priority
        if is_media_url(url):
            # Major boost for direct media URLs
            base_priority *= 25.0
            logger.debug(f"Maximum priority boost for direct media URL: {url}")
            
            # If this is from the current page being processed, boost even further
            if source_url:
                # Boost media from the initial page
                if source_url == context.get('start_url', ''):
                    base_priority *= 2.5  # High boost for media from initial page
                    logger.debug(f"Additional boost for media from initial page: {url}")
                # Also boost media from the current source page
                elif source_url == context.get('source_url', ''):
                    base_priority *= 2.0  # Boost for media from current page
                    logger.debug(f"Additional boost for media from current page: {url}")
        
        # Explicit priority (if provided in context)
        if 'priority' in context:
            base_priority *= context['priority']
            
        # Interstitial retry boost
        if context.get("interstitial_retry"):
            base_priority *= 30.0 # Extreme boost to resolve immediately
            
        # Media context factor (high priority boost)
        # If this URL was found in an image context (e.g., inside <a> containing <img>)
        elif context.get('from_image', False):
            base_priority *= 20.0  # Major boost for image-linked content
        
        # Path similarity to initial/source URL (stay in same section)
        # This is the MOST important factor to keep exploration near user's starting point
        if source_url:
            # First, check if it's an exact sub-path of source URL
            source_domain = urlparse(source_url).netloc.lower()
            source_path = urlparse(source_url).path.lower()
            current_path = urlparse(url).path.lower()
            domain = urlparse(url).netloc.lower()
            
            # If the URL is from the same domain as the original URL
            if domain == source_domain:
                # Major boost for being on the same domain as source
                base_priority *= 2.0
                # Don't log this as it happens for every URL on the same domain
                
                # Calculate path similarity (common directory structure)
                source_parts = [p for p in source_path.split("/") if p]
                current_parts = [p for p in current_path.split("/") if p]
                
                # Find common path prefix length
                common_length = 0
                for i in range(min(len(source_parts), len(current_parts))):
                    if source_parts[i] == current_parts[i]:
                        common_length += 1
                    else:
                        break
                
                # Strong boost for sharing path prefix with original URL
                if common_length > 0:
                    # The more path components in common, the higher the boost
                    path_similarity_factor = 3.0 + (common_length * 2.0)
                    base_priority *= path_similarity_factor
                    logger.debug(f"Boosting URL with similar path: {url} (common={common_length})")
                    
                # Check if this looks like a sibling page of the source URL
                # (same parent directory but different file/endpoint)
                if len(source_parts) >= 1 and len(current_parts) >= 1 and common_length == len(source_parts) - 1:
                    base_priority *= 3.0  # Boost sibling pages
                    logger.debug(f"Boosting sibling page: {url}")
        
        # Domain reputation
        domain = self._get_domain(url)
        if domain in self._domain_scores:
            domain_factor = 1.0 + self._domain_scores[domain]
            base_priority *= domain_factor

        # URL pattern similarity - enhanced with more gallery patterns
        path = urlparse(url).path.lower()
        query = urlparse(url).query.lower()
        full_url = url.lower()
        
        pattern_score = 0
        for pattern, count in self._url_patterns.items():
            if pattern in path or pattern in query:
                pattern_score += count
        if pattern_score > 0:
            pattern_factor = 1.0 + (pattern_score / max(self._url_patterns.values()))
            base_priority *= pattern_factor
            
        # Detect and heavily deprioritize homepage and navigation URLs
        # Check if this is a root/homepage URL (just domain with no path or minimal path)
        parsed = urlparse(url)
        
        # Define patterns that identify homepage URLs
        homepage_paths = ['', '/', '/index.html', '/index.php', '/home', '/main',
                        '/index', '/default.aspx', '/default.html', '/home.html']
        
        # Main domain check (e.g., example.com/ or www.example.com/)
        is_homepage = parsed.path.lower() in homepage_paths
        
        # Check if URL is just domain or www subdomain
        domain_parts = domain.split('.')
        if len(domain_parts) <= 3 and (domain_parts[0] == 'www' or len(domain_parts) == 2):
            if parsed.path == '/' or parsed.path == '':
                is_homepage = True
        
        # Another homepage indicator: no path but has query parameters (e.g., example.com/?page=1)
        if (parsed.path == '/' or parsed.path == '') and parsed.query:
            is_homepage = True
            
        if is_homepage:
            base_priority *= 0.005  # Even more severely reduce priority for homepage
            logger.debug(f"Deprioritizing homepage URL: {url}")
        
        # Detect and deprioritize other navigation URLs
        nav_patterns = ['index', 'home', 'main', 'contact', 'about', 'login', 'signup', 
                       'register', 'search', 'categories', 'tags', 'menu']
        for nav in nav_patterns:
            if nav in path or nav in query:
                base_priority *= 0.2  # Significantly reduce priority for likely navigation pages
        
        # Detect likely content pages based on URL patterns
        if self._is_likely_content_page(url):
            base_priority *= 3.0  # Significant boost for URLs that look like content pages
        
        # Adjust depth factor based on whether we're on the same domain as the source
        if source_url and domain == urlparse(source_url).netloc.lower():
            # Almost no depth penalty for URLs on the same domain as the source
            # This encourages exploration deeper into the source domain
            depth_factor = 1.0 / (0.1 + (depth * 0.1))  # Extremely minimal depth penalty
            # Don't log this as it's too verbose
        else:
            # More significant depth penalty for URLs on other domains
            depth_factor = 1.0 / (0.5 + (depth * 0.5))  # Standard depth penalty
            
        base_priority *= depth_factor
        
        # Detect and boost media-related URLs even if they don't match patterns
        extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.webm', '.avi']
        if any(ext in full_url for ext in extensions):
            base_priority *= 5.0  # Significant boost for URLs that likely point to media files
            
        # Universal pattern recognition for full-size image URLs - common across many sites
        # These are patterns that typically indicate higher quality images
        fullsize_indicators = ['full', 'large', 'original', 'highres', 'hires', 'hi-res', 'max', 'big', 'best']
        if any(indicator in full_url for indicator in fullsize_indicators):
            base_priority *= 4.0  # Major boost for likely full-size images
            logger.debug(f"Boosting likely full-size image URL: {url}")
            
        # Detect URLs that appear to be a higher-quality version of an image
        # This pattern matches thumbnail-to-fullsize patterns across many sites
        image_url_pattern = re.search(r'/([^/]+)/([^/]+)/(\w+)/', full_url)
        if image_url_pattern and any(ext in full_url for ext in extensions):
            base_priority *= 3.0  # Boost potential image content URLs
            logger.debug(f"Boosting potential structured image URL: {url}")
            
        # Boost URLs that appear to be content pages rather than navigation
        # Check for patterns that typically indicate content rather than navigation
        if len(path) > 0 and path != '/':
            # URLs with numeric components often point to specific content (posts, articles, etc.)
            if re.search(r'/\d+', path) or re.search(r'\d+\.html', path):
                base_priority *= 3.0
                logger.debug(f"Boosting likely content URL: {url}")
                
            # URLs with date-like patterns typically indicate content
            if re.search(r'/20\d\d/', path) or re.search(r'/\d{4}/\d{2}/', path):
                base_priority *= 2.5
                logger.debug(f"Boosting dated content URL: {url}")

        return base_priority

    def update_domain_score(self, url: str, media_count: int):
        """Update domain's reputation based on media file discovery"""
        domain = self._get_domain(url)
        if domain:
            self._domain_scores[domain] = (
                self._domain_scores.get(domain, 0) + media_count
            )

    def update_url_pattern(self, url: str, success: bool = True):
        """Update URL pattern statistics based on success/failure"""
        path = urlparse(url).path.lower()
        patterns = [
            "gallery",
            "photo",
            "image",
            "media",
            "video",
            "upload",
            "pictures",
            "album",
            "content",
            "original",
            "fullsize",
            "full-size",
            "highres",
            "hi-res",
            "hires",
            "large",
            "pic",
            "img",
            "view",
            "post",
            "viewer",
            "preview",
            "reel"
        ]
        for pattern in patterns:
            if pattern in path:
                self._url_patterns[pattern] = self._url_patterns.get(pattern, 0)
                if success:
                    self._url_patterns[pattern] += 1
                else:
                    self._url_patterns[pattern] = max(
                        0, self._url_patterns[pattern] - 1
                    )

    async def put(self, url: str, depth: int, source_url: str = "", context: dict = None):
        """Add URL to the priority queue with context information"""
        async with self._lock:
            # Make sure we use the most appropriate source URL for downward path enforcement
            effective_source_url = source_url
            if context and 'start_url' in context:
                # If context contains a start_url (the original URL the user specified),
                # use that for downward path enforcement
                logger.debug(f"Using start_url from context: {context['start_url']} instead of source_url: {source_url}")
                effective_source_url = context['start_url']
                
            priority = self._calculate_url_priority(url, depth, effective_source_url, context)
            
            # If priority is zero, the URL didn't pass the relationship check - skip it
            if priority <= 0.0:
                logger.debug(f"Skipping URL with zero priority: {url}")
                return
                
            # Ensure we never have extremely low priorities that might get stuck in the queue
            if 0 < priority < 0.1:
                logger.debug(f"Boosting low priority URL: {url} from {priority:.4f} to 0.1")
                priority = 0.1
            
            # Log high-value URLs and their priorities for debugging
            if context and context.get('from_image', False):
                logger.debug(f"Image-linked URL priority: {priority:.2f} for {url} (depth={depth})")
            elif priority > 5.0:
                logger.debug(f"High priority URL: {priority:.2f} for {url} (depth={depth})")
                
            item = PrioritizedURL(
                priority=-priority,  # Negative for max-heap behavior
                url=url,
                depth=depth,
                source_url=effective_source_url,  # Store the effective source URL
                context=context or {},
            )
            heappush(self._queue, item)
            self._not_empty.set()  # Signal that queue is not empty

    async def get(self, timeout: float = None) -> Tuple[str, int, str, dict]:
        """Get URL with highest priority"""
        while True:
            async with self._lock:
                if self._queue:
                    item = heappop(self._queue)
                    if not self._queue:
                        self._not_empty.clear()
                    logger.debug(f"Processing URL: {item.url} (depth={item.depth}, priority={-item.priority:.2f})")
                    return item.url, item.depth, item.source_url, item.context

                if not timeout:
                    self._not_empty.clear()

            try:
                async with self._lock:
                    waiter = asyncio.create_task(self._not_empty.wait())
                    self._waiters.append(waiter)

                try:
                    if timeout:
                        await asyncio.wait_for(waiter, timeout)
                    else:
                        await waiter
                finally:
                    self._waiters.remove(waiter)

            except asyncio.TimeoutError:
                raise asyncio.QueueEmpty()
            except asyncio.CancelledError:
                if not waiter.done():
                    waiter.cancel()
                raise

    def empty(self) -> bool:
        """Check if queue is empty"""
        return len(self._queue) == 0

    def task_done(self):
        """Mark task as done (compatibility with asyncio.Queue)"""
        pass
