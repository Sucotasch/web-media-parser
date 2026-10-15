# Web Media Parser — Agent Guide

Desktop app (PySide6) that crawls web pages, detects media (images/video/audio/streams), upgrades thumbnails to full-size URLs (built-in patterns + Imagus Sieve), and downloads files. A Chrome MV3 extension (`extension/`) sends pages/URLs to the app over localhost HTTP and re-implements much of the media logic in JS.

**Language:** Python 3.8+ (dev env is 3.12). **Primary OS:** Windows. **Docs/UI language:** Russian (README/CONTEXT); **code and identifiers:** English.

---

## Quick start

```bash
.\venv\Scripts\activate            # Windows
pip install -r requirements.txt
pip install pytest                 # NOT in requirements.txt
python main.py                     # GUI app
python -m pytest tests -q          # full suite
```

| Command | Purpose |
|---------|---------|
| `python main.py` | Run GUI app |
| `python -m pytest tests -q` | Unit tests (~25 s) |
| `python build_exe.py` | PyInstaller onedir → `dist/WebMediaParser/` |
| Load unpacked `extension/` in `chrome://extensions` | Chrome/Firefox extension (MV3) |

Extension API: `http://127.0.0.1:19876` (`GET /api/status`, `GET /api/queue`, `POST /api/tasks`).

---

## Testing (the only automated gate)

There is **no CI workflow, no `pyproject.toml`/`setup.cfg`/`.flake8`/`.editorconfig`, no typecheck**. Current tree (2026-10-15): **492 passed / 30 skipped**, plus **15/15** Node suites in `extension/tests/*.test.mjs`. Lint is ruff from the `.[lint]` extra: `pip install -e .[lint]` then `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401,F401` (expect exactly the three intentional `webpage_parser.py` E402 lines).

- The 30 skips are Deno-dependent (`test_js_engine.py`, `test_gateway_bypass.py`) — they auto-skip when no Deno binary is resolvable. Skipping is the expected green state; do not "fix" the skips. Verify with `python -m pytest tests -q -rs`.
- Deno resolution order (`src/parser/js_engine/engine.py: find_deno_bin`): `WEB_MEDIA_PARSER_DENO`/`DENO_BIN` env → `deno` PyPI package → `bin/deno.exe` next to the frozen exe → media-downloader's bundled `deno.exe` → `PATH`.
- `tests/conftest.py` puts the project root and `tests/` on `sys.path`, so pytest works from any cwd. Imports are `src.*` and `helpers`.
- Sieve-dependent tests pin `Imagus_sieve_2026.07.15_823.json` through `tests/helpers.py:SIEVE_PATH`. SitePatternManager resolves duplicate rule names "newest file wins", so repointing tests at another snapshot breaks unrelated tests.
- GUI flows need manual verification (`python main.py`).

### Extension JS side

`extension/*.js` are plain scripts — no bundler, no linter. Gate is `node`:

```bash
node --check extension/background.js extension/sieve_updater.js extension/content_script.js
node extension/tests/url_transform.test.mjs   # prints "ALL PASS" on success
```

**Desktop ↔ extension parity.** Media logic exists twice: `applyUrlTransform` (JS) ↔ `site_pattern_manager.apply_link_url_transform` (Python), `discoverFullsize`/`upgradeMedia` (JS) ↔ `SitePatternManager.transform_image_url` (Python); ditto the junk filter and sieve merge. A change to fullsize-upgrade/sieve/junk behavior must land **on both sides** and be covered in **both** suites (`tests/` and `extension/tests/`). Missing this is the most common audit finding in `Audit/`.

Lint is ad-hoc only: `python -m ruff check src/ tests/ --select E722,E402,E741,E731` (see `GATES.md`). No project config — don't add one unprompted.

---

## Repository map

