# Web Media Parser — Full Code Audit & Bug Hunt

| | |
|---|---|
| **Date** | 2026-07-16 |
| **Scope** | Full algorithm trace: GUI → queue → ParserManager → crawl → download → extension |
| **Method** | Source reading only (no speculative “maybe”). Runtime proof for path mismatch + double `task_done`. Unit tests: **12 passed, 1 skipped** |
| **Code changes** | **None** — this document only |
| **Audience** | Junior developer who can fix bugs without re-learning the whole codebase |

---

## 0. How to use this document

1. Read **§1 Algorithm map** once (15 minutes) — enough context to understand every bug.
2. Fix bugs in order of **severity** (P0 → P1 → P2). Each item is self-contained:
   - **Where** (file + function)
   - **Evidence** (real code)
   - **How to verify** the bug
   - **Fix** (copy-paste-oriented)
   - **Regression checklist** (what not to break)
3. After each fix: `python -m pytest tests -q` and manual smoke for Pause/Stop/Page only.

---

## 1. Algorithm map (end-to-end)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ main.py                                                                 │
│  LXML/Brotli patches → QApplication → MainWindow                        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│ MainWindow (GUI thread)                                                 │
│  • TaskQueueManager: list of TaskItem (one active at a time)            │
│  • Start → new ParserManager + new QThread per task                     │
│  • Pause ≈ save_state + stop_parsing (hard stop, state kept)            │
│  • Stop  ≈ stop_parsing + delete partials + delete state                │
│  • ExtensionServer thread: POST /api/tasks → add_task_to_queue          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ QThread.started
┌───────────────────────────────▼─────────────────────────────────────────┐
│ ParserManager.start_parsing()                                           │
│  Creates asyncio.Event/Queue on correct loop                            │
│  Thread AsyncEventLoop → _main_task()                                   │
│  Thread ProgressMonitor → progress bars                                 │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│ _main_task()                                                            │
│  1. load_state(download_path)   # resume if pickle exists               │
│  2. seed start_url OR pending_downloads (extension one-shot)            │
│  3. shared requests.Session (downloads) + aiohttp session (pages)       │
│  4. N × _parser_worker + M × _downloader_worker + _completion_monitor   │
│  5. wait _stop_event → cancel workers → emit task_ended(reason)         │
└─────────────┬───────────────────────────────────┬───────────────────────┘
              │                                   │
┌─────────────▼─────────────┐       ┌─────────────▼───────────────────────┐
│ _parser_worker            │       │ _downloader_worker                  │
│  url_queue.get            │       │  download_queue.get                 │
│  WebpageParser / JSON     │       │  MediaDownloader (thread pool)      │
│  media → download_queue   │       │  domain health / quarantine         │
│  links → url_queue        │       │  interstitial retry → url_queue     │
└───────────────────────────┘       └─────────────────────────────────────┘
```

### 1.1 Important invariants (do not break while fixing)

| Invariant | Why |
|-----------|-----|
| One `ParserManager` instance = one task | Lifecycle baked into `start_parsing` / queues |
| New `QThread` every launch | Cannot restart after `quit()`/`wait()` |
| Asyncio primitives created on the loop thread | Cross-loop errors otherwise |
| Shared downloader session has `Retry(total=0)` | Stop must not hang in urllib3 retries |
| `task_ended` always emitted | Queue auto-next depends on it |
| Settings snapshotted in `TaskItem` at add-time | Later Settings edits must not change queued tasks |

### 1.2 Session / queue file locations (as designed vs as coded)

| Artifact | Intended / used by | Actual path in code |
|----------|--------------------|---------------------|
| Queue JSON | `MainWindow` | `{download_dir}/task_queue.json` |
| Session pickle (save/load) | `ParserManager.save_state` / `load_state` | `{task.download_path}/sessions/{task_id}/last_session.pkl` (or without `{task_id}` if `task_id` is None) |
| Session path for **Stop delete** | `TaskQueueManager.get_state_file_path` | `{base_download_dir}/sessions/{task_id}/state.pkl` ← **DIFFERENT** |

Verified by simulation:

```
TQM delete path: D:\downloads\sessions\abc123\state.pkl
PM save path:    D:\downloads\example_com_20260101_120000\sessions\abc123\last_session.pkl
Same? False
```

---

## 2. Confirmed bugs (ordered by severity)

Legend: **P0** = data loss / wrong core behavior · **P1** = real defect, user-visible · **P2** = robustness / consistency · **P3** = cleanup / docs / tests.

---

### BUG-01 [P0] Pause/Resume drops pending downloads

**Where:**  
- Enqueue: `src/parser/parser_manager.py` → `_process_media_batch`  
- Restore: `src/parser/parser_manager.py` → `load_state`

**Evidence:**

On discovery, URL is added to `downloaded_files` **before** the file is downloaded (set name is misleading — it means “already scheduled”, not “completed”):

```python
# parser_manager.py — _process_media_batch
async with self._processed_lock:
    if abs_url in self.downloaded_files: continue
    self.downloaded_files.add(abs_url)   # ← marked at QUEUE time
