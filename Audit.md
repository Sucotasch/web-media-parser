# Engineering Audit — Web Media Parser

**Date:** 2026-08-07
**Scope:** Full repository review (Python desktop app + Chrome MV3 extension + build/config).
**Method:** Read every source module, extension script and test; ran validations (tests, byte-compile, pyflakes, ruff); verified suspected bugs by reading surrounding code.
**Result:** 2 confirmed runtime bugs, 5 high-risk robustness/security issues, ~20 medium/low issues, test gaps, and dependency/build drift. **No code was modified** — fixes below are ready-to-use.

---

## 0. Validation runs (what was executed)

| Check | Command | Result |
|---|---|---|
| Tests | `python -m pytest tests -q` | **12 passed, 1 skipped, 4 warnings** |
| Byte-compile | `python -m compileall -q src main.py build_exe.py setup.py backup.py` | OK |
| Static | `python -m pyflakes src main.py` | 46 findings (unused imports, 2 real bugs) |
| Static | `python -m ruff check src main.py` | ~180 findings (mostly F401/E701/E702 style, 2 F821 bugs) |
| Env | Python 3.12.10; pyflakes/flake8/ruff installed; mypy/pyright/node NOT available | — |

Tooling config: **no linter/formatter config file exists** (no `pyproject.toml`, `.flake8`, `setup.cfg`, `.editorconfig`). Recommend adding a minimal `pyproject.toml`/`ruff.toml` so style is consistent (see §6.3).

---

## 1. Confirmed bugs (fix these first)

### BUG-1 — `NameError: name 'logger' is not defined` when the extension server thread fails
- **File:** `src/gui/main_window.py`, in `_start_extension_server()` → nested `run_server()`, `except` block (`logger.error(...)`).
- **Evidence:** `import logging` exists at top, but there is **no** module-level `logger = logging.getLogger(__name__)`. pyflakes & ruff both flag: `main_window.py:1212:17: F821 Undefined name 'logger'`.
- **Impact:** If the aiohttp server thread raises on startup/shutdown, the except handler itself raises `NameError`, masking the real error. Low probability but pure bug.
- **Fix:**
```python
# src/gui/main_window.py — top of file, after imports
logger = logging.getLogger(__name__)
```

### BUG-2 — Extension discards every PNG image
- **File:** `extension/content_script.js`, `scanPageMedia()`, `JUNK_PATTERNS`.
```js
/\\.(gif|png|ico)$/i,          // <-- '.png' here
```
- **Evidence:** `addMedia()` calls `isJunkUrl(url)` for every candidate; the pattern above makes **any URL ending in `.png` (or `.gif`/`.ico`) junk**. PNG is the second most common image format; galleries rendered as PNG will show “No media found”.
- **Impact:** Silent massive recall loss in the extension (both popup and Ctrl+Shift+S paths).
- **Fix:** remove `.png`; keep `.gif`/`.ico` (trash formats per product policy, and `.gif` is filtered downstream too):
```js
/\\.(gif|ico)$/i,
```

### BUG-3 — `_is_significant_media` dimension check is dead code for `<img>` elements
- **File:** `src/parser/webpage_parser.py`, `_is_significant_media()` and `_get_best_image_url()`.
- **Evidence:** `_get_best_image_url` stores parsed width/height inside `attributes["dimensions"] = {"width": ..., "height": ...}`. `_is_significant_media` instead reads:
```python
width = int(attrs.get("width", 1000))
height = int(attrs.get("height", 1000))
if width < K.SIGNIFICANT_MEDIA_MIN_DIMENSION or height < K.SIGNIFICANT_MEDIA_MIN_DIMENSION:
    return False
```
`<img>` callers pass `attrs` that **never contain top-level `"width"`/`"height"` keys** → default 1000 always passes → the min-dimension filter never fires for images. The same dead-read affects `utils.is_banner_or_ad()` (aspect-ratio / 1×1 pixel checks also read `attrs["width"]`).
- **Impact:** Small icons/sidebar images with explicit width/height attributes are not filtered by dimension; ad-pixel aspect-ratio heuristic is ineffective.
- **Fix:** read dimensions from the `dimensions` dict when present:
```python
dims = attrs.get("dimensions")
if dims:
    try:
        width = int(dims.get("width", 0))
        height = int(dims.get("height", 0))
        if 0 < width < K.SIGNIFICANT_MEDIA_MIN_DIMENSION or 0 < height < K.SIGNIFICANT_MEDIA_MIN_DIMENSION:
            return False
    except (ValueError, TypeError):
        pass
```
And in `utils.is_banner_or_ad`, prefer `attrs.get("dimensions")` first, then fall back to `attrs["width"]/["height"]`.

### BUG-4 — `Retry-After` HTTP-date crashes the 429 backoff path
- **File:** `src/parser/webpage_parser.py`, `_get_content()`.
```python
retry_after = int(response.headers.get("Retry-After", 5))
```
- **Evidence:** RFC 7231 allows `Retry-After` to be an **HTTP-date** (e.g. `"Wed, 21 Oct 2015 07:28:00 GMT"`). `int()` raises `ValueError` → swallowed by the generic `except Exception` → returns `PARSER_UNKNOWN_ERROR` and **abandons the retry logic entirely**.
- **Fix:**
```python
from email.utils import parsedate_to_datetime
...
raw = response.headers.get("Retry-After", "5")
try:
    retry_after = max(0, int(raw))
except ValueError:
    try:
        retry_after = max(0, int((parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        retry_after = 5
```

### BUG-5 — GET response Content-Type is never validated when HEAD fails
- **File:** `src/downloader/media_downloader.py`, `_do_download()`.
- **Evidence:** Content-Type → “webpage/script content” detection happens **only on the HEAD response**. Many CDNs reject `HEAD` (405/403). In that path the code proceeds to `session.get(...)` and writes whatever comes back — including an HTML error/login page — to `file.jpg`/`.mp4`, and the parser's interstitial-retry heuristic never triggers (that relies on the HEAD error string).
- **Fix:** after the GET, validate content-type before writing, and fall through to the same “webpage/script content” error used by the parser:
```python
response_get = self.session.get(self.url, headers=per_req_hdrs, stream=True, timeout=timeout_to_use)
response_get.raise_for_status()
if response_head is None:
    ct = (response_get.headers.get("Content-Type") or "").lower()
    if any(t in ct for t in ["text/html", "application/javascript", "text/javascript", "text/css", "application/json"]):
        response_get.close()
        return {"success": False, "error": f"Webpage/script content (Content-Type: {ct})"}
```

---

## 2. High-risk robustness / design issues