```
main.py                 # Entry: lxml/brotli patches → QApplication → MainWindow
src/
  app_paths.py          # Portable paths (dev vs frozen); task_state_path() is canonical
  constants.py          # All defaults / setting keys / behavior constants (K.*)
  fix_lxml.py, fix_brotli.py
  core/                 # Task queue model
    task_item.py        # TaskItem, TaskStatus
    task_queue_manager.py   # CRUD + persistence; add_task snapshots settings
  parser/               # Crawl + extract media
    parser_manager.py   # One instance per task; asyncio workers in QThread
    webpage_parser.py   # HTML, lazy-load, gateways, JS redirects
    priority_url_queue.py   # URL scoring; DEFAULT_MAX_LINKS_PER_PAGE cap
    site_pattern_manager.py# Site patterns + Imagus Sieve
    pattern_manager.py  # DEPRECATED alias → SitePatternManager (do not use)
    http_engine.py      # requests vs curl_cffi (browser TLS impersonation); auto-escalates
    js_engine/          # Opt-in Deno workers: sieve sandbox, happy-dom, consent/age gate
    junk_filter.py      # Drops preview/junk media from download queue
    json_parser.py, shared_session.py, utils.py
  downloader/
    media_downloader.py # Single/multi-thread downloads, rate limiting
  gui/
    main_window.py      # Queue UI, start/pause/stop, extension hooks
    settings_dialog.py, log_handler.py
  server/
    http_server.py      # ExtensionServer (aiohttp)
extension/              # Chrome MV3 (background, content script, sieve, popup, tests/)
resources/              # dark_theme.qss, domain_blocklist.txt, junk_allowlist.txt, patterns/
tests/                  # pytest unit tests
Audit/                  # Dated audit/fixplan notes + raw pytest & probe evidence
.ext-dev-loop/          # GITIGNORED agent scratch: browser profiles, fixtures, e2e runs
```

Generated / never commit as product code: `venv/`, `build/`, `dist/`, `release/`, `downloads/`, `__pycache__/`, `settings.json`, `task_queue.json`, `sessions/`, `*.pkl`, `*.log`, `WebMediaParser_v*.zip`, `src/parser/js_engine/deno_cache/`.

---

## Architecture (essentials)

### Task lifecycle

1. User (or extension) adds a task → `TaskQueueManager.add_task` **snapshots settings** and `download_path`.
2. `MainWindow` creates a **new** `ParserManager` + **new** `QThread` per launch.
3. `ParserManager` owns an asyncio loop, parser/downloader workers, download/quarantine queues, and the priority URL queue.
4. Completion always emits `task_ended(reason)` with `"completed" | "stopped" | "failed"`; the queue auto-starts the next task **below** the finished one (tasks placed above keep their state).
5. Queue persists to `task_queue.json` next to the exe (`app_paths.queue_path`). Per-task resume state: `{task_download_path}/sessions/{task_id}/last_session.pkl`, written **only** via `app_paths.task_state_path()` (`clear_task_sessions` deletes those dirs).

### Pause vs Stop

| Action | Active task | Partial files | State pickle |
|--------|-------------|---------------|--------------|
| **Pause** | Soft stop; resume later | Keep | Save |
| **Stop** | Hard stop; queues cleared | Delete `*.part*` | Delete |
| **Close window** | Pause + save queue | Keep | Save |

### Hard constraints (do not break)

- **One `ParserManager` instance = one task.** Never reuse after stop — build a fresh instance.
- **New `QThread` per task.** Never restart a thread that hit `quit()`/`wait()`.
- **Asyncio primitives** (`Event`, `Lock`, queues) must be created **inside** the event-loop thread, not the GUI thread.
- **Cross-thread stop:** `threading.Event` (`_thread_stop_event`) for download threads + progress monitor; `asyncio.Event` for loop-side workers. Both are set in `stop_parsing()`.
- Shared downloader `requests.Session` uses `Retry(total=0)` (`create_shared_downloader_session`) so Stop aborts promptly. The per-download retry count comes from settings instead.
- `DOMAIN_CONCURRENCY_LIMIT = 2` (parser + downloader combined) and `DEFAULT_MAX_LINKS_PER_PAGE = 200` (priority-sorted, top N kept).
- Portable paths go through `src/app_paths.py` — no hardcoded absolute install paths.
- Blocking work goes to its **own executor pool** — `_js_executor` (2 workers) for JS rules, `_dl_executor` for downloads, `_html_executor` for BeautifulSoup/sync fetches — and never inline in a coroutine (measured loop stall 5.02 s → 0.03 s). Created in the loop thread, shut down in `_main_task`'s `finally`.
- **Deno workers are disposable**: recycled every `K.JS_WORKER_MAX_REQUESTS` (200) calls, because `happyDOM.close()` does not actually release memory.
- Defaults and setting keys live in `src/constants.py`; consumers import `K.*` — single source of truth. Recent keys: `SETTING_MAX_FILE_SIZE`/`DEFAULT_MAX_FILE_SIZE` (KB, 0 = unlimited in code; 2 GiB in the GUI defaults), `SIEVE_LOOP_MAX_HOPS = 5`, `JS_WORKER_MAX_REQUESTS = 200`, `SETTING_VERIFY_TLS` (default off; downloader sessions only — the escalation GET stays verified).
- JS engine on Windows: `Popen` calls use `CREATE_NO_WINDOW`, otherwise a console window pops up per Deno spawn for the whole task.