# ...
await self.download_queue.put(media_item)
```

On resume, pending queue items are **skipped** if their URL is in that set:

```python
# parser_manager.py — load_state
for item in state.get("download_queue_items", []):
    if item.get("url") not in self.downloaded_files:
        await self.download_queue.put(item)
    else:
        skipped_downloads += 1   # ← ALL pending items skipped
```

Because every queued item is already in `downloaded_files`, **every pending download is dropped after Pause → Resume**.

**How to verify:**

1. Start crawl on a rich media page.
2. Wait until download queue has items (status “Downloading…”).
3. Pause before many files finish.
4. Resume same task.
5. Observe: few/no remaining downloads; logs may show `skipped N already-downloaded items`.

**Impact:** Silent data loss of the main product feature (download). Critical.

**Recommended fix (safe, minimal):**

Restore **all** `download_queue_items` without filtering by `downloaded_files`. Keep `downloaded_files` as “already scheduled / do not re-discover from HTML”.

```python
# load_state — REPLACE the filter loop with:
for item in state.get("download_queue_items", []):
    await self.download_queue.put(item)
```

Optional stronger design (do later if you touch this area more):

| Set | Meaning | When to add |
|-----|---------|-------------|
| `downloaded_files` | Successfully finished on disk | After `result["success"]` in `_downloader_worker` |
| `queued_media_urls` | Scheduled once | At enqueue in `_process_media_batch` |

If you rename sets, update `save_state` keys carefully and keep backward compatibility when loading old pickles:

```python
self.downloaded_files = set(state.get("downloaded_files", []))
# old pickles: downloaded_files may include only-queued URLs — still OK if load no longer filters
```

**Why safe:**  
- Does not re-download completed files: they are already out of `download_queue_items` (removed by `get()`).  
- Dedup of re-discovery still uses the set filled at enqueue.  
- No performance hit (less filtering).  
- No functional loss — restores intended Pause/Resume.

**Regression checklist:**  
- Pause with empty download queue still works.  
- Full natural completion still works.  
- Same media URL on two pages still downloaded once (still in set at first enqueue).

---

### BUG-02 [P0] State file path mismatch (Stop never deletes real session)

**Where:**  
- Save/load: `ParserManager.save_state` / `load_state`  
- Delete on Stop: `MainWindow.stop_parsing` + `TaskQueueManager.get_state_file_path`

**Evidence:**

```python
# parser_manager.py — save_state / load_state
session_dir = os.path.join(task_download_path, K.SESSION_STATE_SUBDIR)  # task folder
if self.task_id:
    session_dir = os.path.join(session_dir, self.task_id)
full_state_path = os.path.join(session_dir, K.SESSION_STATE_FILENAME)  # "last_session.pkl"
# → {download_path}/sessions/{task_id}/last_session.pkl
```

```python
# task_queue_manager.py — get_state_file_path
session_dir = os.path.join(self._base_dir, "sessions", task_id)  # base download dir!
return os.path.join(session_dir, "state.pkl")
# → {download_dir}/sessions/{task_id}/state.pkl
```

```python
# main_window.py — stop_parsing
state_path = self.task_queue.get_state_file_path(active.id)
if os.path.exists(state_path):
    os.remove(state_path)  # almost never exists → real pickle orphaned
