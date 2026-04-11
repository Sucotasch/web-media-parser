> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser
> - **Auto-generated RAG Query:** "codebase_architecture_diagram, core_algorithm_pseudocode, data_flow_visualization, function_signatures_analysis, class_inheritance_hierarchy, API_endpoint_documentation, database_schema_reverse_engineering, error_handling_mechanisms, performance_bottleneck_identification, module_interdependencies, security_vulnerability_scanning, unit_test_coverage_report, configuration_file_parsing, build_system_dependencies, memory_management_patterns"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `main.py`
>   - `src/parser/parser_manager.py`
>   - `src/parser/webpage_parser.py`
> 
> <details><summary><b>Task Instructions</b></summary>
> 
> ```text
> You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase. Analyze the provided GitHub repository data to create comprehensive technical documentation in Markdown format. Make the code understandable. Your audit must include 1. Real capabilities of the program. 2. Algorithm of operation and architecture - a detailed, step-by-step description of the core algorithms and data flow.
> ```
> </details>

---



# Web Media Parser - Technical Documentation

## 1. Real Capabilities of the Program

The **Web Media Parser** is a highly concurrent, intelligent desktop application designed to deeply crawl websites and APIs to extract and download media files (images, videos). Its capabilities go far beyond basic web scraping:

