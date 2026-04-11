> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser
> - **Auto-generated RAG Query:** "web-media-parser, Gemini-3-3-Pro-preview API, media extraction algorithms, OCR integration, image processing pipelines, PDF parsing bottlenecks, JSON schema validation, error handling (media corruption), concurrency (thread pools), memory leaks (large file processing), file I/O latency, dependency conflicts (external libraries), rate limiting (API calls), tokenization (text segmentation), vector database indexing"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `main.py`
>   - `src/parser/parser_manager.py`
>   - `src/parser/webpage_parser.py`
> 
> <details><summary><b>Task Instructions</b></summary>
> 
> ```text
> You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase.
> 
> Your audit must include:
> 1. **Algorithm & Architecture**: A detailed, step-by-step description of the core algorithms and data flow.
> 2. **Defect Identification**: Pinpoint logical errors, dead code (non-functional functions), bugs, race conditions, and bottlenecks.
> 3. **Performance Impact**: Analyze any adverse performance impacts caused by the identified deficiencies (e.g., memory leaks, O(n^2) loops).
> 4. **Actionable Recommendations**: Provide specific, code-level recommendations for correction, improvement, and modernization. 
> 
> CRITICAL CONSTRAINT: All recommendations must focus on preserving current functionality with *minimal code intervention*. Do not suggest complete rewrites unless absolutely necessary. Format the output as a structured Markdown report.
> ```
> </details>

---

This is a comprehensive code audit of the **Web Media Parser** application based on the provided source code, specifically analyzing the architectural structure, potential defects, and offering actionable recommendations.

---

# Web Media Parser - Code Audit Report

## 1. Algorithm of Operation and Architecture

The application operates as an asynchronous, highly concurrent media scraping and downloading tool. The core architecture is divided into a PySide6 GUI layer, an orchestration manager (`ParserManager`), and discrete asynchronous worker classes (`WebpageParser`, `JSONWebpageParser`, `MediaDownloader`).

### 1.1 Data Flow & Execution Steps

1.  **Initialization:** The `main.py` entry point applies lxml and brotli monkey-patches, sets up a PySide6 QApplication, and initializes the `MainWindow`. Upon clicking "Start", the GUI instantiates `ParserManager` and calls `start_parsing()`.
2.  **Manager Orchestration (`ParserManager`):**
    *   **Thread Isolation:** The application separates the blocking GUI thread from the heavy IO-bound web requests by spawning a daemon thread (`AsyncEventLoopThread`), which runs its own distinct `asyncio` event loop via `_run_event_loop`.
    *   **Queueing:** A `PriorityURLQueue` manages incoming URLs, ensuring media-rich pages are prioritized. A standard `asyncio.Queue` (`download_queue`) acts as the buffer for items cleared for download.
    *   **Worker Allocation:** `_main_task` provisions a defined number of `_parser_worker` tasks and `_downloader_worker` tasks running concurrently using an `AsyncClientManager` for shared persistent HTTP sessions.
3.  **Parsing Phase (`WebpageParser`):**
    *   **Retrieval:** The worker takes a URL. `WebpageParser._get_content()` fetches the page using `aiohttp` with spoofed headers and consent bypass cookies. It handles HTTP errors, timeouts, and detects the character encoding via `chardet`. Crucially, it recursively handles simple JavaScript redirects using regex.
    *   **HTML Analysis:** The raw HTML byte string is decoded and fed into BeautifulSoup. The parser calls `_extract_images()`, `_extract_videos()`, and `_extract_links()`.
    *   **Heuristic Extraction:** `_get_best_image_url()` evaluates elements using a scoring system based on attribute presence (e.g., `data-high-res` scores +100) and explicit image dimensions to locate original-quality assets rather than thumbnails. It evaluates `srcset` properties and parses inline CSS background URLs.
    *   **Dynamic JS Evaluation:** If `process_js` is enabled, `_handle_dynamic_content()` scans `<script>` tags using regex patterns to find direct links to `.mp4`/`.jpg` and iterates over nodes to find frontend framework specific (React, Vue, Angular) lazy-loading attributes.