```

**Impact:**  
- Stop leaves orphan pickles under task folders.  
- Any future code that “reopens” a stopped task ID can load stale queue and re-download or mis-count.  
- Disk clutter; two APIs for “session path” confuse future work.

**Recommended fix (single source of truth):**

1. Add one helper used by **everyone** (prefer next to existing path helpers):

```python
# src/app_paths.py  (or method on TaskQueueManager)
def task_state_path(task_download_path: str, task_id: str | None) -> str:
    """Canonical session pickle path for a task."""
    session_dir = os.path.join(task_download_path, "sessions")
    if task_id:
        session_dir = os.path.join(session_dir, task_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "last_session.pkl")  # keep current filename for compatibility
```

2. Use it in `save_state`, `load_state`, and `stop_parsing` delete.

3. Change `get_state_file_path` to require `download_path`:

```python
def get_state_file_path(self, task: TaskItem) -> str:
    return task_state_path(task.download_path, task.id)
```

4. Update `stop_parsing`:

```python
state_path = self.task_queue.get_state_file_path(active)
if os.path.exists(state_path):
    os.remove(state_path)
```

**Do not** only change the delete path without aligning save — keep one formula.

**Why safe:** Paths already used successfully by save/load; Stop just starts deleting the real file. No change to pickle format.

**Regression checklist:**  
- Pause → pickle appears under `{task_folder}/sessions/{id}/last_session.pkl`.  
- Stop → that file is gone.  
- Resume after Pause still loads.

---

### BUG-03 [P0] “Page only” ignored when starting from URL field (Start without +)

**Where:** `src/gui/main_window.py` → `_start_from_url_input`

**Evidence:**

`+` button correctly passes checkbox:

```python
# add_task_to_queue
task = self.task_queue.add_task(..., one_shot=self.one_shot_check.isChecked())
```

Direct Start path does **not**:

```python
# _start_from_url_input
self.parser_manager = ParserManager(
    url=url,
    download_path=download_path,
    settings=settings,
    log_handler=self.log_handler,
    # missing: task_id=..., one_shot=...
)
task = self.task_queue.add_task(url, settings, download_path)  # one_shot defaults False
```

**How to verify:**

1. Empty queue.  
2. Check **Page only**.  
3. Paste URL, press **Start** (do not press `+`).  
4. Observe crawl follows links (depth > 0), not single-page media only.

**Recommended fix:**

```python
one_shot = self.one_shot_check.isChecked()
# ...
task = self.task_queue.add_task(url, settings, download_path, one_shot=one_shot)
self.task_queue.start_task(task.id)

self.parser_manager = ParserManager(
    url=url,
    download_path=download_path,
    settings=settings,
    log_handler=self.log_handler,
    task_id=task.id,          # also fixes session isolation for this path
    one_shot=one_shot,
)
# Then connect signals + start QThread (existing code)
# Prefer reusing _launch_parser_for_task(task) after add+start to avoid dual paths:
#   task = self.task_queue.add_task(...)
#   self.task_queue.start_task(task.id)
#   self._launch_parser_for_task(task)
```

**Best structure (less duplication):**

```python
def _start_from_url_input(self):
    # validate url + create download_path (existing)
    settings = self.settings_dialog.get_settings()
    one_shot = self.one_shot_check.isChecked()
    task = self.task_queue.add_task(url, settings, download_path, one_shot=one_shot)
    self.task_queue.start_task(task.id)
    self._launch_parser_for_task(task)
    self.one_shot_check.setChecked(False)
    self.update_ui_state(True)
    self.status_bar.showMessage("Parsing started")
```

**Why safe:** Same code path as “add then start selected”; no new semantics. `task_id` makes Pause state consistent with queue tasks.

**Regression checklist:**  
- Start without Page only still deep-crawls.  
- `+` then Start still works.  
- Extension one-shot unchanged.

---

### BUG-04 [P1] Double `task_done()` on `download_queue` can crash worker

**Where:** `src/parser/parser_manager.py` → `_downloader_worker`

**Evidence:**

```python
try:
    if not self.is_running:
        self.download_queue.task_done(); break   # first call
    # ...
finally:
    with self._active_tasks_lock:
        self._active_tasks -= 1
    self.download_queue.task_done()             # second call — always runs after break
