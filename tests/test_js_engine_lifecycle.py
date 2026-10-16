#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""F-15 (2026-10-15): Deno worker process lifecycle.

VERIFY_2026-10-15 §3.5/F-15: neither JS suite asserted a single process-lifecycle
contract — `_kill_proc`, broken pipes, respawn, a rule stuck in `while(true)` and
a large protocol payload had zero mentions. Those suites also skip wholesale when
Deno is absent (27 passed / 30 skipped on this machine), so the guarantees that
protect a live crawl were untested exactly where they matter.

These tests therefore drive the engine against a *fake* process instead of Deno:
they run everywhere, they are fast, and they pin the behaviour a real worker
depends on:

  * a worker is spawned lazily, reused between calls, and respawned after it died;
  * a broken pipe is fail-open (the call returns None, the caller keeps the
    static path) and the dead worker is reaped, not leaked;
  * a hung rule (infinite loop) is killed at the timeout and reported as None —
    never as an exception into the parser thread;
  * terminate() escalation to kill() when the worker ignores the polite signal;
  * the 64 KB payload vector survives the line-delimited JSON protocol intact;
  * shutdown() is idempotent and drops the rule cache.
"""

import json
import os
import subprocess
import logging
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parser.js_engine.engine import DenoJsEngine, child_rss_mb


class _RecordingStdin:
    def __init__(self, broken=False, block_for=0.0):
        self.written = []
        self.flushes = 0
        self.broken = broken
        self.block_for = block_for
        self.closed = False

    def write(self, data):
        if self.broken:
            raise BrokenPipeError("worker is gone")
        if self.block_for:
            # A-15: a child that has not started reading stdin yet (happy-dom
            # import / Deno's Check step) blocks the writer for real.
            time.sleep(self.block_for)
        self.written.append(data)
        return len(data)

    def flush(self):
        if self.broken:
            raise BrokenPipeError("worker is gone")
        self.flushes += 1

    def close(self):
        self.closed = True


class _FakeStdout:
    def __init__(self, lines=(), block_for=0.0, wait_event=None, stdin=None,
                 echo_id=True):
        self._lines = list(lines)
        self._block_for = block_for
        self._wait_event = wait_event
        self._stdin = stdin
        self._echo_id = echo_id
        self.closed = False

    def readline(self):
        if self._wait_event is not None:
            self._wait_event.wait(5)  # bounded so a broken test cannot hang
        if self._block_for:
            time.sleep(self._block_for)  # a rule stuck in while(true)
        if self._lines:
            return self._echo_request_id(self._lines.pop(0))
        return ""  # worker closed stdout

    def _echo_request_id(self, line):
        """The real workers echo the request id (B-16b); so must the double.

        Returns the response line with the id of the most recent request, so a
        canned `{"id": 0, ...}` response does not look like a foreign line.
        """
        if not self._echo_id or self._stdin is None or not self._stdin.written:
            return line
        try:
            request_id = json.loads(self._stdin.written[-1].strip()).get("id")
            response = json.loads(line)
        except Exception:
            return line
        if not isinstance(response, dict) or request_id is None:
            return line
        response["id"] = request_id
        return json.dumps(response) + "\n"

    def close(self):
        self.closed = True


class _FakeProc:
    """Minimal subprocess.Popen stand-in with reap accounting."""

    def __init__(self, alive=True, lines=(), block_for=0.0, broken_pipe=False,
                 refuse_terminate=False, immortal=False, wait_event=None,
                 write_block_for=0.0, echo_id=True):
        self.stdin = _RecordingStdin(broken=broken_pipe, block_for=write_block_for)
        self.stdout = _FakeStdout(lines, block_for, wait_event, stdin=self.stdin,
                                  echo_id=echo_id)
        self._alive = alive
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = []
        self._refuse_terminate = refuse_terminate
        self._waits = 0
        self._immortal = immortal

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminate_calls += 1
        if not (self._refuse_terminate or self._immortal):
            self._alive = False

    def kill(self):
        self.kill_calls += 1
        if not self._immortal:
            self._alive = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self._waits += 1
        if self._refuse_terminate and self._waits == 1:
            raise subprocess.TimeoutExpired("deno", timeout)
        if not self._immortal:
            self._alive = False
        return 0


def _engine(**kwargs):
    """An engine that looks available without a Deno binary on the machine."""
    return DenoJsEngine(bin_path="deno-stub", worker_path="worker.js", **kwargs)


def _response(result):
    return json.dumps({"id": 0, "result": result}) + "\n"


class TestWorkerLifecycle(unittest.TestCase):

    def test_worker_spawned_lazily_and_reused(self):
        proc = _FakeProc(lines=[_response("one"), _response("two")])
        engine = _engine()
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc) as popen:
            self.assertEqual(engine.run_js("code-1", []), "one")
            self.assertEqual(engine.run_js("code-2", []), "two")
        self.assertEqual(popen.call_count, 1,
                         "the worker must be spawned once and reused")
        self.assertEqual(proc.stdin.flushes, 2, "each request must be flushed")

    def test_dead_worker_is_respawned(self):
        engine = _engine()
        engine._proc = _FakeProc(alive=False)
        fresh = _FakeProc(lines=[_response("ok")])
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=fresh) as popen:
            self.assertEqual(engine.run_js("code", []), "ok")
        self.assertEqual(popen.call_count, 1, "an exited worker must be replaced")
        self.assertIs(engine._proc, fresh)

    def test_spawn_failure_is_fail_open(self):
        engine = _engine()
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   side_effect=OSError("deno.exe missing")):
            self.assertIsNone(engine.run_js("code", []))
        self.assertIsNone(engine._proc)


class TestWorkerDeath(unittest.TestCase):

    def test_broken_pipe_is_fail_open_and_respawns(self):
        """A worker that died between calls must not raise into the parser."""
        dead = _FakeProc(broken_pipe=True)
        engine = _engine()
        engine._proc = dead
        alive = _FakeProc(lines=[_response("recovered")])
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=alive) as popen:
            self.assertIsNone(engine.run_js("code-1", []),
                              "a dead worker must degrade to None, not raise")
            self.assertIsNone(engine._proc, "the dead worker must be dropped")
            self.assertEqual(dead.terminate_calls, 1, "the dead worker must be reaped")
            # A different rule (the first result is cached) spawns a fresh worker.
            self.assertEqual(engine.run_js("code-2", []), "recovered")
        self.assertEqual(popen.call_count, 1)

    def test_closed_stdout_is_fail_open(self):
        proc = _FakeProc(lines=[])
        engine = _engine()
        engine._proc = proc
        self.assertIsNone(engine.run_js("code", []))
        self.assertTrue(proc.stdin.written,
                        "the request must have been sent before the read failed")

    def test_hung_rule_is_killed_at_the_timeout(self):
        """`while(true)` inside a sieve rule must not hang the crawl."""
        engine = _engine(timeout=0.2)
        proc = _FakeProc(lines=[_response("never")], block_for=5.0)
        engine._proc = proc

        started = time.time()
        result = engine.run_js("while(true){}", [])
        elapsed = time.time() - started

        self.assertIsNone(result, "a timed-out rule must fail open")
        self.assertLess(elapsed, 2.0, f"the timeout did not bound the call ({elapsed:.2f}s)")
        self.assertEqual(proc.terminate_calls, 1, "the stuck worker must be killed")
        self.assertIsNone(engine._proc)

    def test_rule_error_response_is_none_not_exception(self):
        engine = _engine()
        engine._proc = _FakeProc(lines=[json.dumps({"id": 0, "error": "TypeError: x"}) + "\n"])
        self.assertIsNone(engine.run_js("throw 1", []))


class TestWorkerReaping(unittest.TestCase):

    def test_kill_proc_terminates_and_reaps(self):
        proc = _FakeProc()
        engine = _engine()
        engine._proc = proc
        engine._kill_proc()
        self.assertEqual(proc.terminate_calls, 1)
        self.assertIn(2, proc.wait_calls, "reap after terminate() with a bounded wait")
        self.assertIsNone(engine._proc)

    def test_terminate_escalates_to_kill(self):
        proc = _FakeProc(refuse_terminate=True)
        engine = _engine()
        engine._proc = proc
        engine._kill_proc()
        self.assertEqual(proc.terminate_calls, 1)
        self.assertEqual(proc.kill_calls, 1, "a worker ignoring terminate() must be killed")

    def test_kill_worker_none_is_a_noop(self):
        _engine()._kill_worker(None)  # must not raise

    def test_shutdown_is_idempotent_and_clears_cache(self):
        proc = _FakeProc()
        engine = _engine()
        engine._proc = proc
        engine._cache_set(("code", (), ""), "value")
        engine.shutdown()
        engine.shutdown()  # second call must be safe

        self.assertEqual(proc.terminate_calls, 1)
        self.assertEqual(engine._cache, {})
        self.assertEqual(engine._cache_order, [])
        self.assertIsNone(engine._proc)


class TestProtocolPayload(unittest.TestCase):

    def test_64kb_payload_round_trips_intact(self):
        """The protocol is one JSON line per request; 64 KB must not truncate."""
        big_code = "// padding " + "x" * (64 * 1024)
        big_result = "https://cdn.example.com/" + "y" * (64 * 1024) + ".jpg"
        proc = _FakeProc(lines=[_response(big_result)])
        engine = _engine()
        with patch("src.parser.js_engine.engine.subprocess.Popen", return_value=proc):
            result = engine.run_js(big_code, ["group"])

        self.assertEqual(result, big_result)
        self.assertEqual(len(result), len(big_result))

        sent = "".join(proc.stdin.written)
        self.assertTrue(sent.endswith("\n"), "the request must be newline-terminated")
        request = json.loads(sent)
        self.assertEqual(len(request["code"]), len(big_code),
                         "the request code was truncated on the way to the worker")
        self.assertEqual(request["groups"], ["group"])


class TestPopenIsWiredUp(unittest.TestCase):
    """The fake-process tests above are only meaningful if the engine really
    spawns through subprocess.Popen with the worker script."""

    def test_spawn_uses_the_worker_script_and_no_console_window(self):
        proc = _FakeProc(lines=[_response("ok")])
        engine = _engine()
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc) as popen:
            engine.run_js("code", [])
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], "deno-stub")
        self.assertIn("worker.js", cmd)
        kwargs = popen.call_args[1]
        self.assertTrue(kwargs["stdin"] is subprocess.PIPE)
        self.assertTrue(kwargs["stdout"] is subprocess.PIPE)
        if os.name == "nt":
            self.assertEqual(kwargs.get("creationflags"), subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
class TestWorkerDiagnostics(unittest.TestCase):
    """B-15 / B-19: a worker that will not die, or dies on every call, must not
    be invisible. Everything here runs without Deno (fake process)."""

    def _warnings(self, fn):
        """Collect WARNING+ records from the engine logger (version-agnostic)."""
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        log = logging.getLogger("src.parser.js_engine.engine")
        old_level = log.level
        log.addHandler(handler)
        log.setLevel(logging.WARNING)
        try:
            fn()
        finally:
            log.removeHandler(handler)
            log.setLevel(old_level)
        return [r.getMessage() for r in records]

    def test_kill_worker_closes_the_pipes(self):
        """A survivor must not keep the engine's pipe handles open."""
        proc = _FakeProc()
        engine = _engine()
        engine._proc = proc
        engine._kill_proc()
        self.assertTrue(proc.stdin.closed, "stdin must be closed so the child sees EOF")
        self.assertTrue(proc.stdout.closed)

    def test_surviving_worker_keeps_its_handle_and_is_not_duplicated(self):
        """B-15: a worker that ignores terminate()/kill() stays known to the engine.

        Before the fix `_proc` was cleared first, so the live process leaked and
        the next call spawned a second worker beside it.
        """
        proc = _FakeProc(immortal=True)
        engine = _engine()
        engine._proc = proc

        messages = self._warnings(engine._kill_proc)
        self.assertTrue(any("still alive" in m for m in messages),
                        f"the failed kill was silent: {messages}")
        self.assertIs(engine._proc, proc, "the surviving worker was forgotten")

        with patch("src.parser.js_engine.engine.subprocess.Popen") as popen:
            self.assertIs(engine._ensure_proc(), proc)
        popen.assert_not_called()

    def test_reap_failure_is_logged(self):
        """B-15: `terminate()` raising must not be swallowed in silence."""
        class _RaisingProc(_FakeProc):
            def terminate(self):
                self.terminate_calls += 1
                raise OSError("WinError 5: access denied")

        proc = _RaisingProc()
        engine = _engine()
        engine._proc = proc
        messages = self._warnings(engine._kill_proc)
        self.assertTrue(any("reap failed" in m for m in messages), messages)

    def test_spawn_failure_reports_the_worker_stderr(self):
        """B-19: a doomed worker's own stderr must reach the log."""
        engine = _engine()
        engine._stderr_log.write('error: Module not found "happy_dom"\\n')
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   side_effect=OSError("spawn failed")):
            messages = self._warnings(lambda: self.assertIsNone(engine.run_js("code", [])))
        joined = " | ".join(messages)
        self.assertIn("spawn failed", joined)
        self.assertIn("happy_dom", joined, "the worker's stderr never reached the log")

    def test_stderr_tail_is_logged_at_most_once(self):
        engine = _engine()
        engine._stderr_log.write("boom: bad cache\n")
        first = self._warnings(lambda: engine._log_stderr_tail("first"))
        second = self._warnings(lambda: engine._log_stderr_tail("second"))
        self.assertEqual(len(first), 1, first)
        self.assertIn("boom", first[0])
        self.assertEqual(second, [], "the stderr tail was logged more than once")

    def test_empty_stderr_is_not_logged(self):
        engine = _engine()
        self.assertEqual(self._warnings(lambda: engine._log_stderr_tail("nothing")), [])

    def test_worker_stderr_is_a_file_not_devnull(self):
        proc = _FakeProc(lines=[_response("ok")])
        engine = _engine()
        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc) as popen:
            engine.run_js("code", [])
        kwargs = popen.call_args[1]
        self.assertIsNot(kwargs["stderr"], subprocess.DEVNULL,
                         "worker stderr is discarded again (B-19)")
        self.assertIs(kwargs["stderr"], engine._stderr_log)