4.  **Routing Phase:**
    *   URLs discovered by the parser are filtered against depth limits, domain constraints, blocklists, and stop-words. Valid URLs are pushed back into the `url_queue`.
    *   Media entities are passed into `_process_media_files()`, categorized, hashed (to ensure sanitized file names), deduplicated, and pushed to the `download_queue`.
5.  **Downloading & Telemetry (`MediaDownloader`):**
    *   `_downloader_worker` consumes from the `download_queue`. It checks the host domain against a `domain_health` dictionary. Domains with failures are given stricter timeouts ("probation") or moved entirely into a `quarantine_queue`.
    *   The worker uses `asyncio.get_event_loop().run_in_executor` to execute the synchronous downloading logic (inside `MediaDownloader`) in a separate thread pool to prevent blocking the async loop.
    *   A separate `_monitor_progress` thread tracks statistics and calculates total completion ratio, emitting PySide6 signals to update the UI progress bar.

---

## 2. Defect Identification & Performance Impact

### 2.1 Concurrency Mixing (Asyncio blocking)
**Defect:** In `ParserManager._downloader_worker()`, you are scheduling `MediaDownloader.download` via `run_in_executor`. However, `MediaDownloader` relies on Python's `requests` library (synchronous IO). The default ThreadPoolExecutor size in asyncio is `min(32, os.cpu_count() + 4)`. If `K.SETTING_DOWNLOADER_THREADS` exceeds this or if network latency is high, thread exhaustion occurs, effectively bottlenecking the async event loop because executor scheduling gets backed up.
**Impact:** Severe performance degradation when downloading many small files concurrently. The UI might remain responsive, but network throughput will crash.

### 2.2 Unbounded Queue Growth & Memory Leaks
**Defect:** `url_queue` has no maximum size constraint. During a deep recursive crawl (e.g., `depth > 3`) on a massive domain (like a forum or gallery), the parser workers can discover links at a rate exponentially faster than they can be parsed.
**Impact:** `url_queue` will experience exponential memory growth, eventually causing an Out-Of-Memory (OOM) crash.

### 2.3 Unsafe `BeautifulSoup` Instantiation
**Defect:** `soup = BeautifulSoup(content, "html.parser")` in `WebpageParser.parse()`. `html.parser` is pure Python and notoriously slow, especially on large, malformed DOM structures common in media sites.
**Impact:** Heavy CPU overhead, blocking the async event loop for longer than necessary since BeautifulSoup parsing is CPU-bound and executed directly inside an async function without `run_in_executor`.

### 2.4 Race Conditions in Shutdown Sequence
**Defect:** `ParserManager.stop_parsing()` immediately clears queues via `get_nowait()` and creates a new `PriorityURLQueue`. However, active asynchronous workers may currently be executing an awaitable task and will try to interact with queues or sets (`processed_urls`) after `_stop_event.set()` is triggered but before they exit.
**Impact:** Intermittent `asyncio.QueueEmpty` or `CancelledError` exceptions during application shutdown, occasionally causing zombie threads if the exception isn't caught properly.

### 2.5 `requests.Session` Creation Overhead
**Defect:** `WebpageParser` creates a new synchronous `requests.Session()` with retries inside `_create_sync_session()` on *every single instantiation*, even though it is largely unused (the actual async fetch happens via `self.session` which is an `aiohttp.ClientSession`).
**Impact:** Memory bloat and CPU overhead creating `HTTPAdapter` objects that are never garbage collected efficiently during high concurrency parsing.

---

## 3. Actionable Recommendations

**Goal:** Provide specific, code-level recommendations prioritizing minimal code intervention to preserve current functionality.

### Fix 1: Optimize HTML Parsing (CPU Unblocking)
Switching the BeautifulSoup parser to `lxml` will yield a massive performance increase (up to 10x faster parsing). Furthermore, because parsing large HTML is CPU-bound, wrap the soup creation to prevent blocking the event loop.

