# Fix & Feature Plan — 2026-09-09

Source: Audit.md (2026-09-09) + verified re-reading of both codebases.
Mod reference: `../Imagus-Mass-Download-Mod/` (MIT badge in README.md, owned by us — code reuse permitted).

**Discipline:** each item lists the concrete solution (grounded in real code, with line refs), files
touched, and an acceptance gate (`CHECK` → `EXPECT`) that must pass before the item is done.
No item starts before its gate is written here. New defects found during work are logged in §8
immediately, never ignored. Baseline: `7bac98b`, 265 passed / 30 skipped.

---

## Phase 1 — Critical & High bug fixes (no new features)

### P1-1 · A-1 (CRITICAL): MT-download writes to closed file
**Code fact:** `media_downloader.py` `_download_chunk` — `if write_buffer_chunk: f.write(...)` sits
AFTER the `with open(filename, "wb") as f:` block (verified by runtime repro → `ValueError`).
Every MT download fails on the final sub-1MB buffer → silent fallback to single-thread →
"Threads per File > 1" and the MT rate-limiter (DL-12) are dead code.
**Solution:** move the residual-buffer flush INSIDE the `with` block (last statement of the loop body,
after the loop — still indented inside `with`). No behavior change other than "it now works".
Also verify `_download_with_threads` joins chunk files in order (read that code before editing).
**Files:** `src/downloader/media_downloader.py`, `tests/test_media_downloader.py`.
**Gate:**
- CHECK: `python -m pytest tests/test_media_downloader.py -q` → EXPECT: all pass, including NEW
  `test_mt_chunk_success_writes_full_file`: mock session.get with iter_content yielding 3×600KB chunks
  (residual buffer guaranteed), Accept-Ranges + Content-Length, threads_per_file=2; assert
  `progress_dict["success"] is True` and written file size == total bytes.

### P1-2 · A-2 (HIGH): extension HTTP handler mutates global settings
**Code fact:** `main_window.py:1106-1113` — `settings = self.settings_dialog.get_settings()` returns
the LIVE dict (`settings_dialog.py:970-974` returns `self.settings`), then writes `user_agent` /
`extension_cookies` into it → leaks to all future tasks and to `settings.json` on next save.
**Solution:** `settings = dict(self.settings_dialog.get_settings())` (one line). `add_task` already
deep-copies for the task snapshot, so the copy is safe to mutate.
**Files:** `src/gui/main_window.py`, new test in `tests/test_parser_lifecycle.py`.
**Gate:**
- CHECK: `python -m pytest tests/test_parser_lifecycle.py -q` → EXPECT: pass, including NEW test:
  simulate `add_tasks_from_extension(cookies="session=abc", user_agent="UA-X")`, then assert
  `settings_dialog.get_settings()` has NO `extension_cookies` key and unchanged `user_agent`.

### P1-3 · A-3 (HIGH): Referer never sent (`_source_url` never set)
**Code fact:** `webpage_parser.py:129` and `:360` read `self.settings.get("_source_url")`;
`grep -rn "_source_url" src/` shows reads only — the key is never written. ParserManager already
builds `context["source_url"]` and passes it into WebpageParser.
**Solution (two-sided, belt & braces):**
1. In `WebpageParser._get_content` (both places): `source_url = self.settings.get("_source_url")
   or (self.context or {}).get("source_url")`.
2. In `ParserManager` where WebpageParser is constructed: also set
   `settings_copy["_source_url"] = context["source_url"]` when present (keeps downloader-side
   Referer logic working via task settings snapshot).
**Files:** `src/parser/webpage_parser.py`, `src/parser/parser_manager.py`, tests.
**Gate:**
- CHECK: `python -m pytest tests/test_fullsize_discovery.py tests/test_parser_manager_filtering.py -q`
  → EXPECT: pass, including NEW test: WebpageParser with context `{"source_url": "http://s/p"}` and
  policy `auto` sends `Referer: http://s/p` (assert on captured headers of mock session);
  and when `source_url == self.url`, NO Referer header (existing policy preserved).

