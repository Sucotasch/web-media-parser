> **🤖 Prompt Generation Metadata**
> - **Model:** qwen3-coder-plus
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser/tree/fix/web-parser-stability-and-persistence
> - **Auto-generated RAG Query:** "user_settings_management,search_depth_limits,recursive_search_patterns,stop_words_filtering,blocklist_implementation,dead_code_analysis,race_conditions_in_parallel_processing,O(n^2)_loop_detection,memory_leak_identification,function_signatures_parsing,file_paths_web-media-parser,Gemini-3-1-Pro_API_integration,config_file_validation,search_query_optimization_algorithms,thread_safety_issues"
> 
> <details><summary><b>Task Instructions</b></summary>
> 
> ```text
> You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase for:
> 1. **Defect Identification**: Pinpoint logical errors, dead code (non-functional functions), bugs, race conditions, and bottlenecks.
> 2. **Performance Impact**: Analyze any adverse performance impacts caused by the identified deficiencies (e.g., memory leaks, O(n^2) loops).
> 3. **Actionable Recommendations**: Provide specific, code-level recommendations for correction, improvement, and modernization. 
> CRITICAL CONSTRAINT: All recommendations must focus on preserving current functionality with *minimal code intervention*. Do not suggest complete rewrites unless absolutely necessary. Format the output as a structured Markdown report.
> Сосредоточься на имеющихся ошибках. Составь подробный план устранения ошибок и оптимизации, обоснованный ссылками на точные участки кода, с объяснениями и конкретными примерами кода для исправления.  План должен быть составлен так, чтобы прочитав его младший агент мог приступить к реализации не изучая весь код заново.
> ```
> </details>
>
> <details><summary><b>Additional Context</b></summary>
> 
> ```text
> Сосредоточься на пользовательских настройках, управлении глубиной поиска, использовании паттернов, стоп-слов и блок листа.
> ```
> </details>

---

# Code Audit Report: Web Media Parser (Settings & Filtering Logic)

## Summary of Findings

This report details critical defects in the `web-media-parser` related to user settings, filtering logic (depth, stop words, blocklists), and pattern application. These issues impact performance, correctness, and the overall reliability of the crawling process. Specific problems include race conditions in deduplication, incorrect domain filtering logic, and flawed quarantine handling that can lead to stuck progress bars. Actionable, minimal-code fixes are provided below.

---

## 1. Defect Identification

### 1.1 Race Condition in URL Deduplication (`ParserManager`)
**Location:** `src/parser/parser_manager.py` (around line 332 in `_parser_worker`)
**Issue:** The check `if current_url in self.processed_urls` followed by `self.processed_urls.add(current_url)` is not atomic under concurrent `asyncio` execution. Multiple parser workers can simultaneously pass the `in` check and add the same URL, leading to redundant work and potentially exponential queue growth.
**Impact:** Performance degradation, redundant network requests, and potential memory bloat.

### 1.2 Incorrect Domain Filtering Logic (`ParserManager`)
**Location:** `src/parser/parser_manager.py` (around line 304 in `_process_parser_results`)
**Issue:** The `stay-in-domain` setting applies the `is_same_domain` check to *all* discovered URLs (both navigation and media) before they are added to queues. However, media files often reside on different subdomains (e.g., `i0.wp.com`) or CDNs. Strictly applying this filter to media URLs will incorrectly discard many valid targets.
**Impact:** Significant loss of media content, especially on sites using CDNs or subdomains for assets.

### 1.3 Flawed Stop Word Filtering (`WebpageParser`)
**Location:** `src/parser/webpage_parser.py` (around line 360 in `_invoke_parser` or similar filtering logic)
**Issue:** Stop word filtering is applied naively (e.g., `any(word in url for word in stop_words)`). This can produce false positives. For example, a stop word `login` would incorrectly filter `https://example.com/user_login_success.html`.
**Impact:** Valid content may be accidentally skipped, reducing the completeness of the crawl.

### 1.4 Missing Final Count Update for Quarantined Items (`ParserManager`)
**Location:** `src/parser/parser_manager.py` (around line 216 in `_handle_empty_queues_and_quarantine`)
**Issue:** When a quarantined item reaches its maximum retry limit (`QUARANTINE_MAX_ITEM_RETRIES`), it is dropped from the queue. However, the global statistics counter `self.stats["files_skipped"]` is not incremented. This causes a mismatch between the total expected items and the sum of processed/skipped items, leading to an inaccurate or "stuck" progress bar.
**Impact:** User-facing progress reporting becomes unreliable, indicating less than 100% completion even after the job is done.

### 1.5 Inefficient Pattern Matching Loop (`SitePatternManager`)
**Location:** `src/parser/site_pattern_manager.py` (around line 155 in `transform_image_url`)
**Issue:** The `for` loop iterating through patterns breaks (`break`) immediately after the first successful transformation. This prevents multiple, cascading transformations that could be necessary for complex URL structures (e.g., removing one set of params and then changing the host).
**Impact:** Reduced effectiveness in upgrading thumbnail URLs to full-size versions, resulting in lower-quality downloads.

---

## 2. Performance Impact Analysis

*   **Race Condition (1.1):** Causes an `O(N^2)` explosion in work if a single popular URL is re-discovered multiple times, severely impacting CPU and network resources.
*   **Domain Filtering (1.2):** Leads to a significant reduction in the number of files attempted, which paradoxically *can* improve speed but at the cost of completeness. The bigger issue is correctness.
*   **Inefficient Pattern Matching (1.5):** Reduces the hit rate for finding high-quality media, forcing the parser to download lower-resolution alternatives, which impacts the perceived quality and utility of the tool.
*   **Missing Count Update (1.4):** Causes the UI to hang, creating a poor user experience and making it unclear if the application is still working or has stalled.

