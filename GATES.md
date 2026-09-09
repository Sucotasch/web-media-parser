# GATES.md — Fix & Feature Execution Ledger (2026-09-09)

Baseline: `7bac98b`, 265 passed / 30 skipped. Each gate = one observable outcome.
CHECK commands are run from the project root; EXPECT is matched against combined output.

---

## Phase 1

### G-P1-1 · MT-download writes complete file (closed-file bug fixed)
- CHECK: `python -m pytest tests/test_media_downloader.py -q`
- EXPECT: `passed` present, `failed` absent, `error` absent; new test name `test_mt_chunk_success_writes_full_file` appears in output.

### G-P1-2 · Global settings no longer mutated from HTTP handler
- CHECK: `python -m pytest tests/test_parser_lifecycle.py -q`
- EXPECT: `passed` present, `failed` absent; new test name `test_extension_add_does_not_mutate_global_settings` appears.

### G-P1-3 · Referer sent from context source_url
- CHECK: `python -m pytest tests/test_fullsize_discovery.py tests/test_parser_manager_filtering.py -q`
- EXPECT: `passed` present, `failed` absent, `error` absent; new test name `test_referer_from_context_source_url` appears.

### G-P1-4 · clear_task_sessions removes per-task session dirs
- CHECK: `python -m pytest tests/test_app_paths.py -q`
- EXPECT: `passed` present, `failed` absent; `1 passed` count line present.

### G-P1-5 · Ruff real findings cleared
- CHECK: `python -m ruff check src/ tests/ --select E722,E402,E741,E731 --output-format=concise`
- EXPECT: `All checks passed` or zero lines with `E722|E402|E741|E731` besides intentional noqa.

### G-P1-FULL · Phase 1 regression sweep
- CHECK: `python -m pytest tests -q`
- EXPECT: `failed` count `0` and total passed `>= 265`.

---

## Phase 2

### G-P2-1 · applyUrlTransform substitutes $10 before $1
- CHECK: `node extension/tests/url_transform.test.mjs`
- EXPECT: `ALL PASS` token printed, exit code 0.

### G-P2-2 · Extension sieve synced to 2026.07.15 (823 rules)
- CHECK: `python -c "import json;d=json.load(open('extension/sieve.json',encoding='utf-8'));print(len(d))"`
- EXPECT: `823`.
- CHECK: `grep -c "2026.07.15" extension/background.js`
- EXPECT: `1`.

### G-P2-3 · Extension JS syntax valid after edits
- CHECK: `node --check extension/background.js`
- EXPECT: exit 0, no output.

---

## Phase 3

### G-P3-1 · off:1 rules skipped at load
- CHECK: `python -m pytest tests/test_pattern_manager.py -q`
- EXPECT: `passed` present, `failed` absent; `test_imagus_off_rule_skipped` appears.

### G-P3-2 · data: templates return None (desktop)
- CHECK: `python -m pytest tests/test_fullsize_discovery.py -q`
- EXPECT: `passed` present, `failed` absent; `test_data_template_rejected` appears.

### G-P3-3 · loop recursive re-resolution resolves multi-hop chain
- CHECK: `python -m pytest tests/test_fullsize_discovery.py tests/test_crawler_frontier.py -q`
- EXPECT: `passed` present, `failed` absent, `error` absent; `test_loop_chain_resolves_multi_hop` appears.

### G-P3-4 · dc rules logged, no crash
- CHECK: `python -m pytest tests/test_pattern_manager.py -q`
- EXPECT: `passed` present, `failed` absent (covered with G-P3-1 run).

### G-P3-EXT · Extension data: rejection in JS twin
- CHECK: `node extension/tests/url_transform.test.mjs`
- EXPECT: `ALL PASS` and `data:` case token printed.

---

## Phase 4

### G-P4-1 · Desktop sieve download: validate, atomic replace, reload
- CHECK: `python -m pytest tests/test_pattern_manager.py tests/test_shared_session.py -q`
- EXPECT: `passed` present, `failed` absent; `test_download_imagus_valid_replaces` and `test_download_imagus_invalid_keeps_old` appear.

### G-P4-1b · jsDelivr mirror conversion
- CHECK: `python -m pytest tests/test_pattern_manager.py -q -k jsdelivr`
- EXPECT: `1 passed`.

### G-P4-2 · Extension sieve updater module valid + merge logic proven
- CHECK: `node --check extension/sieve_updater.js`
- EXPECT: exit 0.
- CHECK: `node extension/tests/sieve_merge.test.mjs`
- EXPECT: `ALL PASS` token; merge keeps `_`-prefixed user rules and `off` flags.

---

## Phase 5

### G-P5-1 · Extension tasks added on GUI thread only
- CHECK: `python -m pytest tests/test_parser_lifecycle.py tests/test_http_server.py -q`
- EXPECT: `passed` present, `failed` absent; `test_extension_payload_buffered` appears.

### G-P5-2 · verify_tls setting honored
- CHECK: `python -m pytest tests/test_http_engine.py tests/test_media_downloader.py -q`
- EXPECT: `passed` present, `failed` absent; `test_verify_tls_flag_passed_to_session` appears.

### G-P5-3 · Bounded wait present in both handlers
- CHECK: `grep -c "wait(10000)" src/gui/main_window.py`
- EXPECT: count `>= 3` (stop_parsing existing + two new).

---

## Final

### G-FINAL · Whole suite green, JS syntax clean
- CHECK: `python -m pytest tests -q`
- EXPECT: `failed` `0`; passed `>= 265 + <new tests>`; `error` absent.
- CHECK: `node --check extension/background.js && node --check extension/sieve_updater.js && node --check extension/content_script.js`
- EXPECT: exit 0.