### RISK-1 — `Retry Count` setting is ignored for downloads
- **File:** `src/downloader/media_downloader.py` + `src/parser/parser_manager.py`.
- **Evidence:** `ParserManager._downloader_worker` computes `retries_val` (incl. probation `DEFAULT_DOMAIN_PROBATION_RETRIES`) and passes it to `downloader.download(timeout=..., retries=retries_val)`. But `download()` **ignores the parameter** (`retries param is less used now session handles it`) and, when a shared session is used (always in production), `Retry(total=0)` means **zero retries regardless of settings**. So the user-facing “Retry Count” (default 3) has no effect, and probation retries are moot.
- **Impact:** Transient network errors fail downloads immediately; feature contract mismatch between UI and behavior.
- **Fix (minimal, preserves Stop-responsiveness):** keep `Retry(total=0)` on the shared session, but implement *bounded* retry in `_do_download` around the HEAD+GET when `retries` is passed and `stop_event` not set:
```python
def download(self, timeout=None, retries=None):
    retries = retries if retries is not None else self.settings.get(K.SETTING_RETRY_COUNT, K.DEFAULT_RETRY_COUNT)
    attempt = 0
    while True:
        if self.stop_event and self.stop_event.is_set():
            return {"success": False, "error": "Download manually aborted"}
        try:
            return self._do_download(custom_timeout=timeout or self.settings.get(K.SETTING_TIMEOUT, K.DEFAULT_TIMEOUT))
        except Exception as e:
            attempt += 1
            if attempt > retries:
                logger.error(f"Download failed for {self.filepath}: {str(e)}", exc_info=True)
                return {"success": False, "error": str(e)}
            time.sleep(0.5 * attempt)
```
(Keep the total at 0 so Stop stays immediate.)

### RISK-2 — `requests.Session` shared across downloader threads (not thread-safe)
- **File:** `src/parser/parser_manager.py` (`create_shared_downloader_session` → passed to every `MediaDownloader`).
- **Evidence:** Up to `DEFAULT_DOWNLOADER_THREADS` (8, UI up to 32) worker threads concurrently `get/head` on **one** `requests.Session`. `requests.Session` is documented as **not thread-safe** (shared cookie jar, adapters). Cookies are also mutated from the parser event-loop thread (`_invoke_parser` → `session.cookies.set(...)`).
- **Impact:** Rare intermittent failures / corrupted cookie jar under concurrency; hard to reproduce.
- **Fix options (pick one):**
  1. Keep the shared transport but protect cookie-jar access with a `threading.Lock` in `_invoke_parser` and before `MediaDownloader` requests.
  2. Give each downloader thread its own `Session` (loses TCP reuse, acceptable for correctness).
  3. Use `requests.Session` only for cookie state and create per-request adapters — overkill; prefer option 1.

