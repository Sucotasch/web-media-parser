# GATES.2026-10-15.md — Acceptance ledger for FIXPLAN_2026-10-15

Baseline for this plan: `319 passed / 30 skipped` (measured 2026-10-15 before any change).
One observable outcome per gate; CHECK commands run from the project root, EXPECT is matched
against the combined output. Format follows `GATES.md`.

**Status: stage 0 — 14/14 met; stage 1 — 13/13 met; stage 2 — 8/8 met; stage 3 — 12/12 met
(+ the stage sweep). 0 unmet, 0 abandoned. Measured values below; current tree `443 passed /
30 skipped`.**

Stage 0 is gates-and-tests only (FIXPLAN_2026-10-15 §3/Этап 0): no production behaviour is
changed, so every item below is either a new test, a packaging rule, or a doc correction.
Stage 1 applies the targeted fixes (FIXPLAN_2026-10-15 §3/Этап 1); the one item with a
user-visible effect is called out in G-1-7.

---

## Stage 0 — gates and safety tests

### G-0-1 · A-01 · the bundled pattern file is trackable
- CHECK: `git check-ignore -v resources/patterns/site_patterns.json` then `git ls-files resources/patterns/site_patterns.json`
- EXPECT: `check-ignore` shows the negated rule `!resources/patterns/site_patterns.json`; `ls-files` prints a non-empty path.
- RESULT: **MET** — `.gitignore:190:!resources/patterns/site_patterns.json`; `git ls-files` prints
  `resources/patterns/site_patterns.json` (added with `git add -f`). Stray dev copies of the name
  stay ignored.

### G-0-2 · F-11 · spec and node_modules rules behave
- CHECK: `git check-ignore -v WebMediaParser.spec node_modules/x.js`
- EXPECT: the spec is NOT ignored (negated rule matches); `node_modules/` IS ignored.
- RESULT: **MET** — `!WebMediaParser.spec` matched by `.gitignore:37`; `node_modules/x.js` matched by
  `.gitignore:193:node_modules/`. (`bin/deno.exe` intentionally stays ignored: it is a bundled
  third-party binary, restored by `build_exe.py` / the documented download step, not committed.)

### G-0-3 · F-10 / E-09 · dev tools have a declared home
- CHECK: `python -c "import ast,io;t=ast.parse(io.open('setup.py',encoding='utf-8').read());print([k.arg for n in ast.walk(t) if isinstance(n,ast.Call) for k in n.keywords])"` and `python -m ruff --version`
- EXPECT: `extras_require` present; ruff resolves in an environment installed from `.[lint]`.
- RESULT: **MET** — `extras_require = {"test": ["pytest>=8.0"], "lint": ["ruff>=0.11"]}`;
  `python -m ruff --version` → `ruff 0.11.13`.

### G-0-4 · E-07 · the corrected ledger pairs are runnable
- CHECK: the four corrected pairs at the top of `GATES.md`
- EXPECT: each pair's EXPECT is reachable on the current tree.
- RESULT: **MET** — `tests/test_gateway_bypass.py` holds `test_referer_from_context_source_url`;
  `tests/test_app_paths.py` reports `3 passed`; the ruff selection now includes `E401` and expects
  exactly the three intentional `webpage_parser.py:35-37` E402 lines.

### G-0-5 · E-02 · the sieve dedup test can fail
- CHECK: `python -m pytest tests/test_pattern_manager.py -q`
- EXPECT: a real dedup invariant (loaded rules ≤ unique rule names across the loaded files) plus a
  new two-file dedup test; the vacuous `total < 1000` is gone.
- RESULT: **MET** — `test_default_loader_finds_sieve_and_dedups` now compares against the unique
  rule names parsed from `Imagus_sieve_*.json`; `test_sieve_dedup_drops_duplicate_rule_names`
  loads two real sieve files and asserts 2 → 3 rules (duplicate name counted once).

### G-0-6 · E-03 · a missing sieve prerequisite fails loudly
- CHECK: `python -m pytest tests/test_fullsize_discovery.py -q` with `SIEVE_PATH` pointing at a
  non-existent file (or read `tests/helpers.py:require_sieve`)
- EXPECT: `AssertionError: Imagus sieve snapshot missing: …`, not a `TypeError` from inside a test.
- RESULT: **MET** — `helpers.require_sieve()` is called in `setUpClass`; the old failure mode was
  `TypeError: 'NoneType' object is not subscriptable` deep inside a test.

