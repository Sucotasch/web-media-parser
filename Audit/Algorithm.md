> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** Local Folder
> - **Auto-generated RAG Query:** "codebase_architecture_diagram,main_loop_algorithm,function_signatures,dependency_graph,core_data_structures,API_endpoints,state_machine_flow,error_handling_mechanisms,thread_safety_analysis,performance_bottlenecks,file_structure_hierarchy,algorithm_complexity,input_validation_rules,output_serialization_format,integration_points,code_coverage_metrics,static_analysis_results,build_process_workflow,configuration_management,security_vulnerabilities"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `web-media-parser/src/parser/parser_manager.py`
>   - `web-media-parser/src/parser/webpage_parser.py`
>   - `web-media-parser/src/downloader/media_downloader.py`
> 
> <details><summary><b>Task Instructions</b></summary>
> 
> ```text
> You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase. Analyze the provided GitHub repository data to create comprehensive technical documentation in Markdown format. Make the code understandable. Your audit must include 1. Real capabilities of the program. 2. Algorithm of operation and architecture - a detailed, step-by-step description of the core algorithms and data flow.
> ```
> </details>

---

# Web Media Parser - Technical Code Audit & Architecture Documentation

## 1. Real Capabilities of the Program

Based on a deep audit of the codebase, **Web Media Parser** is not just a standard web scraper; it is a highly concurrent, resilient deep-web media crawler engineered to operate defensively against common anti-bot mechanisms. Its real, code-level capabilities include:

### Advanced Content Extraction
* **Framework-Aware Lazy-Load Parsing:** Beyond standard `<img src="...">` tags, the engine (`WebpageParser`) actively parses JSON-injected data attributes and identifies framework-specific image bindings (React `data-lazy`, Vue `v-lazy`, Angular `[lazyLoad]`).
* **Thumbnail to Full-Size Resolution:** Through the `SitePatternManager`, it resolves high-resolution source URLs from thumbnails using custom JSON transformations or community-driven Imagus Sieve rules.
* **Inline CSS & Meta Tag Scraping:** Discovers media embedded in inline CSS backgrounds (`url(...)`), OpenGraph tags (`og:image`, `og:video`), and `<picture>` source nodes.
* **Intelligent Trash Filtering:** Uses heuristic algorithms (`_is_significant_media`) to discard UI elements, tracking pixels, avatars, and irrelevant SVG/GIF files by cross-referencing file dimensions, CSS visibility (bot-trap detection), and path nomenclature.

### Dynamic Defense Evasion
* **Automated Gateway / Interstitial Bypass:** The `_handle_gateways` method automatically detects age-gates, cookie consents, and NSFW warnings (e.g., "I agree", "Over 18") and actively executes POST/GET bypass payloads to acquire the necessary session cookies.
* **Asynchronous-to-Synchronous Fallback:** If modern async TLS fingerprinting causes an `aiohttp` request to fail or timeout (often due to Cloudflare/WAF), the parser seamlessly falls back to a synchronous, persistent `requests.Session` to fetch the payload.
* **JS Redirect Traversal:** Identifies and traverses obfuscated JavaScript page redirects (`window.location.replace`, `<meta refresh>`) up to a predefined depth.

### High-Performance Networking
* **Multi-Threaded Chunked Downloading:** For large media, `MediaDownloader` evaluates `Accept-Ranges: bytes` headers. If supported, it dynamically spawns localized threads (`_download_with_threads`) to partition and download the file in parallel, writing to shared disk buffers.
* **Connection Pooling:** Maintains a shared HTTP keep-alive connection pool (`create_shared_downloader_session`) across concurrent download workers to eliminate TCP/TLS handshake overhead.
* **Domain Health Quarantining:** URLs originating from domains with sequential network failures are routed to a `quarantine_queue` and retried under a "probation" algorithm, preventing slow/dead domains from blocking the main event loop.

---

## 2. Algorithm of Operation and Architecture

The application operates on an asynchronous producer-consumer architecture, bridging PySide6 GUI threads with Python's `asyncio` event loop.

### 2.1 Core Architectural Components

