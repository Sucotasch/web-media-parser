#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Media downloader class for downloading media files
"""

import os
import re
import time
import threading
from urllib.parse import urlparse
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src import constants as K # Import constants

logger = logging.getLogger(__name__)

# WRITE_BUFFER_SIZE is now in K.WRITE_BUFFER_SIZE

class MediaDownloader:
    """
    Media downloader class for downloading media files
    """

    def __init__(self, url, filepath, settings, media_type="image", source_url=None):
        """
        Initializes MediaDownloader.
        filepath: Full path including desired subdirectories, before final uniqueness.
        """
        self.url = url
        self.filepath = filepath 
        self.settings = settings # Settings from ParserManager, which got them from SettingsDialog
        self.media_type = media_type
        self.source_url = source_url 
        self.progress_callback = None
        self.session = self._create_session()
        self.rate_limit = self.settings.get(K.SETTING_MAX_DOWNLOAD_SPEED, 0) # 0 for unlimited
        # Use K.MAX_THREADS_PER_FILE_CAP as a hard upper limit
        self.threads_per_file = min(
            self.settings.get(K.SETTING_THREADS_PER_FILE, K.DEFAULT_THREADS_PER_FILE),
            K.MAX_THREADS_PER_FILE_CAP 
        )

    def _create_session(self):
        session = requests.Session()
        retry_strategy = Retry(
            total=self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT),
            backoff_factor=0.5, # This could also be a constant
            status_forcelist=[429, 500, 502, 503, 504], # HTTP status codes to retry on
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        headers = {
            "User-Agent": self.settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT),
            "Accept-Language": self.settings.get(K.SETTING_ACCEPT_LANGUAGE, K.DEFAULT_ACCEPT_LANGUAGE),
        }

        if self.media_type == "image": 
            headers["Accept"] = K.DEFAULT_ACCEPT_IMAGE_HEADER
        elif self.media_type == "video": 
            headers["Accept"] = K.DEFAULT_ACCEPT_VIDEO_HEADER
        else: 
            headers["Accept"] = K.DEFAULT_ACCEPT_HEADER # Generic accept

        if self.source_url:
            referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto") # Default to "auto"
            if referrer_policy == "origin":
                parsed_source = urlparse(self.source_url)
                headers["Referer"] = f"{parsed_source.scheme}://{parsed_source.netloc}"
            elif referrer_policy == "auto": # "auto" means send full source_url as referrer
                headers["Referer"] = self.source_url
            # If "none", no Referer header is added.
        
        session.headers.update(headers)
        return session

    def set_progress_callback(self, callback): self.progress_callback = callback

    def download(self, timeout=None, retries=None): # retries param is less used now session handles it
        try:
            # Use specific timeout if provided, else from settings, else default constant
            current_timeout = timeout if timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
            return self._do_download(custom_timeout=current_timeout)
        except Exception as e:
            logger.error(f"Download failed for {self.filepath}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _ensure_unique_filepath_at_destination(self, current_filepath: str) -> str:
        if not os.path.exists(current_filepath):
            return current_filepath
        dir_path, original_basename = os.path.split(current_filepath)
        base_name, ext = os.path.splitext(original_basename)
        counter = 1
        unique_filepath = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
        while os.path.exists(unique_filepath):
            counter += 1
            unique_filepath = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
        logger.debug(f"Adjusted filepath from {current_filepath} to {unique_filepath} due to existing file.")
        return unique_filepath

    def _do_download(self, custom_timeout=None):
        try:
            self.filepath = self._ensure_unique_filepath_at_destination(self.filepath)
            non_media_extensions = [ ".html", ".htm", ".php", ".asp", ".aspx", ".js", ".css", ".json", ".xml"]
            url_lower = self.url.lower()
            if any(url_lower.endswith(ext) or f"{ext}?" in url_lower or f"{ext}#" in url_lower for ext in non_media_extensions):
                return {"success": False, "error": "Non-media file based on URL extension"}
            
            timeout_to_use = custom_timeout if custom_timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
            content_length = 0
            response_head = None # Define response_head before try block

            try:
                response_head = self.session.head(self.url, timeout=timeout_to_use)
                response_head.raise_for_status()
                content_length = int(response_head.headers.get("Content-Length", 0))
                content_type = response_head.headers.get("Content-Type", "").lower()
                if any(t in content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                    return {"success": False, "error": f"Webpage/script content (Content-Type: {content_type})"}
                
                min_img_size_kb = self.settings.get(K.SETTING_MIN_IMG_SIZE, K.DEFAULT_MIN_IMAGE_SIZE_KB)
                min_vid_size_kb = self.settings.get(K.SETTING_MIN_VID_SIZE, K.DEFAULT_MIN_VIDEO_SIZE_KB)

                if content_length > 0:
                    size_kb = content_length / 1024
                    min_size_for_type = min_img_size_kb if self.media_type == "image" else min_vid_size_kb
                    if min_size_for_type > 0 and size_kb < min_size_for_type:
                        return {"success": False, "error": f"File too small ({size_kb:.2f}KB < {min_size_for_type}KB)"}
            except requests.exceptions.RequestException as e:
                logger.warning(f"HEAD request failed for {self.url}: {str(e)}. Will attempt GET.")

            mode = "wb"
            can_multi_thread = (self.threads_per_file > 1 and 
                                content_length > 0 and 
                                content_length > K.WRITE_BUFFER_SIZE * self.threads_per_file and 
                                response_head and response_head.headers.get("Accept-Ranges") == "bytes")

            if can_multi_thread:
                try:
                    # Ensure the target directory exists before multi-threaded download
                    target_dir = os.path.dirname(self.filepath)
                    if target_dir:
                        try:
                            os.makedirs(target_dir, exist_ok=True)
                        except OSError as e:
                            logger.error(f"Could not create target directory {target_dir}: {e}", exc_info=True)
                            return {"success": False, "error": f"Could not create directory: {e}"}
                    
                    logger.info(f"Attempting multi-threaded download for {self.filepath}")
                    result = self._download_with_threads(content_length, custom_timeout=timeout_to_use)
                    if result["success"]: return result
                    logger.warning(f"Multi-threaded download failed for {self.filepath}, falling back to single-threaded.")
                except Exception as e:
                    logger.warning(f"Multi-threaded download for {self.filepath} raised {e}, falling back.", exc_info=True)
            
            logger.info(f"Starting single-threaded download: {os.path.basename(self.filepath)}")
            response_get = self.session.get(self.url, stream=True, timeout=timeout_to_use)
            response_get.raise_for_status()

            if content_length == 0:
                content_length = int(response_get.headers.get("Content-Length", 0))

            # Ensure the target directory exists only when we're about to download
            target_dir = os.path.dirname(self.filepath)
            if target_dir:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                except OSError as e:
                    logger.error(f"Could not create target directory {target_dir}: {e}", exc_info=True)
                    return {"success": False, "error": f"Could not create directory: {e}"}

            write_buffer = bytearray()
            with open(self.filepath, mode) as f:
                start_time = time.time()
                network_chunk_size = 8192  
                downloaded_bytes = 0
                for chunk in response_get.iter_content(chunk_size=network_chunk_size):
                    if chunk:
                        write_buffer.extend(chunk)
                        downloaded_bytes += len(chunk)
                        if self.progress_callback:
                            prog = min(100, int((downloaded_bytes / content_length) * 100)) if content_length > 0 else -1
                            self.progress_callback(prog)
                        if len(write_buffer) >= K.WRITE_BUFFER_SIZE:
                            try: f.write(write_buffer); write_buffer.clear()
                            except Exception as e: return {"success": False, "error": f"Disk write error: {e}"}
                        if self.rate_limit > 0:
                            elapsed = time.time() - start_time
                            expected_time = downloaded_bytes / (self.rate_limit * 1024)
                            if elapsed < expected_time: time.sleep(expected_time - elapsed)
                if write_buffer:
                    try:
                        f.write(write_buffer)
                    except Exception as e:
                        return {"success": False, "error": f"Disk write error: {e}"}

            if content_length > 0 and os.path.getsize(self.filepath) != content_length:
                try:
                    os.remove(self.filepath)
                except OSError:
                    pass
                return {"success": False, "error": f"Size mismatch: expected {content_length}, got {os.path.getsize(self.filepath)}"}

            if self.progress_callback: self.progress_callback(100)
            logger.info(f"Download completed: {os.path.basename(self.filepath)}")
            return {"success": True, "message": "File downloaded successfully"}

        except requests.exceptions.HTTPError as e:
            return {"success": False, "error": f"HTTP error: {e.response.status_code if e.response else 'Unknown'}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Network error: {e}"}
        except Exception as e:
            logger.error(f"Generic download error for {self.url}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _download_with_threads(self, total_size, custom_timeout=None):
        temp_files, threads = [], []
        progress_lock = threading.Lock()
        progress_dict = {"total": 0, "success": True, "errors": []}
        timeout_to_use = custom_timeout if custom_timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
        
        num_threads = min(self.threads_per_file, max(1, total_size // K.MIN_CHUNK_SIZE_PER_THREAD_MT), K.MAX_THREADS_PER_FILE_CAP)
        if num_threads <= 1: return {"success": False, "error": "Not enough parts for multi-thread based on min chunk size"}

        chunk_size_for_threads = total_size // num_threads
        for i in range(num_threads):
            start = i * chunk_size_for_threads
            end = (i + 1) * chunk_size_for_threads - 1 if i < num_threads - 1 else total_size - 1
            temp_file = f"{self.filepath}.part{i}"
            temp_files.append(temp_file)
            thread = threading.Thread(target=self._download_chunk, args=(start, end, temp_file, total_size, progress_dict, progress_lock, timeout_to_use))
            thread.daemon = True; thread.start(); threads.append(thread)
        for thread in threads: thread.join()

        if not progress_dict["success"]:
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass
            return {"success": False, "error": f"Chunk failure(s): {progress_dict['errors']}"}
        try:
            with open(self.filepath, "wb") as outfile:
                for temp_file in temp_files:
                    if not os.path.exists(temp_file): raise IOError(f"Missing part: {temp_file}")
                    with open(temp_file, "rb") as infile: outfile.write(infile.read())
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass  # Clean up successful part
            if os.path.getsize(self.filepath) != total_size:
                try:
                    os.remove(self.filepath)
                except OSError:
                    pass
                return {"success": False, "error": "Combined file size mismatch"}
            if self.progress_callback: self.progress_callback(100)
            return {"success": True, "message": "Multi-threaded download success"}
        except Exception as e:
            if os.path.exists(self.filepath):
                try:
                    os.remove(self.filepath)
                except OSError:
                    pass
            return {"success": False, "error": f"Combining/verifying error: {e}"}
        finally: # Ensure all temp files are attempted to be cleaned up
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass

    def _download_chunk(self, start, end, filename, total_size, progress_dict, progress_lock, timeout_val):
        headers = {"Range": f"bytes={start}-{end}"}
        network_chunk_size_thread = 8192 
        try:
            response = self.session.get(self.url, headers=headers, stream=True, timeout=timeout_val)
            response.raise_for_status()
            write_buffer_chunk = bytearray()
            downloaded_this_chunk = 0 # For this specific chunk part
            with open(filename, "wb") as f:
                for chunk_data in response.iter_content(chunk_size=network_chunk_size_thread):
                    if not progress_dict["success"]: return # Check if another thread failed
                    if chunk_data:
                        write_buffer_chunk.extend(chunk_data)
                        downloaded_this_chunk += len(chunk_data)
                        with progress_lock:
                            progress_dict["total"] += len(chunk_data) # Update overall progress
                            if self.progress_callback:
                                prog = min(99, int((progress_dict["total"] / total_size) * 100))
                                self.progress_callback(prog)
                        if len(write_buffer_chunk) >= K.WRITE_BUFFER_SIZE:
                            f.write(write_buffer_chunk); write_buffer_chunk.clear()
                        # Simplified rate limiting for threaded chunks - focus on buffer primarily
                if write_buffer_chunk: f.write(write_buffer_chunk) 
            
            if os.path.getsize(filename) != (end - start + 1): # Verify this chunk's size
                raise IOError(f"Chunk size mismatch: expected {end - start + 1}, got {os.path.getsize(filename)}")
        except Exception as e:
            error_msg = f"Chunk {filename} failed: {type(e).__name__} - {e}"
            logger.error(error_msg)
            with progress_lock:
                progress_dict["success"] = False
                progress_dict["errors"].append(error_msg)
