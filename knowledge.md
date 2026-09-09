# Project knowledge

This file gives Freebuff context about your project: goals, commands, conventions, and gotchas.

## Quickstart
- **What:** Desktop app (PySide6) that crawls web pages, detects media (images/video/audio/HLS/DASH), upgrades thumbnails to full-size URLs (built-in patterns + Imagus Sieve), and downloads files. Chrome MV3 extension (`extension/`) talks to the app over localhost HTTP. Python 3.8+ (dev env 3.12), primary OS Windows. Docs in Russian; code/identifiers in English.
- **Setup:** `pip install -r requirements.txt` (project venv at `venv/` on Windows). `pytest` is NOT in requirements.txt — install separately.
- **Run:** `python main.py` (GUI). Entry point applies lxml/brotli patches then starts `MainWindow`.
- **Test:** `python -m pytest tests -q` (21 modules under `tests/`, 265 passed / 30 skipped incl. `test_js_engine.py`, `test_junk_filter.py`). Deno-dependent tests auto-skip when the Deno binary isn't on PATH; curl_cffi must be installed for the P3 escalation tests. Shared helpers live in `tests/helpers.py` + `tests/conftest.py`.
- **Lint:** None configured — `python -m pyflakes src/ tests/` used ad-hoc; no pyproject.toml/setup.cfg/.flake8/.editorconfig. Match surrounding style by hand.
- **Build:** `python build_exe.py` → PyInstaller onedir → `dist/WebMediaParser/` with `.exe` and deps. Release = two zips: app `WebMediaParser_v<ver>.zip` + extension `WebMediaParser_extension_v<ver>.zip`.
- **Extension:** Load unpacked `extension/` via `chrome://extensions` (Developer mode). API: `http://127.0.0.1:19876` (`GET /api/status`, `GET /api/queue`, `POST /api/tasks`).

## Architecture
- **Key directories:**
  - `src/core/` — task queue model: `task_item.py` (TaskItem, TaskStatus), `task_queue_manager.py` (CRUD, persistence to `task_queue.json`).
  - `src/parser/` — `parser_manager.py` (coordinator, one instance per task, asyncio workers in a QThread), `webpage_parser.py` (HTML, lazy-load, gateways, JS redirects; `_extract_images/_extract_videos/_extract_jsonld_media`), `priority_url_queue.py` (URL scoring), `site_pattern_manager.py` (patterns + Imagus Sieve, `transform_image_url`), `pattern_manager.py` (**DEPRECATED** — use SitePatternManager), `json_parser.py`, `shared_session.py`, `utils.py` (`should_skip_crawl_url`), `junk_filter.py` (P2-lite: ad/tracker/forum-chrome URL classifier, allowlist `junk_allowlist.txt`, precision-first), `js_engine/` (P0+P1: `engine.py` DenoJsEngine, `worker.js` sieve-JS sandbox, `dom_worker.js` happy-dom DOM worker, offline npm cache `deno_cache/` — gitignored, bundled at build).
  - `src/downloader/media_downloader.py` — single- and multi-thread downloads, rate limiting.
  - `src/gui/` — `main_window.py` (queue UI, start/pause/stop), `settings_dialog.py`, `log_handler.py`.
  - `src/server/http_server.py` — extension bridge.
  - `src/app_paths.py` — portable path resolution (dev vs frozen PyInstaller). `src/constants.py` — all defaults/setting keys (`K.*`, single source of truth).
  - `resources/` — `dark_theme.qss`, `domain_blocklist.txt`, `patterns/site_patterns.json`. `extension/` — Chrome MV3 (content script, sieve rules, popup).
- **Data flow:** User or extension adds task → `TaskQueueManager.add_task` **snapshots settings + download_path** → MainWindow creates a **new** `ParserManager` + **new** `QThread` per launch → ParserManager runs asyncio loop with parser/downloader workers + URL priority queue → completion emits `task_ended(task_id, reason)` (`"completed"|"stopped"|"failed"`) → queue auto-starts the next task **below** the finished one (tasks placed above keep their state). Per-task resume state in `sessions/{task_id}/state.pkl` (pickle). Settings dialog has 5 tabs (Parsing, Filters, Performance, HTTP, Logging).

## JS engine (P0+P1, Deno)
- Setting `SETTING_JS_ENGINE` (`js_engine`): `static` (default, unchanged behavior) | `deno` (Settings → JS Engine → Deno). `js_engine=None` means rules keep old static path.
- P0: `to`-rules that can't be JS→Python converted run in the dependency-free `worker.js` (location/URL shims, `$[n]` groups, `#ext#` variants). P1: `url`/`res` rules run in `dom_worker.js` (happy-dom, `enableJavaScriptEvaluation=false`, NO `--allow-*` flags); `$._` carries raw fetched page text (Imagus convention), nested arrays are flattened, `#` fullsize marker stripped. DOM mode needs the bundled `deno_cache/npm` (populated at build time; `deno vendor` was removed in Deno 2).
- Sandbox: workers get no permissions; console.* routed to stderr (stdout is the JSON protocol). Fail-open: any engine error → `None` → static path. Timeout 5s/call (covers cold Deno start; was 2s), workers killed on `shutdown()` (kill + bounded wait).
- Windows: both `Popen`s use `CREATE_NO_WINDOW` (`_popen_kwargs()` in `engine.py`) — otherwise deno.exe console windows pop up for the whole task.
- Build: `build_exe.py` bundles `bin/deno.exe` (~121 MB), `bin/worker.js`, `bin/dom_worker.js`, `bin/deno_cache/npm` (~13 MB). `resources/junk_allowlist.txt` is copied next to the exe.
- P2-lite (`filter_junk`, default on): drop `junk_allowlist.txt` next to the exe to whitelist domains (one per line, `#` comments). `SETTING_FILTER_JUNK=False` keeps behavior byte-identical.

## Conventions
- **Hard constraints (do not break):** One ParserManager = one task (never reuse after stop). New QThread per task (never restart after `quit().wait()`). Asyncio primitives (Event/Lock/queues) must be created **inside** the event-loop thread, not the GUI thread. Downloader shared `requests.Session` uses retry total `0` so Stop aborts promptly. Domain concurrency capped at 2 (parser+downloader combined). Session path only via `app_paths.task_state_path()`. No hardcoded absolute install paths.
- **Pause vs Stop:** Pause = soft stop, keep `*.part*` + state.pkl (resume later). Stop = hard stop, delete `*.part*` + state.pkl. Close window = pause + save queue.
- **Style:** Shebang + `# -*- coding: utf-8 -*-` + module docstring where existing files do. Type hints on new public APIs. Logging via `logging.getLogger(__name__)` + GUI via `GUILogHandler`. Qt heavy work stays off the GUI thread. Constants from `src.constants` (`K.*`), no magic numbers. Karpathy discipline: think before coding, simplicity first, surgical changes only.
- **Things to avoid:** Don't revive `pattern_manager.py` as primary API. Don't refactor/reformat whole files while fixing a narrow bug. Don't add scrape-bypass/exploit features beyond existing legitimate behavior. Don't commit secrets, personal `settings.json`, `venv/`, `build/`, `dist/`, `downloads/`, `__pycache__/`, `sessions/`.
- **Gotchas:** `setup.py` install_requires is **stale** — trust `requirements.txt`. `CONTEXT.md` has deep historical design notes but may lag the code — trust the code. Known open issue: Resume may re-download files (downloaded_files filtering). Shared knowledge in `AGENTS.md` (agent guide), `README.md` (user features), `QWEN.md` + `.agents/skills/karpathy/` (dev discipline).