---

## Coding conventions

Follow the project's Karpathy discipline — the tracked source is `.agents/skills/karpathy/SKILL.md` (an older `QWEN.md` was deleted; don't go looking for it):

1. **Think before coding** — surface assumptions and tradeoffs; ask when ambiguous.
2. **Simplicity first** — no speculative features or unused flexibility.
3. **Surgical changes** — match existing style; no drive-by refactors.
4. **Goal-driven** — prefer a failing test or a concrete verify step.

### Style

- Modules: shebang + `# -*- coding: utf-8 -*-` + module docstring where existing files do.
- Type hints on new public APIs; match the surrounding density.
- Logging via `logging.getLogger(__name__)`; GUI log via `GUILogHandler`.
- PySide6 signals/slots; heavy work never on the GUI thread.
- Constants via `from src import constants as K` — no scattered magic numbers.

### What not to do

- Do not revive `pattern_manager.py` as the primary API — use `SitePatternManager`.
- Do not "fix" unrelated dead code or reformat whole files while solving a narrow bug.
- Do not add exploit tooling or scrape-bypass features beyond existing legitimate client behavior.
- Do not commit secrets, personal `settings.json`, or `dist/`/`build/`/`release/` artifacts.

---

## Deeper docs

| File | Use when |
|------|----------|
| `README.md` / `README_EN.md` | User-facing features, install, extension usage |
| `CONTEXT.md`, `knowledge.md` | As-built architecture notes; **trust the code when they disagree** (E-06 2026-10-15 fixed the drifted parts: test counts, `state.pkl` → `last_session.pkl`, the deleted `QWEN.md`, the "stale setup.py" claim — deps mirror `requirements.txt` exactly) |
| `Audit/`, `Audit.md` | Dated audits + fixplans; `GATES.md` holds per-gate CHECK/EXPECT commands (its pass-count baselines are stale — use the current pytest numbers above) |
| `PLAN.md` | Phase plan for crawl/parity work |
| `docs/DENO_JS_ENGINE_DESIGN.md` | Design + P0–P4 status log of the Deno/HTTP-engine work |
| `docs/DEV_GUIDE_MEDIA_CRAWL_IMPROVEMENTS.md` | Historical work plan for crawl/junk/fullsize improvements (mostly implemented) |

Note: `setup.py` mirrors `requirements.txt` and is the canonical dependency list for `pip install .`; `requirements.txt` remains the truth for the app and `build_exe.py`. Build/release artifacts also land in `release/`.

---

## Common change recipes

| Goal | Start here |
|------|------------|
| New default / setting | `src/constants.py` → `settings_dialog.py` → consumers in `parser_manager` / downloader |
| URL priority / crawl order | `src/parser/priority_url_queue.py` |
| HTML extraction / gateways | `src/parser/webpage_parser.py` (`_extract_images`, `_extract_videos`, `_extract_jsonld_media`) |
| Thumbnail → fullsize rules | `site_pattern_manager.py` + `resources/patterns/site_patterns.json` / root Imagus sieve JSON — **and the extension JS twin + `extension/tests/`** |
| Sieve transforms | `site_pattern_manager.py` — `transform_image_url`, `apply_link_url_transform` |
| Download reliability | `src/downloader/media_downloader.py` |
| Queue UI / auto-next | `src/gui/main_window.py`, `src/core/task_queue_manager.py` |
| Extension protocol | `src/server/http_server.py` + `extension/` |
| Portable paths / packaging | `src/app_paths.py`, `build_exe.py` (also bundles `bin/deno.exe` and `deno_cache/npm`) |
| Crawl filtering (stop words, URL skip) | `src/parser/utils.py` — `should_skip_crawl_url` (segment-aware) |
| HTTP engine / TLS impersonation | `src/parser/http_engine.py` (requests ↔ curl_cffi escalation) |
| JS gateway (Deno, consent/age bypass) | `src/parser/js_engine/engine.py` + `gateway_worker.js` / `dom_worker.js` |
| Junk/preview media filtering | `src/parser/junk_filter.py` + `resources/junk_allowlist.txt` |

## Extension e2e (needs a real browser)

`.ext-dev-loop/` (gitignored) holds browser profiles, fixtures and e2e artifacts used by the `ext-dev-loop` workflow, which drives the unpacked extension through a real Chrome/Firefox profile via a probe script. Reuse whatever profiles/fixtures exist there before creating new ones.
