#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""DenoJsEngine — executes Imagus sieve JS rules via a Deno subprocess.

Design (see docs/DENO_JS_ENGINE_DESIGN.md, P0+P1):
  - Long-lived Deno worker processes (JSON-over-stdio) reused for the whole
    parse run: ~0.1 ms per call measured for plain rules, vs ~300 ms per cold
    `deno run` spawn.
  - Two worker modes:
      * worker.js      — dependency-free sandbox for `to`/`url` rules that
                         only need location/URL shims (P0).
      * dom_worker.js  — happy-dom based DOM worker (P1) for `res`/`url`
                         rules that query a real parsed document. happy-dom
                         is resolved offline from a bundled DENO_DIR cache
                         (populated at build time). Site scripts are NOT
                         executed (enableJavaScriptEvaluation=false); only
                         the user's own sieve rule runs.
  - Binary resolution order:
      1. env WEB_MEDIA_PARSER_DENO / DENO_BIN / js_engine_bin (explicit)
      2. `deno` Python package (deno.find_deno_bin) if installed
      3. bin/deno.exe next to the frozen exe (bundled build) — the user chose
         to bundle, so the shipped binary wins over any system deno
      4. media-downloader's bundled bin/deno.exe (common on this machine)
      5. `deno` on PATH
  - Sandboxing: workers are started WITHOUT --allow-net/--allow-read/--allow-
    write/--allow-env; sieve rules are untrusted code and cannot touch
    disk/network. Fail-open: any error (no binary, worker crash, timeout, JS
    exception) returns None; callers fall back to the static Python path.