```

Python `finally` runs on `break`. Runtime proof:

```
double task_done raises: ValueError task_done() called too many times
```

`url_queue.task_done` is a no-op (`priority_url_queue.py`), so the same pattern there is harmless. **`asyncio.Queue.task_done` is not.**

**How to verify:**  
Hard to hit in UI always (needs item `get()` then immediate stop before process). Unit-level:

```python
# conceptual test
# get item, set is_running False, run one loop iteration → ValueError
```

**Impact:** Worker dies with traceback; downloads for remaining items may stall; completion monitor may hang until idle timeout weirdness.

**Recommended fix:** Never call `task_done` inside `try` if `finally` also calls it.

```python
try:
    if not self.is_running:
        break   # only finally calls task_done
    url = media_item["url"]
    # ... rest unchanged
finally:
    with self._active_tasks_lock:
        self._active_tasks -= 1
    self.download_queue.task_done()
```

Also fix the rare empty-item branch **before** the `try` that increments `_active_tasks` — currently:

```python
if not media_item:
    self.download_queue.task_done(); continue  # OK — outside try/finally
```

Keep that as-is (only one call).

Same cleanup in `_parser_worker` for consistency (even though url_queue is no-op):

```python
if not self.is_running:
    break  # remove early task_done(); finally handles it
# remove task_done() before continue on already-processed:
if current_url in self.processed_urls:
    continue  # finally still task_done + active_tasks--
```

**Why safe:** Matches asyncio Queue contract (one `task_done` per `get`). No change to download logic.

---

### BUG-05 [P1] Race: Pause/switch can mark task STOPPED instead of PAUSED

**Where:**  
- `MainWindow.toggle_pause` — order mostly correct (mark PAUSED first)  
- `MainWindow._pause_current_for_switch` — **wrong order**  
- `MainWindow.on_task_ended` — overwrites status if not PAUSED

**Evidence:**

```python
# _pause_current_for_switch — BAD ORDER
pm.stop_parsing()          # eventually emits task_ended("stopped")
active.mark_paused()       # may run AFTER task_ended processed
self.task_queue.clear_active()
```

```python
# on_task_ended
if active.status != TaskStatus.PAUSED:
    if reason == "stopped":
        active.mark_stopped()   # race window
```

`toggle_pause` is safer (marks PAUSED first). Switch-on-Start is not.

**How to verify:**  
Start task A, immediately Start task B (or select B and Start). Under load, A may show **Stopped** instead of **Paused** and lose “Resume” UX.

**Recommended fix:**

Align switch with toggle_pause:

```python
def _pause_current_for_switch(self):
    pm = self.parser_manager
    if pm is None:
        self.task_queue.clear_active()
        return
    active = self.task_queue.active_task
    if not active:
        return

    # 1. Mark PAUSED first (before any stop signal / task_ended)
    active.mark_paused()
    self._update_task_row(active.id)

    # 2. Save state
    try:
        if pm.loop and not pm.loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                pm.save_state(active.download_path), pm.loop
            )
            future.result(timeout=15)
    except Exception as e:
        self.log_handler.error(f"Error saving state on switch: {e}")

    # 3. Stop workers (task_ended will see PAUSED and not overwrite)
    pm.stop_parsing()

    # 4. Free active slot
    self.task_queue.clear_active()
```

And harden `on_task_ended`:

```python
def on_task_ended(self, reason: str):
    active = self.task_queue.active_task
    # Also: if parser_manager.task_id matches a PAUSED task in queue, never mark_stopped
    if active and active.status != TaskStatus.PAUSED:
        # existing mark_completed / mark_stopped / mark_failed
        ...
    else:
        # paused path: only update stats if useful
        if active and self.parser_manager:
            active.stats = dict(self.parser_manager.get_stats())
```

When `clear_active` already ran, `active` is None — status stays PAUSED. That is OK **if** mark_paused happened first.

**Why safe:** No change to natural completion (`reason == "completed"` still marks completed). Only protects intentional pause.

**Regression checklist:**  
- Natural complete → status Completed, auto-next.  
- Stop button → Stopped (must still call `mark_stopped` **before** or in `stop_parsing` path — already does).  
- Pause → Paused + Resume works.

---

### BUG-06 [P1] Incomplete single-thread downloads left on disk after Stop

**Where:**  
- Write path: `MediaDownloader._do_download` (single-thread writes final filepath directly)  
- Cleanup: `TaskQueueManager.cleanup_partial_files`

**Evidence:**

Multi-thread uses `.partN` and cleanup globs `*.part*`. Single-thread:

```python
with open(self.filepath, mode) as f:  # final name, not .partial
    for chunk in response_get.iter_content(...):
        if self.stop_event and self.stop_event.is_set():
            return {"success": False, "error": "Download manually aborted"}
            # file left half-written, non-zero size
