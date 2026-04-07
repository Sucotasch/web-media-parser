#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Media downloader class for downloading media files
"""

import os
import time
import asyncio
import logging
from urllib.parse import urlparse
import aiohttp
import aiofiles
from src import constants as K

logger = logging.getLogger(__name__)

class MediaDownloader:
    """
    Media downloader class for downloading media files using pure aiohttp and asyncio.
    """

    def __init__(self, url, filepath, settings, media_type="image", source_url=None):
        self.url = url
        self.filepath = filepath 
        self.settings = settings
        self.media_type = media_type
        self.source_url = source_url
        self.stop_event: asyncio.Event = None
        self.progress_callback = None
        self.rate_limit = self.settings.get(K.SETTING_MAX_DOWNLOAD_SPEED, 0)
        self.threads_per_file = min(
            self.settings.get(K.SETTING_THREADS_PER_FILE, K.DEFAULT_THREADS_PER_FILE),
            K.MAX_THREADS_PER_FILE_CAP 
        )

    def set_progress_callback(self, callback): self.progress_callback = callback
    def set_stop_event(self, event): self.stop_event = event
    def _is_stopped(self): return self.stop_event is not None and self.stop_event.is_set()

    def _get_headers(self):
        headers = {}
        if self.media_type == "image": 
            headers["Accept"] = K.DEFAULT_ACCEPT_IMAGE_HEADER
        elif self.media_type == "video": 
            headers["Accept"] = K.DEFAULT_ACCEPT_VIDEO_HEADER
        else: 
            headers["Accept"] = K.DEFAULT_ACCEPT_HEADER
            
        headers.update({
            "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "image" if self.media_type == "image" else "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "Cache-Control": "no-cache",
        })

        if self.source_url:
            referrer_policy = self.settings.get(K.SETTING_REFERRER_POLICY, "auto")
            if referrer_policy == "origin":
                parsed_source = urlparse(self.source_url)
                headers["Referer"] = f"{parsed_source.scheme}://{parsed_source.netloc}"
            elif referrer_policy == "auto":
                headers["Referer"] = self.source_url
        return headers

    async def download(self, session: aiohttp.ClientSession, timeout=None, retries=None):
        try:
            current_timeout = timeout if timeout is not None else self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT)
            current_retries = retries if retries is not None else self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT)
            return await self._do_download_async(session, current_timeout, current_retries)
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

    async def _do_download_async(self, session: aiohttp.ClientSession, timeout, max_retries):
        self.filepath = self._ensure_unique_filepath_at_destination(self.filepath)
        non_media_extensions = [ ".html", ".htm", ".php", ".asp", ".aspx", ".js", ".css", ".json", ".xml"]
        url_lower = self.url.lower()
        if any(url_lower.endswith(ext) or f"{ext}?" in url_lower or f"{ext}#" in url_lower for ext in non_media_extensions):
            return {"success": False, "error": "Non-media file based on URL extension"}

        req_headers = self._get_headers()
        attempt = 0
        
        while attempt <= max_retries:
            if self._is_stopped():
                return {"success": False, "error": "Aborted by user"}
            
            try:
                client_timeout = aiohttp.ClientTimeout(total=timeout)
                
                # First try HEAD request
                content_length = 0
                accept_ranges = False
                try:
                    async with session.head(self.url, headers=req_headers, timeout=client_timeout) as resp_head:
                        if resp_head.status == 200:
                            content_length = int(resp_head.headers.get("Content-Length", 0))
                            accept_ranges = (resp_head.headers.get("Accept-Ranges") == "bytes")
                            
                            content_type = resp_head.headers.get("Content-Type", "").lower()
                            if any(t in content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                                return {"success": False, "error": f"Webpage/script received instead of media (HEAD Content-Type: {content_type})"}
                                
                            min_img_size_kb = self.settings.get(K.SETTING_MIN_IMG_SIZE, K.DEFAULT_MIN_IMAGE_SIZE_KB)
                            min_vid_size_kb = self.settings.get(K.SETTING_MIN_VID_SIZE, K.DEFAULT_MIN_VIDEO_SIZE_KB)
                            if content_length > 0:
                                size_kb = content_length / 1024
                                min_size_for_type = min_img_size_kb if self.media_type == "image" else min_vid_size_kb
                                if min_size_for_type > 0 and size_kb < min_size_for_type:
                                    logger.debug(f"File too small ({size_kb:.2f}KB < {min_size_for_type}KB) for {self.url}")
                                    return {"success": False, "error": f"File too small ({size_kb:.2f}KB < {min_size_for_type}KB)"}
                except Exception as e:
                    logger.warning(f"HEAD request failed for {self.url}: {str(e)}. Proceeding to GET.")

                target_dir = os.path.dirname(self.filepath)
                if target_dir:
                    os.makedirs(target_dir, exist_ok=True)

                can_multi_thread = (self.threads_per_file > 1 and 
                                    content_length > 0 and 
                                    content_length > K.WRITE_BUFFER_SIZE * self.threads_per_file and 
                                    accept_ranges)

                if can_multi_thread:
                    logger.info(f"Starting chunked download for {self.filepath}")
                    res = await self._download_chunks_async(session, req_headers, content_length, timeout)
                    if res["success"]: return res
                    logger.warning(f"Chunked download failed for {self.url}, falling back to standard stream.")

                # Standard single-stream GET
                logger.info(f"Starting standard stream download for {self.filepath}")
                async with session.get(self.url, headers=req_headers, timeout=client_timeout) as resp_get:
                    resp_get.raise_for_status()
                    
                    real_content_type = resp_get.headers.get("Content-Type", "").lower()
                    if any(t in real_content_type for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
                        return {"success": False, "error": f"Integrity check failed: Got {real_content_type}."}

                    dl_len = int(resp_get.headers.get("Content-Length", content_length))
                    downloaded_bytes = 0
                    start_time = time.time()
                    
                    async with aiofiles.open(self.filepath, "wb") as f:
                        async for chunk in resp_get.content.iter_chunked(8192):
                            if self._is_stopped():
                                await f.close() # Ensure aiofiles releases the lock
                                try: os.remove(self.filepath)
                                except: pass
                                return {"success": False, "error": "Aborted: stop requested"}
                                
                            await f.write(chunk)
                            downloaded_bytes += len(chunk)
                            
                            if self.progress_callback:
                                prog = min(100, int((downloaded_bytes / dl_len) * 100)) if dl_len > 0 else -1
                                self.progress_callback(prog)
                                
                            if self.rate_limit > 0:
                                elapsed = time.time() - start_time
                                expected_time = downloaded_bytes / (self.rate_limit * 1024)
                                if elapsed < expected_time: await asyncio.sleep(expected_time - elapsed)

                if dl_len > 0:
                    actual_size = os.path.getsize(self.filepath) if os.path.exists(self.filepath) else 0
                    if actual_size != dl_len:
                        try: os.remove(self.filepath)
                        except: pass
                        return {"success": False, "error": f"Size mismatch: expected {dl_len}, got {actual_size}"}

                if self.progress_callback: self.progress_callback(100)
                logger.info(f"Download completed: {os.path.basename(self.filepath)}")
                return {"success": True, "message": "File downloaded successfully"}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                attempt += 1
                if attempt > max_retries:
                    return {"success": False, "error": f"Network error after {max_retries} retries: {e}"}
                await asyncio.sleep(0.5 * attempt) # Backoff
            except Exception as e:
                logger.error(f"Download error for {self.url}: {str(e)}", exc_info=True)
                return {"success": False, "error": str(e)}

    async def _download_chunks_async(self, session: aiohttp.ClientSession, base_headers, total_size, timeout_val):
        num_threads = min(self.threads_per_file, max(1, total_size // K.MIN_CHUNK_SIZE_PER_THREAD_MT), K.MAX_THREADS_PER_FILE_CAP)
        if num_threads <= 1: return {"success": False, "error": "Not enough parts for multi-thread"}

        chunk_size = total_size // num_threads
        tasks = []
        temp_files = []
        progress_dict = {"total": 0, "success": True}
        client_timeout = aiohttp.ClientTimeout(total=timeout_val)

        for i in range(num_threads):
            start = i * chunk_size
            end = (i + 1) * chunk_size - 1 if i < num_threads - 1 else total_size - 1
            temp_file = f"{self.filepath}.part{i}"
            temp_files.append(temp_file)
            headers = base_headers.copy()
            headers["Range"] = f"bytes={start}-{end}"
            
            tasks.append(
                self._download_single_chunk(session, headers, start, end, temp_file, total_size, progress_dict, client_timeout)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check for failures and clear temps
        for res in results:
            if isinstance(res, Exception) or res is False:
                progress_dict["success"] = False

        if not progress_dict["success"]:
            for tf in temp_files:
                if os.path.exists(tf):
                    try: os.remove(tf)
                    except: pass
            return {"success": False, "error": "Chunk download failed"}

        # Combine
        try:
            async with aiofiles.open(self.filepath, "wb") as outfile:
                for tf in temp_files:
                    if not os.path.exists(tf): raise IOError(f"Missing part: {tf}")
                    async with aiofiles.open(tf, "rb") as infile: 
                        chunk_data = await infile.read()
                        await outfile.write(chunk_data)
                    try: os.remove(tf)
                    except: pass
            
            if os.path.getsize(self.filepath) != total_size:
                try: os.remove(self.filepath)
                except: pass
                return {"success": False, "error": "Combined size mismatch"}
                
            return {"success": True}
        except Exception as e:
            try: os.remove(self.filepath)
            except: pass
            return {"success": False, "error": f"Combine error: {e}"}
        finally:
            for tf in temp_files:
                if os.path.exists(tf):
                    try: os.remove(tf)
                    except: pass

    async def _download_single_chunk(self, session, headers, start, end, filename, total_size, progress_dict, timeout):
        try:
            async with session.get(self.url, headers=headers, timeout=timeout) as resp:
                resp.raise_for_status()
                async with aiofiles.open(filename, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        if self._is_stopped():
                            progress_dict["success"] = False
                            return False
                        await f.write(chunk)
                        progress_dict["total"] += len(chunk)
                        if self.progress_callback:
                            prog = min(99, int((progress_dict["total"] / total_size) * 100))
                            self.progress_callback(prog)
                            
            if os.path.getsize(filename) != (end - start + 1):
                raise IOError("Chunk size mismatch")
            return True
        except Exception as e:
            logger.error(f"Chunk error {filename}: {e}")
            progress_dict["success"] = False
            return False