class TestTimeoutGrace(unittest.TestCase):
    """B-16: a response that lands on the deadline must not be discarded."""

    def test_response_arriving_inside_the_grace_window_is_kept(self):
        """B-16 (detector): the grace join must rescue a deadline response.

        The reader is released 10 ms AFTER its 200 ms deadline, i.e. inside the
        grace window the fixed code waits (200–250 ms). The failing case is
        deterministic: `time.sleep` never returns early, so without the grace the
        check at the deadline always finds the reader still blocked, the worker
        is killed and its answer discarded — the test went RED in every run with
        the grace removed.

        The passing side needs the sleep to overrun by less than the remaining
        grace (≈40 ms of a 210 ms sleep, >4x the usual scheduler jitter). The
        earlier form released the reader through a `threading.Timer` at exactly
        the moment the grace expired, so it had no margin at all and failed
        intermittently under full-suite load (observed once in this run).
        """
        engine = _engine(timeout=0.2)
        proc = _FakeProc(lines=[_response("late-but-valid")], block_for=0.21)
        engine._proc = proc

        result = engine.run_js("code", [])

        self.assertEqual(result, "late-but-valid",
                         "a response that arrived at the deadline was thrown away")
        self.assertEqual(proc.terminate_calls, 0,
                         "a healthy worker was killed at the deadline")

    def test_truly_stuck_rule_is_still_killed(self):
        """Control for the grace above: a genuinely stuck worker is still killed."""
        engine = _engine(timeout=0.2)
        proc = _FakeProc(lines=[_response("never")], block_for=5.0)
        engine._proc = proc

        started = time.time()
        self.assertIsNone(engine.run_js("while(true){}", []))
        elapsed = time.time() - started

        self.assertLess(elapsed, 2.0, f"the timeout stopped bounding the call ({elapsed:.2f}s)")
        self.assertEqual(proc.terminate_calls, 1)
        self.assertIsNone(engine._proc)


