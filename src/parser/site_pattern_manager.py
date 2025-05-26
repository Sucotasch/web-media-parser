#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Site pattern manager for loading and applying site-specific patterns for media extraction
Based on structured site_patterns.json format
"""

import os
import re
import sys
import json
import logging
from typing import Dict, List, Any, Optional, Tuple, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SitePatternManager:
    """
    Manager for loading and applying site-specific patterns for media extraction
    Supports advanced features like CSS selectors and API integrations
    """

    def __init__(self, enable_built_in=True, custom_pattern_path=None):
        self.patterns = {}
        self.global_settings = {}
        self.loaded_files = []
        self.enable_built_in = enable_built_in
        self.custom_pattern_path = custom_pattern_path
        
        # Load patterns
        self.load_patterns()
    
    def load_patterns(self):
        """
        Load patterns from built-in and custom sources
        """
        # Clear existing patterns
        self.patterns = {}
        self.global_settings = {}
        self.loaded_files = []
        
        # Try loading custom patterns if specified
        if self.custom_pattern_path and os.path.exists(self.custom_pattern_path):
            success = self._load_pattern_file(self.custom_pattern_path)
            if success:
                logger.info(f"Successfully loaded custom site patterns from {self.custom_pattern_path}")
        
        # If no custom patterns or custom patterns failed to load, use built-in patterns
        if not self.patterns and self.enable_built_in:
            # Check for patterns file in various locations
            
            # First try the actual executable directory (for standalone exe)
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
                exe_patterns_path = os.path.join(exe_dir, "site_patterns.json")
                if os.path.exists(exe_patterns_path):
                    built_in_path = exe_patterns_path
                    logger.info(f"Using patterns from executable directory: {built_in_path}")
                    with open(built_in_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if 'version' in data:
                            logger.info(f"Loaded patterns version: {data['version']}")
                    self._load_pattern_file(built_in_path)
                    return
            
            # If not found, use the patterns file from the application directory
            exec_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            built_in_path = os.path.join(exec_dir, "site_patterns.json")
            
            # If not found in application directory, try resources directory
            if not os.path.exists(built_in_path):
                built_in_path = os.path.join(exec_dir, "resources", "patterns", "site_patterns.json")
                
            # For PyInstaller bundle
            if not os.path.exists(built_in_path):
                # Get the PyInstaller _MEIPASS directory if available
                base_dir = getattr(sys, '_MEIPASS', exec_dir)
                built_in_path = os.path.join(base_dir, "resources", "patterns", "site_patterns.json")
                
            if os.path.exists(built_in_path):
                self._load_pattern_file(built_in_path)
                logger.info(f"Using built-in patterns from {built_in_path}")
    
    def _load_pattern_file(self, file_path):
        """
        Load patterns from a JSON file
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Process site patterns
            if 'patterns' in data:
                # New format with patterns array
                for pattern in data['patterns']:
                    if 'site' in pattern and pattern.get('enabled', True):
                        site_name = pattern['site']
                        self.patterns[site_name] = pattern
            else:
                # Process old format or individual entries
                for key, value in data.items():
                    if key == 'global_settings':
                        self.global_settings = value
                    elif key.startswith('['): 
                        # Skip special entries like [Google_Images]
                        continue
                    elif isinstance(value, dict):
                        # Check if it's a valid pattern with required fields
                        if 'site' in value or 'domains' in value or 'url_patterns' in value:
                            site_name = value.get('site', key)
                            self.patterns[site_name] = value
            
            # Get global settings if available
            if 'global_settings' in data:
                self.global_settings = data['global_settings']
            
            # Count valid patterns
            valid_patterns = len(self.patterns)
            
            self.loaded_files.append(file_path)
            logger.info(f"Loaded {valid_patterns} site patterns from {file_path}")
            return True
        except Exception as e:
            logger.error(f"Error loading pattern file {file_path}: {str(e)}")
            return False
    
    def get_patterns_for_url(self, url: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Get applicable patterns for a URL
        Returns a list of (pattern_name, pattern_data) tuples
        """
        applicable_patterns = []
        
        # Parse URL
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            path = parsed_url.path.lower()
            full_url = url.lower()
        except Exception:
            return []
        
        # Find patterns for this domain/URL
        for pattern_name, pattern_data in self.patterns.items():
            try:
                # First check domains
                pattern_domains = pattern_data.get('domains', [])
                domain_match = False
                
                for pattern_domain in pattern_domains:
                    if pattern_domain.lower() in domain:
                        domain_match = True
                        break
                
                # If domain doesn't match, check URL patterns
                if not domain_match and 'url_patterns' in pattern_data:
                    url_patterns = pattern_data['url_patterns']
                    for url_pattern in url_patterns:
                        try:
                            if re.search(url_pattern, full_url, re.IGNORECASE):
                                domain_match = True
                                break
                        except Exception as e:
                            logger.debug(f"Error matching URL pattern {url_pattern}: {str(e)}")
                
                # If we have a match, add to applicable patterns
                if domain_match:
                    applicable_patterns.append((pattern_name, pattern_data))
            except Exception as e:
                logger.debug(f"Error processing pattern {pattern_name}: {str(e)}")
        
        return applicable_patterns
    
    def transform_image_url(self, url: str, source_url: str) -> str:
        """
        Apply patterns to transform thumbnail URLs to fullsize image URLs
        """
        # Get applicable patterns
        patterns = self.get_patterns_for_url(url) or self.get_patterns_for_url(source_url)
        if not patterns:
            # Apply global patterns if available
            return self._apply_global_transformations(url)
        
        original_url = url
        transformed = False
        
        # Try each pattern
        for pattern_name, pattern_data in patterns:
            try:
                # Check if pattern contains image transformations
                if 'image_transformations' in pattern_data:
                    transform_data = pattern_data['image_transformations']
                    
                    # Check for replace patterns
                    if 'replace_patterns' in transform_data:
                        for replace_pattern in transform_data['replace_patterns']:
                            source = replace_pattern.get('source')
                            target = replace_pattern.get('target')
                            
                            if source and target:
                                try:
                                    new_url = re.sub(source, target, url, flags=re.IGNORECASE)
                                    if new_url != url:
                                        url = new_url
                                        transformed = True
                                        logger.debug(f"Transformed image URL using {pattern_name} pattern: {original_url} -> {url}")
                                except Exception as e:
                                    logger.debug(f"Error applying replace pattern {source}: {str(e)}")
                
                # If pattern has imagus_patterns section
                elif 'imagus_patterns' in pattern_data:
                    imagus_data = pattern_data['imagus_patterns']
                    
                    # Photo/media transformations
                    for transform_type in ['photo_transform', 'media', 'image']:
                        if transform_type in imagus_data:
                            transform_patterns = imagus_data[transform_type]
                            if isinstance(transform_patterns, list):
                                for transform in transform_patterns:
                                    source = transform.get('source')
                                    target = transform.get('target')
                                    
                                    if source and target:
                                        try:
                                            new_url = re.sub(source, target, url, flags=re.IGNORECASE)
                                            if new_url != url:
                                                url = new_url
                                                transformed = True
                                                logger.debug(f"Transformed image URL using {pattern_name} imagus pattern: {original_url} -> {url}")
                                        except Exception as e:
                                            logger.debug(f"Error applying imagus pattern {source}: {str(e)}")
                
                # If we found a transformation, break
                if transformed:
                    break
                    
            except Exception as e:
                logger.debug(f"Error applying pattern {pattern_name}: {str(e)}")
                continue
        
        # If no specific pattern worked, try global patterns
        if not transformed:
            url = self._apply_global_transformations(url)
            
        return url
    
    def _apply_global_transformations(self, url: str) -> str:
        """
        Apply global thumbnail transformations to a URL
        """
        if not self.global_settings or 'common_image_patterns' not in self.global_settings:
            return url
            
        original_url = url
        transformed = False
        
        # Get thumbnail transformations
        common_patterns = self.global_settings['common_image_patterns']
        if 'thumbnail_transform' in common_patterns:
            for transform in common_patterns['thumbnail_transform']:
                source = transform.get('source')
                target = transform.get('target')
                
                if source and target:
                    try:
                        new_url = re.sub(source, target, url, flags=re.IGNORECASE)
                        if new_url != url:
                            url = new_url
                            transformed = True
                            logger.debug(f"Transformed image URL using global pattern: {original_url} -> {url}")
                    except Exception as e:
                        logger.debug(f"Error applying global pattern {source}: {str(e)}")
                        
        return url
    
    def get_loaded_files(self) -> List[str]:
        """
        Get list of loaded pattern files
        """
        return self.loaded_files
    
    def get_pattern_count(self) -> int:
        """
        Get number of loaded patterns
        """
        return len(self.patterns)