### G-0-7 · E-04 · the `data:` guard is actually exercised
- CHECK: `python -m pytest tests/test_fullsize_discovery.py -q -k data_template`
- EXPECT: one test asserts `None` for the no-engine configuration; a second drives a JS-engine
  double that returns `data:,evil` and asserts the guard returns `None`.
- RESULT: **MET** — `test_data_template_rejected_js_branch` (exact `None`) and
  `test_data_template_rejected_when_js_engine_returns_data` (guard at `site_pattern_manager.py:1069`).

### G-0-8 · F-08 / F-09 · test hygiene
- CHECK: `python -m pytest tests -q` twice and compare `%TEMP%` before/after
- EXPECT: no new `wmp_*` directories; no assertion that cannot fail.
- RESULT: **MET** — `mkdtemp` leaks replaced with `TemporaryDirectory` + `addCleanup` in
  `test_fullsize_discovery.py` (3 sites) and `test_task_queue_persistence.py` (1 site); the four
  tautologies (`assertIsInstance(links, set)`, assertion-free bounded-scan test,
  `assertIn(result, (True, False))`) replaced with value assertions.

### G-0-9 · E-01 · `build_exe.py` is cwd-independent and fails on a locked `dist/`
- CHECK: `python -c "import ast;ast.parse(open('build_exe.py').read())"` and read the clean step
- EXPECT: the script anchors to its own directory before any relative path is used; a failed
  `shutil.rmtree` aborts the build instead of warning.
- RESULT: **MET** — `os.chdir(os.path.dirname(os.path.abspath(__file__)))` runs at import;
  the `PermissionError` warning branch is now a `SystemExit` naming the running `.exe`.

### G-0-10 · F-13 · session-state round trip
- CHECK: `python -m pytest tests/test_parser_manager_state.py -q`
- EXPECT: `5 passed`; a save → load round trip restores set/queue/stats state, no `*.tmp` remains,
  a missing or corrupt pickle is a swallowed no-op, and task ids stay isolated.
- RESULT: **MET** — `5 passed in 0.90s`. Before this file `save_state` had zero test coverage and
  every `load_state` mention in the suite was an `AsyncMock`.

### G-0-11 · F-14 · Stop branches and progress reporting
- CHECK: `python -m pytest tests/test_media_downloader.py -q`
- EXPECT: `32 passed`; new tests cover Stop before the attempt (no request at all), Stop mid-stream,
  Stop during the ranged multi-thread phase (both chunk threads abort, no published file), the
  `_download_with_threads` early return, and `progress_callback` on both paths.
- RESULT: **MET** — `32 passed in 11.88s` (6 new). Measured along the way: an operator Stop is
  reported without a `failure_kind` (never penalises the host); the MT phase leaves no `*.part*`,
  while the single-thread Stop path leaves one `*.partial` because the Stop branch calls
  `os.remove()` on its own open file (Windows refuses, `OSError` swallowed) — **Этап 2's** target
  (`test_download_integrity.py`), not fixed here.