class TestStdinWriteIsBounded(unittest.TestCase):
    """A-15: a child that does not read stdin must not hang the caller.

    `proc.stdin.write` blocks once the pipe buffer is full (~72 KB — every real
    gallery page) and the child only starts reading after its own startup
    (happy-dom's npm import, Deno's Check step). The read timeout never covered
    that window, so `shutdown()` waited forever on the worker lock and Stop
    could not end the task.
    """

    def test_blocked_write_is_bounded_and_the_worker_is_killed(self):
        engine = _engine(timeout=0.1)
        proc = _FakeProc(lines=[_response("ok")], write_block_for=0.6)
        engine._proc = proc

        started = time.time()
        result = engine.run_js("code", [])
        elapsed = time.time() - started

        self.assertIsNone(result, "a blocked write must fail open")
        self.assertLess(elapsed, 1.0, f"the caller was stuck for {elapsed:.2f}s")
        self.assertGreaterEqual(proc.kill_calls or proc.terminate_calls, 1,
                                "the worker with the blocked pipe was left running")
        self.assertIsNone(engine._proc)

    def test_a_normal_write_still_round_trips(self):
        """Control: the guarded write path is still the normal path."""
        engine = _engine()
        proc = _FakeProc(lines=[_response("fine")])
        engine._proc = proc
        self.assertEqual(engine.run_js("code", []), "fine")
        self.assertEqual(proc.stdin.flushes, 1)