### P1-4 · A-4 (HIGH): Clear History deletes wrong sessions path
**Code fact:** `main_window.py:611` uses `{download_dir}/sessions`; real layout is
`{download_dir}/{task_folder}/sessions/{task_id}/last_session.pkl` (`app_paths.task_state_path`,
`app_paths.py:66-77`). `{download_dir}/sessions` never exists → silent no-op.
**Solution:** extract pure helper `clear_task_sessions(download_dir) -> int` into `src/app_paths.py`
(walks one level down, rmtree's each `*/sessions`), call it from `_clear_download_history`,
log the count. Deprecate-but-keep `app_paths.sessions_dir()` untouched (dead code, no drive-by).
**Files:** `src/app_paths.py`, `src/gui/main_window.py`, `tests/test_app_paths.py` (new).
**Gate:**
- CHECK: `python -m pytest tests/test_app_paths.py -q` → EXPECT: pass, including: build
  `{tmp}/site_x/sessions/{id}/last_session.pkl` + `{tmp}/site_x/file.jpg`, run
  `clear_task_sessions(tmp)` → EXPECT: sessions dirs gone, `file.jpg` intact, return value == 1.

### P1-5 · A-8 (LOW, same phase since trivial): ruff real findings
- `priority_url_queue.py:75` bare `except:` → `except (ValueError, TypeError):`
- `site_pattern_manager.py:577` variable `l` → `line`
- `tests/test_parser_lifecycle.py:139` lambda → def
- `http_engine.py:151` add `# noqa: E402` (intentional lazy import)
**Gate:**
- CHECK: `python -m ruff check src/ tests/ --select E722,E402,E741,E731` → EXPECT: 0 errors.

---

## Phase 2 — Extension correctness (small, self-contained)

### P2-1 · B-2: `applyUrlTransform` corrupts `$10`
**Code fact:** `extension/background.js:128-145` substitutes ascending `$1..$n`; `$10` contains `$1`.
Desktop Python twin (`site_pattern_manager.apply_link_url_transform`) already substitutes DESCENDING
(comment in code says exactly this). Fix: mirror it — loop `for (let i = matchGroups.length - 1; i >= 1; i--)`.
**Files:** `extension/background.js`.
**Gate:**
- CHECK: manual — `node -e` script (no test runner in extension/): transform template `"$10-$1"` with
  groups `["full","a","b",...,"j"]` → EXPECT: `"j-a"` (i.e. `$10` intact before `$1` replaced).
  Persist as `extension/tests/url_transform.test.mjs` runnable via `node` so it can fail honestly.

### P2-2 · B-1: sync extension sieve with the newer file
**Code fact:** `extension/sieve.json` = 849 rules (2026.04.01), `background.js:10` pins
`SIEVE_VERSION = "2026.04.01"`; newer `Imagus_sieve_2026.07.15_823.json` exists in repo root and in
Mod's dir. (Both derive from the same upstream; 823 is newer by date.)
**Solution:** copy the 2026.07.15 file over `extension/sieve.json`, bump `SIEVE_VERSION`.
NOT the full C-1 online-update machinery here — that's Phase 4 (needs its own design+gates).
**Files:** `extension/sieve.json`, `extension/background.js` (one constant).
**Gate:**
- CHECK: `python -c "import json;d=json.load(open('extension/sieve.json',encoding='utf-8'));print(len(d))"`
  → EXPECT: `823`; CHECK: `grep SIEVE_VERSION extension/background.js` → EXPECT: `2026.07.15`.

### P2-3 · B-3/B-4 (extension low): watchdog TDZ + fullsize time budget
- `background.js` chromeDownload listener: hoist `let watchdog = null;` above `addListener`,
  guard `if (watchdog) clearTimeout(watchdog)`.
- `discoverFullsize`: add shared `Date.now() + 45000` deadline checked per link (mirrors desktop
  `FULLSIZE_DISCOVER_TIME_BUDGET`).
**Gate:**
- CHECK: `node --check extension/background.js` → EXPECT: exit 0 (syntax); manual review note in
  PR/commit that deadline is tested only in desktop twin.

---

## Phase 3 — Sieve engine parity with Mod (desktop first, then extension)

### P3-1 · C-3a: honor `off: 1` rules
**Code fact:** Mod upstream skips disabled rules (`src/js/background.js:401: if (rule.off)`); our
`_load_imagus_file` (`site_pattern_manager.py:197`) never checks it → 8 disabled rules run.
**Solution:** in `_load_imagus_file` loop: `if rule_data.get('off'): continue` BEFORE the
`_loaded_sieve_rule_names.add` (so a later file can still provide an enabled variant — matches
Mod semantics of "disabled by user", and our multi-file load order PAT-2).
**Files:** `src/parser/site_pattern_manager.py`, `tests/test_pattern_manager.py`.
**Gate:**
- CHECK: `python -m pytest tests/test_pattern_manager.py -q` → EXPECT: pass, including NEW:
  fixture sieve with `[A]{off:1,...}` and `[B]` normal → `get_link_rule` returns None for A's URL,
  works for B.

### P3-2 · C-4: stop mangling `data:` templates (29 rules in shipped sieve)
**Code fact:** verified by runtime repro: `"data:,$&"` → `apply_link_url_transform` →
`https://data:,abc123` (garbage fetch). Mod treats `data:` as no-fetch marker.
**Solution:** in `apply_link_url_transform` (Python) and `applyUrlTransform` (extension):
if the substituted result starts with `data:` → return `None` (no fetchable transform; caller's
existing "no transformed → probe linked page itself" path already handles this correctly —
verified in `_discover_linked_fullsize` lines ~600-604).
**Files:** `src/parser/site_pattern_manager.py`, `extension/background.js`, tests.
**Gate:**
- CHECK: `python -m pytest tests/test_fullsize_discovery.py -q` → EXPECT: pass, including NEW:
  rule `url: "data:,$&"` → `apply_link_url_transform` returns None; and a `data:` result inside
  JS-branch template is also rejected.

### P3-3 · C-2: `loop` — corrected semantics from Mod source (Audit.md C-2 premise was wrong)
**Code fact (verified in `../Imagus-Mass-Download-Mod/src/includes/content.js:1481 & 4478`):**
`loop` is NOT "N iterations of the url template". It's a bitmask gate for **recursive re-resolution**:
after a rule produces a result string `ret`, if `rule.loop & (use_img ? 2 : 1)` (1 = matched via
link, 2 = via img), the result string is fed BACK into the rule engine (`PVI.find({href: ret})`)
with `IMGS_loop_count`, hard-capped at 5 hops, self-reference → abort. `loop_param`/`dc` (service.js:547)
picks which side's bitmask applies.
**Solution (desktop):** in `ParserManager._discover_linked_fullsize.probe()` wrap the resolve step
in a bounded loop (max 5 iterations, `K.SIEVE_LOOP_MAX_HOPS = 5`):
- iteration 0: current behavior (apply url-template → fetch → extract res);
- if `res` yields exactly-one URL that itself matches a sieve `link` rule AND
  `rule.get('loop') & 1` → treat that URL as the new link, re-run `get_link_rule` +
  `apply_link_url_transform` + fetch + extract; accumulate final media URLs only;
- stop conditions: no new link-match, hop cap, or time budget (reuse existing `deadline` —
  no NEW budget: a looping rule consumes the same 45s page budget, so no perf regression);
- cycle guard: keep a `seen_transformed` set; repeated fetch_url → break (analog of Mod's
  `ret === trg.href` self-protection).
Extension twin (`discoverFullsize`) gets the same bounded loop in Phase 3b after desktop proves out.
**Files:** `src/parser/parser_manager.py`, `src/constants.py` (one constant), tests.
**Gate:**
- CHECK: `python -m pytest tests/test_fullsize_discovery.py tests/test_crawler_frontier.py -q`
  → EXPECT: pass, including NEW: two-rule fixture chain (rule1 loop:1 resolves thumbnail-link to
  viewer URL matching rule2 which resolves to CDN image) → final discovered == CDN image,
  thumbnail resolved; chain with cyclic rules terminates (mock fetches counted ≤ 5 hops+1) and
  total probe time stays within the existing per-page budget.

### P3-4 · C-3b: `dc` (16 rules) — log-only this phase
`_load_imagus_file`: `if rule_data.get('dc'): logger.debug(...)`. No behavior change; real support
deferred to a dedicated task (Mod's dc semantics are tied to its own engine internals).
**Gate:** CHECK: `python -m pytest tests/test_pattern_manager.py -q` → EXPECT: pass (no regressions).

---

## Phase 4 — Online sieve update (C-1), desktop then extension

### P4-1 · Desktop: manual "Download sieve update" (explicit user action)
**Security contract (from Audit.md C-1):** sieve rules contain executable JS (Deno/`exec`) —
auto-update in the DESKTOP app is prohibited; user must click.
**Solution:**
- `SitePatternManager.download_imagus_from_url(url, timeout=30) -> tuple[bool, str]` using the
  shared requests session (NOT aiohttp — runs on GUI action), fetch → validate JSON object →
  `validRuleCount` (rules having `link` or `img` + `to`) > 0 → atomic replace
  (`tmp file + os.replace`) of the configured sieve file → reload.
- `SettingsDialog`: field "Sieve update URL" (default from Mod's:
  `https://raw.githubusercontent.com/kuzn123/Imagus-Sieve-RuBoard/master/update.txt`) + button
  "Download latest" + progress label; on success log count, on failure keep old file (validated —
  Mod's `validRuleCount === 0` rejection, service.js:139-146).
- jsDelivr mirror fallback: convert raw.githubusercontent URL → cdn.jsdelivr.net on failure
  (port of Mod `jsDelivrMirror`, service.js:74-83 — pure string function, trivially testable).
**Files:** `src/parser/site_pattern_manager.py`, `src/gui/settings_dialog.py`, `src/constants.py`,
tests.
**Gate:**
- CHECK: `python -m pytest tests/test_pattern_manager.py tests/test_shared_session.py -q` → EXPECT:
  pass, including NEW (mock HTTP): valid payload replaces file + reloads; invalid JSON → old file
  untouched + False; `validRuleCount==0` → rejected; jsDelivrMirror URL conversion unit test.
- Manual gate: `python main.py` → Settings → Download latest against real repo → EXPECT: success
  log, rules count grows, app still parses (verified once manually).

### P4-2 · Extension: automatic weekly update (MV3 sandbox allows it)
Port Mod's mechanism (service.js:74-200, 855-895) into `extension/background.js`:
`chrome.alarms` weekly + on-start check, `If-Modified-Since` from stored `sieveUpdateLast`,
jsDelivr fallback, backoff retries, `validRuleCount` validation, merge preserving `_`-prefixed
user rules and `off` flags (port of Mod's merge), replace `SIEVE_VERSION` pin with timestamp.
Popup: "Update sieve now" button + last-update display.
**Files:** `extension/background.js`, `extension/manifest.json` (alarms permission),
`extension/popup/popup.js|html`, `extension/sieve_updater.js` (new, isolated for testability).
**Gate:**
- CHECK: `node --check extension/sieve_updater.js && node --check extension/background.js` → EXPECT: 0.
- CHECK: `node extension/tests/sieve_merge.test.mjs` (new) → EXPECT: merge keeps `_`-rules and
  `off` flags; invalid fetch (mock) keeps old sieve; EXPECT success token printed.
- Manual gate: load unpacked → click Update → EXPECT: storage.sieveRules updated, no console errors.

---

## Phase 5 — Medium app fixes (independent, can interleave after Phase 1)

### P5-1 · A-5: queue mutation from HTTP thread
Solution per Audit.md: buffer extension payload in `add_tasks_from_extension`, do `add_task` on the
GUI thread via existing `extension_tasks_added` signal (GUI-1 one-shot path already does this —
extend the same signal to carry the full payload; keep HTTP handler side effect-free).
**Gate:** CHECK: `python -m pytest tests/test_parser_lifecycle.py tests/test_http_server.py -q`
→ EXPECT: pass incl. NEW test asserting `add_task` is invoked only after the queued signal is
processed (qt bot not available — test the payload-buffering pure function directly).

### P5-2 · A-6: TLS verify setting
`K.SETTING_VERIFY_TLS="verify_tls"`, `K.DEFAULT_VERIFY_TLS=False` (preserve current behavior),
checkbox in Settings→HTTP, `verify=self.settings.get(...)` at the 4 call sites
(`webpage_parser._sync_fetch`, `_execute_bypass`, `_try_escalate_fetch`, `media_downloader._try_escalate_get`).
Plus one-shot `logger.warning` on first `verify=False` fetch per process (pattern of
`_missing_warned` in http_engine).
**Gate:** CHECK: `python -m pytest tests/test_http_engine.py tests/test_media_downloader.py -q`
→ EXPECT: pass incl. NEW: default False passes `verify=False`; True passes `verify=True` (mock assert).

### P5-3 · A-7: bounded thread wait in GUI
`on_task_ended`/`on_parsing_finished`: `quit()` + `wait(10000)` + warning log on timeout
(already the pattern in `stop_parsing`).
**Gate:** manual — hard to unit-test QThread teardown; verify by code review + smoke run
`python main.py` (start/stop task, no freeze).

---

## §8 Defect log (new findings during execution — appended, never ignored)

- [2026-09-09, planning] Audit.md C-2 premise incorrect: Mod's `loop` is recursive re-resolution
  with a bitmask (`loop & 1` link-side, `loop & 2` img-side, ≤5 hops, self-ref protection),
  NOT "N iterations of url template" (content.js:1481, 4478). P3-3 implements the corrected
  semantics. → [2026-09-09] RESOLVED: Audit.md C-2 rewritten with verified bitmask semantics
  (incl. Block D test-gap item); rule counts corrected to 95 (extension/sieve.json) / 96 (2026.07.15 file).
- [2026-09-09, P1-3 testing] Test-design note: `K.SETTING_REFERRER_POLICY` is the string
  `"referrer"` (constants.py:224) — an initial test used `"referrer_policy"` and silently
  did nothing. Anyone hand-writing settings must use the exact key. Covered by
  test_referer_suppressed_with_policy_none.
- [2026-09-09, P3-3 testing] DEFECT FOUND & FIXED in `extract_res_urls`: bare relative res
  matches (group 1 without scheme, e.g. `abc.jpg`) were mangled to `https:abc.jpg` by the
  old `if not u.startswith('http'): u = 'https:' + u`. Was hidden because shipped rules
  capture protocol-relative URLs. Fix: `//`-prefixed → `https:` prepend (extension
  semantics kept); other relative → urljoin against the fetched page (`href`), not the
  source page. Covered by the loop-chain tests (they exercise relative resolution).
- [2026-09-09, P5-2] Test conflict: legacy DL-4 test asserted `verify` must NOT be passed
  to the escalation GET; A-6 makes the flag explicit. DL-4 test updated to the new
  contract (default False preserved).
- [2026-09-09, P4-1] `create_shared_downloader_session` lives in `media_downloader.py` and
  takes a `settings` arg — not in `shared_session.py` as the module name suggests.
  Cosmetic inconsistency, left as-is (no drive-by refactor).

### Re-verification pass (2026-09-09, second review of own work)

- [BUG-1 · REGRESSION FOUND & FIXED] A-6 as first implemented silently WEAKENED TLS on the
  escalation GET: that path historically passed NO verify kwarg (curl_cffi defaults to
  verify=True), but the first fix sent verify=False by default. Confirmed via curl_cffi docs
  (requests-compatible API supports `verify`). Fix: `http_engine.tls_verify(settings,
  legacy_default=False)` for the escalation GET — verify stays ON unless the user explicitly
  disables it; the four historically-verify=False paths keep default False. Tests updated
  (DL-4 + new tri-state assertions in test_verify_tls_flag_passed_to_session).
- [BUG-3 · FOUND & FIXED] A-5 buffering introduced a shutdown data-loss window: closeEvent
  saved the queue BEFORE buffered extension payloads materialized (they lived only in RAM on
  the HTTP thread), so tasks requested seconds before quit were lost. Fix: closeEvent now
  flushes `_pending_extension_payloads` through `_apply_extension_task_payload` before
  `task_queue.save()`; `_clear_download_history` drops them (user asked to clear all).
- [BUG-5 · FOUND & FIXED] Loop-chain exception handler discarded `link_url` from `consumed`,
  but `link_url` mutates across loop hops — a mid-chain failure would have released the
  WRONG url (intermediate hop), losing the original thumbnail link. Fix: capture
  `original_link_url` before the loop; the handler releases that. (Only the original link
  is ever in `consumed`; intermediate hops are transient fetch targets.)
- [BUG-2 · COSMETIC, SIMPLIFIED] Non-one-shot payload loop recomputed `ts` per item — same
  value every iteration; hoisted out of the loop with a comment (legacy same-second folder
  naming kept).
- [BUG-4 · DOCUMENTED, NOT A BUG] MV3 service worker may suspend before the 15s startup
  `setTimeout(runSieveUpdate)` fires — acceptable: the weekly `chrome.alarms` is the
  authoritative trigger and the popup button covers on-demand checks. Comment added in
  background.js.

## Execution order & dependencies

P1-1 → P1-2 → P1-3 → P1-4 → P1-5 (all independent of each other; run gates after each).
P2-* independent. P3-1, P3-2, P3-4 independent; P3-3 last (largest, needs P3-1's `off` skip so
disabled loop-rules don't fire). P4-1 needs P3-1 (off-filter must exist before downloading new
sieves). P4-2 independent of desktop. P5-* anytime after P1.
Final gate: full `python -m pytest tests -q` ≥ 265 passed, 0 failed; new tests all green;
`node --check` on every touched extension file.