```

```python
# cleanup_partial_files only:
# - **/*.part*
# - size == 0 files
# incomplete non-zero files remain
```

**Impact:** Corrupt “complete-looking” files in the download folder after Stop.

**Recommended fix (two layers, both small):**

**A. Downloader — write to temp, rename on success (preferred):**

```python
temp_path = self.filepath + ".partial"
# write to temp_path
# on success:
os.replace(temp_path, self.filepath)
# on abort / error:
try:
    os.remove(temp_path)
except OSError:
    pass
return {"success": False, "error": "Download manually aborted"}
```

**B. Cleanup — extend glob:**

```python
for p in glob.glob(os.path.join(dp, "**", "*.partial"), recursive=True):
    ...
for p in glob.glob(os.path.join(dp, "**", "*.part*"), recursive=True):
    ...
```

Do **A** first so Stop + cleanup agree. Do not delete all non-zero files (would destroy good downloads).

**Why safe:**  
- Successful downloads still end at the same final path.  
- Unique-name lock still applies to final path before download.  
- Performance: one rename (cheap).

**Regression checklist:**  
- Successful image download opens correctly.  
- Multi-thread still works (already uses parts).  
- Stop mid-download leaves no half file under final name.

---

### BUG-07 [P1] `stop_event` is `asyncio.Event` used from worker threads

**Where:**  
- Pass: `_downloader_worker` → `MediaDownloader(..., stop_event=self._stop_event)`  
- Check: `MediaDownloader._do_download` / `_download_chunk` in `run_in_executor` threads

**Evidence:**

```python
stop_event=self._stop_event  # asyncio.Event created in start_parsing
# later in thread:
if self.stop_event and self.stop_event.is_set():
```

`asyncio.Event` is not a threading primitive. `is_set()` often works as a plain flag read on CPython, but:

- Not documented as thread-safe for all operations.  
- Mixing models is fragile under load / future asyncio changes.

**Recommended fix:**

```python
# ParserManager.start_parsing
self._stop_event = asyncio.Event()
self._thread_stop_event = threading.Event()  # NEW

# stop_parsing:
self._thread_stop_event.set()
self.loop.call_soon_threadsafe(self._stop_event.set)
# ...

# start_parsing reset:
self._thread_stop_event = threading.Event()

# MediaDownloader:
stop_event=self._thread_stop_event
```

Keep `asyncio.Event` for workers on the loop; use `threading.Event` only for executor downloads.

**Why safe:** Same semantics, clearer concurrency model. No performance impact.

---

### BUG-08 [P1] Invalid Referer when policy is `origin`

**Where:** `src/parser/webpage_parser.py` → `_get_content`

**Evidence:**

```python
elif referrer_policy == "origin":
    request_specific_headers["Referer"] = get_domain(self.url)
# get_domain → "example.com"  (no scheme)
# Valid origin Referer should be "https://example.com"
```

Downloader does it correctly:

```python
headers["Referer"] = f"{parsed_source.scheme}://{parsed_source.netloc}"
```

**Impact:** Some CDNs/sites reject or ignore bad Referer → more empty pages / 403.

**Recommended fix:**

```python
elif referrer_policy == "origin":
    p = urlparse(self.url)
    request_specific_headers["Referer"] = f"{p.scheme}://{p.netloc}"
```

**Why safe:** Matches downloader behavior; standard Origin/Referer form.

---

### BUG-09 [P2] `mark_running()` wipes task stats on Resume

**Where:** `src/core/task_item.py` → `mark_running`

**Evidence:**

```python
def mark_running(self) -> None:
    self.status = TaskStatus.RUNNING
    self.started_at = datetime.now()
    self.completed_at = None
    self.error_message = None
    self.stats = {}   # ← wipe
```

Called from `TaskQueueManager.start_task` on every start, including Resume. Stats reappear only after timer copies from `ParserManager` (and after `load_state`). Brief UI zeroing; if load fails, stats gone forever in queue JSON.

**Recommended fix:**

```python
def mark_running(self, reset_stats: bool = False) -> None:
    self.status = TaskStatus.RUNNING
    if self.started_at is None:
        self.started_at = datetime.now()
    self.completed_at = None
    self.error_message = None
    if reset_stats:
        self.stats = {}
