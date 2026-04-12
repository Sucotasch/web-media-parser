> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser/tree/fix/web-parser-stability-and-persistence
> - **Auto-generated RAG Query:** "codebase_architecture_diagram,core_algorithm_pseudocode,function_signatures,module_interdependencies,API_endpoints,data_flow_graph,state_machine_transitions,error_handling_mechanisms,thread_safety_annotations,performance_bottlenecks,unit_test_coverage,integration_test_scenarios,configuration_management,build_system_dependencies,static_code_analysis_results,codebase_entry_points,algorithm_time_complexity,concurrency_models,external_dependency_versions"
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

The **Web Media Parser** is a highly concurrent, intelligent desktop application designed to deeply crawl websites to extract and download media files (images, videos, etc.). Going significantly beyond a simple web scraper, a deep analysis of its codebase reveals a robust, production-ready extraction engine. Its real capabilities include:

*   **Hybrid Concurrency Model (Qt + Asyncio):** It seamlessly integrates a `PySide6` graphical user interface with an underlying `asyncio` engine. It manages heavy network I/O without blocking the UI thread by offloading the `ParserManager` to an isolated `QThread`, which in turn manages its own `asyncio` event loops, workers, and tasks.
*   **Intelligent Prioritization & URL Queuing:** Instead of a standard FIFO web crawler, it uses a custom `PriorityURLQueue`. This component mathematically ranks queued URLs to ensure that direct media links or highly probable media containers are processed and downloaded *before* standard navigation links. 
*   **Site-Specific Pattern Transformations:** Through the `SitePatternManager`, the program dynamically injects custom parsing rules and regex pattern transformations. This is specifically used to upgrade thumbnails to full-size images, resolve indirect media links, and bypass lightweight obfuscation common in modern web galleries.
*   **Domain Health Monitoring & Quarantining:** The parser is resilient against network instability. It tracks `domain_health` continuously. If a specific domain repeatedly fails or times out, it is dynamically added to a `quarantined_domains` set and managed via a `quarantine_queue`. This prevents the entire pipeline from halting due to a single dead external CDN.
*   **State Persistence and Session Recovery:** It incorporates granular pause, resume, and recovery mechanics. State is serialized and saved asynchronously (e.g., `last_session.pkl`). If the application crashes or is closed, it can reload the previous state, resuming exactly where it left off, avoiding redundant downloads by leveraging a `downloaded_files` registry.
*   **Optimized Network Pooling:** Implements shared session management (`shared_session.py`). It utilizes `aiohttp` for non-blocking asynchronous parsing and creates shared `requests.Session` objects strictly bound to the lifecycle of the downloader threads to optimize TCP keep-alive, reduce SSL handshake overhead, and respect concurrency limits.

---

## 2. Algorithm of Operation and Architecture

The architecture relies on a multi-stage processing pipeline that acts as a Producer-Consumer network. The "Producers" are the HTML parsing workers discovering links, and the "Consumers" are the downloader workers grabbing the binary data.

Here is the detailed, step-by-step description of the core algorithm and data flow:

### Phase 1: Initialization and UI Bootstrapping
1. **User Interaction (`main_window.py`):** The user provides a target URL, selects a download directory, and configures settings (parser threads, downloader threads, search depth). 
2. **Session Recovery Evaluation:** Upon clicking "Start", the `MainWindow` checks for an existing `last_session.pkl`. If found, it dispatches an asynchronous task (`_load_previous_state`) to re-populate internal queues and registries before initiating new network requests.
3. **Thread Delegation:** The application instantiates `ParserManager`. To prevent the UI from freezing, `ParserManager.moveToThread(self.parser_thread)` is invoked. A Qt Signal triggers `start_parsing` within this dedicated thread.

### Phase 2: Engine Initialization (`parser_manager.py`)
1. **Event Loop Creation:** The `ParserManager` creates a fresh `asyncio` event loop.
2. **Primitive Instantiation:** Asynchronous control primitives (`_pause_event`, `_stop_event`, `download_queue`, `quarantine_queue`) are dynamically generated inside the running event loop to prevent context runtime errors.
3. **Queue Seeding:** The `PriorityURLQueue` resets its internal locks and seeds the `start_url` into the queue with highest priority, marking it with metadata: `{"is_start_url": True}`.
4. **Session Bootstrapping:** A shared downloading session (`_shared_downloader_session`) is created, explicitly scoped to live only as long as the `_main_task`.

### Phase 3: The `_main_task` Coordinator
The core loop of the application is managed by `_main_task()`.
1. It reads user settings to determine thread allocation (e.g., `K.SETTING_PARSER_THREADS`, `K.SETTING_DOWNLOADER_THREADS`).
2. It spawns an array of `_parser_worker` tasks (Producers).
3. It spawns an array of `_downloader_worker` tasks (Consumers).
4. Both arrays run concurrently. The `_main_task` uses `asyncio.gather` to wait for all workers to finish while continually monitoring for `_stop_event` toggles.

### Phase 4: The Parser Workers (Producers)
Each `_parser_worker` runs an infinite asynchronous loop until the queue is depleted or a stop signal is received:
1. **Fetch:** Awaits a URL from the `PriorityURLQueue`. 
2. **Pause Check:** Awaits the `_pause_event.wait()` if the user has paused the app.
3. **Network Request:** Uses `aiohttp` to fetch the webpage. If it fails, the domain's health score degrades; if it hits the threshold, the URL is shifted to the `quarantine_queue`.
4. **HTML / JS Analysis:** Passes the raw payload to the HTML parser (BeautifulSoup/lxml). It scans for `<img>`, `<video>`, `<source>`, and `<a>` tags.
5. **Pattern Application:** Discovered URLs are passed through `SitePatternManager`. If an image matches a known "thumbnail" pattern, the regex engine automatically mutates the URL string to target the full-resolution asset.
6. **Classification & Routing:**
    *   **Media URLs** (images, videos) are pushed to the `download_queue`.
    *   **Navigation URLs** (internal links up to the configured `max_depth`) are ranked and pushed back into the `PriorityURLQueue`.
7. **Deduplication:** URLs are hashed or tracked in `processed_urls` to ensure cyclic links do not cause infinite loops.

### Phase 5: The Downloader Workers (Consumers)
Each `_downloader_worker` runs concurrently alongside the parsers:
1. **Consume:** Awaits a URL from the `download_queue`.
2. **Verification:** Checks the `downloaded_files` set. If the file is already downloaded, it skips the network request.
3. **Binary Fetching:** Uses the synchronous/asynchronous shared session to pull the binary stream of the file. By using a shared session, it benefits from connection pooling.
4. **File I/O:** Saves the binary stream to disk chunk-by-chunk using `aiofiles` to prevent memory bloat on large video files.
5. **Progress Emission:** Emits Qt Signals (`current_progress_updated`, `total_progress_updated`) back to the main thread. The Qt event loop receives these signals and safely updates the UI progress bars and text logs.

### Phase 6: Graceful Termination and Persistence
1. **Completion Detection:** When the `PriorityURLQueue` and `download_queue` report themselves as empty, and no active workers are processing data, the event loop initiates teardown.
2. **Session Persistence:** If the user manually clicks "Stop" or closes the app, `main_window.save_and_stop()` triggers an asynchronous save. `ParserManager` dumps the state of `processed_urls`, `downloaded_files`, and the current queue to `last_session.pkl`.
3. **Resource Cleanup:** The `_shared_downloader_session` is safely closed, `aiohttp` client managers are terminated, the Qt thread is quit, and a final statistical summary is dispatched to the UI's logging window.