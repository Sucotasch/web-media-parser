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

logger = logging.getLogger(__name__)

WORKER_BASENAME = "worker.js"
DOM_WORKER_BASENAME = "dom_worker.js"
GATEWAY_WORKER_BASENAME = "gateway_worker.js"

# Timeout for a single JS rule call (seconds). Rules are tiny; this is a
# safety net against pathological infinite loops inside a sieve rule. Kept
# low because run_js blocks its caller synchronously (it is called from the
# async parser path via transform_image_url) — a long stall would freeze the
# whole event loop.
DEFAULT_CALL_TIMEOUT = 2.0

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


def _media_downloader_candidate():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or ""
    if _is_windows() and base:
        p = os.path.join(base, "media-downloader", "bin", "deno.exe")
        if os.path.exists(p):
            return p
    return None


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
    for cand in [_package_candidate(), _frozen_candidates(),
                 _media_downloader_candidate(), _path_candidate()]:
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
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_popen_kwargs(),
            )
        except Exception as e:
            logger.debug(f"Deno worker spawn failed: {e}")
            self._proc = None
            raise
        return self._proc

    def _kill_proc(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except Exception:
            pass

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
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **_popen_kwargs(),
            )
        except Exception as e:
            logger.debug(f"Deno DOM worker spawn failed: {e}")
            self._dom_proc = None
            raise
        return self._dom_proc

    def _kill_dom_proc(self):
        proc, self._dom_proc = self._dom_proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except Exception:
            pass

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
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **_popen_kwargs(),
            )
        except Exception as e:
            logger.debug(f"Deno gateway worker spawn failed: {e}")
            self._gateway_proc = None
            raise
        return self._gateway_proc

    def _kill_gateway_proc(self):
        proc, self._gateway_proc = self._gateway_proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except Exception:
            pass

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

    # --- cache ----------------------------------------------------------

    def _cache_get(self, key):
        return self._cache.get(key)

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
            if cached is not None:
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
        request = json.dumps({
            "id": 0,
            "code": code,
            "groups": groups,
            "pageUrl": page_url,
        })
        try:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
        except Exception:
            # Broken pipe — worker died; re-spawn on the next call.
            self._kill_proc()
            raise
        # Read the single response line with a timeout via a watcher thread.
        resp_line = self._read_line_timeout(proc)
        if resp_line is None:
            raise TimeoutError("Deno worker timed out")
        try:
            resp = json.loads(resp_line)
        except ValueError:
            raise ValueError(f"Bad worker response: {resp_line[:120]!r}")
        if resp.get("error"):
            # JS rule threw (e.g. it needs DOM). Not a fatal error.
            logger.debug(f"Deno JS rule error: {resp['error'][:160]}")
            return None
        return resp.get("result")

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
        request = json.dumps({
            "id": 0,
            "html": html,
            "pageUrl": page_url,
            "code": code,
            "groups": groups,
            "href": href,
        })
        try:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
        except Exception:
            # Broken pipe — worker died; re-spawn on the next call.
            self._kill_dom_proc()
            raise
        resp_line = self._read_line_timeout(proc, killer=self._kill_dom_proc)
        if resp_line is None:
            raise TimeoutError("Deno DOM worker timed out")
        try:
            resp = json.loads(resp_line)
        except ValueError:
            raise ValueError(f"Bad DOM worker response: {resp_line[:120]!r}")
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
                request = json.dumps({
                    "id": 0,
                    "html": html,
                    "pageUrl": page_url or "",
                    "textPatterns": patterns,
                    "overlaySelectors": overlays,
                })
                try:
                    proc.stdin.write(request + "\n")
                    proc.stdin.flush()
                except Exception:
                    self._kill_gateway_proc()
                    raise
                resp_line = self._read_line_timeout(
                    proc, killer=self._kill_gateway_proc, timeout=timeout
                )
                if resp_line is None:
                    raise TimeoutError("Deno gateway worker timed out")
                resp = json.loads(resp_line)
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