```

Call `mark_running(reset_stats=True)` only for brand-new runs if you want empty stats; for Resume keep existing stats until PM overwrites with loaded values.

**Why safe:** Display-only unless you depend on empty stats (you do not).

---

### BUG-10 [P2] UI “Page Limit” actually limits **downloaded files**

**Where:**  
- Label: Settings dialog “Page Limit”  
- Logic: `_parser_worker`

```python
if self.page_limit > 0 and self.stats["files_downloaded"] >= self.page_limit:
```

**Impact:** User expects N pages crawled; product stops after N successful downloads.

**Recommended fix (pick one, document it):**

| Option | Change |
|--------|--------|
| **A. Match UI (recommended if product intent is page cap)** | Compare `self.stats["pages_processed"] >= self.page_limit` |
| **B. Match code (recommended if intent is download cap)** | Rename UI label to “Download limit (files)” and setting key comment |

Do **not** change silently without UI rename — either way update tooltip.

Prefer **B** if current users rely on download caps; **A** if README promises page limits. README says page-oriented limits in places — verify product intent with owner. Safest default without product answer: **rename UI to match code** (no behavior change).

---

### BUG-11 [P2] Defaults diverge: `constants.py` vs `SettingsDialog.default_settings`

**Evidence (examples):**

| Setting | `constants.py` | `settings_dialog.py` defaults |
|---------|----------------|---------------------------------|
| `parser_threads` | 2 | 4 |
| `downloader_threads` | 4 | 8 |
| `page_timeout` | 60 (`DEFAULT_PAGE_TIMEOUT`) | 30 |
| `min_image_size` | 0 | 40 |

`DEFAULT_SETTINGS_VALUES` in constants and dialog defaults are not a single source of truth.

**Impact:** Tests/code paths using `K.DEFAULT_*` disagree with first-run GUI settings.

**Recommended fix:**  
Dialog builds defaults **only** from `K.DEFAULT_SETTINGS_VALUES` (+ any pure-UI keys like `log_to_file`). Delete duplicated literal dict values.

**Why safe:** One source of truth; pick the product-desired numbers once and put them in `constants.py`.

---

### BUG-12 [P2] Extension / HTTP thread mutates queue without GUI-thread affinity

**Where:** `MainWindow._start_extension_server` → `add_tasks_from_extension` runs in aiohttp server thread.

**Evidence:**

```python
task = self.task_queue.add_task(...)  # mutates list from non-GUI thread
task._pending_downloads = items
QTimer.singleShot(100, _do_update)   # only UI refresh marshaled
```

Qt objects + shared list without lock: race with GUI Start/Pause.

**Recommended fix:**

```python
# In HTTP callback — only marshal work to GUI:
def add_tasks_from_extension(...):
    payload = (urls, one_shot, user_agent, cookies)
    QMetaObject.invokeMethod(
        self, "_add_tasks_from_extension_slot",
        Qt.ConnectionType.QueuedConnection,
        # or QTimer.singleShot(0, lambda: self._apply_extension_tasks(...))
    )
```

Implement `_apply_extension_tasks` with the current body (all `task_queue` mutations on GUI thread). Return value to HTTP is trickier if callback must be sync — options:

1. Queue work and return `{"added": len(urls), "queued": true}` immediately (best effort).  
2. Use a `threading.Event` + result box filled on GUI thread (harder).

Prefer (1) for simplicity; extension already treats “added” loosely.

**Why safe:** Standard Qt pattern; avoids list corruption.

---

### BUG-13 [P2] `setup.py` install_requires is stale vs `requirements.txt`

**Evidence:** `setup.py` still lists `requests-html`, `html5lib`, `Pillow`; missing `aiohttp`, modern stack.

**Impact:** `pip install .` installs wrong deps.

**Recommended fix:** Point packaging at `requirements.txt` or sync lists; document “use requirements.txt”.

---

### BUG-14 [P2] Cookie sync to downloader session without domain

**Where:** `_invoke_parser`

```python
for name, value in cookies.items():
    self._shared_downloader_session.cookies.set(name, value)
