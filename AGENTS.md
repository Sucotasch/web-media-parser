# Web Media Parser — Agent Guide

Desktop app (PySide6) that crawls web pages, detects media (images/video/audio/streams), upgrades thumbnails to full-size URLs (built-in patterns + Imagus Sieve), and downloads files. Optional Chrome extension talks to the app over localhost HTTP.

**Language:** Python 3.8+ (dev env is 3.12). **Primary OS:** Windows. **UI language in docs:** Russian (README/CONTEXT); **code and identifiers:** English.

---

## Quick start

```bash
# From project root (prefer project venv)
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
pip install pytest               # tests are not in requirements.txt
python main.py
```

| Command | Purpose |
|---------|---------|
| `python main.py` | Run GUI app |
| `python -m pytest tests -q` | Unit tests |
| `python build_exe.py` | PyInstaller onedir → `dist/WebMediaParser/` |
| Load unpacked `extension/` | Chrome extension (MV3) |

Extension API: `http://127.0.0.1:19876` (`GET /api/status`, `GET /api/queue`, `POST /api/tasks`).

---

## Repository map

```
main.py                 # Entry: lxml/brotli patches → QApplication → MainWindow
src/
  app_paths.py          # Portable paths (dev vs frozen PyInstaller)
  constants.py          # All defaults / setting keys / behavior constants (K.*)
  fix_lxml.py, fix_brotli.py
  core/                 # Task queue model
    task_item.py        # TaskItem, TaskStatus
    task_queue_manager.py
  parser/               # Crawl + extract media
    parser_manager.py   # One instance per task; asyncio workers in QThread
    webpage_parser.py   # HTML, lazy-load, gateways, JS redirects
    priority_url_queue.py
    site_pattern_manager.py  # Site patterns + Imagus Sieve
    pattern_manager.py  # DEPRECATED → use SitePatternManager
    json_parser.py, shared_session.py, utils.py
  downloader/
    media_downloader.py # Single- and multi-thread downloads
  gui/
    main_window.py      # Queue UI, start/pause/stop, extension hooks
    settings_dialog.py, log_handler.py
  server/
    http_server.py      # ExtensionServer (aiohttp)
extension/              # Chrome MV3 (content script, sieve, popup)
resources/              # dark_theme.qss, domain_blocklist, site_patterns.json
tests/                  # pytest unit tests
CONTEXT.md              # Deep architecture notes (may lag code slightly)
```

Ignore/generated (do not commit as product code): `venv/`, `build/`, `dist/`, `downloads/`, `__pycache__/`, `settings.json`, `task_queue.json`, session state under `sessions/`.

---

## Architecture (essentials)

### Task lifecycle

1. User (or extension) adds a task → `TaskQueueManager.add_task` **snapshots settings** and `download_path`.
2. `MainWindow` creates a **new** `ParserManager` + **new** `QThread` per launch (`QThread` must not be restarted after `quit()`/`wait()`).
3. `ParserManager` owns an asyncio loop, parser/downloader workers, download/quarantine queues, and priority URL queue.
4. Completion always emits `task_ended(reason)` with `"completed" | "stopped" | "failed"`.
5. Queue auto-starts the next queued task on natural completion.
6. Persist queue: `task_queue.json` next to app (via `app_paths.queue_path` / download dir usage in GUI). Per-task resume state: `sessions/{task_id}/state.pkl`.

### Pause vs Stop

| Action | Active task | Partial files | State pickle |
|--------|-------------|---------------|--------------|
| **Pause** | Soft stop; resume later | Keep | Save |
| **Stop** | Hard stop; queues cleared | Delete `*.part*` | Delete |
| **Close window** | Pause + save queue | Keep | Save |

### Hard constraints (do not break)

