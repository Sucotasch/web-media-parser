# GATES.2026-10-15.md — Acceptance ledger for FIXPLAN_2026-10-15

Baseline for this plan: `319 passed / 30 skipped` (measured 2026-10-15 before any change).
One observable outcome per gate; CHECK commands run from the project root, EXPECT is matched
against the combined output. Format follows `GATES.md`.

**Status: stage 0 — 14/14 met; stage 1 — 13/13 met; stage 2 — 8/8 met; stage 3 — 12/12 met;
stage 4 — 8/8 met; stage 5 — 11/11 met (2 live-Chrome items NOT MEASURED, see G-5-LIVE);
stage 6 — 1/1 met; stage 7 — 4/4 met (+ the stage sweeps). 0 unmet, 0 abandoned.
Measured values below; current tree `492 passed / 30 skipped`, node 14/14.**

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

## Stage 4 — the Deno/JS engine (FIXPLAN_2026-10-15 §3/Этап 4)

A-08, A-15, A-16, B-02, B-16(b), B-17, B-18. Baseline entering the stage:
`443 passed / 30 skipped`. New file `tests/test_executor_offload.py` (2 tests);
`test_js_engine_lifecycle.py` grew from 13 to 32 tests (its fake worker now echoes the request id,
like the real one).

### G-4-1 · A-16 · a blocking JS rule no longer freezes the event loop
- CHECK: `python -m pytest tests/test_executor_offload.py -q`
- EXPECT: with a JS call that blocks 0.3 s, the loop's largest gap between ticks stays < 0.1 s —
  on both JS-bearing paths (image extraction and the fullsize probe).
- RESULT: **MET** — `2 passed`. `webpage_parser._get_best_image_url` is a coroutine that awaits
  `transform_image_url` in the dedicated JS pool, and all three probe calls
  (`apply_link_url_transform` × 2, `extract_res_urls`) go through `run_in_executor`. This is the
  audit's own quantity (it measured a 5.02 s freeze for `while(true){}`); the stand-in blocks for
  0.3 s instead of running Deno, which is not installed here. 2 mutations (direct call on the loop)
  → RED.

### G-4-2 · B-02 · blocking work has dedicated pools
- CHECK: source + `python -m pytest tests/test_executor_offload.py -q`
- EXPECT: downloads, HTML parsing and JS rules no longer share one executor, and the pools are
  retired with the task.
- RESULT: **MET (by construction, not by measurement)** — `_dl_executor` (downloader_threads),
  `_html_executor` (parser_threads) and `_js_executor` (`K.JS_ENGINE_POOL_WORKERS = 2`) are created in
  `_main_task` and shut down (`wait=False`) in its `finally`; `run_in_executor(None, …)` was replaced
  at every site (`BeautifulSoup`, the sync fallback, escalation, the sync bypass, the gateway click,
  `MediaDownloader.download`). **Limitation:** the pool starvation the audit describes (8 default
  slots for 12 configured workers) is a scheduling effect; with no Deno and no live wall-clock
  measurement in this stage it is asserted structurally, not measured.

### G-4-3 · A-15 · a blocked stdin write cannot hang the caller
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k StdinWrite`
- EXPECT: a child that does not read stdin stops the caller within the timeout, the worker is
  killed, and a normal write still round-trips.
- RESULT: **MET** — `2 passed`. The write runs on its own thread and is bounded by the call timeout;
  on expiry the right worker (plain/DOM/gateway) is killed and the caller sees `OSError` (fail-open).
  Mutation (direct blocking write) → RED.

### G-4-4 · B-16(b) / A-08 · responses are correlated by request id
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k ResponseIdCorrelation`
- EXPECT: every request carries a fresh id; one foreign line is skipped and the real answer used; two
  foreign lines kill the worker and fail open.
- RESULT: **MET** — `3 passed`. Requests used to be hard-coded `"id": 0` and the answer was never
  checked. Mutation (no id check) → RED.