```

`RequestsCookieJar.set` without domain/path may not attach cookies to subsequent host requests correctly.

**Recommended fix:**

```python
from urllib.parse import urlparse
domain = urlparse(url).hostname
for name, value in cookies.items():
    self._shared_downloader_session.cookies.set(name, value, domain=domain, path="/")
```

Test against an age-gated site that sets cookies on gateway bypass.

---

### BUG-15 [P3] SSL verification disabled on sync fallback / gateway bypass

**Where:** `webpage_parser.py` `_sync_fetch`, `_execute_bypass` — `verify=False`.

**Impact:** MITM risk on fallback path; also suppresses cert warnings noise.

**Recommendation:** Keep as optional setting `allow_insecure_ssl` default False; only disable verify when user enables it. Not a quick silent flip — sites with bad certs currently depend on this. Treat as product decision.

---

### BUG-16 [P3] All GIFs treated as trash

**Where:** `constants.TRASH_MEDIA_EXTENSIONS` includes `.gif`; `is_trash_media`.

**Impact:** Intentional for animated spam, but drops content GIFs.

**Recommendation:** Setting “download GIFs” default off; or size threshold. Product decision, not a free fix.

---

### BUG-17 [P3] `Analysis_Results.md` is outdated

Claims `normalize_url` missing in `webpage_parser` — **false today** (imported at lines 25–28, used in gateways). Do not “fix” a fixed issue.

---

### BUG-18 [P3] Tests do not cover P0 paths

**Current suite:** URL detection, patterns, downloader filters, shared session, JS processing, parser filtering. **12 passed, 1 skipped.**

**Missing coverage for:**

- `load_state` + `downloaded_files` interaction  
- Session path helpers  
- double `task_done`  
- `one_shot` flag plumbing from GUI  

**Recommended tests to add after fixes** (see §4).

---

## 3. Issues checked and **not** confirmed as bugs

| Claim | Verdict |
|-------|---------|
| `normalize_url` NameError in gateways | **Not present** — import exists |
| Double `task_done` on `url_queue` crashes | **No** — `PriorityURLQueue.task_done` is `pass` |
| `parsing_finished` never fires on natural complete | **Works** — sets `_completed_naturally` + emits |
| `task_ended` missing on stop | **Works** — always emitted in `_main_task.finally` |
| Pause uses soft `pause_parsing()` | **By design** Pause = save + hard stop + reload; soft pause API is unused but not a bug by itself |
| Message history unbounded | **Fixed** — `deque(maxlen=5000)` |

---

## 4. Suggested fix order for a junior developer

| Day | Work | Verify |
|-----|------|--------|
| 1 | **BUG-01** load_state re-queue | Pause mid-download → Resume → files continue |
| 1 | **BUG-04** single task_done | `pytest` + stop while downloading |
| 2 | **BUG-02** unified state path | Pause creates pickle; Stop deletes **same** file |
| 2 | **BUG-03** one_shot + task_id on direct Start | Page only Start does not crawl links |
| 3 | **BUG-05** pause order | Switch tasks → first stays Paused |
| 3 | **BUG-06** `.partial` writes | Stop leaves no truncated final files |
| 4 | **BUG-07, 08, 09, 14** | Smoke + one gateway site |
| 5 | **BUG-10–13** + tests | Full `pytest` + packaging note |

Do **not** refactor ParserManager architecture while fixing these — surgical patches only.

---

## 5. Concrete patch sketches (copy orientation)

### 5.1 BUG-01 only (minimal)

**File:** `src/parser/parser_manager.py`  
**Function:** `load_state`  
**Find:**

```python
skipped_downloads = 0
for item in state.get("download_queue_items", []):
    if item.get("url") not in self.downloaded_files:
        await self.download_queue.put(item)
    else:
        skipped_downloads += 1
if skipped_downloads:
    logger.info(f"load_state: skipped {skipped_downloads} already-downloaded items from download_queue")
```

**Replace with:**

```python
restored = 0
for item in state.get("download_queue_items", []):
    await self.download_queue.put(item)
    restored += 1
if restored:
    logger.info(f"load_state: restored {restored} pending download items")
```

### 5.2 BUG-04 only (minimal)

**File:** `src/parser/parser_manager.py`  
**Function:** `_downloader_worker`  
**Find:**

```python
if not self.is_running:
    self.download_queue.task_done(); break