### G-0-12 · F-15 · Deno worker lifecycle
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q`
- EXPECT: `13 passed` with or without Deno on the machine; lazy spawn + reuse, respawn after death,
  broken pipe → fail-open + reap, `while(true)` → killed at the timeout (bounded wall clock),
  terminate → kill escalation, 64 KB payload intact, idempotent shutdown.
- RESULT: **MET** — `13 passed in 0.26s` (no skips: the suite drives a fake process, so it does not
  inherit the 30 Deno skips).

### G-0-FULL · Stage 0 regression sweep
- CHECK: `python -m pytest tests -q` (Python), `node extension/tests/<file>.test.mjs` (all 12),
  `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401 --output-format=concise`,
  `python -m pyflakes tests/*.py build_exe.py setup.py`
- EXPECT: all green; test count strictly greater than the 319 baseline; no new skips; ruff reports
  only the three intentional E402 lines; pyflakes silent.
- RESULT: **MET** — Python `345 passed, 30 skipped in 22.11s` (baseline 319/30, **+26 tests, same
  30 skips**; all 30 are Deno, verified with `pytest -rs`); node 12/12 `ALL PASS`; ruff reports
  exactly 3 × E402 `webpage_parser.py:35-37`; pyflakes exit 0.

### G-0-13 · E-06 · doc drift corrected
- CHECK: `grep -n "state.pkl\|265 passed\|install_requires is" knowledge.md CONTEXT.md README.md README_EN.md GATES.md PLAN.md AGENTS.md`
- EXPECT: no claim that the state file is `state.pkl`, no undated 265/180 baseline, no "stale
  setup.py"; skip reasons describe only the real cause.
- RESULT: **MET** — `state.pkl` renamed in README/README_EN/knowledge/CONTEXT (the anchor for
  Stop's cleanup step), module count 15/21 → 24, baselines re-dated against a re-measured
  `345 passed / 30 skipped`, the `curl_cffi missing`/`network placeholder` skip legend replaced
  with the measured "all 30 are Deno", and `QWEN.md` references dropped. `setup.py` vs
  `requirements.txt` parity re-verified by AST: 13 entries each, identical.

### G-0-14 · A-02 · the persistence note is honest
- CHECK: read the A-02 note in `CONTEXT.md` §8
- EXPECT: one dated paragraph stating that `last_session.pkl` is a plain pickle that carries
  gateway/age-gate cookies, and why no encryption/filtering is applied.
- RESULT: **MET** — the note records the 2026-10-15 product decision (local single-user contour;
  filtering would break resume on age-gated/authorised pages) and tells the user not to publish
  their download directory.

---

## Stage 1 — targeted fixes (no user-visible change, one documented exception)

Baseline entering the stage: `345 passed / 30 skipped` (stage 0 exit).

### G-1-1 · B-01 · the saved state cannot be mutated mid-pickle
- CHECK: `python -m pytest tests/test_parser_manager_state.py -q -k snapshot`
- EXPECT: the pickled payload keeps the value observed under `_processed_lock`, not a later write.
- RESULT: **MET** — `save_state` snapshots `stats` / `domain_health` (dict copies) inside the lock;
  the test mutates both from inside a `pickle.dumps` spy and asserts the file still holds the
  snapshot. Mutation (live references restored) → RED.

### G-1-2 · B-14 · one-shot warnings under concurrency
- CHECK: `python -m pytest tests/test_http_engine.py -q -k warning`
- EXPECT: a stress run (16 threads × 10 rounds) logs each warning once, and two deterministic
  lock-contract tests fail if the flag decision is not protected.
- RESULT: **MET** — `_warn_lock` guards both one-shot flags. The stress test alone is *not* a race
  detector (measured: stays green without the lock — the GIL keeps the window too narrow); the two
  "lock held by the test" tests are the detectors, and both go RED without it.

### G-1-3 · B-15 · a worker that refuses to die is visible
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k "TestWorkerDiagnostics or TestWorkerReaping"`
- EXPECT: pipes closed, reap failures logged, the surviving handle kept (no duplicate spawn).
- RESULT: **MET** — `_kill_worker` closes stdin/stdout (and a foreign stderr), logs reap failures,
  and `_kill_proc` keeps `self._proc` while `poll()` is not None. Three mutations → RED.

### G-1-4 · B-19 · worker stderr reaches the log
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k stderr`
- EXPECT: `stderr` is a real file (not `DEVNULL`), the tail is logged once, empty output is silent.
- RESULT: **MET** — per-engine `TemporaryFile`, `_log_stderr_tail()` on spawn failure / reap failure /
  survive; four mutations (kwarg, missing call, one-shot flag, empty guard) → RED.

### G-1-5 · B-16 · a deadline response is not thrown away
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k grace`
- EXPECT: the answer lands, the worker is not killed; a genuinely stuck rule is still killed.
- RESULT: **MET** — the grace re-check keeps a response that arrives after the deadline. The first
  form released the reader through a `threading.Timer` at exactly the moment the grace expired, so it
  had no margin and failed once under full-suite load (stage 2 found it RED in a 394-test run).
  Re-timed: the reader sleeps 10 ms past its own 200 ms deadline, i.e. 40 ms inside the 50 ms grace.
  The failing side is deterministic (`time.sleep` never returns early → RED in every run without the
  grace), the passing side tolerates >4x the usual scheduler jitter. 5/5 isolated runs green, 2/2
  full-suite runs green.

### G-1-6 · F-12 · bounded dedup memory, dead state removed
- CHECK: `python -m pytest tests/test_crawler_frontier.py -q -k TestSeenMemoryBound`
- EXPECT: `_seen` stops growing at `K.MAX_SEEN_URLS`, oldest-first eviction, `_url_scores` gone.
- RESULT: **MET** — `4 passed` (module total 26). Mutations (eviction disabled; bypassing the
  bounded helper) → RED.

### G-1-7 · F-01 · one source of settings defaults
- CHECK: `python -m pytest tests/test_settings_defaults.py -q`
- EXPECT: the dialog spreads `K.DEFAULT_SETTINGS_VALUES`, defines no canonical key itself, and every
  declared `SETTING_*` has a default.
- RESULT: **MET** — `5 passed`. The dialog's 30-key copy is gone; the 8 keys it used to omit now
  come from K, `SETTING_PROXY` gained a default, and `K.DEFAULT_STOP_WORDS` became the deduplicated
  union (26 words) of the two lists. **Only user-visible effect in stage 1:** a missing/invalid
  `stop_words` value (settings.json absent or corrupt) now yields the 26-word list users already got
  from the dialog instead of silently narrowing the crawl filter to 9 words. Normal installs read
  their own settings.json and are unaffected. Mutations → RED.

### G-1-8 · F-02 · history clear goes through the queue API
- CHECK: `python -m pytest tests/test_task_queue_persistence.py -q -k TestHistoryClearUsesQueueApi`
- EXPECT: `clear_active()` emits `active_task_changed`, and `main_window` no longer touches
  `task_queue._queue` / `task_queue._active_id`.
- RESULT: **MET** — `3 passed`. Mutations (internals restored; emit removed) → RED.

### G-1-9 · C-01 · a busy extension port is reported
- CHECK: `python -m pytest tests/test_gui_wiring.py -q -k PortConflict`
- EXPECT: a second server on the same port raises, records `start_error`, and a later successful
  start clears it.
- RESULT: **MET** — two real `ExtensionServer`s on one port; the GUI half (signal + status bar +
  `QMessageBox.warning`) is source-pinned because instantiating `MainWindow` in a test would bind
  19876 and rewrite the real `task_queue.json`. Mutation (error not recorded) → RED.
  This is the plan's "two instances" check, exercised in-process instead of by hand.

### G-1-10 · C-03 · sieve download targets the visible path
- CHECK: `python -m pytest tests/test_gui_wiring.py -q -k VisiblePath`
- EXPECT: an unsaved widget path is the download target and lands in `self.settings`; an empty path
  asks for one instead of downloading.
- RESULT: **MET** — a real `SettingsDialog` driven under `QT_QPA_PLATFORM=offscreen` with a pattern-
  manager double that records the target. Mutation (stale settings value) → RED.

### G-1-11 · C-06 · the replaced QThread is deleted by Qt
- CHECK: `python -m pytest tests/test_gui_wiring.py -q -k qthread`
- EXPECT: `old_thread.finished.connect(old_thread.deleteLater)` before the reference is dropped.
- RESULT: **MET** — source-pinned (see G-1-9 for why MainWindow is not instantiated). Mutation → RED.

### G-1-12 · C-09 · the queue is persisted as it changes
- CHECK: `python -m pytest tests/test_gui_wiring.py -q -k "autosave or SignalsThatDrive"`
- EXPECT: add/remove/status all mark the queue dirty, the 2 s timer writes it, and the manager
  really emits those signals.
- RESULT: **MET** — the GUI half is source-pinned, the manager half is a runtime test (signals fire;
  `save()` is atomic and round-trips). Mutation (autosave call removed) → RED.

### G-1-FULL · Stage 1 regression sweep
- CHECK: `python -m pytest tests -q`, `node extension/tests/<file>.test.mjs` (all 12),
  `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401 --output-format=concise`,
  `python -m pyflakes tests/*.py build_exe.py setup.py`, plus a real boot
  (`QT_QPA_PLATFORM=offscreen python main.py`)
- EXPECT: all green; count strictly greater than the 345 baseline; no new skips; boot without a
  traceback and the extension server on 19876.
- RESULT: **MET** — Python `380 passed, 30 skipped in 24.75s` (**+35 tests**, same 30 Deno skips);
  node 12/12 `ALL PASS`; ruff reports exactly the 3 intentional `webpage_parser.py:35-37` E402;
  pyflakes clean. Boot: settings loaded, `Extension API server started on
  http://localhost:19876`, 0 occurrences of `Traceback`/`Error` in the log; the process was then
  stopped and the port verified free again.

---

## Stage 2 — the media write path (FIXPLAN_2026-10-15 §3/Этап 2)

One pass over `MediaDownloader._do_download` / `_download_chunk` / `_download_with_threads`
(A-07, B-08, B-09, B-10, B-11, B-12, B-13, NF-3 + NF-8) plus the new settings
(`K.SETTING_MAX_FILE_SIZE`, `K.SETTING_REQUIRE_VERIFIED_LENGTH`, `K.SNIFF_PREFIX_BYTES`).
New module `tests/test_download_integrity.py` (18 tests). Baseline entering the stage:
`394 passed / 30 skipped`.

### G-2-1 · A-07 · one file can no longer fill the disk
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k "the_cap or chunks"`
- EXPECT: a declared length over the cap is refused before the body is requested; a stream that
  crosses the cap is stopped before the write; the MT chunk loop honours the same cap; a local
  policy carries no `failure_kind` (so domain health is untouched).
- RESULT: **MET** — `3 passed`. On the wire (scratch raw-socket harness, `d:/tmp/vfy_p2_write2.py`,
  not committed — it builds its own server and downloader, so it is reproducible from this
description alone): a host
  declaring 60 bytes and then streaming ~10 MB close-delimited is stopped at a 64 KB cap with an
  empty download folder; a 1024 KB cap refuses a declared 8 MB body before the GET. Defaults: the
  code fallback is 0 = unlimited (hand-built settings dicts stay uncapped) while
  `K.DEFAULT_SETTINGS_VALUES` ships 2 GiB (2097152 KB) for a fresh profile; the dialog shows and
  edits the same value (offscreen smoke: spinbox 2097152, `get_settings_from_ui()` returns it,
  `sanitize_settings({'max_file_size': 'abc'})` → 2097152).

### G-2-2 · B-11 · a page shell is sniffed before the first write
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k "sniff or text_plain or xml"`
- EXPECT: an HTML login page served as `text/plain` is refused with `FAILURE_CONTENT_PAGE` and
  leaves nothing on disk; a user-enabled `image/svg+xml` still downloads; `<?xml` from a non-image
  type is refused.
- RESULT: **MET** — `3 passed`. Wire measurement: the 51-byte login page (the audit's own input)
  comes back `Body is not media (sniffed page content)` / `content_page`, folder empty; the SVG
  control is byte-identical on disk. Sniffing is re-checked on every chunk until the 512-byte window
  fills, so a body shorter than one chunk is still inspected.

### G-2-3 · B-12 / NF-8 · bodies without a verifiable length
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k "unverified or chunked or strict or empty"`
- EXPECT: close-delimited → `success` + `unverified_length: True`; chunked → trusted, no flag;
  the opt-in strict mode refuses; an empty body is never a success.
- RESULT: **MET** — `4 passed`. Wire measurement: 1000 bytes with no `Content-Length` anywhere →
  `success` + `unverified_length: True` + a warning line; the same body sent chunked → `success`
  without the flag. Strict mode is a real setting (`require_verified_length`, bool-normalised in
  `sanitize_settings`), not a hidden constant.

### G-2-4 · B-13 · Stop is not held by the retry backoff
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k backoff`
- EXPECT: a Stop landing inside a 2.0 s backoff exits in well under half a second.
- RESULT: **MET** — `1 passed`; the measured delay from the Stop to the returned result is
  **< 0.5 s** (the assertion), against the 2.36 s the audit measured on the old unconditional
  `time.sleep(0.5 * attempt)`.

### G-2-5 · B-10 / NF-3 · the multi-threaded assembly is atomic and leaves no residue
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k mt_`
- EXPECT: the final name is published by exactly one `os.replace` from `*.mt.partial` and is never
  opened for writing; a failed assembly leaves nothing at the final name; a Stop during the chunk
  phase leaves the download folder empty.
- RESULT: **MET** — `3 passed`. The pre-change code run (stash, same tests) shows the residue
  `['big.jpg.1fb8.partial']` after a Stop-during-MT download — the Windows open-handle defect — and
  `os.replace` called 0 times (the assembly wrote the final name directly). `cleanup_partial_files`
  already matched `*.partial` (which covers `*.mt.partial`) and `*.partN`; the sweeper test now pins
  `video.mp4.a1b2.mt.partial` while `user.partial.png` still survives (GUI-9, no `*.part*` widening).

### G-2-6 · B-08 · nothing is written outside the download root
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k "outside or subdir"`
- EXPECT: a `..` destination is refused by the downloader without a host penalty; a page URL
  carrying dot segments cannot build `..` subdirectories.
- RESULT: **MET** — `2 passed`. On the pre-change code both go RED, the parser one with the measured
  path `test_downloads\startdomain.com\..\..\a.jpg`.
  **Correction found while testing:** the first version of the downloader guard ran the check on
  `os.path.normpath(filepath)`, which resolves the segments away — measured: the `..` destination
  downloaded successfully to the parent folder. The guard now inspects the raw segments.

### G-2-7 · B-09 · the download session follows `verify_tls`
- CHECK: `python -m pytest tests/test_download_integrity.py -q -k tls` and
  `python -m pytest tests/test_http_engine.py -q`
- EXPECT: the shared and own sessions carry `http_engine.tls_verify(..., legacy_default=False)`
  (verification ON unless the user turned it off); the escalation GET stays verified.
- RESULT: **MET** — `2 passed` + the http-engine module green. **A regression of this stage was
  caught here by the full suite**: the `session.verify` assignment had been placed inside the
  `if proxy_url:` block together with `return session`, so a session without a proxy returned `None`
  (`test_shared_downloader_session_default_is_requests` → `AssertionError: isinstance(None, …)`).
  Fixed, and the new test pins both the setting and "a session is returned at all".

### G-2-8 · Stage 2 regression sweep
- CHECK: `python -m pytest tests -q`, `node extension/tests/*.test.mjs`,
  `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401,F401 --output-format=concise`,
  plus the wire run `d:/tmp/vfy_p2_write2.py` and an offscreen settings-dialog smoke
- EXPECT: all green, no new skips, no new lint findings.
- RESULT: **MET** — Python `398 passed, 30 skipped` (**+18 this stage, +79 since the 319 baseline**;
  same 30 Deno skips); node `ALL PASS`; ruff reports exactly the 3 intentional
  `webpage_parser.py:35-37` E402 (the F401 set is clean after the `threading` import the re-timed
  grace test no longer needs); wire run **7/7**; dialog smoke builds the new widgets with the 2 GiB
  default. Real boot (`QT_QPA_PLATFORM=offscreen python main.py`): settings loaded, empty queue,
  `Extension API server started on http://localhost:19876`, `GET /api/status` and `GET /api/queue`
  answered `200 {"active_task": null, "queue_length": 0, "files_downloaded": 0}`, an empty
  `POST /api/tasks` answered the intended `400 No valid URLs provided`, 0 tracebacks in the process
  log; the process was then stopped and port 19876 verified free again.

### Measurement caveat (this machine)
`requests` reported `Transfer-Encoding: chunked` for responses the local server had sent
close-delimited. A raw-socket server that writes no TE header at all reproduced it, and the trigger
is the **browser User-Agent**: default UA → bytes unchanged, Chrome UA → the response arrives
re-framed as chunked (a security-product/DPI web filter on this machine rewrites responses for
browser-like clients). Consequences for this stage: (1) the framing-sensitive wire cases pin a
neutral UA so the wire carries what the server wrote (`NEUTRAL_UA` in `d:/tmp/vfy_p2_write2.py`);
(2) requests' TE report is *correct* in both directions — the chunked branch really saw chunk
framing — so the B-12 classification stands. Without the neutral UA the close-delimited case simply
cannot be produced over loopback here; it is covered by the unit test's response double.

---

## Stage 3 — the parser (FIXPLAN_2026-10-15 §3/Этап 3)

A-03, A-04, A-05, A-06, B-03 … B-07, D-10, D-11, D-12. Baseline entering the stage:
`398 passed / 30 skipped`. New test files: `tests/test_parser_extraction.py` (+23); extended:
`test_parser_manager_state.py` (+2), `test_crawler_frontier.py` (+2), `test_parser_manager_filtering.py`
(+3), `test_url_detection.py` (+5), `test_pattern_manager.py` (+3), `test_junk_filter.py` (+4).

### G-3-1 · A-03 · the page/download in flight survives a Pause
- CHECK: `python -m pytest tests/test_parser_manager_state.py -q -k InflightRestore`
- EXPECT: the interrupted page is back in the URL queue and the interrupted download back in the
  download queue after save → load; a task with nothing in flight restores empty queues.
- RESULT: **MET** — `2 passed`. `_inflight_url_item` / `_inflight_media` are set when a worker
  consumes an item and cleared in its `finally`; both are in the snapshot and re-queued by
  `load_state` (they were already consumed, so re-queuing cannot duplicate anything). Mutations
  (key not saved; restore disabled) → RED.

### G-3-2 · A-04 · a blocklist entry covers the whole site
- CHECK: `python -m pytest tests/test_parser_manager_filtering.py -q -k DomainBlocklist`
- EXPECT: `www.`/`img.`/`a.b.` subdomains of a blocked domain are not queued, a look-alike host that
  merely contains the name still is, and the loader normalizes case/`*.`/IP entries.
- RESULT: **MET** — `3 passed`. Suffix matching on the registrable-name boundary replaced the raw
  netloc comparison (the audit measured 6 subdomains crawled). An IP entry stays literal: deriving a
  base domain from `1.2.3.4` would have produced `3.4`. Mutation (raw netloc compare) → RED.

### G-3-3 · A-05 · a URL dropped by the relationship gate keeps its dedup slot
- CHECK: `python -m pytest tests/test_crawler_frontier.py -q -k ZeroPriority`
- EXPECT: a cross-domain URL dropped by the zero-priority gate is NOT in `_seen`, and the same URL
  arriving later as a media lookup (`from_image`) is queued; really-queued URLs are still deduped.
- RESULT: **MET** — `2 passed`. `_mark_seen` moved after the priority gate, before the heap push.
  Mutation (pre-fix order restored) → RED.

### G-3-4 · A-06 · a protocol-relative `to` gets exactly one scheme
- CHECK: `python -m pytest tests/test_pattern_manager.py -q -k protocol_relative`
- EXPECT: no `:////` in the output; the shipped Imgflip-h-p rule resolves to
  `https://i.imgflip.com/abc123.jpg`.
- RESULT: **MET** — `2 passed`. The regex branch and the `to_callable` branch now handle a leading
  `//` before falling back to the page scheme (the JS branch already did). Mutation → RED.

### G-3-5 · B-03 · the page's declared encoding wins over statistics
- CHECK: `python -m pytest tests/test_parser_extraction.py -q -k DeclaredCharset`
- EXPECT: BOM → `<meta charset>` / `http-equiv` in the first 4 KB → statistical detection over a
  wider window; a generated-name or unknown codec never reaches `decode()`.
- RESULT: **MET** — `7 passed`. A >16 KB ASCII prefix 1251 page decodes back to Cyrillic (the audit's
  repro; the old 2 KB chardet window reported ascii). `chardet`'s answer is validated through
  `codecs.lookup` (`MacRoman` → `mac-roman`, `NoSuchCodec-9000` → `utf-8`). 4 mutations → RED.

### G-3-6 · B-04 · every exit path of `parse()` releases the sync session
- CHECK: `python -m pytest tests/test_parser_extraction.py -q -k SyncSessionClosed`
- EXPECT: the error-status return and the no-content return both close it (`_sync_session is None`);
  a broken `close()` does not break the parse.
- RESULT: **MET** — `3 passed`. 2 mutations → RED.

### G-3-7 · B-05 · the inner `<img>` of a `<picture>` is collected once
- CHECK: `python -m pytest tests/test_parser_extraction.py -q -k picture`
- EXPECT: no duplicate URL, and no variant lost (the `<picture>` branch expands `srcset` fully).
- RESULT: **MET** — `1 passed`. Skipped by containment in `<picture>` (`id()`, not by URL — two
  unrelated `<img>` may legitimately share a URL). Mutation → RED.

### G-3-8 · B-06 · text attributes are not media URLs
- CHECK: `python -m pytest tests/test_parser_extraction.py -q -k "alt_text or text_attributes or unrelated_attribute or free_text"`
- EXPECT: `<img alt="sunset.jpg">` yields no candidate; title/aria-label/class/id are not URLs; a
  non-URL-shaped custom attribute and free text in a `data-*` attribute are rejected; a relative
  `data-*` path still works.
- RESULT: **MET** — `5 passed` (+1 guard that a real `src` still wins). 3 mutations → RED.

### G-3-9 · B-07 · an empty 2xx body is an empty page, not a failure
- CHECK: `python -m pytest tests/test_parser_extraction.py -q -k EmptyBody`
- EXPECT: `200` + empty body → `PARSER_SUCCESS` with an empty parse, no sync fallback (no second
  request); an empty `404` stays an HTTP error.
- RESULT: **MET** — `2 passed`; `_sync_session is None` proves the second request never happened.
  Mutation → RED.

### G-3-10 · D-10 · one ad/junk token table for both gates
- CHECK: `python -m pytest tests/test_junk_filter.py -q -k "token_table or gates_agree or legitimate_paths or widening"`
- EXPECT: `/impressions/1.jpg` and `/impressions/1` are both junk; `pixel-art`, `downloads`,
  `impressionist-paintings`, `forum.php` stay crawlable.
- RESULT: **MET** — `7 passed`. The table is now `utils._AD_KEYWORDS` (the union of the two former
  tables), `junk_filter` delegates to it and the crawl gate applies it. **Visible consequence,
  measured and pinned by its own test:** the matcher splits on non-word characters, so a hyphenated
  slug carrying a token (`/album/impressions-of-iceland/1`) is now skipped by the crawl gate too —
  the media gate already dropped its images. `AD_PATH_TOKENS` is gone. Mutation → RED.

### G-3-11 · D-11 · one media-type classifier for every discovery route
- CHECK: `python -m pytest tests/test_url_detection.py tests/test_parser_extraction.py -q -k "media_type or MediaTypeRoutes"`
- EXPECT: `clip.mov`/`movie.mkv` → video, `song.mp3` → audio on the JSON route AND on the HTML
  route (a `data-*` attribute); the two route results are identical for the same URL.
- RESULT: **MET** — `5 passed` + `4 passed`. `utils.guess_media_type` holds the tables;
  `json_parser._guess_media_type` delegates and the four inline copies in `webpage_parser` are gone
  (the F401 ruff finding for the now-unused `urlparse` import was fixed, not suppressed).
  2 mutations → RED.

### G-3-12 · D-12 · an uppercase host finds its sieve rules
- CHECK: `python -m pytest tests/test_pattern_manager.py tests/test_url_detection.py -q -k "uppercase or lowercased"`
- EXPECT: `I.4CDN.ORG` and `BOARDS.4CHAN.ORG` produce the same variant set as their lowercase forms.
- RESULT: **MET** — `3 passed`. `get_domain` and `priority_url_queue._get_domain` return the
  lowercase netloc. Measured before and after: **1 variant** for the uppercase host (rule lookup
  missed entirely) vs **6**, and 6 after the fix. Mutation → RED.

### G-3-FULL · Stage 3 regression sweep
- CHECK: `python -m pytest tests -q`, `node extension/tests/*.test.mjs` (all 12),
  `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401,F401`, `python -m pyflakes tests/*.py build_exe.py setup.py`
- EXPECT: all green, no new skips, no new lint findings.
- RESULT: **MET** — Python `443 passed, 30 skipped in 27.7s` (**+45 this stage**, same 30 Deno skips);
  node 12/12; ruff reports exactly the 3 intentional `webpage_parser.py:37-39` E402 (the lines moved
  by the two lines added at the top of the file: `import codecs` — the finding count is unchanged);
  pyflakes silent.

**Falsification, stage 3: 18 mutations, all RED** (3 state/queue items, A-06, 4 encoding, 2 session,
1 picture, 3 attribute, 1 empty-body, 1 token-gate, 2 media-type, 1 domain-case).

---

## Verification method note

The new tests were falsified before being trusted: **17 targeted mutations** of production code
across stages 0-1 (`load_state` set restore; `_kill_worker` kill escalation; `download()`early Stop
check; the MT chunk `progress_callback`; B-01 live references; both B-14 lock removals; B-15 handle,
pipe-close and reap-log; B-19 stderr kwarg, tail call, one-shot flag and empty guard; B-16 grace;
F-12 eviction and helper bypass; F-01 dialog re-definition and missing default; F-02 internals and
emit; C-01 error recording; C-03 stale path; C-06 deleteLater; C-09 autosave) each made the
corresponding test go RED, and every mutation was reverted — `git diff` shows no production change
in stage 0 and only the intended files in stage 1. Two first attempts were rejected as vacuous
(B-14's stress-only race test, B-16's 5 ms timing margin) and were replaced by real detectors before
being counted. Untested-but-asserted behaviour was measured first: the RESULT lines carry the
measured numbers.

Stage 2 was falsified the same way and at the module level: `git stash` of the changed production
files and running the new module against the pre-change tree → **11 of its then-14** downloader
tests RED
(the three green ones are deliberate guards: the user-enabled-SVG control, the chunked-framing
control, and "a chunk failure leaves no truncated file", which the old code happened to satisfy
because it removed its part files). After the module grew to 18 tests, the B-08 pair was re-run the
same way against the pre-change `parser_manager.py`/`media_downloader.py` → both RED, the parser one
printing the escaping path it had built. Two defects were found *by* this stage's own verification
rather than by inspection: the `session.verify` placement that returned `None` from
`create_shared_downloader_session` (caught by the existing http-engine test, now pinned by a new one)
and the `os.path.normpath`-based traversal guard that could never fire (caught by its own new test).
The flaky grace-window timing test inherited from stage 1 was found here too and re-timed.