"""

import os
import sys
import json
import shutil
import logging
import threading
import subprocess
import tempfile

logger = logging.getLogger(__name__)

WORKER_BASENAME = "worker.js"
DOM_WORKER_BASENAME = "dom_worker.js"
GATEWAY_WORKER_BASENAME = "gateway_worker.js"

# Timeout for a single JS rule call (seconds). Rules are tiny; this is a
# safety net against pathological infinite loops inside a sieve rule. Must
# also cover the cold start of the Deno worker (spawn + compile + happy-dom
# import); 2.0s was too tight on slow disks/antivirus machines and silently
# disabled DOM rules. 5.0s matches docs/DENO_JS_ENGINE_DESIGN.md (DL-8).
DEFAULT_CALL_TIMEOUT = 5.0

# Bounded result cache: same (code, groups, pageUrl) rarely repeats, but a
# gallery page re-matches identical thumb URLs; cap to avoid unbounded growth.
CACHE_MAX_ENTRIES = 512


def _is_windows():
    return os.name == "nt"


def _env_candidates():
    """Explicit user-provided binary locations."""
    out = []
    env = os.environ.get("WEB_MEDIA_PARSER_DENO") or os.environ.get("DENO_BIN")
    if env:
        out.append(env)
    try:
        from src import constants as K
        custom = os.environ.get(K.SETTING_JS_ENGINE + "_bin")
        if custom:
            out.append(custom)
    except Exception:
        pass
    return out


def _package_candidate():
    """`deno` PyPI package (official Deno binary redistributor)."""
    try:
        import deno  # type: ignore
        return deno.find_deno_bin()
    except Exception:
        return None


def _path_candidate():
    return shutil.which("deno") or shutil.which("deno.exe")


def _frozen_candidates():
    """Bundled binaries next to the PyInstaller onedir exe."""
    if not getattr(sys, "frozen", False):
        return []
    exe_dir = os.path.dirname(sys.executable)
    return [
        os.path.join(exe_dir, "bin", "deno.exe"),
        os.path.join(exe_dir, "deno.exe"),
        os.path.join(exe_dir, "deno"),
    ]


def find_deno_bin():
    """Locate a usable Deno binary, or None."""
    for cand in (_env_candidates() + [None]):
        if cand and os.path.exists(cand):
            return cand
    # NOTE: no %APPDATA% / user-profile lookup here — the app is portable and
    # must not depend on anything outside its own folder (bundled bin/deno.exe
    # is checked first) plus explicit env-var / PATH overrides.
    for cand in [_package_candidate(), _frozen_candidates(), _path_candidate()]:
        if isinstance(cand, list):
            for c in cand:
                if c and os.path.exists(c):
                    return c
        elif cand and os.path.exists(cand):
            return cand
    return None


def _find_worker_script():
    """Locate worker.js next to this module or bundled next to the exe."""
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, WORKER_BASENAME)
    if os.path.exists(local):
        return local
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for cand in (os.path.join(exe_dir, "bin", WORKER_BASENAME),
                     os.path.join(exe_dir, WORKER_BASENAME)):
            if os.path.exists(cand):
                return cand
    return None


def _find_dom_worker_script():
    """Locate dom_worker.js next to this module or bundled next to the exe."""
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, DOM_WORKER_BASENAME)
    if os.path.exists(local):
        return local
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for cand in (os.path.join(exe_dir, "bin", DOM_WORKER_BASENAME),
                     os.path.join(exe_dir, DOM_WORKER_BASENAME)):
            if os.path.exists(cand):
                return cand
    return None


def _find_gateway_worker_script():
    """Locate gateway_worker.js next to this module or bundled next to the exe."""
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, GATEWAY_WORKER_BASENAME)
    if os.path.exists(local):
        return local
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for cand in (os.path.join(exe_dir, "bin", GATEWAY_WORKER_BASENAME),
                     os.path.join(exe_dir, GATEWAY_WORKER_BASENAME)):
            if os.path.exists(cand):
                return cand
    return None


def _popen_kwargs():
    """Subprocess flags that keep the Deno child invisible and isolated.

    Without CREATE_NO_WINDOW, every `deno run` from a windowed PyInstaller
    app pops a visible console window on Windows that lives as long as the
    worker process (observed: windows appearing during a parse run and only
    closing after the task is stopped). start_new_session detaches the child
    from the parent's process group on Unix so a kill never leaves orphans.
    """
    kwargs = {}
    if _is_windows():
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


# --- worker memory ----------------------------------------------------------

_WIN_MEM_API = None


def _windows_mem_api():
    """Lazy (ctypes, PROCESS_MEMORY_COUNTERS, psapi) trio.

    Wrapped in a function so the module imports on every platform and only a
    Windows run pays for the ctypes setup.
    """
    global _WIN_MEM_API
    if _WIN_MEM_API is None:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        _WIN_MEM_API = (ctypes, PROCESS_MEMORY_COUNTERS, psapi)
    return _WIN_MEM_API


def child_rss_mb(proc):
    """Resident memory of a worker process in MB, or None when unreadable.

    Errors.txt (2): the DOM worker's footprint grows with every parsed document
    and does not shrink (happyDOM.close() releases nothing meaningful — an
    explicit globalThis.gc() after close left 1.1 MB/call of growth on a 3 KB
    page), so the recycling decision needs a real memory reading rather than a
    call count. Fail-open: an unreadable value leaves the count rule in charge.

    Windows reads the child handle subprocess already holds
    (GetProcessMemoryInfo); Linux reads /proc/<pid>/statm. Any other platform —
    and any error — returns None.
    """
    if proc is None:
        return None
    pid = getattr(proc, "pid", None)
    if not pid:
        return None
    try:
        if _is_windows():
            ctypes, counters_cls, psapi = _windows_mem_api()
            handle = getattr(proc, "_handle", None)
            if not handle:
                return None
            counters = counters_cls()
            counters.cb = ctypes.sizeof(counters_cls)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return counters.WorkingSetSize / (1024 * 1024)
        with open(f"/proc/{pid}/statm", "r") as fh:
            pages = int(fh.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        return None


def _find_deno_cache_dir():
    """Bundled DENO_DIR with vendored npm deps (happy-dom) for the DOM worker.

    Populated at build time by running the dom worker once with
    DENO_DIR=<cache>. Returns None when absent — the DOM mode then stays
    unavailable and sieve JS res/url rules keep the old skip behaviour
    (fail-open)."""
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, "deno_cache")
    if os.path.isdir(local):
        return local
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for cand in (os.path.join(exe_dir, "bin", "deno_cache"),
                     os.path.join(exe_dir, "deno_cache")):
            if os.path.isdir(cand):
                return cand
    return None


class DenoJsEngine:
    """Execute Imagus JS sieve rules in a reusable Deno subprocess worker."""

    def __init__(self, bin_path=None, worker_path=None, timeout=DEFAULT_CALL_TIMEOUT):
        self._bin = bin_path or find_deno_bin()
        self._worker = worker_path or _find_worker_script()
        self._dom_worker = _find_dom_worker_script()
        self._gateway_worker = _find_gateway_worker_script()
        self._deno_cache = _find_deno_cache_dir()
        self._timeout = timeout
        self._proc = None
        self._lock = threading.Lock()
        self._dom_proc = None
        self._dom_lock = threading.Lock()
        self._gateway_proc = None
        self._gateway_lock = threading.Lock()
        self._cache = {}
        self._cache_order = []
        # B-19: a doomed worker used to be completely silent (stderr=DEVNULL,
        # every failure path logged at DEBUG). Keep the worker's stderr in a
        # per-engine temp file so a broken bundled cache / a syntax error in
        # worker.js surfaces as one warning instead of nothing happening.
        self._stderr_log = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
        self._stderr_flagged = False
        # B-16(b)/A-08: every request carries its own id and the response must
        # echo it. Requests used to be hard-coded to 0 with no verification, so
        # a desynchronised stream (a rule writing to stdout) was silently
        # attributed to the current request.
        self._request_id = 0
        # B-17: the DOM worker holds one parsed happy-dom document per call; the
        # process is recycled every K.JS_WORKER_MAX_REQUESTS calls instead of
        # growing for the whole task (measured: 70 MB vs 118 MB after 50 calls).
        self._proc_calls = 0
        self._dom_calls = 0
        self._gateway_calls = 0
        try:
            from src import constants as K
            self._max_requests = getattr(K, "JS_WORKER_MAX_REQUESTS", 200)
            self._max_rss_mb = getattr(K, "JS_WORKER_MAX_RSS_MB", 256)
        except Exception:
            self._max_requests = 200
            self._max_rss_mb = 256

    # --- availability ---------------------------------------------------

    def available(self) -> bool:
        return bool(self._bin) and bool(self._worker)

    def dom_available(self) -> bool:
        """DOM mode (happy-dom res rules) ready: worker script + npm cache."""
        return bool(self._bin) and bool(self._dom_worker) and bool(self._deno_cache)

    def bin_path(self):
        return self._bin

    # --- process lifecycle ----------------------------------------------

    def _ensure_proc(self):
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        # (Re)spawn the worker. Deno's first run compiles the script; --quiet
        # suppresses the "Check file:///..." banner.
        cmd = [self._bin, "run", "--quiet", self._worker]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_log,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_popen_kwargs(),
            )
        except Exception as e:
            logger.warning(f"Deno worker spawn failed: {e}")
            self._log_stderr_tail("spawn failure")
            self._proc = None
            raise
        return self._proc

    def _log_stderr_tail(self, reason: str = "", limit: int = 2000) -> None:
        """B-19: log the worker's stderr once, when something went wrong.

        Without this, a worker that dies on every call (incomplete bundled
        deno_cache, syntax error in worker.js) produced zero log lines above
        DEBUG — the user turned the engine on and nothing happened. One-shot: a
        reproducibly broken setup would otherwise log per call.
        """
        if self._stderr_flagged:
            return
        try:
            self._stderr_log.flush()
            self._stderr_log.seek(0)
            tail = self._stderr_log.read()[-limit:]
        except Exception:
            return
        if not tail or not tail.strip():
            return
        self._stderr_flagged = True
        logger.warning(f"Deno worker stderr{' (' + reason + ')' if reason else ''}: "
                       f"{tail.strip()[-limit:]!r}")

    def _kill_worker(self, proc):
        """Terminate a worker subprocess and fully reap it (DL-9).

        A bare kill() leaves the child un-reaped (zombie on POSIX until GC,
        leaked handles on Windows). Bounded: 2s grace for terminate(), then
        kill() + a final wait() to reap.

        B-15: this used to swallow every failure silently — a worker that
        refused to die leaked as a process plus two pipe handles per timed-out
        call, with no trace in the log. Failures are now reported, the pipes are
        closed (the survivor never sees EOF otherwise), and the caller decides
        whether the handle can be dropped.
        """
        if proc is None:
            return
        reaped = False
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        pass
            reaped = proc.poll() is not None
        except Exception as e:
            logger.warning(f"Deno worker reap failed: {e}")
            self._log_stderr_tail("reap failure")
        finally:
            for stream in (getattr(proc, "stdin", None), getattr(proc, "stdout", None)):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            # stderr is the engine's own temp file (B-19) — closing it here would
            # break the next spawn; only a foreign stderr handle is closed.
            proc_stderr = getattr(proc, "stderr", None)
            if proc_stderr is not None and proc_stderr is not self._stderr_log:
                try:
                    proc_stderr.close()
                except Exception:
                    pass
        if not reaped:
            self._log_stderr_tail("worker survived terminate/kill")

    def _kill_proc(self):
        """Kill the plain worker, keeping the handle while it is still alive.

        B-15: `self._proc` used to be cleared before the kill was attempted, so a
        failed kill made the live process invisible: nothing would ever reap it
        and the next call spawned a second worker next to it.
        """
        proc = self._proc
        if proc is None:
            return
        self._kill_worker(proc)
        if proc.poll() is None:
            logger.warning("Deno worker is still alive after terminate()/kill() — "
                           "keeping the handle so it can be retried")
            return
        self._proc = None

    def _ensure_dom_proc(self):
        if self._dom_proc is not None and self._dom_proc.poll() is None:
            return self._dom_proc
        cmd = [self._bin, "run", "--quiet", self._dom_worker]
        env = os.environ.copy()
        env["DENO_DIR"] = self._deno_cache
        try:
            self._dom_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_log,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **_popen_kwargs(),
            )
        except Exception as e:
            logger.warning(f"Deno DOM worker spawn failed: {e}")
            self._log_stderr_tail("DOM spawn failure")
            self._dom_proc = None
            raise
        return self._dom_proc

    def _kill_dom_proc(self):
        proc = self._dom_proc
        if proc is None:
            return
        self._kill_worker(proc)
        if proc.poll() is None:
            logger.warning("Deno DOM worker is still alive after terminate()/kill()")
            return
        self._dom_proc = None

    def _ensure_gateway_proc(self):
        """Spawn the P4 gateway worker (happy-dom, JS evaluation ON).

        Uses the same DENO_DIR npm cache as the DOM worker. Separate process
        and lock from the DOM worker so a slow gateway click (up to its own
        timeout) never blocks sieve res/url rules on other pages.
        """
        if self._gateway_proc is not None and self._gateway_proc.poll() is None:
            return self._gateway_proc
        cmd = [self._bin, "run", "--quiet", self._gateway_worker]
        env = os.environ.copy()
        env["DENO_DIR"] = self._deno_cache
        try:
            self._gateway_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_log,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **_popen_kwargs(),
            )
        except Exception as e:
            logger.warning(f"Deno gateway worker spawn failed: {e}")
            self._log_stderr_tail("gateway spawn failure")
            self._gateway_proc = None
            raise
        return self._gateway_proc

    def _kill_gateway_proc(self):
        proc = self._gateway_proc
        if proc is None:
            return
        self._kill_worker(proc)
        if proc.poll() is None:
            logger.warning("Deno gateway worker is still alive after terminate()/kill()")
            return
        self._gateway_proc = None

    def shutdown(self):
        """Terminate all workers. Safe to call multiple times."""
        with self._lock:
            self._kill_proc()
            self._cache.clear()
            self._cache_order.clear()
        with self._dom_lock:
            self._kill_dom_proc()
        with self._gateway_lock:
            self._kill_gateway_proc()
        # B-19: release the stderr temp file. Safe to call repeatedly (`shutdown`
        # is documented as idempotent); a closed file only costs the tail log.
        try:
            self._stderr_log.close()
        except Exception:
            pass

    # --- cache ----------------------------------------------------------

    # DL-10: cached None results (failed rules) must be distinguishable from a
    # cache miss, otherwise every failing rule re-runs on each matching thumb.
    _MISS = object()

    def _cache_get(self, key):
        return self._cache.get(key, self._MISS)

    def _cache_set(self, key, value):
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            # Drop oldest half of the FIFO to keep the cache bounded.
            drop = self._cache_order[:CACHE_MAX_ENTRIES // 2]
            for k in drop:
                self._cache.pop(k, None)
            self._cache_order = self._cache_order[CACHE_MAX_ENTRIES // 2:]
        if key not in self._cache:
            self._cache_order.append(key)
        self._cache[key] = value

    # --- core call ------------------------------------------------------

    def run_js(self, code: str, groups, page_url: str = "") -> str | None:
        """Execute a sieve JS rule body (after ':') and return a string or None.

        Thread-safe; blocks up to self._timeout. Fail-open: returns None on
        any error so callers keep the static path unchanged.
        """
        if not self.available() or not code:
            return None
        code = code.lstrip()
        code = code[1:] if code.startswith(":") else code
        groups = list(groups or [])
        key = (code, tuple(groups), page_url or "")
        with self._lock:
            cached = self._cache_get(key)
            if cached is not self._MISS:
                return cached

            result = None
            try:
                result = self._call(code, groups, page_url or "")
            except Exception as e:
                logger.debug(f"Deno JS rule failed ({type(e).__name__}: {e})")
                result = None
            self._cache_set(key, result)
            return result

    def _call(self, code: str, groups, page_url: str):
        proc = self._ensure_proc()
        request_id = self._next_request_id()
        request = json.dumps({
            "id": request_id,
            "code": code,
            "groups": groups,
            "pageUrl": page_url,
        })
        try:
            self._send(proc, request, self._kill_proc)
        except Exception:
            # Broken pipe / blocked write — worker died or is stuck; respawn next
            # call.
            self._kill_proc()
            raise
        resp = self._read_matching_response(proc, request_id, self._kill_proc)
        self._recycle_if_due(self._kill_proc, "proc")
        if resp.get("error"):
            # JS rule threw (e.g. it needs DOM). Not a fatal error.
            logger.debug(f"Deno JS rule error: {resp['error'][:160]}")
            return None
        return resp.get("result")

    def _recycle_if_due(self, killer, counter_attr: str) -> None:
        """B-17: retire a worker after K.JS_WORKER_MAX_REQUESTS calls.

        The next call pays one cold spawn (~300 ms) and starts from a fresh
        process, which is what bounds resident memory on long galleries.

        Errors.txt (2): a call count alone does not bound memory. The DOM
        worker holds one parsed document per call and 200 calls of a 40 KB page
        measured 1.4 GB in the child; the worker is therefore also retired once
        its own resident memory reaches K.JS_WORKER_MAX_RSS_MB. Fail-open: an
        unreadable reading leaves the count rule in charge.
        """
        count = getattr(self, "_" + counter_attr + "_calls") + 1
        setattr(self, "_" + counter_attr + "_calls", count)
        if self._max_requests and count >= self._max_requests:
            logger.debug("Recycling the %s worker after %d calls", counter_attr, count)
            setattr(self, "_" + counter_attr + "_calls", 0)
            killer()
            return
        rss = child_rss_mb(getattr(self, "_" + counter_attr + "_proc", None))
        if rss is not None and self._max_rss_mb and rss >= self._max_rss_mb:
            logger.info(
                f"Recycling the {counter_attr} worker: {rss:.0f} MB resident "
                f"(budget {self._max_rss_mb} MB) after {count} call(s)")
            setattr(self, "_" + counter_attr + "_calls", 0)
            killer()

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, proc, payload: str, killer) -> None:
        """A-15: write one protocol line without hanging the caller.

        `proc.stdin.write` is fully blocking (`bufsize=1` flushes immediately)
        and the child does not read stdin until its own startup finished —
        happy-dom's `import` for the DOM/gateway workers, Deno's "Check" step
        for the plain one. Anything past the pipe buffer (~72 KB: every real
        gallery page) therefore blocked the caller indefinitely, and the read
        timeout never covered it: `shutdown()` then waited forever on the
        worker lock, so Stop could not end the task.

        The write runs on its own thread; if it does not complete within the
        call timeout, the worker is killed and the caller sees an OSError
        (fail-open).
        """
        err = []

        def _write():
            try:
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()
            except Exception as e:  # broken pipe — the caller respawns
                err.append(e)

        t = threading.Thread(target=_write, daemon=True)
        t.start()
        t.join(self._timeout)
        if t.is_alive():
            killer()
            raise OSError("Deno worker stdin write blocked")
        if err:
            raise err[0]

    def _read_matching_response(self, proc, request_id: int, killer):
        """B-16(b): read the response whose `id` matches our request.

        One foreign line is skipped (its answer is the next line); a second
        mismatch means the stream is desynchronised for good, so the worker is
        killed and the caller fails open.
        """
        for _ in range(2):
            line = self._read_line_timeout(proc, killer=killer)
            if line is None:
                raise TimeoutError("Deno worker timed out")
            try:
                resp = json.loads(line)
            except ValueError:
                raise ValueError(f"Bad worker response: {line[:120]!r}")
            if resp.get("id") == request_id:
                return resp
            logger.warning(
                "Deno worker answered with a foreign request id (%r) — skipping the line",
                resp.get("id"),
            )
        killer()
        raise ValueError("Deno worker protocol desynchronised (repeated id mismatch)")

    def _read_line_timeout(self, proc, killer=None, timeout: float | None = None) -> str | None:
        """Read one stdout line, aborting (and killing the worker) on timeout.

        `killer` is a callable that terminates the right process (plain vs
        DOM vs gateway worker); defaults to killing the plain worker. `timeout`
        overrides the instance default (used by the gateway worker, which
        needs more headroom than a sieve rule call)."""
        timeout = self._timeout if timeout is None else timeout
        result_holder = [None]

        def _reader():
            try:
                line = proc.stdout.readline()
                result_holder[0] = line
            except Exception:
                result_holder[0] = None

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            # B-16: `join` can return while the reader is still a few
            # microseconds away from storing its line, so a response that
            # arrived exactly at the deadline used to be thrown away — together
            # with the healthy worker, at the cost of a ~300 ms cold respawn on
            # the next call. Give the reader a short grace and re-check.
            t.join(timeout=0.05)
            if t.is_alive():
                # Worker is stuck (infinite loop inside a rule). Kill and re-raise.
                (killer or self._kill_proc)()
                raise TimeoutError("Deno worker timed out")
        line = result_holder[0]
        if not line:
            raise OSError("Deno worker closed stdout")
        return line.strip()

    # --- DOM mode (P1: sieve JS res/url rules against a real document) ----

    def run_dom_js(self, code: str, html: str, page_url: str = "",
                   groups=None, href: str = ""):
        """Evaluate a sieve JS rule against a parsed document (happy-dom).

        Returns a list of URL strings or None (fail-open on any error).
        Blocks up to self._timeout. No result cache: each call carries a
        unique page document.
        """
        if not self.dom_available() or not code:
            return None
        code = code.lstrip()
        code = code[1:] if code.startswith(":") else code
        groups = list(groups or [])
        with self._dom_lock:
            try:
                return self._call_dom(code, html or "", page_url or "",
                                      groups, href or "")
            except Exception as e:
                logger.debug(f"Deno DOM rule failed ({type(e).__name__}: {e})")
                return None

    def _call_dom(self, code: str, html: str, page_url: str, groups, href: str):
        proc = self._ensure_dom_proc()
        request_id = self._next_request_id()
        request = json.dumps({
            "id": request_id,
            "html": html,
            "pageUrl": page_url,
            "code": code,
            "groups": groups,
            "href": href,
        })
        try:
            self._send(proc, request, self._kill_dom_proc)
        except Exception:
            # Broken pipe / blocked write — worker died or is stuck; respawn next
            # call.
            self._kill_dom_proc()
            raise
        resp = self._read_matching_response(proc, request_id, self._kill_dom_proc)
        self._recycle_if_due(self._kill_dom_proc, "dom")
        if resp.get("error"):
            logger.debug(f"Deno DOM rule error: {resp['error'][:160]}")
            return None
        result = resp.get("result")
        return result if isinstance(result, list) else None

    # --- P4 gateway click mode (consent/age buttons, JS evaluation ON) -----

    def run_gateway_click(self, html: str, page_url: str = "",
                          text_patterns=None, overlay_selectors=None,
                          timeout: float = 6.0):
        """Simulate a click on a consent/age-gate button in happy-dom.

        Unlike the sieve DOM worker (enableJavaScriptEvaluation=false), the
        gateway worker evaluates the page's JS so the button's onclick handler
        really runs. Returns a dict {cookies, html_after, redirect,
        reload_requested} or None (fail-open on any error, including no button
        found). Bounded: one synchronous call, worker killed on timeout.
        """
        if (not self.available() or not self._gateway_worker
                or not self._deno_cache or not html):
            return None
        patterns = [p for p in (text_patterns or []) if isinstance(p, str)]
        overlays = [s for s in (overlay_selectors or []) if isinstance(s, str)]
        with self._gateway_lock:
            try:
                proc = self._ensure_gateway_proc()
                request_id = self._next_request_id()
                request = json.dumps({
                    "id": request_id,
                    "html": html,
                    "pageUrl": page_url or "",
                    "textPatterns": patterns,
                    "overlaySelectors": overlays,
                })
                try:
                    self._send(proc, request, self._kill_gateway_proc)
                except Exception:
                    self._kill_gateway_proc()
                    raise
                resp_line = self._read_line_timeout(
                    proc, killer=self._kill_gateway_proc, timeout=timeout
                )
                if resp_line is None:
                    raise TimeoutError("Deno gateway worker timed out")
                resp = json.loads(resp_line)
                if resp.get("id") != request_id:
                    raise ValueError(
                        f"Deno gateway worker answered with a foreign id: {resp.get('id')!r}"
                    )
                self._recycle_if_due(self._kill_gateway_proc, "gateway")
                if resp.get("error"):
                    logger.debug(f"Deno gateway click error: {resp['error'][:160]}")
                    return None
                result = resp.get("result")
                if not isinstance(result, dict):
                    return None
                out = {
                    "cookies": result.get("cookies") or {},
                    "html_after": result.get("html_after") or "",
                    "redirect": result.get("redirect"),
                    "reload_requested": bool(result.get("reload_requested")),
                }
                return out
            except Exception as e:
                logger.debug(f"Deno gateway click failed ({type(e).__name__}: {e})")
                return None