### G-4-5 · A-08 / B-18 / B-17 · the worker scripts carry their guards
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k WorkerSourceGuards`,
  `node --check` on all three workers
- EXPECT: `Deno` is shadowed INSIDE the compiled rule body in `worker.js` and `dom_worker.js`; every
  worker caps its accumulated request at 64 MiB; both happy-dom workers release their Window after
  the response is built.
- RESULT: **MET** — `3 passed`, `node --check` exit 0. **Limitation:** Deno is not installed on this
  machine (the suite's 30 skips), so the JS half is pinned at the source level; the Python half
  (id correlation, fail-open) is tested behaviourally in G-4-4. 3 mutations → RED.

### G-4-6 · B-17 · workers are recycled after a call budget
- CHECK: `python -m pytest tests/test_js_engine_lifecycle.py -q -k Recycling`
- EXPECT: the call after `K.JS_WORKER_MAX_REQUESTS` (200) spawns a fresh worker; with the budget off
  the worker is reused (control).
- RESULT: **MET** — `2 passed`; the test drives a budget of 2 and observes `Popen` called twice.
  Mutation (no recycling) → RED. The RSS measurement from the plan (70 MB vs 118 MB per 200 calls)
  needs Deno and is not re-run here.

### G-4-FULL · Stage 4 regression sweep
- CHECK: `python -m pytest tests -q`, `node extension/tests/*.test.mjs`,
  `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401,F401`, `python -m pyflakes …`
- EXPECT: all green, no new skips, no new lint findings.
- RESULT: **MET** — Python `455 passed, 30 skipped in 28.4s` (**+12 this stage**, same 30 Deno skips);
  node 12/12; ruff reports exactly the 3 intentional `webpage_parser.py:37-39` E402; pyflakes silent;
  `node --check` clean on all three workers.

**Falsification, stage 4: 8 mutations, all RED** (2 offload, blocked write, id check, recycling,
acc cap, Deno shadow, happy-dom release).

---

## Stage 6 — credentials: one item left, and it is a type check (FIXPLAN §3/Этап 6)

A-02 and D-04 were dropped by product decision (FIXPLAN §2.4 — local trusted contour; filtering
would break age-gate/authorized sites). C-04 is what remains: the three scalars in the `/api/tasks`
body were never type-checked. Baseline entering the stage: `455 passed / 30 skipped`.

### G-6-1 · C-04 · the body's scalars are typed and cleaned
- CHECK: `python -m pytest tests/test_http_server.py -q -k ScalarCleaning`
- EXPECT: `{"user_agent": 5, "cookies": {"a": "b"}}` returns 200 with both overrides dropped;
  `one_shot` becomes a real bool; CR/LF cannot be injected through a header value; over-long values
  are capped at `MAX_SCALAR_LENGTH`.
- RESULT: **MET** — `4 passed` (`TestScalarCleaning`). The audit's measured `TypeError` is gone:
  `_clean_header_value`/`_clean_cookie_value` reject non-strings, strip CR/LF and truncate at 512,
  and `one_shot` is coerced with `bool()`. Mutation (the `isinstance` guard removed) → RED
  (`AttributeError` on the integer reaches aiohttp as a 500).

---

## Stage 7 — GUI and server (FIXPLAN §3/Этап 7)

C-02, C-05 (+NF-6), C-07, C-08. New files `tests/test_gui_pause_nonblocking.py` (7 tests) and
`tests/test_queue_thread_safety.py` (9); `test_http_server.py` grew 7 → 15, `test_gui_wiring.py`
10 → 14.

### G-7-1 · C-05 / NF-6 · the real wire path for large and oversized bodies
- CHECK: `python -m pytest tests/test_http_server.py -q -k RequestBodySize`
- EXPECT: a real aiohttp client posts a ~1.1 MiB batch → 200 with all items delivered; a declared
  17 MiB body → 413 with the size error in the JSON, and the task callback never runs; the chunked
  (no `Content-Length`) path is answered by the middleware, not by the handler.
- RESULT: **MET** — `4 passed`. `client_max_size = 16 MiB` plus a middleware that refuses a declared
  over-limit length BEFORE reading a byte and turns aiohttp's own `HTTPRequestEntityTooLarge` into
  the same 413. The handler re-raises that exception — its catch-all `except Exception` around
  `request.json()` was what rewrote the rejection as a misleading `Invalid JSON` 400 (the audit's
  finding, and the reason the middleware alone would never have fired). Measured batch: 12 000
  realistic items = 1.17 MiB. Mutation (cap back to 1 MiB) → RED.

### G-7-2 · C-07 · Pause does not block the GUI thread
- CHECK: `python -m pytest tests/test_gui_pause_nonblocking.py -q`
- EXPECT: a click whose state save takes 1.0 s returns in < 0.2 s; the task is marked PAUSED and the
  active slot freed before returning; the save still COMPLETES before the stop is issued; a failed
  save still stops the task and is reported to the user log.
- RESULT: **MET** — `7 passed`. `_save_then_stop()` chains `await pm.save_state(...)` and
  `pm.stop_parsing()` inside ONE coroutine on the parser loop and returns immediately (the old code
  was `run_coroutine_threadsafe(...).result(timeout=15)` on the GUI thread — the plan's measured
  5.47 s freeze). The ordering half is asserted, not assumed: `pm.order == ["save", "stop"]`.
  `closeEvent` still waits on purpose (bounded 5 s) — the process is exiting, so a background save
  would be lost; that deviation is documented at the call site. Mutation (`.result()` restored) → RED.

### G-7-3 · C-08 · the extension thread and the GUI thread stop interleaving
- CHECK: `python -m pytest tests/test_queue_thread_safety.py -q`
- EXPECT: a mutation waits for an in-progress read; `snapshot()` returns `active_task_id`,
  `queue_length`, `files_downloaded` and `task_ids` from ONE lock acquisition; a signal slot that
  calls back into the manager from another thread does not deadlock; 4×60 concurrent add/start/clear
  cycles lose nothing; `/api/status` answers consistently while a writer thread churns the queue.
- RESULT: **MET** — `9 passed`. `TaskQueueManager` gained an `RLock` (every mutator and both
  properties), a public `snapshot()`, and the rule that signals are emitted AFTER the lock is
  released; `MainWindow.get_status` reads through `snapshot()`; the extension payload buffer got its
  own `threading.Lock` (`_drain_pending_extension_payloads`), replacing a tuple swap that could drop
  an append. Honest scope note: the 40-request endpoint test is an invariant/smoke test (every
  response self-consistent, no exceptions) — the DETERMINISTIC detector is
  `test_a_mutation_waits_for_an_in_progress_read`, and that is the one the falsification uses.
  Mutation (lock replaced by a no-op) → RED.

### G-7-4 · C-02 · the sieve download leaves the GUI thread
- CHECK: `python -m pytest tests/test_gui_wiring.py -q -k BackgroundThread`
- EXPECT: a click whose download takes 1.0 s returns in < 0.2 s, the button is disabled while the
  worker runs and re-enabled when it reports; the worker runs on a different thread; the jsDelivr
  retry happens inside the worker; a failure is reported without freezing.
- RESULT: **MET** — `4 passed`. `_on_sieve_download` now only validates and starts a
  `threading.Thread`; results arrive over the `sieve_download_status` signal, connected once in
  `__init__` (`"progress"` keeps the button disabled, `"done"/"error"` re-enables it). The existing
  C-03 test now waits for that queued signal instead of reading the result synchronously — same
  assertion, different arrival time. Mutation (synchronous call restored) → RED.

### G-7-FULL · Stage 6-7 regression sweep
- CHECK: `python -m pytest tests -q`
- EXPECT: all green, no new skips.
- RESULT: **MET** — `483 passed, 30 skipped in 42.0s` (**+28** over stage 4's 455; the same 30 Deno
  skips). The five mutations below were applied one at a time and reverted; `git status` after the
  sweep shows only the intended files.

**Falsification, stages 6-7: 5 mutations, all RED** (16 MiB cap, C-04 type guard, blocking Pause,
no-op queue lock, synchronous sieve download). One first attempt was rejected as an equivalent
mutant (it kept the non-string guard while changing its shape) and was replaced by a mutation that
really drops the guard before being counted.

---

## Stage 5 — the extension sieve core and "the popup must not hang" (FIXPLAN §3/Этап 5)

A-09, A-10, A-11, A-12, NF-1, NF-2, NF-7 (one substitution + matching + normalization), A-13,
A-14, D-01, D-02, D-03, D-05, D-06, D-07, D-08, D-09. New suites:
`extension/tests/substitution.test.mjs`, `extension/tests/background_resilience.test.mjs`,
`tests/test_sieve_substitution_vectors.py`, `tests/test_sieve_cross_impl.py`, and the shared
fixture `extension/tests/vectors/substitution.json`. Baseline entering the stage:
`483 passed / 30 skipped`, node 12/12.

### G-5-1 · A-10 / A-12 / NF-2 / NF-7 · ONE substitution, anchored to the spec
- CHECK: `node extension/tests/substitution.test.mjs`, `python -m pytest tests/test_sieve_substitution_vectors.py -q`
- EXPECT: 28 substitution vectors + 5 expansion vectors pass on BOTH sides; the node suite
  re-derives every expected value with V8's own `String.replace` first, so a hand-edited fixture
  fails instead of blessing a wrong implementation.
- RESULT: **MET** — node `ALL PASS`, pytest `5 passed`. `sieve.js getSubstitution` and
  `site_pattern_manager.get_substitution` are the same ECMAScript GetSubstitution; `background.js`'s
  private descending-order loop is gone (`applyUrlTransform` delegates), and so is Python's
  `_sanitize_imagus_target` (`\g<n>` + private clamping). Measured on the audit's own cases:
  `$11200x1200` with 2 groups → `11200x1200` (was `x1200` in the extension), `$&` → the whole match
  (was left literal there), `$0` → literal (was the whole match in Python).

### G-5-2 · A-09 / A-11 / NF-1 · both URL forms match, every variant is absolute
- CHECK: `node extension/tests/sieve_rules.test.mjs`
- EXPECT: a `^`-anchored rule matches the scheme-stripped form; a full-URL rule still matches
  first; `//host/...` takes the PAGE's scheme; a root-relative variant resolves against the page;
  a `data:` result stays `data:`; `#ext#` blocks and multi-line templates expand fully.
- RESULT: **MET** — `ALL PASS`. `matchRuleUrl` tries the full URL then the stripped twin,
  `normalizeVariant` runs where variants are born (`applySieveRulesAll`) and at the two other
  producers (`upgradeMediaList` resize path, `processLink`), and the popup's relative-path handling
  is replaced by it. The suite's old expectations for relative variants (`["/full/1"]`) were
  rewritten to absolute URLs — that is the finding, not a test that needed loosening.

### G-5-3 · A-10 · the LIVE rule set: zero divergence between the two implementations
- CHECK: `python -m pytest tests/test_sieve_cross_impl.py -q`
- EXPECT: every bundled rule with a plain string `to` produces the same substitution and the same
  expansion in Python and in JS; the rule count stays in the hundreds.
- RESULT: **MET** — `4 passed`. 823 rules in `extension/sieve.json`, 815 live (8 carry `off: 1`),
  **314** carry a string `to` and are compared rule by rule on an identical synthetic match
  (`WHOLE`, `G1`…`Gn`); 313 of them actually change the template, so the comparison is not vacuous.
  The 158 JS rules have no string form to compare and are excluded by construction; the rules
  Python cannot compile at all are counted and asserted to stay under 20 (measured: 1).

### G-5-4 · A-13 / NF-9 / D-01 · the keepalive cannot throw, leak, or be missing
- CHECK: `node extension/tests/background_resilience.test.mjs -k keepalive` (whole file run below)
- EXPECT: `alarms.create` returning `undefined` (Chrome < 111) does not throw and counts exactly one
  reference; a throwing `alarms` API does not throw, reports failure and leaves the refcount at
  zero; release tolerates a broken `clear()`; the command path holds a keepalive.
- RESULT: **MET** — `ALL PASS`. The increment moved AFTER the alarm request (the audit's NF-9: the
  old order left `_keepaliveRefs` inflated at 1 forever), `console.warn` replaces the throw, and
  `commandScanAndProcess` — the third path, which had none — now brackets its whole body with
  acquire/release. `sieve_updater.updateSieve` takes the caller's keepalive and releases it in a
  `finally` (tested for the success, 304 and failure paths).

### G-5-5 · D-09 / D-02 · a failing branch still answers; the popup can always leave "Scanning..."
- CHECK: `node extension/tests/background_resilience.test.mjs` (replyWith cases) + source pins
- EXPECT: a rejected promise answers `{ok: false, error}`; no `onMessage` branch calls
  `.then(sendResponse)` directly; the scan has a second report channel.
- RESULT: **MET** — `ALL PASS`. `replyWith` wraps all **seven** async branches (the audit said six;
  the file has seven), the scan's completion and failure both call `broadcastScanFinished`
  (`chrome.runtime.sendMessage({action:"scanFinished"})`), and the popup listens for it — so a
  failed final `storage.local.set` (the D-02 case: the write sat under an empty `catch`) can no
  longer leave the button on "Scanning...".

### G-5-6 · A-14 · one scan, one download batch
- CHECK: `node extension/tests/background_resilience.test.mjs` (source pins),
  `node extension/tests/chrome_download_headers.test.mjs` (batch guard, behavioural)
- EXPECT: `handleStartScan` and the command path refuse to start while `_scanBusy`; the scan
  releases the flag in `finally`; a second `chromeDownload` while a batch runs returns
  `{saved: 0, busy: true}` and the first batch still completes.
- RESULT: **MET** — `ALL PASS` both files. The old `commandBusy` flag only covered the command
  entry point; the shared `_scanBusy` now covers the popup path too, which used to start a second
  full scan beside a running one.

### G-5-7 · D-03 · navigation links are capped, media links are not
- CHECK: `node extension/tests/content_scan.test.mjs`
- EXPECT: 1000 navigation anchors → 200 collected; a media-bearing anchor from the END of the
  document still reaches discovery; 300 media anchors are all kept (control).
- RESULT: **MET** — `ALL PASS`. Cap = the desktop twin's `DEFAULT_MAX_LINKS_PER_PAGE` (200), applied
  to `linkRest` only; media anchors keep their uncapped, first-in-list position.

### G-5-8 · D-05 · the WordPress/CDN size suffix is stripped here too
- CHECK: `node extension/tests/upgrade_media.test.mjs`, `node extension/tests/background_resilience.test.mjs`
- EXPECT: `photo-300x200.jpg` → `photo.jpg` through the REAL `upgradeMediaList` pipeline, with
  `original_url` kept and `transformed`/`source` set; a query string survives; a name that merely
  contains digits is untouched.
- RESULT: **MET** — `ALL PASS` both files. The suffix directly before the extension is removed
  (same anchor as the desktop twin's step 4); the sieve/JS route for those sites stays unavailable
  under MV3 CSP, which is why the pure-string twin exists — as it does on the desktop (ERR-7).

### G-5-9 · D-06 · the sieve response is bounded before it is parsed
- CHECK: `node extension/tests/sieve_merge.test.mjs`
- EXPECT: a body over `MAX_SIEVE_BYTES` (8 MiB) is refused without `JSON.parse`; a declared
  `Content-Length` over the cap is refused without reading the body; a normal bundle still updates
  (control); the stored rules are untouched in both refusal cases.
- RESULT: **MET** — `ALL PASS`. Measured before the fix (plan §0/NF-…): a 20 MiB body was parsed
  without complaint.

### G-5-10 · D-07 · a filename Chrome accepts
- CHECK: `node extension/tests/background_resilience.test.mjs`
- EXPECT: `..%2F..%2Fx.png` has no separator left and is not empty; `CON.png` is neutralised; a
  trailing dot/space, control characters and over-long names are handled; a normal name is
  untouched.
- RESULT: **MET** — `ALL PASS`. `sanitizeFilename` lives in `shared.js` (loaded by both the popup
  and the worker) and is applied on both send paths; the percent-decoding happens BEFORE the scrub,
  so `%2F` cannot smuggle a separator through.

### G-5-11 · D-08 · a dropped rule is reported
- CHECK: `node extension/tests/sieve_rules.test.mjs`
- EXPECT: a rule with an invalid `img` regex is dropped, logged at debug level, and the run ends
  with one aggregate warning naming the count.
- RESULT: **MET** — `ALL PASS`. `parseSieve` counts `unparsed` and warns once
  (`"Sieve: 1 rule(s) could not be parsed"`); the suite injects its own console and observes both
  calls — before this the rule simply vanished.

### G-5-LIVE · the two live-Chrome items — NOT MEASURED
- CHECK: (plan) "Save (Chrome)" on a page with the 7 `//`-rules, and a forced `storage.local.set`
  failure, both driving a real browser.
- EXPECT: a file downloads instead of a `file://` URL; the popup leaves "Scanning..." with an error.
- RESULT: **NOT RUN** — there is no live Chrome/browser automation in this environment, and the
  Deno/Chrome halves of earlier stages were reported the same way. The behaviour those two checks
  exercise is covered structurally instead: NF-1/A-06 (`normalizeVariant`, unit- and
  vector-tested on both sides, including the 7 protocol-relative templates through the cross-check)
  and D-02 (the second report channel plus the popup's listener). This is a stated gap, not a pass.

### G-5-FULL · Stage 5 regression sweep
- CHECK: `python -m pytest tests -q`, `node extension/tests/*.test.mjs`, `node --check` on every
  extension script, `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401,F401`
- EXPECT: all green, no new skips, no new lint findings.
- RESULT: **MET** — Python `492 passed, 30 skipped` (**+9** this stage; the same 30 Deno skips),
  node **14/14** (12 before; `substitution` and `background_resilience` are new), `node --check`
  clean on all six extension scripts, ruff reports only the 3 intentional `webpage_parser.py` E402.

**Falsification, stage 5: 11 mutations, all RED** (greedy `$N` in JS, full-URL-only matching,
unnormalized variants in both languages, throwing `keepaliveAcquire`, one `onMessage` branch without
`replyWith`, the D-05 call site, the D-07 scrub, the D-03 cap, the D-06 size cap, Python's `$nn`
fallback, Python's normalizer).

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