- **One `ParserManager` instance = one task.** Do not reuse after stop without a fresh instance.
- **New `QThread` per task.** Never restart a finished thread.
- **Asyncio primitives** (`Event`, `Lock`, queues) must be created **inside** the event-loop thread, not the GUI thread.
- **Cross-thread stop:** `threading.Event` (`_thread_stop_event`) for download threads and progress monitor; `asyncio.Event` for loop-side workers. Both set in `stop_parsing()`.
- **Downloader shared `requests.Session`** uses retry total `0` so Stop can abort promptly (`create_shared_downloader_session`).
- **Domain concurrency** capped (`DOMAIN_CONCURRENCY_LIMIT = 2` parser+downloader combined).
- **Max links per page** capped (`DEFAULT_MAX_LINKS_PER_PAGE = 200`) — sort by priority, keep top N.
- **Session path** unified via `app_paths.task_state_path()` — save/load/delete all use same formula.
- Paths for portable builds go through `src/app_paths.py` — no hardcoded absolute install paths.
- Defaults and setting keys live in `src/constants.py`; GUI reads from `K.*` (single source of truth).

---

## Coding conventions

Follow the project’s Karpathy-style discipline (also in `QWEN.md`, `.agents/skills/karpathy/`):

1. **Think before coding** — surface assumptions and tradeoffs; ask when ambiguous.
2. **Simplicity first** — no speculative features, abstractions, or “flexibility” not requested.
3. **Surgical changes** — touch only what the task requires; match existing style; do not drive-by refactor.
4. **Goal-driven** — prefer a failing test or clear verify step before/after behavior changes.

### Style

- Modules: shebang + `# -*- coding: utf-8 -*-` + module docstring where existing files do.
- Prefer type hints on new public APIs; match surrounding code density.
- Logging via `logging.getLogger(__name__)`; GUI log via `GUILogHandler`.
- Qt: PySide6 signals/slots; heavy work stays off the GUI thread.
- Constants: import `from src import constants as K` (or equivalent) rather than scattering magic numbers.

### What not to do

- Do not revive `pattern_manager.py` as the primary API — use `SitePatternManager`.
- Do not “fix” unrelated dead code or reformat whole files while solving a narrow bug.
- Do not add exploit tooling or scrape-bypass features beyond existing legitimate client behavior.
- Do not commit secrets, personal `settings.json`, or large `dist/`/`build/` artifacts.

---

## Testing

```bash
pip install pytest   # if missing from venv
python -m pytest tests -q
```

Current test modules:

- `test_url_detection.py`, `test_js_processing.py`
- `test_pattern_manager.py`, `test_parser_manager_filtering.py`
- `test_media_downloader.py`, `test_shared_session.py`

For behavior changes in parser/downloader/queue: add or extend unit tests. GUI flows may need manual verification (`python main.py`).

---

## Dependencies

Canonical list: `requirements.txt` (PySide6, aiohttp, requests, bs4, lxml, filetype, brotli, …).

Note: `setup.py` install_requires is **stale** relative to `requirements.txt` — prefer `requirements.txt` for installs and packaging truth.

---

## Deeper docs

| File | Use when |
|------|----------|
| `README.md` | User-facing features, install, extension usage |
| `CONTEXT.md` | Historical design of task queue, ParserManager lifecycle, known pitfalls |
| `Audit/` | External design/audit write-ups |
| `Analysis_Results.md` | Ad-hoc bug notes during analysis |

If `CONTEXT.md` conflicts with code, **trust the code** and update CONTEXT only when asked.

---

## Common change recipes

| Goal | Start here |
|------|------------|
| New default / setting | `src/constants.py` → `settings_dialog.py` → consumers in `parser_manager` / downloader |
| URL priority / crawl order | `src/parser/priority_url_queue.py` |
| HTML extraction / gateways | `src/parser/webpage_parser.py` |
| Thumbnail → fullsize rules | `site_pattern_manager.py` + `resources/patterns/site_patterns.json` / Imagus sieve JSON |
| Download reliability | `src/downloader/media_downloader.py` |
| Queue UI / auto-next | `src/gui/main_window.py`, `src/core/task_queue_manager.py` |
| Extension protocol | `src/server/http_server.py` + `extension/` |
| Portable paths / packaging | `src/app_paths.py`, `build_exe.py` |
| Media extraction (images/video/JSON-LD) | `webpage_parser.py` — `_extract_images`, `_extract_videos`, `_extract_jsonld_media` |
| Crawl filtering (stop words, URL skip) | `src/parser/utils.py` — `should_skip_crawl_url` (segment-aware) |
| Sieve transforms (thumb→fullsize) | `site_pattern_manager.py` — `transform_image_url` |