class TestResponseIdCorrelation(unittest.TestCase):
    """B-16(b)/A-08: a response is only accepted for the request that asked.

    Requests used to be hard-coded to `id: 0` and the answer was never checked,
    so a rule that wrote its own line to stdout (via the `Deno` global, the
    A-08 injection) had that line attributed to the current request.
    """

    def test_every_request_carries_a_fresh_id(self):
        engine = _engine()
        proc = _FakeProc(lines=[_response("a"), _response("b")])
        engine._proc = proc
        engine.run_js("c1", [])
        engine.run_js("c2", [])
        ids = [json.loads(line)["id"] for line in proc.stdin.written]
        self.assertEqual(len(set(ids)), 2, f"request ids reused: {ids}")

    def test_a_foreign_line_is_skipped_and_the_real_answer_used(self):
        engine = _engine()
        # The engine's first request id is 1: line one is the injected line, the
        # second carries the real answer for THIS request.
        proc = _FakeProc(lines=[json.dumps({"id": 999, "result": "https://evil/x.jpg"}) + "\n",
                                json.dumps({"id": 1, "result": "https://real/x.jpg"}) + "\n"],
                         echo_id=False)
        engine._proc = proc

        self.assertEqual(engine.run_js("code", []), "https://real/x.jpg")
        self.assertEqual(proc.terminate_calls, 0, "a single foreign line is recoverable")

    def test_repeated_foreign_lines_kill_the_worker_and_fail_open(self):
        engine = _engine()
        proc = _FakeProc(lines=[json.dumps({"id": 999, "result": "a"}) + "\n",
                                json.dumps({"id": 998, "result": "b"}) + "\n"],
                         echo_id=False)
        engine._proc = proc

        self.assertIsNone(engine.run_js("code", []))
        self.assertGreaterEqual(proc.kill_calls or proc.terminate_calls, 1)


