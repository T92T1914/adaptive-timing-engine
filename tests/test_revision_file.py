"""Admission happens before workers; actual caller inputs reach default analysis."""
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import subprocess
import sys
from unittest.mock import patch

from adaptive_timing import revision_file as runner
from adaptive_timing.receipt_check import inspect_bytes
from adaptive_timing.revised_analysis import AnalysisSession, analyze


def manifest(rows):
    return json.dumps({"schema_version": 1, "revisions": rows}).encode()


def row(values, offset=0):
    return {"not_before": offset, "values": values}


class RevisionFileTests(unittest.TestCase):
    def assert_summary(self, report, values):
        current = report["session"]["current"]
        counts = Counter(values)
        self.assertEqual(current, {"count": len(values), "total": sum(values),
                                  "histogram": [counts[i] for i in range(256)],
                                  "digest": hashlib.sha256(bytes(values)).hexdigest()})
        self.assertTrue(report["session"]["planner_stopped"])
        self.assertEqual(report["session"]["outstanding"], 0)
        self.assertEqual(report["session"]["reserved_resources"], [])
        verdict = inspect_bytes(json.dumps(report).encode(), record="session")
        self.assertEqual(verdict["consistency"], "consistent", verdict)

    def test_actual_default_and_empty_final_input(self):
        for final in ([1, 2, 255, 2], []):
            with self.subTest(final=final):
                data = manifest([row([9]), row(final, .001)])
                report = runner.run_manifest(data)
                self.assertTrue(report["complete"], report)
                self.assert_summary(report, final)
                self.assertEqual(report["input_sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual([d["revision"] for d in report["deliveries"]], [1, 2])
                self.assertEqual([d["requested_not_before"] for d in report["deliveries"]], [0, .001])
                self.assertEqual([d["actual_delivery_session_elapsed"] for d in report["deliveries"]],
                                 [r["offered_at"] for r in report["session"]["revisions"]])

    def test_invalid_inputs_create_no_session(self):
        invalid = [b'\xff', b'{', b'{"schema_version":1,"schema_version":1,"revisions":[]}',
                   b'{"schema_version":1,"revisions":[{"not_before":NaN,"values":[]}]}',
                   b'{"schema_version":1,"revisions":[{"not_before":1e999,"values":[]}]}',
                   b'[' * 9 + b']' * 9, b' ' * (runner.MAX_BYTES + 1),
                   json.dumps({"schema_version": True, "revisions": [row([])]}).encode(),
                   json.dumps({"schema_version": 2, "revisions": [row([])]}).encode(),
                   json.dumps({"schema_version": 1, "revisions": [row([])], "callback": "x"}).encode()]
        invalid += [manifest(rows) for rows in ([], [row([])] * 65, [row([True])],
                    [row([-1])], [row([256])], [row([1.0])], [row([0] * 65537)],
                    [row([], True)], [row([], -1)], [row([], 10.01)],
                    [row([]), row([])], [row([], 1), row([], .5)],
                    [{"not_before": 0, "values": [], "path": "never-open"}])]
        with patch.object(runner, "AnalysisSession") as factory:
            for data in invalid:
                with self.subTest(prefix=data[:70]):
                    with self.assertRaises(runner.AdmissionError):
                        runner.run_manifest(data)
            factory.assert_not_called()

    def test_bounds_and_numeric_tokens(self):
        admitted = runner.admit_bytes(manifest([row([255] * 65536)]))
        self.assertEqual(len(admitted[0][1]), 65536)
        self.assertEqual(len(runner.admit_bytes(manifest([row([], i / 10) for i in range(64)]))), 64)
        for token in ('9' * 65, '1e' + '9' * 63, 'Infinity', '-Infinity'):
            with self.subTest(token=token):
                data = ('{"schema_version":1,"revisions":[{"not_before":' + token + ',"values":[]}]}').encode()
                with self.assertRaises(runner.AdmissionError):
                    runner.admit_bytes(data)

    def test_gated_refusal_replacement_stale_and_current(self):
        started, release = threading.Event(), threading.Event()
        elapsed = [0.0]
        sessions, pending = [], []

        def gated(values, cancellation, progress):
            def observed(count):
                progress(count)
                if len(values) == 2048 and count == 1024:
                    started.set()
                    if not release.wait(2):
                        raise TimeoutError("test gate")
            return analyze(values, cancellation, observed)

        class GatedSession(AnalysisSession):
            def __init__(self):
                super().__init__(analyzer=gated)
                sessions.append(self)

            def offer(self, values):
                result = super().offer(values)
                if result == 3:
                    release.set()
                return result

            def step(self, timeout=0):
                self._planner.wait_idle(1)
                result = super().step(timeout)
                if self.revision == 1:
                    if not started.wait(1):
                        raise AssertionError("first operation did not reach gate")
                    elapsed[0] = .01
                elif self.revision == 2:
                    if self.pending_revision != 2:
                        raise AssertionError("second dispatch was not retained")
                    dispatch = self._pending[0]
                    super().step(0)
                    if self._pending[0] is not dispatch:
                        raise AssertionError("refused dispatch identity changed")
                    pending.append(dispatch.id)
                    elapsed[0] = .02
                return result

        try:
            with patch.object(runner, "AnalysisSession", GatedSession), patch.object(
                    runner.time, "perf_counter", side_effect=lambda: elapsed[0]):
                report = runner.run_manifest(manifest([row([1] * 2048), row([2], .01), row([3, 4], .02)]))
            self.assertTrue(report["complete"], report)
            self.assert_summary(report, [3, 4])
            self.assertEqual(pending, ["analysis-0002"])
            self.assertGreaterEqual(report["session"]["metrics"]["refused"], 1)
            self.assertEqual(report["session"]["revisions"][1]["disposition"], "superseded_before_admission")
            self.assertTrue(any(r["generation"] == 1 and r["stale"] for r in report["session"]["results"]))
        finally:
            release.set()
            for session in sessions:
                session.close()

    def test_stalled_runner_clock_times_out_and_cleans(self):
        with patch.object(runner.time, "perf_counter", return_value=0):
            report = runner.run_manifest(manifest([row([], .001)]), timeout=.02)
        self.assertFalse(report["complete"])
        self.assertEqual(report["runner_error"]["type"], "TimeoutError")
        self.assertEqual(report["deliveries"], [])
        self.assertEqual(report["session"]["outstanding"], 0)
        self.assertTrue(report["session"]["planner_stopped"])

    def test_execution_failure_and_interrupt_are_incomplete(self):
        class FailedSession(AnalysisSession):
            def __init__(self):
                def failed(*args):
                    raise RuntimeError("controlled callable failure")
                super().__init__(analyzer=failed)
        with patch.object(runner, "AnalysisSession", FailedSession):
            report = runner.run_manifest(manifest([row([1])]))
        self.assertFalse(report["complete"])
        self.assertEqual(report["session"]["results"][0]["status"], "failed")
        self.assertEqual(report["session"]["outstanding"], 0)
        verdict = inspect_bytes(json.dumps(report).encode(), record="session")
        self.assertEqual(verdict["consistency"], "consistent", verdict)
        with patch.object(AnalysisSession, "offer", side_effect=KeyboardInterrupt):
            report = runner.run_manifest(manifest([row([])]))
        self.assertFalse(report["complete"])
        self.assertEqual(report["runner_error"]["type"], "KeyboardInterrupt")
        self.assertTrue(report["session"]["planner_stopped"])

    def test_cli_no_overwrite_or_input_alias_and_invalid_no_work(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = Path(directory) / "input.json"
            data = manifest([row([])])
            path.write_bytes(data)
            with patch.object(runner, "AnalysisSession") as factory:
                self.assertEqual(runner.main([str(path), "--output", str(path)]), 2)
                self.assertEqual(path.read_bytes(), data)
                path.write_bytes(b'{')
                self.assertEqual(runner.main([str(path)]), 2)
                factory.assert_not_called()
            path.write_bytes(data)
            target = Path(directory) / "receipt.json"
            self.assertEqual(runner.main([str(path), "--output", str(target)]), 0)
            self.assertTrue(json.loads(target.read_bytes())["complete"])
            before = target.read_bytes()
            self.assertEqual(runner.main([str(path), "--output", str(target)]), 2)
            self.assertEqual(target.read_bytes(), before)

    def test_cli_close_failure_returns_delivery_status(self):
        class FailedClose(io.StringIO):
            def close(self):
                super().close()
                raise OSError("controlled final flush failure")
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as printed:
            path = Path(directory) / "input.json"
            path.write_bytes(manifest([row([])]))
            with patch.object(runner, "open", return_value=FailedClose(), create=True):
                self.assertEqual(runner.main([str(path), "--output", "unused-new-path"]), 2)
            self.assertEqual(json.loads(printed.getvalue())["delivery"], "failed")

    def test_stdout_delivery_failures_return_two(self):
        class FailedStream(io.StringIO):
            def __init__(self, phase):
                super().__init__()
                self.phase = phase

            def write(self, value):
                if self.phase == "write":
                    raise BrokenPipeError("controlled stdout write failure")
                return super().write(value)

            def flush(self):
                if self.phase == "flush":
                    raise OSError("controlled buffered stdout flush failure")
                return super().flush()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            original = manifest([row([])])
            path.write_bytes(original)
            for phase, failed_diagnostic in (("write", False), ("flush", False), ("write", True), ("write", None)):
                with self.subTest(phase=phase, failed_diagnostic=failed_diagnostic):
                    stdout = FailedStream(phase)
                    stderr = (None if failed_diagnostic is None else
                              FailedStream("write") if failed_diagnostic else io.StringIO())
                    with patch.object(runner, "run_manifest", return_value={"complete": True}), patch(
                            "sys.stdout", stdout), patch("sys.stderr", stderr):
                        self.assertEqual(runner.main([str(path)]), 2)
                    self.assertEqual(path.read_bytes(), original)

    def test_argument_errors_and_help_preserve_exit_status(self):
        probe = r'''
import io, runpy, sys
action, phase = sys.argv[1:]
class Failed(io.StringIO):
    def write(self, value):
        if phase == 'write':
            raise BrokenPipeError('controlled argument-channel write failure')
        return super().write(value)
    def flush(self):
        if phase == 'flush':
            raise OSError('controlled argument-channel buffered failure')
if action == 'help':
    sys.stdout = None if phase == 'absent' else Failed()
    arguments = ['--help']
else:
    sys.stderr = None if phase == 'absent' else Failed()
    arguments = ['unused.json', '--timeout', 'not-a-number']
sys.argv = ['adaptive_timing.revision_file'] + arguments
runpy.run_module('adaptive_timing.revision_file', run_name='__main__')
'''
        for action in ('arguments', 'help'):
            for phase in ('write', 'flush', 'absent'):
                with self.subTest(action=action, phase=phase):
                    result = subprocess.run([sys.executable, '-c', probe, action, phase],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertNotIn('Exception ignored', result.stderr)

    def test_normal_help_and_argument_errors_create_no_session(self):
        for arguments, expected, channel in ((['--help'], 0, 'stdout'),
                                             (['x', '--timeout', 'invalid'], 2, 'stderr'),
                                             ([], 2, 'stderr')):
            with self.subTest(arguments=arguments), patch.object(runner, 'AnalysisSession') as factory:
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(runner.main(arguments), expected)
                self.assertFalse(stdout.closed)
                self.assertFalse(stderr.closed)
                text = stdout.getvalue() if channel == 'stdout' else stderr.getvalue()
                self.assertIn('usage:', text)
                if expected == 0:
                    self.assertIn('--timeout', text)
                    self.assertEqual(stderr.getvalue(), '')
                else:
                    self.assertIn('error:', text)
                    self.assertEqual(stdout.getvalue(), '')
                factory.assert_not_called()

    def test_cli_valid_stdout_keeps_embedding_streams_open(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.json'
            original = manifest([row([2, 7])])
            source.write_bytes(original)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(runner.main([str(source)]), 0)
            self.assertFalse(stdout.closed)
            self.assertFalse(stderr.closed)
            self.assertEqual(stderr.getvalue(), '')
            self.assert_summary(json.loads(stdout.getvalue()), [2, 7])
            self.assertEqual(source.read_bytes(), original)

    def test_module_entrypoint_preserves_status_after_stream_failure(self):
        probe = r'''
import io, runpy, sys
mode = sys.argv[1]
class Failed(io.StringIO):
    def write(self, value):
        if mode != 'flush':
            raise BrokenPipeError('controlled stream failure')
        return super().write(value)
    def flush(self):
        if mode == 'flush':
            raise OSError('controlled flush failure')
sys.stdout = Failed()
if mode == 'both':
    sys.stderr = Failed()
if mode == 'absent':
    sys.stdout = sys.stderr = None
sys.argv = ['adaptive_timing.revision_file', sys.argv[2]]
runpy.run_module('adaptive_timing.revision_file', run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_bytes(manifest([row([])]))
            for mode in ("write", "flush", "both", "absent"):
                with self.subTest(mode=mode):
                    result = subprocess.run([sys.executable, "-c", probe, mode, str(source)],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("Exception ignored", result.stderr)
            process = subprocess.Popen([sys.executable, "-m", "adaptive_timing.revision_file", str(source)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                process.stdout.close()
                self.assertEqual(process.wait(timeout=5), 2)
                diagnostic = process.stderr.read().decode()
                self.assertEqual(json.loads(diagnostic)["delivery"], "failed")
                self.assertNotIn("Traceback", diagnostic)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