*   **Intelligent Media Discovery:** Scans standard HTML elements (`<img>`, `<video>`, `<picture>`, `<iframe>`), inline CSS background images, and parses JSON API responses for media URLs.
*   **High-Resolution & Original Priority:** It does not blindly download thumbnails. It applies heuristic analysis on attributes like `data-original`, `data-src`, `data-fullsize`, `srcset`, and specific CSS classes to actively prefer the highest quality, original media files.
*   **Dynamic Content & JS Framework Support:** Attempts to bypass dynamically rendered limitations by inspecting inline JavaScript code and framework-specific lazy-loading attributes (e.g., React's `className="lazy-load"`, Vue's `v-lazy`, Angular's `[lazyLoad]`). It also tracks and executes simple JavaScript-based HTTP redirects.
*   **Extensible Site Patterns:** Incorporates a `SitePatternManager` that loads custom JSON-based regex rules. This allows for site-specific optimizations, such as rewriting CDN URLs or bypassing specific hotlink protections.
*   **Advanced Link Following & Filtering:** Traverses websites recursively up to a user-defined search depth limit. It supports domain restrictions (staying within the start URL's domain), blocklists, and skipping URLs that contain specific "stop words".
*   **Resilience & Domain Quarantining:** Actively monitors the health of external domains. If a specific CDN or domain repeatedly fails or times out, the application places that domain in a "quarantine queue", ensuring the entire scraper is not bottlenecked by a single dead server.
*   **Session Management & Persistence:** Supports pausing and resuming tasks. The application can serialize its exact state (queues, processed URLs, statistics) to disk using Python's `pickle`, allowing users to resume interrupted scraping sessions at a later time.
*   **Concurrency & Performance:** Built heavily upon Python's `asyncio` and `aiohttp`, utilizing separate pools of asynchronous workers for HTML parsing and media downloading.
*   **Modern GUI:** Provides a seamless user experience using PySide6 (Qt6), featuring a dark theme, real-time logging with color-coding based on log severity, live progress bars, and granular configuration dialogs.

---

## 2. Algorithm of Operation and Architecture

The application is built on a multi-stage, non-blocking asynchronous pipeline orchestrated by the `ParserManager`. The architecture is split between the UI thread, a background asynchronous event loop, and a dedicated progress monitoring thread.

### 2.1. Initialization and GUI Flow
1. **Startup (`main.py`):** The application begins by applying low-level compatibility patches for `lxml` and `brotli` to ensure standard parsing doesn't crash. It then configures global logging, loads the PySide6 Qt application, applies a custom dark stylesheet (`dark_theme.qss`), and renders the `MainWindow`.
2. **User Configuration:** The user enters a target URL and a target directory. Through the `SettingsDialog`, they dictate the number of asynchronous parsing threads, downloading threads, search depth limits, timeouts, and file size constraints.
3. **Execution Trigger:** Pressing the "Start" button instantiates the `ParserManager` and spins up a dedicated background thread (`AsyncEventLoopThread`) to run the asyncio loop, ensuring the GUI remains entirely responsive.

### 2.2. The Parsing Pipeline (`ParserManager`)
The `ParserManager` controls the flow of data using two primary async queues: `url_queue` (a highly specialized `PriorityURLQueue` that scores and prioritizes media-rich URLs) and `download_queue` (a queue for finalized media dictionaries ready for disk writing).

1. **Worker Spawning:** The manager spins up two distinct sets of asyncio tasks: `_parser_worker` (based on `SETTING_PARSER_THREADS`) and `_downloader_worker` (based on `SETTING_DOWNLOADER_THREADS`).
2. **URL Retrieval:** Parser workers continually attempt to pop the highest-priority URL from the `url_queue`.
3. **Parser Selection:** The worker evaluates the URL. If the URL contains `/api/`, `/json/`, or query parameters requesting JSON, it passes the URL to `JSONWebpageParser`. Otherwise, it utilizes the standard `WebpageParser`.
4. **Webpage Parsing (`WebpageParser.parse`):**
   *   **Fetching:** Utilizes `aiohttp.ClientSession` with randomized headers and automatic injection of bypass cookies (e.g., `cookieconsent_status=dismiss`). It resolves potential Javascript redirects and handles character encoding fallbacks via `chardet`.
   *   **HTML Parsing:** The decoded HTML string is fed into `BeautifulSoup4`.
   *   **Media Extraction:** The parser runs specialized methods (`_extract_images`, `_extract_videos`). It uses a scoring system based on attributes—e.g., adding +100 priority if `data-high-res` is present, or calculating potential area if `width` and `height` attributes exist.
   *   **Dynamic JS Extraction:** It parses `<script>` block contents using regex (`JS_PATTERNS`) to find hidden direct links to `.mp4` and `.jpg` files, circumventing the need for a headless browser like Selenium.
   *   **Link Discovery:** Discovers outgoing `<a>` tags and Canonical links, resolving them to absolute URLs.
5. **Result Evaluation:** 
   *   Discovered links are checked against the domain blocklist, same-domain constraints, and stop-words array. Valid links are pushed into the `url_queue` with `current_depth + 1`.
   *   Discovered media files are sorted by their priority scores and pushed into the `download_queue`.

### 2.3. The Downloading Pipeline
1. **Queue Consumption:** A `_downloader_worker` retrieves a media dictionary from the `download_queue`.
2. **Health Check & Quarantine:** The worker checks the `domain_health` dictionary mapping for the media's host.
   *   If the domain has failed too many times, it is placed into a "probation" state, receiving tighter timeouts.
   *   If it exceeds `QUARANTINE_FAILURE_THRESHOLD`, the URL is dumped into the `quarantine_queue` and skipped for now. The manager will revisit the quarantine queue only when the main queues are entirely empty.
3. **File Creation:** Calculates a sanitized, collision-free filename (using MD5 hashes if the URL lacks a valid filename) and constructs a nested directory tree based on the source URL's path components.
4. **Downloading:** The `MediaDownloader` instances execute the actual byte-transfer in a ThreadPoolExecutor to prevent blocking the async loop, handling partial chunking and retry-backoffs.

### 2.4. Telemetry, Monitoring, and Shutdown
*   **Progress Monitor:** A lightweight daemon thread (`_monitor_progress`) runs on a sleep loop, calculating the ratio of `files_downloaded / (images_found + videos_found)`. It emits thread-safe Qt Signals (`total_progress_updated`, `current_progress_updated`) to animate the GUI bars.
*   **Graceful Shutdown:** If the user issues a Stop command, an `asyncio.Event()` named `_stop_event` is fired. The queues are instantly flushed. If configured, the `ParserManager` will dump the remaining state to `SESSION_STATE_FILENAME` via `pickle` before terminating the asyncio workers, allowing for exact continuation in the future.