class TestWorkerRecycling(unittest.TestCase):
    """B-17: a worker is retired after K.JS_WORKER_MAX_REQUESTS calls.

    The DOM worker parses a page into a fresh happy-dom Window per call and the
    process lives for the whole task, so memory only stopped growing when the
    process did (measured: 70 MB with recycling vs 118 MB without).
    """

    def test_worker_is_recycled_after_the_call_budget(self):
        proc = _FakeProc(lines=[_response("a")] * 3)
        engine = _engine()
        engine._max_requests = 2

        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc) as popen:
            self.assertEqual(engine.run_js("c1", []), "a")
            self.assertEqual(engine.run_js("c2", []), "a")  # budget reached → recycle
            self.assertEqual(engine.run_js("c3", []), "a")  # fresh worker

        self.assertGreaterEqual(proc.terminate_calls, 1)
        self.assertEqual(popen.call_count, 2,
                         "the call after the budget must spawn a fresh worker")

    def test_budget_disabled_keeps_one_worker(self):
        """Control: with the budget off the worker is reused (no needless spawn)."""
        proc = _FakeProc(lines=[_response("a")] * 3)
        engine = _engine()
        engine._max_requests = 0

        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc) as popen:
            for _ in range(3):
                self.assertEqual(engine.run_js("c", []), "a")

        self.assertEqual(popen.call_count, 1)

    def test_worker_is_recycled_when_its_memory_reaches_the_budget(self):
        """Errors.txt (2): a CALL count cannot bound memory.

        The DOM worker keeps one parsed document per call (measured: 1.4 MB/call
        on a 4 KB page, 13 MB/call on a 40 KB one), so 200 calls of a real page
        put 1.4 GB into the child. The worker must be retired on its own
        resident size, long before the call budget.
        """
        proc = _FakeProc(lines=[_response("a")] * 3)
        engine = _engine()
        engine._max_requests = 0        # count rule off: only memory can recycle
        engine._max_rss_mb = 256

        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc) as popen, \
                patch("src.parser.js_engine.engine.child_rss_mb",
                      return_value=300.0):
            self.assertEqual(engine.run_js("c1", []), "a")
            self.assertGreaterEqual(proc.terminate_calls, 1,
                                    "a worker over its memory budget must be retired")
            self.assertEqual(engine.run_js("c2", []), "a")

        self.assertEqual(popen.call_count, 2, "the next call needs a fresh worker")

    def test_an_unreadable_memory_reading_keeps_the_count_rule_only(self):
        """Control: fail-open — no reading must never mean "recycle every call"."""
        proc = _FakeProc(lines=[_response("a")] * 3)
        engine = _engine()
        engine._max_requests = 0

        with patch("src.parser.js_engine.engine.subprocess.Popen",
                   return_value=proc), \
                patch("src.parser.js_engine.engine.child_rss_mb", return_value=None):
            for _ in range(3):
                self.assertEqual(engine.run_js("c", []), "a")

        self.assertEqual(proc.terminate_calls, 0)