1. **ParserManager (The Orchestrator):** Runs a dedicated background event loop thread containing multiple Parser Workers (Producers) and Downloader Workers (Consumers).
2. **PriorityURLQueue:** Manages the URL frontier. It scores domains dynamically; domains yielding rich media are prioritized, enforcing a depth-first-search for high-value targets.
3. **WebpageParser / JSONWebpageParser:** Fetches target URLs, manipulates DOM/JSON, executes bypasses, and yields new Links (to the URL Queue) and Media (to the Download Queue).
4. **MediaDownloader:** Manages disk I/O, range-request multi-threading, and rate limiting.

### 2.2 Detailed Step-by-Step Data Flow

#### Phase 1: Initialization & Bootstrapping
1. The GUI invokes `ParserManager.start_parsing()`.
2. A new `asyncio` Event Loop is instantiated in a daemon thread (`AsyncEventLoopThread`). 
3. The `PriorityURLQueue` is seeded with the initial URL (depth 0).
4. The `_main_task` boots up, provisioning `N` `_parser_worker` tasks and `M` `_downloader_worker` tasks. A global `AsyncClientManager` is initialized for parsers, and a shared `requests.Session` is created for downloaders.

#### Phase 2: Page Parsing (Producer Loop)
1. A `_parser_worker` dequeues a target URL from the `url_queue`.
2. **Pre-Flight Checks:** The domain is checked against the internal `blocked_domains` list and the `processed_urls` registry.
3. **Fetching (`WebpageParser._get_content`):**
   * The worker attempts an `aiohttp` GET request with a tightly constrained timeout.
   * *Bypass check:* Pre-injects consent cookies (e.g., `gdpr_accepted=true`).
   * *Fallback:* If a connection error occurs, execution drops to a blocking `requests.get()` via `run_in_executor` to bypass restrictive firewalls.
   * Resolves any found JS/Meta redirects.
4. **DOM Extraction:**
   * The `BeautifulSoup` parsed DOM is analyzed. Hidden elements are skipped to avoid honeypots (`_is_element_visible`).
   * **Gateways:** If the page lacks media but contains trigger text ("confirm your age"), `_execute_bypass` is triggered, and cookies are synced back to the shared downloader session.
   * **Resolution:** `<img src>`, `srcset`, and JSON bindings are fed into the `SitePatternManager` to attempt resolution to a larger, uncompressed original media URL.
5. **Yielding:**
   * Discovered internal links are pushed back to the `url_queue` (depth + 1).
   * High-quality media links are packaged as dictionaries and pushed to the `download_queue`.

#### Phase 3: Media Downloading (Consumer Loop)
1. A `_downloader_worker` pulls a media dictionary from the `download_queue`.
2. **Domain Health Check:** Checks if the domain is currently in the `quarantined_domains` set. If so, drops it into the `quarantine_queue` to prevent immediate stalling.
3. **Head Request:** Executes a `HEAD` request to validate `Content-Type` and `Content-Length`. Rejects HTML/Text masquerading as images (often interstitial landing pages).
4. **Transfer:**
   * **Multi-threaded (Large files):** If `Accept-Ranges` is valid and file size exceeds limits, `_download_with_threads` partitions the file into chunks. Threads write data simultaneously to `.partX` temporary files.
   * **Single-threaded (Small files):** Downloads sequentially directly into memory buffers, flushing to disk via `bytearray` when `WRITE_BUFFER_SIZE` is met.
5. **Resolution:** Upon success, temporary chunks are merged. `files_downloaded` statistics are incremented. If the target failed, the domain's failure counter is incremented, risking quarantine.

#### Phase 4: Lifecycle Management (Pause/Resume & Stop)
* **Idle Completion:** A background `_completion_monitor` constantly evaluates the queues. If all queues are empty and network tasks are 0 for a predefined threshold, natural completion is triggered.
* **State Checkpointing:** If the user hits "Pause", `ParserManager.save_state` pauses the queues and dumps the URL frontiers, processed registries, and domain health metrics to disk using `pickle` via `aiofiles`. Upon resuming, the task queue is deserialized back into memory, ensuring zero data loss across GUI reloads.