### RISK-3 — Localhost HTTP API is CSRF-able by any website
- **File:** `src/server/http_server.py` (+ `extension/background.js`).
- **Evidence:** `Access-Control-Allow-Origin: *` on `http://127.0.0.1:19876/api/tasks`, no auth token. Any page open in the browser can `fetch("http://127.0.0.1:19876/api/tasks", {method:"POST", ...})` and inject download tasks (browser sends the POST fine; CORS only gates reading the response, and `*` even allows that).
- **Impact:** A malicious site can force the desktop app to download arbitrary URLs (bandwidth / disk abuse; content ends up in the user's download dir).
- **Fix:** restrict CORS to the extension's origin and/or require a per-session token:
```python
ALLOWED_ORIGINS = {"chrome-extension://<YOUR-EXTENSION-ID>"}
origin = request.headers.get("Origin", "")
if origin and origin in ALLOWED_ORIGINS:
    headers["Access-Control-Allow-Origin"] = origin
```
(and optionally require `X-Auth-Token` that the extension reads from `chrome.storage.local`). Also: `/api/queue` currently returns the same payload as `/api/status` — return the actual queue (`[{id, url, status, ...}]`).

### RISK-4 — Extension executes arbitrary JS from sieve JSON (`new Function`)
- **File:** `extension/sieve.js`, `executeSieveJS()`; `extension/popup/popup.js` (“Load sieve rules...”).
- **Evidence:** rules from user-loaded JSON are compiled with `new Function(fnBody)` and executed in the content-script isolated world (which has `chrome.runtime`/`chrome.storage` access).
- **Impact:** A malicious/crafted sieve file can run code inside the extension context (page-data exfiltration, storage writes). The Python side (`site_pattern_manager._build_js_callable`) has the same class of issue via `exec`, but files there are loaded from disk by the user with awareness.
- **Fix (cheap):** only allow JS rules when the file came from the bundled `sieve.json`; for user-uploaded files, skip `:`-rules and log a warning. Document the trust boundary.

### RISK-5 — Resume priority loss + URL normalization instability
- **File:** `src/parser/parser_manager.py` `load_state()`, `src/parser/utils.py` `normalize_url()`.
- **Evidence:** `normalize_url` lowercases host, strips fragment and trailing slash, but **does not sort/standardize query parameters** and does not strip tracking params (`utm_*`, `fbclid`). `downloaded_files` / `processed_urls` are keyed on normalized URLs, so the same resource with differently-ordered query params is treated as a new URL → duplicate downloads with `_1`/`_2` suffixes after Resume (the known open issue in `CONTEXT.md` §15).
- **Fix (safe, high-value):** normalize query order and drop known tracking keys in `normalize_url`:
```python
from urllib.parse import urlencode, parse_qsl
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
...
query = parse_qsl(parsed.query, keep_blank_values=True)
query = sorted((k, v) for k, v in query if k.lower() not in TRACKING_PARAMS)
normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path, query=urlencode(query), fragment="").geturl()
```
Add a unit test for `a.jpg?b=2&a=1` == `a.jpg?a=1&b=2`.

---

## 3. Correctness / logic nits (medium, safe to fix)

1. **`page_limit` “0 = unlimited” unreachable in UI** — `settings_dialog.py` `page_limit_spin.setRange(1, 1000)` but tooltip says `0 = unlimited` and `parser_manager` honors `0`. Change range to `(0, 1000)` and clamp `sanitize_settings` to `(0, 10000)`.
2. **`_extract_videos` has no significance filter** — ad/tracker `<video>` embeds and tiny players are appended unconditionally (only `_extract_images` filters). Apply `_is_significant_media("video", abs_url, attrs)` before appending (matches DEV_GUIDE WP-2.3).
3. **Gateway suspicion formula still the old one** — `webpage_parser._handle_gateways()`: `is_suspicious = len(self.media_files) < 5 or any(kw in text_content ...)`. The `or` keyword branch alone triggers on many normal pages (false gateway clicks). Use the safer scoring from DEV_GUIDE WP-5.3 (require `GATEWAY_OVERLAY_SELECTORS` / age phrases, keep keyword-only trigger only when media is very low). Note: `K.GATEWAY_OVERLAY_SELECTORS` is currently **never used**.
4. **`K.THUMBNAIL_URL_HINTS` is dead** — defined in `constants.py`, referenced nowhere. Either wire it into `variant_attrs["likely_thumbnail"]` (DEV_GUIDE WP-2.1) or delete.
5. **JSON parser emits `media_type="file"`** — `json_parser._guess_media_type()` returns `"file"`; it flows into `media_files`, but `parser_manager` stats only count `image`/`video`, and `_get_filename_from_url` gives unknown types `.mp4`. Map `"file"` → `"image"` (or treat as a dedicated type with its own handling and stats key).
6. **Fallback UA overrides configured UA** — `webpage_parser._get_content()` sync fallback sets `fb_headers = {"User-Agent": K.DEFAULT_USER_AGENT}`; request headers override the session's configured UA. Use `self.settings.get(K.SETTING_USER_AGENT, K.DEFAULT_USER_AGENT)`.
7. **`AsyncClientManager` timeout defaults** — `shared_session.py` `__init__` uses `settings.get("page_timeout", 60)` but `K.DEFAULT_PAGE_TIMEOUT` is 30 and `"connect_timeout"/"sock_read_timeout"` keys don't exist in `DEFAULT_SETTINGS_VALUES` (docstring claims they do). Align with constants or remove the dead args (`connector_args` is also unused — F841).
8. **`should_skip_crawl_url` stop-words → segment semantics** — `DEFAULT_STOP_WORDS` contains `"about_us"`, `"privacy_policy"` (underscored) which never match real path segments; the dialog defaults already contain plain `"about"`, `"privacy"`, etc. Clean the list and note in UI tooltip that stop words now match **path segments**, not substrings (this prevents `"ad"`-style false positives — keep it segment-aware).
9. **`sanitize_settings` doesn't validate `stop_words`** — a corrupted `settings.json` with `stop_words: "login"` (string) iterates characters. Normalize to a list of non-empty stripped strings.
10. **`is_banner_or_ad` substring `"ads"`/`"banner"` false positives** — e.g. `instagram.com/...` avoided already, but generic paths like `/adsync/` etc. match. Prefer segment-aware matching consistent with `should_skip_crawl_url`.

---

## 4. Code quality / consistency / dead code

| # | Location | Issue | Suggested action |
|---|---|---|---|
| Q1 | `src/gui/main_window.py` | `time`, `Signal`, `QSize`, `QItemSelectionModel`, `QMetaObject`, `QIcon`, `QDesktopServices` unused imports (F401) | Remove |
| Q2 | `src/gui/main_window.py` | `TaskItem` referenced in annotation `-> 'TaskItem | None'` but never imported (F821, string-annotation so non-fatal) | `from src.core.task_item import TaskItem` |
| Q3 | `src/gui/main_window.py` | `run_coroutine`/`_run_coroutine` and `_apply_pause_state` are dead (no callers; `asyncio.create_task` needs a running loop — the GUI loop never runs) | Remove; drop the GUI-thread `self.loop` too |
| Q4 | `src/gui/main_window.py` | `csv_status` assigned, never used (F841); also CSV import ignores the `Status` column | Remove variable or use status to skip already-completed |
| Q5 | `src/gui/main_window.py` | Queue file path uses `os.path.join(self.download_dir, "task_queue.json")` while `app_paths.queue_path()` (next to exe) exists and is unused → two competing locations | Unify on `queue_path()` |
| Q6 | `src/gui/log_handler.py` | `datetime`, `QTextEdit`, `Qt`, `QMetaObject` unused (F401) | Remove |
| Q7 | `src/core/task_queue_manager.py` | `Dict`, `datetime`, `K` unused (F401) | Remove |
| Q8 | `src/core/task_queue_manager.py` | `add_task` shallow-copies `settings`; nested `stop_words` list is shared across tasks | `copy.deepcopy(settings)` |
| Q9 | `src/parser/priority_url_queue.py` | `_url_scores` attribute written nowhere, read nowhere; `Optional`/`Set` unused; `_get_domain` uses bare `except` (E722); `task_done()` is a no-op (fine for heap, but then remove `task_done()` calls in workers to avoid implying queue semantics) | Clean up |
| Q10 | `src/parser/priority_url_queue.py` | `datetime`/`timestamp` on `PrioritizedURL` unused in ordering | Remove or use for FIFO tie-break |
| Q11 | `src/parser/parser_manager.py` | `traceback` unused; many one-line `if/for` compound statements (E701/E702) — match repo style or add formatter | Refactor only when touching |
| Q12 | `src/parser/parser_manager.py` | `save_state` reads `self.url_queue._queue` and `download_queue._queue` without the lock — benign today (GIL + event loop), but comment it or snapshot under `_lock` | Document |
| Q13 | `src/parser/site_pattern_manager.py` | `res_rule`, `has_ext_pattern`, `path`, `transformed` unused locals (F841); `elif 'imagus_patterns'` means a pattern with **both** `image_transformations` and `imagus_patterns` only applies the first | Use `if/if`, or document precedence |
| Q14 | `src/parser/site_pattern_manager.py` | `_extract_domain_from_regex` won't match domains in `site_patterns.json` whose `link` starts with `https?://` style `\\^` etc. — verify rule coverage with the 849-rule file (see §6.2) | Add unit test |
| Q15 | `src/parser/webpage_parser.py` | unused imports `os`, `time`, `mimetypes`, `Set`, `Union`, `filetype`, `brotli`-guarded, `is_valid_url`, `is_same_domain` (F401) | Remove |
| Q16 | `src/parser/shared_session.py` | `from typing import Optional` at **bottom** of file (E402) — works, fragile | Move to top |
| Q17 | `src/parser/json_parser.py` | `asyncio`, `WebpageParser`, `HAS_BROTLI`, `normalize_url` unused imports (F401) | Remove |
| Q18 | `src/parser/utils.py` | `os`, `urljoin` unused; `extract_largest_image_from_srcset` has no callers | Remove or mark used-by-tests |
| Q19 | `src/fix_lxml.py` / `fix_brotli.py` | `sys`/`os` unused; `fix_lxml` `print()`s instead of logging | Clean; log |
| Q20 | `src/gui/settings_dialog.py` | `QDoubleSpinBox`, `QScrollArea`, `sys` unused; duplicate `"login"` in default stop words; “Pattern Info” label only shows custom/built-in count, not loaded sieve count | Clean + show sieve count |
| Q21 | `extension/background.js` | `const lock = { acquire(){}, release(){} }` no-op; `referer` field passed in `chromeDownload` items but never used by `chrome.downloads.download` | Remove dead fields; document |
| Q22 | `extension/background.js` | `(linkRegex.lastIndex = 0, linkRegex.test(linkUrl))` comma-expression — works but obscure | Split into a helper `testLink(rule, url)` |
| Q23 | `extension/popup/popup.js` | `FULLSIZE_SOURCES` duplicated in 3 files (popup, background, and desktop `_get_media_priority`) | Extract to one shared constant in `sieve.js` |

---

## 5. Security notes

1. **RISK-3** (CORS `*` on localhost API) — see §2; highest-priority security item.
2. **Header injection** — `sanitize_settings` strips CR/LF from `user_agent`/`accept_language`; good. Extend the same strip to `proxy` (a `\r\n` there would be rejected by requests anyway, but be consistent).
3. **`verify=False` in sync fallback** — `webpage_parser` sync fetch and `_execute_bypass` disable TLS verification (`verify=False`). Intentional (TLS-fingerprint blocks), but document it and consider a setting flag.
4. **`backup.py`** — zips everything except excludes; `settings.json` (may contain cookies/proxy creds) is included. Add `settings.json`, `task_queue.json`, `sessions/` to excludes.
5. **`setup.py`** — stale and misleading (see §6.1). Anyone installing via `setup.py` gets `requests-html`, `html5lib`, `Pillow` which the app doesn't use, and **won't** get `aiohttp`/`PySide6`/`filetype` → broken install.
6. **No secrets in repo** — verified none committed (`.gitignore` covers `settings.json`, `venv/`, etc.).

---

## 6. Dependency / build / config

### 6.1 `setup.py` vs `requirements.txt` drift
`setup.py` `install_requires` = `PySide6>=6.4`, `requests`, `requests-html`, `beautifulsoup4`, `html5lib`, `Pillow` — while the app actually uses `aiohttp`, `aiofiles`, `lxml`, `filetype`, `chardet`, `brotli`, `brotlicffi`, `certifi`, `cryptography`. Also `entry_points` references `main:main` (fine). **Action:** sync `setup.py` to `requirements.txt` (or delete `setup.py` and rely on `requirements.txt`), since `README`/`AGENTS.md` treat `requirements.txt` as canonical.

### 6.2 Imagus sieve loading — double-load and coverage
- `SitePatternManager.load_patterns()`: when no custom path is set and `enable_built_in=True`, `search_dirs` includes `resources/patterns/` and — when frozen — the exe dir; it **re-loads** `Imagus_sieve_*.json` and `site_patterns.json`. In dev, the project root copy `Imagus_sieve_2026.04.01_849.json` is only picked up if it sits in `resources/patterns/` (it's at repo root) — verify the app actually loads the 849 rules in dev. Add a test that loads the bundled sieve and asserts `len(rules) >= 800` and `js_skipped` count is logged.
- `_load_imagus_file` indexes only the **first domain** extracted from a `link` regex into `imagus_rules[domain]`; rules whose `link` matches multiple domains are partially applied. Acceptable, but document.

### 6.3 Lint/format config
Add `pyproject.toml` with minimal `[tool.ruff]` config (line-length 120, ignore E501/E701/E702 if you want to keep the current compact style) so future work has a baseline. Currently ruff reports ~180 items with zero project-specific configuration.

### 6.4 `build_exe.py` nits
- Uses bare `pyinstaller` command (PATH-dependent) — use `[sys.executable, "-m", "PyInstaller", ...]`.
- `--hidden-import=lxml.html.clean` — modern `lxml` dropped `html.clean` (that's exactly why `fix_lxml.py` exists); this hidden import can fail the build. Either keep it (it's tolerated if absent) or remove and rely on `fix_lxml`.
- `--add-data=resources/patterns/site_patterns.json;resources/patterns` — `resources/patterns` target dir is created implicitly; OK on Windows, but the `;` separator is non-portable (fine for this Windows-only project).

### 6.5 `.gitignore` — missing generated artifacts
Add: `backups/`, `*.log`, `sessions/`, `.qwen/`, `.mimocode/`, `Audit*.md` (if not intended for commit), `__pycache__/` (verify), `*.pyc`.

---

## 7. Tests — gaps and hygiene

Current coverage: URL detection, sieve transform smoke, downloader filters, shared-session manager, one parser-manager filtering test. **12 tests, 1 skipped (network).**

**Missing (add in priority order):**
1. `tests/test_link_skip.py` — `should_skip_crawl_url` (privacy/login skip; gallery allowed; `"ad"` not substring).
2. `tests/test_task_queue_manager.py` — add/remove/move/save/load round-trip, active-slot rules, `load()` resets RUNNING→PAUSED.
3. `tests/test_priority_url_queue.py` — put/get ordering, `bypass_checks`, zero-priority drop, `_is_downward_url` cases.
4. `tests/test_webpage_parser_extract.py` — pure-HTML fixtures: `_extract_images`/`_extract_links` merge (`from_image` preservation), `_extract_jsonld_media`, `_is_significant_media` with real `dimensions` dict (regression for BUG-3).
5. `tests/test_normalize_url.py` — query-param normalization (regression for RISK-5).
6. Extension — no automated tests; at minimum add a Node-free sanity check or a small `tests/test_extension_patterns.py` that runs the `JUNK_PATTERNS`/`FULLSIZE_SOURCES` logic against fixtures in Python.

**Test hygiene:**
- `tests/test_parser_manager_filtering.py` instantiates `ParserManager`, which creates an `asyncio.new_event_loop()` per test that is never closed → resource leak warning. Wrap in `try/finally: loop.close()` (or `pytest.fixture`).
- Tests hardcode `sys.path.insert` — prefer `pytest.ini`/`pyproject.toml` with `pythonpath = ["."]`.
- The `RuntimeWarning: TestResult has no addDuration` comes from mixing `unittest` + pytest on 3.12; harmless, but converting to plain pytest fixtures removes it.

---

## 8. Remaining concerns (not safely auto-fixable)

1. **`requests.Session` shared across threads** (§RISK-2) — needs a deliberate decision (lock vs per-thread sessions); don't change blindly.
2. **Requests-html legacy** — `requirements.txt` no longer lists `requests-html`; if any code path still imports it (none found), the fallback would fail. Verified none — safe.
3. **Sieve JS→Python callable coverage** — `site_pattern_manager._try_parse_imagus_js` is a partial JS interpreter; complex rules are silently skipped. Measure `js_skipped` on the 849-rule file and decide whether to invest more (probably not — document).
4. **GUI behavior** — Pause/Resume, Stop, auto-next, and extension callback thread-safety were reviewed by reading; no automated coverage. Manual smoke test recommended after changes.
5. **Performance** — `priority_url_queue._calculate_url_priority` runs several regexes per link; for pages with 200 links × deep crawls this is fine, but `_is_likely_content_page` re-parses the URL 3× per call (`urlparse` called repeatedly). Micro-optimization only if needed.
6. **`verify=False`** in fallback/`_execute_bypass` (§5.3) — keep, but make it a named constant with a comment.

---

## 9. Assumptions made

1. **No `git commit` was made** before the review (the prompt's “Commit all changes first” was skipped deliberately): the working tree contains uncommitted personal files (`knowledge.md`, `ReviewPrompt.txt`, `.agents/`, `Errors.txt`, `Log.txt` deletion) that shouldn't be committed without your confirmation. Review reflects the **working tree**, not HEAD.
2. **No code was modified** — this document is the deliverable.
3. Environment: Python 3.12.10 on Windows; node/mypy/pyright unavailable, so JS syntax and type-hint checks were manual.
4. Files referenced by `docs/DEV_GUIDE_MEDIA_CRAWL_IMPROVEMENTS.md` (`Audit/FULL_AUDIT_2026-07-16.md`, `Audit/CHROME_EXTENSION_AUDIT_2026-07-16.md`) do **not exist** in the repo — the DEV_GUIDE work packages WP-0…WP-5 appear to have been mostly implemented already (verified: `should_skip_crawl_url`, Referer-origin fix, `from_image` merge guard, WP-size strip, JSON-LD extraction, max-links cap are all present in code).
5. Sieve data files (`sieve.json`, `Imagus_sieve_*.json`) were treated as data, not reviewed line-by-line.

---

## Appendix A — ruff/pyflakes summary (top of the list)

Confirmed real defects from static analysis:
```
src/gui/main_window.py:524:38: F821 Undefined name `TaskItem`
src/gui/main_window.py:1212:17: F821 Undefined name `logger`
src/gui/main_window.py:685:21: F841 Local variable `csv_status` unused
src/parser/shared_session.py:98:13: F841 `connector_args` unused
src/parser/site_pattern_manager.py:174/281/511/720: F841 unused locals
src/parser/priority_url_queue.py:60:9: E722 bare except
```
Remaining findings are F401 (unused imports) and E701/E702 (multiple statements per line) — listed fully in §4. Suggest `ruff check --fix` for the F401 set, then manual review for the rest.

---

*End of audit. Fixes ordered: BUG-1 → BUG-5 → RISK-1..5 → §3 nits → §4 cleanup → §6 config → §7 tests.*

---

## Addendum — решения и выполненные действия (2026-08-07)

1. **BUG-2 (PNG в расширении)** — по решению автора пропуск PNG **осознанный** (декоративный PNG-мусор). Вместо жёсткого блока реализована **настройка выбора форматов для десктопа** (по выбору «Только десктоп»):
   - `src/constants.py`: `DEFAULT_ENABLED_IMAGE/VIDEO/AUDIO_FORMATS` + ключи `SETTING_ENABLED_*_FORMATS` (дефолты сохраняют прежнее поведение: GIF/SVG/ICO/CUR выключены, m3u8/mpd включены).
   - `src/parser/utils.py`: `is_format_allowed(url, media_type, settings)` — сегмент-безопасный allowlist, не блокирует не-медиа расширения и unknown-типы.
   - Подключён в `webpage_parser._is_significant_media`, `_extract_images`, `_extract_videos`, `json_parser`, финальный чек `media_downloader`.
   - `src/gui/settings_dialog.py`: вкладка Filters → группы чекбоксов «Image Formats» / «Video Formats» + apply/get/sanitize.
   - Тесты: `tests/test_format_filter.py` (6 кейсов). **Итог: 18 passed, 1 skipped.**
   - Расширение не тронуто (открытый follow-up — единая опция форматов в popup).

2. **Deno-интеграция** — по решению «пока только анализ»: оформлен `docs/DENO_JS_ENGINE_DESIGN.md`. Проверено по фактам: пакет `deno` официальный (авторы Deno, Windows поддерживается, `pip install deno`); media-downloader передаёт `bin/deno.exe` в yt-dlp через `--js-runtimes` (доказательство — `Log.txt`: `[youtube] [jsc:deno] Solving JS challenges using deno`), что решает JS-челленджи YouTube, а **не** Cloudflare. Чистый Deno против современного CF (Turnstile/TLS-JA3) неэффективен; дешёвый путь к CF — cookies браузера из расширения (+ опц. curl_cffi). Дизайн предлагает P0 (Imagus JS-правила через Deno-воркер), P1 (render-pass linkedom/jsdom), P2 (экспериментальный старый CF-челлендж), P3 (curl_cffi отдельно). Код не писался.

3. **Cloudflare** — решение принято в рамках п.2: без новых тяжёлых зависимостей пока; усиление — через существующий механизм cookies расширения.

4. **Реальные баги — исправлено (приоритет: сначала баги, Deno — потом).** По правилу «читай реальный код, не предполагай» каждый фикс сверялся с текущим кодом, затем покрыт регрессионным тестом. Итог: **39 passed, 1 skipped** (было 18/21).
   - **BUG-1** (`main_window.py`): добавлен модульный `logger = logging.getLogger(__name__)`.
   - **BUG-3** (`webpage_parser._is_significant_media` + `utils.is_banner_or_ad`): чтение размеров из `attrs["dimensions"]` (dict) **с fallback на топ-уровневые `attrs["width"]/["height"]`** — оба формата поддерживаются (см. п.5 ниже).
   - **BUG-4** (`webpage_parser._get_content`): `Retry-After` HTTP-date парсится через `email.utils.parsedate_to_datetime` (с fallback 5s), раньше `int()` валил ветку 429.
   - **BUG-5** (`media_downloader._do_download`): при неудачном HEAD проверяется Content-Type ответа GET до записи — HTML/JS/CSS/JSON не сохраняются под `.jpg`/`.mp4`.
   - **RISK-1** (`media_downloader.download`): bounded retry-цикл по настройке Retry Count (учитывает стоп-событие; классификация ошибок: network/timeout/5xx/429 — retry; «file too small»/skip — без ретрая). `Retry(total=0)` на session сохранён (мгновенный Stop).
   - **RISK-3** (`http_server`): CORS ограничен происхождением расширения `chrome-extension://` (запросы без `Origin` — локальные — пропускаются).
   - **RISK-5** (`utils.normalize_url`): сортировка query-параметров + отсев tracking (`utm_*`, `fbclid`, `gclid`, …) + тест на стабильность.
   - **RISK-2** (`parser_manager`): cookie-sync в `_invoke_parser` обёрнут в `_cookie_lock`, прикреплённый в `create_shared_downloader_session` (сессия-пул в `shared_session.py`; блокировка вокруг мутаций cookie_jar, чтение в `_extract_cookies` тоже под lock).
   - **§3.1** (`settings_dialog`): `page_limit_spin` диапазон `(0, 1000)` — «0 = без лимита» снова достижим; clamp в sanitize.
   - **§3.6** (`webpage_parser._get_content` fallback): UA берётся из настроек `SETTING_USER_AGENT`, а не хардкод `K.DEFAULT_USER_AGENT`.
   - Тесты: `tests/test_bugfixes.py` (normalize_url, banner/ad dimensions, значимые размеры — включая picture-path и строковые width, bounded retries, stop-event). Итог: **41 passed, 1 skipped** (после фикса регрессии п.5 — 39, после доп. тестов — 41).

5. **Новая находка при фиксе BUG-3 (обнаружено ревью, исправлено).** Изначальный фикс BUG-3 читал **только** `attrs["dimensions"]`, что делало фильтр минимальных размеров мёртвым для `<picture>`-источников: ветка `_extract_images` для `<picture>`/`<source>` строит `attrs` с **топ-уровневым** `width` (`{"width": source_data.get("width"), ...}`), без dict `dimensions` — до фикса фильтр для них работал через `attrs.get("width")`. `_is_significant_media` теперь читает оба формата (как `is_banner_or_ad`): `dims = attrs.get("dimensions") if isinstance(...dict...) else {}; width = dims.get("width", attrs.get("width", 0))`. Проверено по всем вызовам: `<img>` (dict dimensions), `<picture>` (топ-уровневый width), favicon/meta/css/JS (без размеров → пропуск), `_extract_videos` не вызывает `_is_significant_media`. Тесты: топ-уровневый `width: 64` → отсекается; `1920` → проходит; `0` → проходит; строковый `"auto"` → без краха, проходит; height-only `32` → отсекается. Урок: при переделке «мёртвого» кода проверять **все** точки вызова — у одного фильтра может быть несколько форматов входных данных.

6. **§3 (ниты корректности) — исправлено.** Все пункты §3 закрыты, покрыты тестами `tests/test_sec3_fixes.py` + расширен `test_bugfixes.py`. Итог: **59 passed, 1 skipped**.
   - **§3.2 (`_extract_videos`)** — `<video>`-файлы проходят полный `_is_significant_media` (отсекает крошечные/ad-плееры); iframe/meta-эмбеды фильтруются только по `is_format_allowed` + `is_banner_or_ad`. **Важно:** полный фильтр для эмбедов НЕ применяется — `SIGNIFICANT_MEDIA_IGNORE_PATTERNS` содержит "youtube"/"vk.com", что уронило бы легитимные эмбеды (регрессия, поймана ревьюером). Аналогично шаг 3 (image-noise patterns) пропускается для `media_type == "video"` — иначе `load-movie.mp4` (содержит "ad-") отсекался бы. DEV_GUIDE WP-2.3.
   - **§3.3 (`_handle_gateways`)** — формула WP-5.3: `GATEWAY_OVERLAY_SELECTORS` (теперь используется!) + age-фразы; общие модалки (`.modal-content`, `#disclaimer` → новый `GATEWAY_GENERIC_OVERLAY_SELECTORS`) срабатывают только с consent-текстом; голые consent-слова — только при почти нулевом медиа. Новый helper `_select_one_safe` (невалидные CSS-селекторы не роняют).
   - **§3.4 (`K.THUMBNAIL_URL_HINTS`)** — подключён: `variant_attrs["likely_thumbnail"]` в `_extract_images`, деприоритизация ×0.5 в `parser_manager._get_media_priority` (софт-сигнал, не фильтр).
   - **§3.5 (`json_parser`)** — `_guess_media_type` для unknown возвращает `"image"` вместо `"file"` (статистика/филенеймы знают только image/video; "file" давал ошибочный `.mp4`).
   - **§3.7 (`shared_session`)** — таймауты выровнены с `K.SETTING_PAGE_TIMEOUT`/`K.DEFAULT_*` (был хардкод 60); удалён мёртвый `connector_args`; `Optional` перенесён в верхний импорт.
   - **§3.8 (`DEFAULT_STOP_WORDS`)** — убраны подчёркнутые `about_us`/`privacy_policy`/`terms_of_service` (никогда не матчат сегменты пути) → `about`/`privacy`/`terms`.
   - **§3.9 (`sanitize_settings`)** — `stop_words` нормализуется в список непустых строк без дублей (защита от строки/мусора в settings.json).
   - **§3.10 (`is_banner_or_ad`)** — URL-матчинг переведён на **токены** (`_AD_KEYWORDS`, `_matches_ad_keyword` с правилом множественного числа +1s, `_ad_tokens_from_url` — host+path), атрибуты class/id/alt тоже токенизированы. Фиксит ложные срабатывания: `"ads"` больше не бьёт `downloads/photo.jpg`, `"ad"` не бьёт `media`. Добавлены точные ad-network токены (adsense, doubleclick, googlesyndication…). Внимание (от ревьюера): query-параметры больше не сканируются (было `?campaign=x` → теперь нет) и составные токены без разделителя (`topbanner`) не матчатся — осознанная цена за точность; при необходимости вернуть — см. `_AD_KEYWORDS`.
   - `§3.1` и `§3.6` были закрыты ранее (page_limit range; UA из настроек в fallback).

7. **§4/§5 — решения и выполненное.**
   - **§5.2 (`verify=False`) и §5.5 (sieve `new Function`) — оставить как есть (решение автора).** Обоснование (согласовано): threat model у скрейпера обратный классическому — мы не передаём конфиденциальные данные, а **читаем чужие** сайты; отключение проверки TLS — осознанная цена доступа к CDN/анти-ботам. Sieve-файлы пользователь подключает вручную — это осознанное доверие, скипать пользовательские JS-правила = ломать собственную функцию.
   - **§5.1 — выполнено:** `sanitize_settings` теперь вырезает CR/LF и из `proxy` (было только UA/accept_language) — защита от header-injection через settings.json.
   - **§5.3 — выполнено:** `backup.py` исключает `settings.json`, `task_queue.json`, `sessions/` (могут содержать cookies, proxy-креды, личные URL). **Нюанс (пойман ревьюером):** изначальная правка фильтровала только директории (`dirs[:]`), файлы всё равно попадали в зип — добавлен file-level exclude. Покрыто тестом (патч `create_backup` + проверка содержимого зипа).
   - **§5.4 (`setup.py`) — остаётся открытым** (решение: синхронизировать или удалить — на усмотрение автора, см. §6.1).
   - **Q5 — выполнено:** путь `task_queue.json` унифицирован на `app_paths.queue_path()` (рядом с exe) во всех трёх местах `main_window.py` (load/delete/save). Внимание: старый файл в `download_dir` при апдейте молча не подхватится.
   - **Q8 — выполнено:** `TaskQueueManager.add_task` делает `copy.deepcopy(settings)` (вложенный `stop_words` больше не шарится между задачами).
   - **Q13 — выполнено:** в `transform_image_url` секция `imagus_patterns` теперь отдельный `if` (а не `elif`) — правило с **обеими** секциями применяет обе. **Дополнительная находка:** `if source and target` отбрасывал правила-удаления с **пустым target** — в реальном `site_patterns.json` есть такое правило (`thumbs/th_` → `""`), которое молча не работало. Исправлено во всех трёх местах (`image_transformations`, `imagus_patterns`, `_apply_global_transformations`) → `target is not None`. Покрыто регрессионными тестами.
   - Итог тестов: **66 passed, 1 skipped** (было 59).

8. **Открытые пункты (не тронуты, ждут решения):** §4 (чистка 44 неиспользуемых импортов — механическая, можно `ruff --fix`; Q2/Q3/Q4/Q7/Q9–Q12/Q14–Q23), §5.4 (setup.py), §6 (build/.gitignore), §7 (остальные тесты). Deno — после фиксов (см. `docs/DENO_JS_ENGINE_DESIGN.md`).

9. **§4 чистка импортов/мёртвого кода + §5.4 + §6 — выполнено (решение автора: «на усмотрение, по правилам минимума риска»).** Итог тестов: **66 passed, 1 skipped**; pyflakes чист (осталось 2 намеренных side-effect brotli с `# noqa: F401`); `ruff check src main.py --select F401,F841,F821` → **All checks passed!**; compileall OK; `python setup.py --version` → 1.0.0.
   - **§5.4 (`setup.py`) — синхронизирован с `requirements.txt`** (а не удалён): `install_requires` теперь = PySide6/requests/aiohttp/aiofiles/bs4/lxml/filetype/chardet(+brotli/brotlicffi/certifi/cryptography) с маркером платформы для cchardet. Было: requests-html/html5lib/Pillow (не используются) без aiohttp/PySide6/filetype → сломанный `pip install .`. Замечание о каноничности requirements.txt добавлено в docstring.
   - **Q2 — выполнено:** `from src.core.task_item import TaskItem` в `main_window.py` (была строковая аннотация без импорта — F821).
   - **Q3 — выполнено:** удалены мёртвые `_apply_pause_state` (pass-only), `run_coroutine`/`_run_coroutine` (не имели вызывающих; GUI-поток не гоняет loop). Проверено: реальные вызовы — только `asyncio.run_coroutine_threadsafe` (другой метод stdlib).
   - **Q4 — выполнено:** убрана неиспользуемая `csv_status` (F841).
   - **Q7 — выполнено:** неиспользуемые импорты в `task_queue_manager.py`.
   - **Чистка 44 F401 → 2 (оба намеренные brotli с `# noqa: F401`):** main_window (time/Signal/QSize/QItemSelectionModel/QMetaObject/QIcon/QDesktopServices), log_handler (datetime/QTextEdit/Qt/QMetaObject), json_parser (asyncio/WebpageParser/HAS_BROTLI/normalize_url), webpage_parser (os/time/mimetypes/Set/Union/filetype/is_valid_url/is_same_domain; brotli оставлен как side-effect), utils, parser_manager (traceback и др.), priority_url_queue (Optional/Set), media_downloader (re/RequestsCookieJar), settings_dialog (QDoubleSpinBox/QScrollArea/sys), fix_brotli/fix_lxml (sys/os), http_server, shared_session (Optional перенесён в верхний импорт — E402 закрыт), site_pattern_manager.
   - **F841 в `site_pattern_manager` — 4 шт. удалены:** `res_rule` (не читался; коммент «to или res» в коде не реализован — только `to` используется), `has_ext_pattern` (логика #ext# живёт в генерируемой func_src), `path` в `get_patterns_for_url` (матчинг по `full_url`), `transformed` в `_apply_global_transformations` (функция возвращает url). Также удалён `import re as _re` в `_build_js_callable` (остался без использования после удаления has_ext_pattern).
   - **§6.4 (`build_exe.py`)** — `pyinstaller` → `[sys.executable, "-m", "PyInstaller", ...]`; убран `--hidden-import=lxml.html.clean` (современный lxml убрал html.clean — это и есть причина существования fix_lxml.py).
   - **§6.5 (`.gitignore`)** — добавлены runtime-артефакты: `sessions/`, `backups/`, `*.log`, `__pycache__/`, `*.pyc`. `Audit*.md` и `.mimocode/`/`.qwen/` сознательно не добавлены (Audit — рабочий документ для ревью; скрытые агентные папки решено не прятать, чтобы не маскировать следы агентской работы в git status).
   - **Q10/Q11/Q12/Q14/Q16–Q23 остались задокументированными** (мелкая механика/косметика/JS-сторона, не влияют на поведение; Q23 — общий констант для 3 файлов, требует согласования структуры расширения).
   - **Финальное ревью (code-reviewer) — замечания отработаны:**
     - **`self.loop` в `__init__` удалён** — после удаления `run_coroutine`/`_run_coroutine` (единственных потребителей) он стал мёртвым (grep подтвердил: только присваивание на стр. 72–73). Вместе с ним удалён и невызываемый async-метод `_load_previous_state` (его функциональность давно перенесена в `ParserManager._main_task()` — см. комментарий в `_launch_parser_for_task`). GUI-поток больше не создаёт событийный цикл; весь async живёт в `pm.loop` и в thread-local loop сервера.
     - **`chardet`/`cchardet` сплит в setup.py — это НЕ мой регресс:** платформенные маркеры (`chardet; sys_platform == 'win32'`, `cchardet; sys_platform != 'win32'`) скопированы **дословно из requirements.txt** (канонического источника), где они существовали до этой сессии. setup.py теперь честно зеркалит requirements.txt. Неудобство на не-Windows (`import chardet` при установленном только cchardet) — предсуществующая особенность канонического файла; целевая платформа — Windows, поведение не меняю.
     - **`import re as _re` в `_build_js_callable` удалён безопасно** (ревьюер подтвердил): единственным его использованием был удалённый `has_ext_pattern`; генерируемая `func_src` несёт собственный `import re as _re` в отдельном namespace, а `_js_expr_to_python*` имеют локальные импорты.
   - **Итоговая валидация:** `pytest` → **66 passed, 1 skipped**; `compileall` OK; `pyflakes` чист (2 намеренных side-effect brotli с `# noqa: F401`); `ruff check --select F401,F841,F821` → **All checks passed!**; `python setup.py --version` → 1.0.0.

10. **КРИТИЧЕСКИЙ БАГ краулера — бесконечная рекурсия URL (обнаружен при живом тесте, исправлен).** Симптомы: лог раздувается до 34 МБ, «ничего не загружается», приоритеты до `270270000.00`. Механизм: vBulletin-страницы (`vipergirls.to/threads/...`) содержат **относительные** ссылки без ведущего `/` (`threads/members/...`, `threads/forum.php`); `urljoin` относительной ссылки на базу-«slug» без завершающего `/` (`/threads/16341233-...`) каждый раз добавляет ещё один сегмент `/threads/`. Раздутый URL (`threads/threads/threads/...`) уникален → проходит дедупликацию `processed_urls` → снова парсится → экспоненциальный рост. Воспроизведено локально: 4 итерации `urljoin` дают `threads/threads/members/threads/members/...`. **Фикс (2 уровня):**
   - **`normalize_url`**: схлопывание **run'ов из 3+ одинаковых соседних сегментов** пути (`threads/threads/threads/x` → `threads/x`). Сознательно НЕ схлопываются одиночные дубли (`/a/a/b` сохраняется) — `normalize_url` применяется и к download-URL, переписывание легитимного удвоенного каталога дало бы 404 на CDN (замечание ревьюера).
   - **`PriorityURLQueue.put`**: нормализация URL до постановки в очередь (схлопнутый URL отсеивается по `processed_urls`) + предохранители: >50 сегментов пути, длина >2000, любой сегмент повторяется >3 раз → скип с debug-логом. `source_url`/`start_url` тоже нормализуются перед relationship-check (консистентность сравнения путей).
   - **Валидация:** 6 новых регрессионных тестов (схлопывание, сохранение 2×-каталога, guard для чередующегося блота, нормальные URL проходят) → **73 passed, 1 skipped**; pyflakes/ruff чисты; exe пересобран + смоук-тест OK.

11. **Fullsize-дискавери через sieve link→url→res (по принципу автора: stay-in-domain не должен ограничивать медиа-ссылки и thumbnail-переходы).** Обнаружено при живом тесте: на vipergirls-треде расширение находит 46 fullsize-оригиналов, а приложение — ничего, хотя на странице 62 thumbnail'а (`image.imx.to/u/t/...`) и 62 ссылки-перехода (`imx.to/i/...`). Трассировка алгоритма расширения (`background.js discoverFullsize`) показала: **это не GET-страницы, а Imagus-цепочка** — правило `imx.to-h-x`: `link: "^(imx\.to/)i(/\w+)"`, `url: "$1i$2 :imgContinue="` (всё после ` :` — form-POST данные), `res: "=\"centred\" src=\"([^\"]+)\""`. Расширение **POST'ит** `imgContinue=` на `imx.to/i/CODE` и из ответа вытаскивает fullsize `image.imx.to/u/i/YYYY/MM/DD/CODE.jpg` (не `/u/`, не `/u/t/` — проверено вживую). В приложении `SitePatternManager.transform_image_url` умел только `img`+`to` (чистый трансформ URL), цепочка `link`+`url`+`res` не реализована, а сами ссылки-переходы убивались на двух уровнях: `PriorityURLQueue._is_downward_url` (priority 0 для чужого домена) и `stay_in_domain` в `_process_parser_results`. **Фикс (зеркало расширения):**
   - **`SitePatternManager`** — новые методы: `get_link_rule` (матчинг `link`-регекса на stripped/full URL), `apply_link_url_transform` (подстановка `$1..$n`/`$&` в убывающем порядке — защита от `$10`-коллизии; split POST-данных после ` :`; JS-`url` скипается как в SW расширения), `extract_res_urls` (`res`-регексы + fallback `<img src>`-скан с фильтром jpg/webp/avif/heic/bmp/tiff; JS-`res` отбрасывается самозащитой `startswith(':')`).
   - **`ParserManager._discover_linked_fullsize`** — асинхронный probe (семафор 5, таймаут 8с/запрос): для каждой from_image-ссылки, матчнувшейся в sieve, применяет url-трансформ (POST/GET) и извлекает fullsize. **Медиа-лукапы не ограничиваются по количеству** — счётный лимит (изначально скопированный из браузерного расширения `slice(0,50)`) заменён на **ресурсный тайм-бюджет** `FULLSIZE_DISCOVER_TIME_BUDGET = 45` (сек на страницу): тред на 500 картинок разрезолвит все 500, а дохлый хост не подвесит воркер (по исчерпании бюджета оставшиеся ссылки просто остаются на thumbnail'ах). Аналогично from_image-ссылки выведены из-под «раскопочного» капа `max_links_per_page` — кап теперь применяется только к обычным ссылкам (галерея с >200 миниатюрами не теряет ни одной). Возвращает `(discovered, resolved_thumbnails, consumed_links)`. Ссылки с JS-only правилом (напр. catch-all `[MediaGrabber]` матчит любой URL) **не потребляются** — остаются обычному краулу. `_process_parser_results`: discovery выполняется ДО постановки медиа в очередь; найденный fullsize вытесняет thumbnail из батча; потреблённые ссылки не идут в краул.
   - **Доменные проверки по принципу автора** — медиа-ссылки и from_image-переходы больше НЕ ограничиваются: `_is_downward_url` ранний `True` для `from_image`/`is_media_url`; `stay_in_domain` в `_process_parser_results` применяется только к "раскопкам" (обычные ссылки). Ограничение: discovery запускается **только для внешних** переходов (`not is_same_domain(u, start_url)`) — своидоменные ссылки идут обычным краулом (WebpageParser обрабатывает lazy/data-src галереи лучше, чем res/img-scan fallback — замечание ревьюера учтено).
   - **Семафор домена** — `_process_parser_results` (discovery + постановка медиа) вынесен ЗА пределы source-domain семафора в `_parser_worker` (probe'ы ходят на другие домены; страница с сотней ссылок больше не блокирует парсинг исходного домена — замечание ревьюера учтено).
   - **Приоритеты:** `sieve-res`/`link-direct` получили boost ×3 в `_get_media_priority` (fullsize раньше thumbnail'ов).
   - **Живая проверка:** 5 реальных imx.to-ссылок с тред-страницы → все 5 resolved в `image.imx.to/u/i/...` (src: sieve-res) за 2.5с; thumbnail'ы помечены на удаление. **87 passed, 1 skipped** (+14 новых тестов: цепочка imx.to, url-трансформ, res-извлечение, доменные исключения, интеграция discovery через `_process_parser_results` с фейковой сессией, своидоменная не-потреблённость); pyflakes чист (E701/E702 в `ruff` — предсуществующий компактный стиль, не новый код); exe пересобран + смоук-тест OK.

12. **КРИТИЧЕСКИЙ БАГ — полное зависание загрузки на 5+ минут (обнаружен по логу реального запуска, исправлен).** Симптомы: на том же vipergirls-треде fullsize-дискавери находит 62 полных изображения, но приложение «зависает» — 5 минут бездействия, ни одного скачанного файла, остановка только вручную. Трассировка лога (`dist/WebMediaParser/web_media_parser.log`): 21 предмет взят downloader-воркерами, 13 провалились `Webpage/script content`, 8 зависли без единого лога; парсер ушёл «парсить» `image.imx.to/u/i/...jpg` как HTML-страницу. **Две причины:**
   - **HEAD не следует редиректам** (`requests.head()` по умолчанию `allow_redirects=False`): photo-host `image.imx.to` отвечает `302` на CDN `i.imx.to/i/...jpg` с `Content-Type: text/html` на самом 302. Приложение видело «HTML-страницу» → `Webpage/script content` → фейл (13 шт). Проверено вживую: обычный `requests.head` → 302+text/html; тот же `head(..., allow_redirects=True)` → **200 image/jpeg 3.7 МБ** — настоящая картинка.
   - **WebpageParser пытался парсить JPEG как HTML** (ре-квеей «Targeting photo-host landing page» после фейла): `process_js=True` на 3.7 МБ бинарника → **зависание навсегда** (воспроизведено: >180с, в изоляции без process_js — 3.8с). Зависший парсер **держал слот domain-семафора** `image.imx.to` (лимит 2) → 8 downloader-воркеров вечно ждали `sem.acquire()` → мёртвый стоп всей очереди.
   - **Фикс (2 уровня):**
     - `MediaDownloader._do_download`: HEAD-запрос теперь с `allow_redirects=True` — редирект на CDN разрешается на этапе HEAD (Content-Type, размер), GET скачивает напрямую. Это устраняет ложные `Webpage/script content` и ре-квееи.
     - `WebpageParser._get_content`: guard на `Content-Type` `image/*|video/*|audio/*` — бинарный медиа-ответ НЕ читается и НЕ парсится как HTML (возврат пустого успешного результата). Парсер больше не может зависнуть на JPEG/MP4-«прокладке» и не блокирует семафор домена.
   - **Валидация:** живое воспроизведение обоих фиксов — HEAD+GET через ту же shared-session (`Retry(total=0)`) → 200 image/jpeg, magic `FF D8 FF E0`; WebpageParser на той же прокладке → 2.7с (было >180с зависание). **88 passed, 1 skipped**; compileall OK; pyflakes чист (brotli-импорт — предсуществующий side-effect, `# noqa`); exe пересобран + смоук OK (GUI жив 8с). **Примечание:** `build_exe.py` штатно очищает `dist/` (в т.ч. лог и `settings.json`); после сборки `settings.json` восстановлен из корневого (путь к sieve `Imagus_sieve_2026.07.15_823.json` сохранён).
