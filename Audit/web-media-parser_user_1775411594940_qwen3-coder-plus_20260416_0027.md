> **🤖 Prompt Generation Metadata**
> - **Model:** qwen3-coder-plus
> - **Target Repository:** Local Folder
> - **Auto-generated RAG Query:** "job queue,parsing pause,resume functionality,state restoration,file reprocessing,duplicate downloads,concurrency,race condition,thread safety,async processing,task management"
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
> Сосредоточься на очереди заданий, паузе и возобновлении парсинга. Восстановление состояния при resume, почему при resume парсинг начинается заново и загружаются те же файлы.
> ```
> </details>

---

# Code Audit Report: Web Media Parser

## Executive Summary

This audit identifies critical defects in the task queuing, pause/resume, and state management components of the Web Media Parser. The primary issue causing files to be re-downloaded upon resume is an incorrect pause logic that hard-stops the engine instead of pausing it, leading to data loss and an inability to restore the exact previous state. The findings present a significant risk of inconsistent behavior and reduced reliability.

## Defects Identified

### 1. Critical: Incorrect Pause Logic Leading to Data Loss and Re-download on Resume

**Location:** `src/gui/main_window.py` (Parts 5 & 6)

**Problem:**
The current pause mechanism in `MainWindow.toggle_pause` incorrectly calls `self.parser_manager.stop_parsing()` instead of a proper pause command. `stop_parsing` performs a hard shutdown, cancelling all tasks and closing queues, which means the internal state of what has been processed is lost or not accurately reflected in the saved pickle file. When the application is later resumed, the state restoration is based on a stale or corrupted snapshot, causing the parser to re-visit pages and re-attempt downloads for items already processed or partially downloaded.

**Code Snippet (Incorrect):**
```python
# In MainWindow.toggle_pause
# ... (saving state)
self.parser_manager.stop_parsing() # This hard-stops everything, invalidating the saved state's accuracy!
```

**Performance Impact:**
- **Data Loss:** In-flight network requests and pending queue items are lost.
- **Re-processing:** Upon resume, the same URLs and media are crawled and downloaded again, leading to wasted bandwidth and time.
- **Inconsistent State:** The saved state does not reflect the true state of the queues and processed items at the moment of the pause request.

**Recommendation:**
Introduce a proper `pause_parsing` method in `ParserManager` that uses an `asyncio.Event` (`_pause_event`) to signal all worker coroutines to suspend their `await get()` operations on queues. The `MainWindow` should first save the state while the parser is still running (or in a brief, controlled pause) and then call this new `pause_parsing` method. The `stop_parsing` method should be reserved for final termination.

### 2. High: Unsafe Direct Access to `asyncio.Queue._queue` for Serialization

**Location:** `src/parser/parser_manager.py` (likely in the `save_state` method, inferred from audit reports)

**Problem:**
The `save_state` method directly accesses `self.download_queue._queue` (and likely `self.url_queue._queue`) to serialize its contents. This is an internal implementation detail of `asyncio.Queue` and is not thread-safe. Concurrent modifications by parser/download workers could lead to a corrupted or inconsistent snapshot being saved.

**Code Snippet (Inferred from audits as unsafe pattern):**
```python
# Example of problematic code in save_state
download_queue_items = list(self.download_queue._queue) # UNSAFE!
```

**Performance Impact:**
- **Race Conditions:** Corrupted queue state during save/resume cycles, leading to missing or duplicated items.
- **Non-deterministic Behavior:** Application might fail to resume correctly or process items twice.

**Recommendation:**
Implement a safe snapshot mechanism for the queues. This can be achieved by wrapping the internal data structure (like `collections.deque`) in an `asyncio.Lock` and providing a public `async def get_snapshot(self)` method that acquires the lock, copies the data, and returns it. The `save_state` logic should then use this method.

### 3. High: Incorrect State Restoration Flow

**Location:** `src/gui/main_window.py` (likely in the startup/resume logic, inferred from problem description)

**Problem:**
The application's logic for determining whether to start a fresh parse or resume from a saved state appears flawed. If the state file exists, it should be loaded, and parsing should resume. However, the hard-stop on pause implies that the loading logic might be bypassed or not correctly initializing the queues and processed registries, causing a fallback to a fresh start.

**Performance Impact:**
- **User Confusion:** Expected behavior (resume from last point) does not occur.
- **Resource Waste:** Same content is re-processed.

**Recommendation:**
Ensure the startup/resume logic in `main_window.py` correctly checks for a `last_session.pkl`, loads it using `parser_manager.load_state()`, and only starts a new task if no valid session state exists or if the user explicitly chooses to start a new one. The loaded state must fully populate all queues and internal registries.

## Actionable Remediation Plan

### Fix #1: Implement Correct Pause/Resume Mechanism in `ParserManager`

1.  **Locate:** `src/parser/parser_manager.py`
2.  **Action:** Add a private `asyncio.Event` for pausing and implement the pause/resume logic in the worker coroutines.
    ```python
    import asyncio

    class ParserManager:
        def __init__(...):
            # ... existing initialization ...
            self._pause_event = asyncio.Event()
            self._pause_event.set()  # Initially, it's *not* paused

        async def pause_parsing(self):
            """Set the pause event, signaling workers to stop fetching new tasks."""
            self.log_handler.info("Pause signal received. Workers will pause soon.")
            self._pause_event.clear()

        async def resume_parsing(self):
            """Clear the pause event, signaling workers to resume."""
            self.log_handler.info("Resume signal received. Workers will resume.")
            self._pause_event.set()

        # In each worker coroutine (_parser_worker, _downloader_worker, etc.)
        async def _parser_worker(self):
            while True:
                # Before getting a new task, wait for the pause event to be set
                await self._pause_event.wait()
                
                try:
                    # Set a short timeout to periodically check the pause state
                    task = await asyncio.wait_for(self.url_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue # Loop back to check pause state again
                
                # Process the task...
                # ... existing task processing logic ...
        
        @property
        def is_paused(self):
            return not self._pause_event.is_set()
    ```

### Fix #2: Update `MainWindow.toggle_pause` to Use New Methods

1.  **Locate:** `src/gui/main_window.py`
2.  **Action:** Refine the `toggle_pause` method to interact with the new pause/resume system. The sequence should be "Save State -> Send Pause Signal".
    ```python
    def toggle_pause(self):
        """Pause active task or Resume selected task."""
        selected = self._get_selected_task()

        if selected and selected.status == TaskStatus.PAUSED and not self.task_queue.active_task:
            self._launch_task(selected)
            return

        if not self.parser_manager or not self.task_queue.active_task:
            return

        active = self.task_queue.active_task
        self.log_handler.info(f"Toggling pause for task {active.id}...")

        if self.parser_manager.is_paused:
            # We are currently paused, so resume
            self.status_bar.showMessage("Resuming...")
            # Use run_coroutine_threadsafe to call the async resume method
            future = asyncio.run_coroutine_threadsafe(
                self.parser_manager.resume_parsing(), self.parser_manager.loop
            )
            future.result(timeout=10) # Wait for confirmation
            active.mark_running()
            self.update_ui_state(running=True)
            self.status_bar.showMessage("Parsing resumed")
        else:
            # We are currently running, so pause
            self.status_bar.showMessage("Saving state before pausing...")
            state_path = self.task_queue.get_state_file_path(active.id)

            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.parser_manager.save_state(state_path), self.parser_manager.loop
                )
                future.result(timeout=15) # Ensure state is saved before pausing
                self.log_handler.info(f"State saved for {active.id}")
            except Exception as e:
                self.log_handler.error(f"Error saving state on pause: {e}")

            # Now send the pause signal
            future = asyncio.run_coroutine_threadsafe(
                self.parser_manager.pause_parsing(), self.parser_manager.loop
            )
            future.result(timeout=10) # Wait for confirmation
            active.mark_paused()
            self.update_ui_state(running=False)
            self.status_bar.showMessage("Parsing paused")
    ```
3.  **Update `stop_parsing` in `MainWindow`:** The `on_parsing_finished` handler should be the only place calling the *final* `self.parser_manager.stop_parsing()` which closes queues and tears down the event loop. The old pause logic calling `stop_parsing` directly should be removed.

### Fix #3: Refactor Queues for Safe Snapshotting

1.  **Locate:** `src/parser/priority_url_queue.py` and consider creating a new file like `src/core/safe_snapshot_queue.py`.
2.  **Action:** Create a new queue class that provides a thread-safe way to get a snapshot. For example:
    ```python
    # File: src/core/safe_snapshot_queue.py
    import asyncio
    import collections
    
    class SafeSnapshotQueue:
        def __init__(self):
            self._deque = collections.deque()
            self._lock = asyncio.Lock()
            self._getters = collections.deque() # For internal asyncio.Queue-like waiting

        def empty(self):
            return len(self._deque) == 0

        def qsize(self):
            return len(self._deque)

        async def put(self, item):
            async with self._lock:
                self._deque.append(item)
                # Notify any waiting getters if available

        async def get(self):
            while True:
                async with self._lock:
                    if self._deque:
                        return self._deque.popleft()
                # Wait for an item to become available
                getter = asyncio.Future()
                self._getters.append(getter)
                try:
                    await getter
                except:
                    self._getters.remove(getter)
                    raise

        async def get_snapshot(self):
            """Returns a list of items currently in the queue without modifying it."""
            async with self._lock:
                return list(self._deque)
    ```
3.  **Update `ParserManager`:** Replace `asyncio.Queue` instances used for state-saving (e.g., `download_queue`) with the new `SafeSnapshotQueue`. Update the `save_state` method to call `await self.download_queue.get_snapshot()`.

### Fix #4: Verify State Loading Logic

1.  **Locate:** The application's startup logic, likely in `main.py` or initiated by `MainWindow`.
2.  **Action:** Ensure the startup flow checks for `sessions/last_session.pkl`. If found, it should load the state into the `ParserManager` *before* starting any workers. This involves calling `asyncio.run_coroutine_threadsafe(parser_manager.load_state(path), loop)` and awaiting its completion before calling `start_parsing`. This ensures all queues and internal states are correctly populated from the saved snapshot before the crawling/resume begins.