**In `src/parser/webpage_parser.py`:**
Change the `BeautifulSoup` call to use `lxml` (since it's already in `requirements.txt`).
```python
# Before
soup = BeautifulSoup(content, "html.parser")

# After
# Note: For strict non-blocking, consider using run_in_executor, but for minimal intervention, simply switching the parser engine provides the largest gain.
soup = BeautifulSoup(content, "lxml") 
```

### Fix 2: Remove Unused Synchronous Session
The instantiation of `requests.Session` inside `WebpageParser` is useless overhead. Web requests use `aiohttp.ClientSession` (`self.session`). The sync session should be removed completely to save memory.

**In `src/parser/webpage_parser.py`:**
Remove `self.sync_session = self._create_sync_session()` from `__init__`.
Remove the entire `_create_sync_session(self)` method to clean up dead code.

### Fix 3: Graceful Queue Draining & Shutdown
To prevent race conditions and exceptions during termination, modify `stop_parsing` so it cleanly cancels tasks rather than aggressively replacing queue object references in memory.

**In `src/parser/parser_manager.py`:**
```python
# In stop_parsing() method:
# Before:
self.url_queue = PriorityURLQueue() # DANGEROUS: Orphans old queue reference

# After:
# Allow _main_task's all_tasks.cancel() to handle thread termination safely.
# Do not overwrite self.url_queue while workers are alive.
while not self.url_queue.empty():
    try:
        self.url_queue.get_nowait()
        self.url_queue.task_done()
    except Exception:
        break
```

### Fix 4: Bound Queue Sizes to Prevent OOM
Add a maximum size parameter to the queues. When the queue is full, the parser workers will automatically pause generating new URLs until the queue clears up, effectively creating backpressure.

**In `src/parser/parser_manager.py`:**
```python
# Change initialization
self.download_queue = asyncio.Queue(maxsize=10000)
# Ensure your Custom PriorityURLQueue class is modified to accept and respect maxsize.
```

### Fix 5: Optimize Regex Pattern Compilation
The regex patterns in `WebpageParser` (like `JS_PATTERNS`, `CDN_PATTERNS`) are declared as raw strings in class-level dictionaries. `re.findall(pattern, ...)` recompiles these expressions on the fly repeatedly inside deep `for` loops.

**In `src/parser/webpage_parser.py`:**
Pre-compile the heavy regex dictionaries at the module level or class-level initialization so they evaluate instantly.
```python
# E.g., for JS_PATTERNS image_sources
COMPILED_JS_IMAGE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in JS_PATTERNS["image_sources"]]

# inside _extract_media_from_js:
for compiled_pattern in COMPILED_JS_IMAGE_PATTERNS:
    for match in compiled_pattern.finditer(js_content):
        # ... logic ...
```

---

## 4. Secondary Analysis (Architectural Overview)

*   **Redundant Type Checking:** In `_process_media_batch()`, there is a check `if is_webpage_url(abs_url) and not is_media_url(abs_url):`. Because `is_webpage_url` internally checks `is_media_url` and returns `False` if it is, the second half of the boolean statement is redundant and incurs unnecessary regex parsing overhead.
*   **Filename Extension Logic Bug:** In `_get_filename_from_url`, `if potential_ext and 1 < len(potential_ext) <= 5:` does not account for extensions like `.jpeg` or query strings getting caught in the extension logic (e.g. `image.jpg?v=123` results in `.jpg?v=123` becoming the extension, which fails length constraints, causing fallback to defaults). Using `yarl` or `urllib.parse` to strip query strings *before* `os.path.splitext` is required.
*   **Dependency Management:** `chardet` is used as an explicit fallback/import. `cchardet` (C-based) is significantly faster, but it is effectively dead and breaks in Python 3.10+. Recommending `charset-normalizer` as a modern, actively maintained drop-in replacement in `requirements.txt` instead of relying heavily on `chardet`.