```

**Replace with:**

```python
if not self.is_running:
    break
```

### 5.3 Unit tests to add (after fixes)

```python
# tests/test_load_state_downloads.py  (new)
import asyncio
import pickle
import os

def test_load_state_restores_pending_even_if_in_downloaded_files(tmp_path):
    """BUG-01: pending download_queue items must be restored."""
    # Build a fake state pickle where url is in both downloaded_files and download_queue_items
    # Call ParserManager.load_state
    # Assert download_queue.qsize() == 1
    ...

def test_task_done_not_double_on_stop_flag():
    """BUG-04: one get → one task_done."""
    ...
```

```python
# tests/test_session_paths.py
def test_save_and_stop_delete_same_path(tmp_path):
    """BUG-02"""
    ...
```

---

## 6. Performance notes for proposed fixes

| Fix | Perf impact |
|-----|-------------|
| BUG-01 restore all pending | Neutral / slightly more downloads (correct ones) |
| BUG-02 path unify | None |
| BUG-03 one_shot | Fewer requests when Page only works — intended |
| BUG-04 task_done | Avoids worker death — improves reliability |
| BUG-06 temp file | One extra rename; negligible |
| BUG-07 threading.Event | Negligible |
| BUG-12 marshal to GUI | Slight latency on extension add; safer |

None of the P0/P1 fixes require new threads, larger locks, or O(n²) scans.

---

## 7. Functionality that must remain after fixes

- Deep crawl with stay-in-domain + stop words + blocklist  
- Imagus / site patterns thumbnail → fullsize  
- Extension one-shot with `pending_downloads`  
- Multi-thread download when `Accept-Ranges: bytes`  
- Quarantine + interstitial HTML retry  
- CSV import/export  
- Auto-start next task on natural completion only  
- Portable paths via `app_paths` for frozen exe  

---

## 8. Algorithm hotspots (where future bugs usually hide)

| File | Role | Risk |
|------|------|------|
| `parser_manager.py` | Orchestration | Highest — queues, stop, state |
| `main_window.py` | Lifecycle UI | Pause/Stop/switch races |
| `media_downloader.py` | Bytes on disk | Partial files, uniqueness |
| `priority_url_queue.py` | Crawl order | Zero priority drops links |
| `webpage_parser.py` | HTML extract | Gateways, fallback TLS |
| `task_queue_manager.py` | Persistence | Path contracts |
| `http_server.py` + extension | IPC | Thread affinity |

---

## 9. Summary table

| ID | Sev | Title | Primary file |
|----|-----|-------|--------------|
| BUG-01 | P0 | Resume drops pending downloads | `parser_manager.py` |
| BUG-02 | P0 | Session path mismatch Stop vs save | `task_queue_manager.py` / `parser_manager.py` |
| BUG-03 | P0 | Page only ignored on direct Start | `main_window.py` |
| BUG-04 | P1 | Double `task_done` on download queue | `parser_manager.py` |
| BUG-05 | P1 | Pause/switch race → STOPPED | `main_window.py` |
| BUG-06 | P1 | Truncated files after Stop | `media_downloader.py` |
| BUG-07 | P1 | asyncio.Event in download threads | `parser_manager.py` |
| BUG-08 | P1 | Bad origin Referer | `webpage_parser.py` |
| BUG-09 | P2 | Resume clears stats | `task_item.py` |
| BUG-10 | P2 | Page limit = file limit | settings + parser |
| BUG-11 | P2 | Divergent defaults | constants vs dialog |
| BUG-12 | P2 | Extension mutates queue off GUI thread | `main_window.py` |
| BUG-13 | P2 | Stale setup.py | `setup.py` |
| BUG-14 | P2 | Cookies without domain | `parser_manager.py` |
| BUG-15–18 | P3 | SSL, GIFs, stale notes, test gaps | various |

---

## 10. Sign-off

- Code was traced for the full task lifecycle and download pipeline.  
- Path mismatch and double `task_done` were **runtime-verified**.  
- Unit tests currently pass but **do not cover P0 bugs** — green CI is not sufficient.  
- Proposed fixes are surgical and preserve architecture invariants in §1.1.  
- No production code was modified in this audit.

**Next step for implementer:** Start with BUG-01 and BUG-04 (same file, high value, low risk), then BUG-02 and BUG-03.