class TestChildMemoryReading(unittest.TestCase):
    """The helper the memory budget depends on."""

    def test_reads_a_real_child_process(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            rss = child_rss_mb(proc)
        finally:
            proc.kill()
            proc.wait(timeout=5)
        if rss is None:
            self.skipTest("no readable resident memory for a child on this platform")
        self.assertGreater(rss, 1.0, "a running Python child is more than 1 MB resident")

    def test_unreadable_inputs_return_none(self):
        self.assertIsNone(child_rss_mb(None))
        self.assertIsNone(child_rss_mb(object()))


class TestWorkerSourceGuards(unittest.TestCase):
    """A-08 / B-18 / B-17 in the worker scripts themselves.

    Deno is not installed on this machine (the suite's 30 skips), so the JS half
    of these fixes cannot be exercised here. The Python half of A-08/B-16(b) is
    tested behaviourally above; the worker-side guards are pinned at the source.
    """

    def _source(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src", "parser", "js_engine", name)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_deno_global_is_shadowed_inside_the_compiled_rule_body(self):
        # The shadow must be concatenated INTO the function body — a comment or
        # a stray statement elsewhere would not shadow the global.
        self.assertIn("const Deno = undefined;\\n' + wrapped",
                      self._source("worker.js"))
        self.assertIn("const Deno = undefined;\\n' + code",
                      self._source("dom_worker.js"))

    def test_every_worker_caps_the_accumulated_request(self):
        for name in ("worker.js", "dom_worker.js", "gateway_worker.js"):
            self.assertIn("if (acc.length > (64 << 20)) Deno.exit(1);",
                          self._source(name), name)

    def test_happydom_windows_are_released(self):
        for name in ("dom_worker.js", "gateway_worker.js"):
            src = self._source(name)
            self.assertIn("win.happyDOM.close()", src, name)
            self.assertIn("finally {", src, f"{name}: the release must run on every path")
            # …and AFTER the response is built: closing first empties the
            # document the rule was about to read (NF-10).
            self.assertGreater(src.index("win.happyDOM.close()"),
                               src.index("return JSON.stringify({ id, result"),
                               f"{name}: happyDOM.close() runs before the response is built")