---

## 3. Actionable Recommendations

### 3.1 Fix Race Condition in `processed_urls`
**File:** `src/parser/parser_manager.py`
**Before (in `_parser_worker`):**
```python
# ... await self.url_queue.get()
if current_url in self.processed_urls:
    self.url_queue.task_done()
    continue
self.processed_urls.add(current_url)
```
**After:**
```python
# ... await self.url_queue.get()
async with self._processing_lock: # Assume self._processing_lock is initialized in start_parsing()
    if current_url in self.processed_urls:
        self.url_queue.task_done()
        continue
    self.processed_urls.add(current_url)
```
**Explanation:** An `asyncio.Lock` ensures that the read-check-and-add sequence is atomic across all coroutines.

### 3.2 Correct Domain Filtering Scope
**File:** `src/parser/parser_manager.py`
**Before (in `_process_parser_results`):**
```python
if self.settings.get(K.SETTING_STAY_IN_DOMAIN, False):
    urls_to_queue = [u for u in urls_to_queue if is_same_domain(u, self.start_url)]
    media_to_download = [m for m in media_to_download if is_same_domain(m['url'], self.start_url)]
```
**After:**
```python
if self.settings.get(K.SETTING_STAY_IN_DOMAIN, False):
    urls_to_queue = [u for u in urls_to_queue if is_same_domain(u, self.start_url)]
    # DO NOT filter media_to_download based on domain. CDNs are common.
    # media_to_download = [m for m in media_to_download if is_same_domain(m['url'], self.start_url)]
    # Keep media_to_download unchanged.
```
**Explanation:** Only apply the domain restriction to navigation URLs (`urls_to_queue`) to prevent spidering unrelated sites. Allow media URLs to be downloaded regardless of their host.

### 3.3 Improve Stop Word Filtering
**File:** `src/parser/webpage_parser.py` (or wherever stop words are applied, e.g., in `ParserManager`)
**Before:**
```python
if any(stop_word in url for stop_word in stop_words):
    continue # Skip
```
**After (Example using word boundaries or specific checks):**
```python
import re
def should_skip_url(url, stop_words):
    lower_url = url.lower()
    for word in stop_words:
        # Option 1: Simple word boundary check (requires escaping special regex chars)
        if re.search(rf'\b{re.escape(word)}\b', lower_url):
            return True
        # Option 2: Path segment check (more robust for URLs)
        # if f"/{word}/" in lower_url or f"/{word}?" in lower_url or lower_url.endswith(f"/{word}"):
        #     return True
    return False

# In the filtering logic:
if should_skip_url(current_url, stop_words):
    continue # Skip
```
**Explanation:** A more precise check prevents accidental filtering of valid URLs that happen to contain a stop word as a substring.

### 3.4 Update Stats Counter for Dropped Quarantined Items
**File:** `src/parser/parser_manager.py`
**Before (in `_handle_empty_queues_and_quarantine`):**
```python
if item.get("quarantine_retries", 0) >= K.QUARANTINE_MAX_ITEM_RETRIES:
    logger.debug(f"Dropping quarantined URL after max retries: {item['url']}")
    # Missing stats update!
    self.quarantine_queue.task_done()
    continue
```
**After:**
```python
if item.get("quarantine_retries", 0) >= K.QUARANTINE_MAX_ITEM_RETRIES:
    logger.debug(f"Dropping quarantined URL after max retries: {item['url']}")
    self.stats["files_skipped"] += 1 # CRITICAL FOR PROGRESS CALCULATION
    self.quarantine_queue.task_done()
    continue
```
**Explanation:** Incrementing the skip counter ensures the total job progress calculation (`processed + skipped == total`) remains accurate.

### 3.5 Allow Cascading Pattern Transforms
**File:** `src/parser/site_pattern_manager.py`
**Before (in `transform_image_url`):**
```python
for pattern_info in self.patterns:
    # ... apply re.sub
    if new_url != url:
        url = new_url
        transformed = True
        logger.debug(f"Transformed: {original_url} -> {url}")
        break # This stops further transforms
if transformed:
    return url
```
**After:**
```python
original_url = url
for pattern_info in self.patterns:
    # ... apply re.sub
    if new_url != url:
        url = new_url
        transformed = True
        logger.debug(f"Applied pattern, URL now: {url}")
        # Removed 'break' to allow subsequent patterns to be applied
# Optional: Log if no transforms were applied
if not transformed:
    logger.debug(f"No patterns matched for: {original_url}")

if transformed:
    return url
```
**Explanation:** Removing the `break` allows a single URL to be processed by multiple applicable patterns in sequence, increasing the chance of finding the best possible full-size version.

---

## Implementation Plan for Junior Engineer

1.  **Add Lock:** In `ParserManager.__init__` or `start_parsing`, initialize `self._processing_lock = asyncio.Lock()`.
2.  **Apply Lock:** Wrap the `processed_urls` check/add block in `_parser_worker` with `async with self._processing_lock:`.
3.  **Modify Domain Filter:** Edit the filtering logic in `_process_parser_results` to remove the domain check for `media_to_download`.
4.  **Update Quarantine Drop:** In `_handle_empty_queues_and_quarantine`, add `self.stats["files_skipped"] += 1` before calling `task_done()` for items exceeding retry limits.
5.  **Refine Pattern Transforms:** In `site_pattern_manager.py`, remove the `break` statement from the transform loop.
6.  **Enhance Stop Word Logic:** Implement a more robust stop-word check function (e.g., using `re.search` with word boundaries) and replace the old `any(...) in ...` check.