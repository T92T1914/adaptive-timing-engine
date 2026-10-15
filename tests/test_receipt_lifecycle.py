"""Actual bounded lifecycle receipts qualify passive checker positive cases.

The capture helper uses the producer only to obtain input to the checker.
Expected lifecycle and numeric facts below are independently fixed. No private
capture file or external installation is required to run these tests.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from adaptive_timing.execution import OperationCancelled
from adaptive_timing.revised_analysis import AnalysisSession, analyze


SCENARIOS = (
    "empty_closed", "empty_input", "live_running", "new_offer_clears_current",
    "cancel_after_reconcile", "queued_cancel", "partial_cancel_timeout",
    "submit_failure_successor", "queued_after_cutoff", "false_result",
    "summary_looking_mapping", "unavailable_source_control", "closing_reentrant",
)


class ManualClock:
    def __init__(self):
        self.value = 100_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds=0.01):
        self.value += seconds


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


class _Capture:
    """Keep all actual worker ownership inside one serial capture scope."""

    def __init__(self):
        self.sessions = []
        self.gates = []
        self.snapshots = {}
        self.witness = {}

    def session(self, **kwargs):
        session = AnalysisSession(**kwargs)
        self.sessions.append(session)
        return session

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    @staticmethod
    def wait(gate):
        _require(gate.wait(2), "actual callable did not reach its controlled gate")

    @staticmethod
    def dispatch(session):
        _require(session._planner.wait_idle(2), "actual planner did not settle")
        # Hold owner reconciliation only. The actual future and callable run.
        with patch.object(session._execution, "wait", return_value=()):
            session.step()

    @staticmethod
    def row(session, revision):
        return next(row for row in session.report()["revisions"]
                    if row["revision"] == revision)

    def physical(self, session, revision):
        ident = self.row(session, revision)["job_id"]
        future = session._execution._jobs[ident].future
        _require(future is not None, "expected an actual started callable")
        return future.result(timeout=2)

    def complete(self, session, values):
        revision = session.offer(values)
        self.dispatch(session)
        self.physical(session, revision)
        session.step()
        return revision

    def snapshot(self, label, session):
        self.snapshots[label] = session.report()

    def gated_analysis(self):
        entered, release = threading.Event(), self.gate()
        calls = []

        def operation(values, cancellation, progress):
            calls.append(values)

            def observed(count):
                progress(count)
                if values and values[0] == 7 and count == 1024:
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("lifecycle capture gate was not released")

            return analyze(values, cancellation, observed)

        return operation, entered, release, calls

    def cleanup(self):
        for gate in self.gates:
            gate.set()
        retired = []
        for session in reversed(self.sessions):
            session.close()
            threads = tuple(session._execution._pool._threads)
            facts = {
                "planner_stopped": not session._planner._thread.is_alive(),
                "worker_threads_stopped": all(not thread.is_alive() for thread in threads),
                "physical_worker_threads": len(threads),
                "outstanding": session._execution.outstanding,
                "reserved_resources": list(session._execution.reserved_resources),
                "pending_revision": session.pending_revision,
            }
            _require(facts["planner_stopped"], "capture left its planner running")
            _require(facts["worker_threads_stopped"], "capture left its callable worker running")
            _require(facts["physical_worker_threads"] <= 1, "capture exceeded one physical worker")
            _require(facts["outstanding"] == 0 and not facts["reserved_resources"]
                     and facts["pending_revision"] is None, "capture retained physical ownership")
            retired.append(facts)
        return retired


def capture_lifecycle(name):
    """Return actual report snapshots and separately recorded control witnesses."""
    if name not in SCENARIOS:
        raise ValueError("unknown finite lifecycle capture")
    capture = _Capture()
    control = "actual source execution with deterministic owner and callable gates"
    try:
        if name == "empty_closed":
            session = capture.session()
            session.close()
            capture.snapshot("closed", session)
        elif name == "empty_input":
            session = capture.session(clock=ManualClock())
            capture.complete(session, ())
            session.close()
            capture.snapshot("closed", session)
        elif name == "live_running":
            operation, entered, release, calls = capture.gated_analysis()
            session = capture.session(clock=ManualClock(), analyzer=operation)
            session.offer((7,) * 2048)
            capture.dispatch(session)
            capture.wait(entered)
            capture.snapshot("running", session)
            capture.witness["actual_callable_entries"] = len(calls)
        elif name == "new_offer_clears_current":
            clock = ManualClock()
            session = capture.session(clock=clock)
            capture.complete(session, (1, 2, 3))
            capture.snapshot("adopted", session)
            clock.advance()
            session.offer((8, 8))
            capture.snapshot("new_offer", session)
        elif name == "cancel_after_reconcile":
            session = capture.session(clock=ManualClock())
            revision = capture.complete(session, (1, 2, 3))
            capture.snapshot("adopted", session)
            capture.witness["cancel_confirmed"] = session.cancel(revision)
            capture.snapshot("late_cancel", session)
            session.close()
            capture.snapshot("closed", session)
        elif name in ("queued_cancel", "queued_after_cutoff"):
            operation, entered, release, calls = capture.gated_analysis()
            clock = ManualClock()
            session = capture.session(clock=clock, analyzer=operation, capacity=2,
                                      pending_seconds=0.25)
            first = session.offer((7,) * 2048)
            capture.dispatch(session)
            capture.wait(entered)
            clock.advance()
            second = session.offer((3, 3))
            capture.dispatch(session)
            queued_id = capture.row(session, second)["job_id"]
            capture.witness["queued_future_is_none"] = (
                session._execution._jobs[queued_id].future is None)
            _require(capture.witness["queued_future_is_none"], "second callable ran before ownership retired")
            if name == "queued_cancel":
                capture.witness["cancel_confirmed"] = session.cancel(second)
                session.step()
                capture.snapshot("queued_canceled", session)
            else:
                clock.advance(0.5)
                session.step()
                capture.snapshot("accepted_queued_after_cutoff", session)
            release.set()
            capture.physical(session, first)
            session.step()
            if name == "queued_after_cutoff":
                capture.physical(session, second)
                session.step()
            session.close()
            capture.snapshot("closed", session)
            capture.witness["actual_callable_entries"] = len(calls)
        elif name == "partial_cancel_timeout":
            entered, cancellation_seen, cleaned = (threading.Event() for _ in range(3))
            release = capture.gate()

            def operation(values, cancellation, progress):
                analyze(values[:1024], threading.Event(), progress)
                entered.set()
                if not cancellation.wait(5):
                    raise TimeoutError("capture cancellation was not requested")
                cancellation_seen.set()
                if not release.wait(5):
                    raise TimeoutError("capture cleanup gate was not released")
                cleaned.set()
                raise OperationCancelled()

            session = capture.session(clock=ManualClock(), analyzer=operation)
            revision = session.offer((4,) * 2048)
            capture.dispatch(session)
            capture.wait(entered)
            capture.witness["running_cancel_confirmed"] = session.cancel(revision)
            capture.wait(cancellation_seen)
            capture.snapshot("cancel_requested_cleanup_pending", session)
            capture.witness["cleanup_before_release"] = cleaned.is_set()
            release.set()
            capture.snapshots["closed_timeout"] = session.finish(timeout=0)
            capture.witness["cleanup_after_finish"] = cleaned.is_set()
        elif name == "closing_reentrant":
            operation, entered, release, calls = capture.gated_analysis()
            session = capture.session(clock=ManualClock(), analyzer=operation)
            session.offer((7,) * 2048)
            capture.dispatch(session)
            capture.wait(entered)
            original_close = session._planner.close

            def observe_during_close(*args, **kwargs):
                capture.snapshot("closing_before_retirement", session)
                release.set()
                return original_close(*args, **kwargs)

            # Owner-thread instrumentation observes a real close before its
            # delegated cleanup. The actual close and callable are not replaced.
            with patch.object(session._planner, "close", side_effect=observe_during_close):
                session.close()
            capture.snapshot("closed_after_retirement", session)
            capture.witness["actual_callable_entries"] = len(calls)
            control = "actual close with controlled owner-thread report reentry before delegated cleanup"
        elif name == "submit_failure_successor":
            clock = ManualClock()
            session = capture.session(clock=clock)
            original_submit = session._execution._pool.submit
            attempts = []

            def submit_once(*args, **kwargs):
                attempts.append(1)
                if len(attempts) == 1:
                    raise RuntimeError("controlled one-time worker submission failure")
                return original_submit(*args, **kwargs)

            control = "actual source execution with only pool.submit controlled to fail once"
            with patch.object(session._execution._pool, "submit", side_effect=submit_once):
                session.offer((13,))
                capture.dispatch(session)
                session.step()
                capture.snapshot("failed_no_start", session)
                clock.advance()
                capture.complete(session, (3, 4, 5))
            session.close()
            capture.snapshot("healthy_closed", session)
            capture.witness["pool_submit_attempts"] = len(attempts)
        elif name in ("false_result", "summary_looking_mapping"):
            value = False
            if name == "summary_looking_mapping":
                value = {"count": 1, "total": 7, "histogram": [0] * 256,
                         "digest": "0" * 64}
                value["histogram"][7] = 1
            session = capture.session(clock=ManualClock(),
                                      analyzer=lambda values, cancellation, progress: value)
            capture.complete(session, (7,))
            capture.snapshot("live_adoption_error", session)
            session.close()
            capture.snapshot("closed_adoption_error", session)
            control = "actual source execution with a trusted non-Summary analyzer return"
        else:
            session = capture.session()
            session.close()
            import adaptive_timing.revised_analysis as producer
            unavailable = Path(producer.__file__).resolve().parent / "runtime.py"
            original_read = Path.read_bytes

            def controlled_read(path):
                if path == unavailable:
                    raise PermissionError("controlled source metadata read refusal")
                return original_read(path)

            # This changes one metadata read, never the actual source file.
            with patch.object(Path, "read_bytes", controlled_read):
                capture.snapshot("closed_unavailable_source", session)
            control = "actual empty session with a controlled source metadata read refusal"
    finally:
        cleanup = capture.cleanup()
    return {"scenario": name, "control": control, "snapshots": capture.snapshots,
            "witness": capture.witness, "cleanup": cleanup}


class ReceiptLifecycleTests(unittest.TestCase):
    def capture(self, name):
        captured = capture_lifecycle(name)
        self.assertEqual(len(captured["cleanup"]), 1)
        self.assertEqual(captured["cleanup"][0]["outstanding"], 0)
        return captured

    def consistent(self, captured):
        from adaptive_timing.receipt_check import inspect_bytes
        complete_snapshots = {
            ("empty_closed", "closed"), ("empty_input", "closed"),
            ("submit_failure_successor", "healthy_closed"),
            ("queued_after_cutoff", "closed"),
            ("unavailable_source_control", "closed_unavailable_source"),
        }
        for label, receipt in captured["snapshots"].items():
            with self.subTest(snapshot=label):
                complete = (captured["scenario"], label) in complete_snapshots
                self.assertIs(receipt["complete"], complete)
                raw = json.dumps(receipt, allow_nan=False).encode("utf-8")
                verdict = inspect_bytes(raw)
                self.assertEqual(verdict["admission"], "accepted", verdict)
                self.assertEqual(verdict["consistency"], "consistent", verdict)
                self.assertEqual(verdict["completion"],
                                 "complete" if complete else "incomplete", verdict)
                self.assertEqual(verdict["issues"], [], verdict)

    def test_empty_closed_session_is_complete_without_an_answer(self):
        captured = self.capture("empty_closed")
        report = captured["snapshots"]["closed"]
        self.assertTrue(report["complete"])
        self.assertIsNone(report["current"])
        self.assertEqual(report["revision"], 0)
        self.assertEqual(report["results"], [])
        self.consistent(captured)

    def test_actual_empty_input_has_empty_summary_and_digest(self):
        captured = self.capture("empty_input")
        report = captured["snapshots"]["closed"]
        self.assertEqual(report["current"]["count"], 0)
        self.assertEqual(report["current"]["total"], 0)
        self.assertEqual(report["current"]["histogram"], [0] * 256)
        self.assertEqual(report["current"]["digest"],
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(report["metrics"]["physical_completed"], 1)
        self.consistent(captured)

    def test_running_progress_is_not_a_reconciled_started_result(self):
        captured = self.capture("live_running")
        report = captured["snapshots"]["running"]
        self.assertEqual(captured["witness"]["actual_callable_entries"], 1)
        self.assertEqual(report["outstanding"], 1)
        self.assertEqual(report["reserved_resources"], ["analysis-cpu"])
        self.assertEqual(report["metrics"]["physical_values"], 1024)
        self.assertEqual(report["metrics"]["physical_started"], 0)
        self.assertEqual(report["results"], [])
        self.assertFalse(report["closed"])
        self.consistent(captured)

    def test_new_offer_clears_current_but_retains_historical_adoption(self):
        captured = self.capture("new_offer_clears_current")
        adopted, report = (captured["snapshots"][name] for name in ("adopted", "new_offer"))
        self.assertEqual(adopted["current"]["total"], 6)
        self.assertIsNone(report["current"])
        self.assertFalse(report["revisions"][0]["current"])
        self.assertIsNotNone(report["revisions"][0]["adopted_at"])
        self.assertTrue(report["results"][0]["usable"])
        self.assertEqual(report["revision"], 2)
        self.consistent(captured)

    def test_late_cancel_does_not_mutate_immutable_reconciliation_flags(self):
        captured = self.capture("cancel_after_reconcile")
        report = captured["snapshots"]["late_cancel"]
        self.assertTrue(captured["witness"]["cancel_confirmed"])
        self.assertTrue(report["revisions"][0]["cancel_requested"])
        self.assertFalse(report["results"][0]["cancellation_requested"])
        self.assertTrue(report["results"][0]["usable"])
        self.assertIsNone(report["current"])
        self.assertEqual(captured["snapshots"]["closed"]["stop_reason"], "no_current_result")
        self.consistent(captured)

    def test_queued_cancel_has_null_start_and_preserves_running_owner(self):
        captured = self.capture("queued_cancel")
        report = captured["snapshots"]["queued_canceled"]
        result, = report["results"]
        self.assertEqual(result["generation"], 2)
        self.assertEqual(result["status"], "canceled")
        self.assertIsNone(result["started_at"])
        self.assertIsNone(result["callable_seconds"])
        self.assertEqual(report["outstanding"], 1)
        self.assertEqual(report["reserved_resources"], ["analysis-cpu"])
        self.assertEqual(captured["witness"]["actual_callable_entries"], 1)
        self.consistent(captured)

    def test_owner_reentry_can_observe_closed_before_physical_retirement(self):
        captured = self.capture("closing_reentrant")
        closing = captured["snapshots"]["closing_before_retirement"]
        retired = captured["snapshots"]["closed_after_retirement"]
        self.assertTrue(closing["closed"])
        self.assertFalse(closing["planner_stopped"])
        self.assertFalse(closing["complete"])
        self.assertEqual(closing["outstanding"], 1)
        self.assertEqual(closing["reserved_resources"], ["analysis-cpu"])
        self.assertTrue(retired["closed"])
        self.assertTrue(retired["planner_stopped"])
        self.assertEqual(retired["outstanding"], 0)
        self.assertEqual(retired["reserved_resources"], [])
        self.assertEqual(retired["stop_reason"], "no_current_result")
        self.consistent(captured)

    def test_result_cancellation_requires_row_request_in_only_one_direction(self):
        from adaptive_timing.receipt_check import inspect_bytes
        captured = self.capture("queued_cancel")
        report = captured["snapshots"]["queued_canceled"]
        self.assertTrue(report["results"][0]["cancellation_requested"])
        self.assertTrue(report["revisions"][1]["cancel_requested"])
        report["revisions"][1]["cancel_requested"] = False
        verdict = inspect_bytes(json.dumps(report).encode("utf-8"))
        self.assertEqual(verdict["admission"], "accepted")
        self.assertEqual(verdict["consistency"], "contradictory")
        self.assertTrue(any(issue["code"] == "cancellation"
                            and issue["path"] == "$.results[0].cancellation_requested"
                            for issue in verdict["issues"]), verdict)

    def test_unsubmitted_cancellation_requires_its_row_request(self):
        from adaptive_timing.receipt_check import inspect_bytes
        capture = _Capture()
        try:
            clock = ManualClock()
            session = capture.session(clock=clock)
            revision = session.offer((9,))
            clock.advance()
            self.assertTrue(session.cancel(revision))
            session.close()
            report = session.report()
        finally:
            capture.cleanup()
        self.assertEqual(report["revisions"][0]["disposition"], "canceled_before_admission")
        original = inspect_bytes(json.dumps(report).encode("utf-8"))
        self.assertEqual(original["consistency"], "consistent", original)
        report["revisions"][0]["cancel_requested"] = False
        verdict = inspect_bytes(json.dumps(report).encode("utf-8"))
        self.assertEqual(verdict["admission"], "accepted")
        self.assertEqual(verdict["consistency"], "contradictory")
        self.assertTrue(any(issue["code"] == "cancellation"
                            and issue["path"] == "$.revisions[0].cancel_requested"
                            for issue in verdict["issues"]), verdict)

    def test_timeout_receipt_waits_for_partial_cancel_cleanup(self):
        captured = self.capture("partial_cancel_timeout")
        running = captured["snapshots"]["cancel_requested_cleanup_pending"]
        closed = captured["snapshots"]["closed_timeout"]
        self.assertFalse(captured["witness"]["running_cancel_confirmed"])
        self.assertFalse(captured["witness"]["cleanup_before_release"])
        self.assertTrue(captured["witness"]["cleanup_after_finish"])
        self.assertEqual(running["outstanding"], 1)
        self.assertEqual(closed["stop_reason"], "timeout")
        self.assertFalse(closed["complete"])
        self.assertEqual(closed["results"][0]["status"], "canceled")
        self.assertEqual(closed["metrics"]["physical_values"], 1024)
        self.assertEqual(closed["outstanding"], 0)
        self.consistent(captured)

    def test_controlled_submission_failure_has_no_start_then_healthy_successor(self):
        captured = self.capture("submit_failure_successor")
        failed = captured["snapshots"]["failed_no_start"]["results"][0]
        closed = captured["snapshots"]["healthy_closed"]
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["started_at"])
        self.assertIsNone(failed["queue_seconds"])
        self.assertIsNone(failed["callable_seconds"])
        self.assertIn("controlled one-time worker submission failure", failed["error"])
        self.assertEqual(captured["witness"]["pool_submit_attempts"], 2)
        self.assertEqual(closed["current"]["total"], 12)
        self.assertEqual(closed["metrics"]["admitted"], 2)
        self.assertEqual(closed["metrics"]["physical_started"], 1)
        self.assertTrue(closed["complete"])
        self.consistent(captured)

    def test_already_accepted_queue_may_start_after_submission_cutoff(self):
        captured = self.capture("queued_after_cutoff")
        queued = captured["snapshots"]["accepted_queued_after_cutoff"]
        closed = captured["snapshots"]["closed"]
        self.assertEqual(queued["outstanding"], 2)
        self.assertEqual(queued["revisions"][1]["disposition"], "admitted")
        row = closed["revisions"][1]
        self.assertLess(row["admitted_at"], row["deadline"])
        self.assertGreater(row["adopted_at"], row["deadline"])
        self.assertEqual(closed["current"]["total"], 6)
        self.assertEqual(closed["metrics"]["stale"], 1)
        self.assertEqual(captured["witness"]["actual_callable_entries"], 2)
        self.consistent(captured)

    def assert_non_summary(self, name):
        captured = self.capture(name)
        live = captured["snapshots"]["live_adoption_error"]
        closed = captured["snapshots"]["closed_adoption_error"]
        self.assertEqual(live["results"][0]["status"], "completed")
        self.assertTrue(live["results"][0]["usable"])
        self.assertIsNone(live["current"])
        self.assertIn("Summary", live["revisions"][0]["adoption_error"])
        self.assertIsNone(live["stop_reason"])
        self.assertEqual(closed["stop_reason"], "no_current_result")
        self.assertFalse(closed["complete"])
        self.consistent(captured)

    def test_false_return_is_completed_but_not_a_numeric_adoption(self):
        self.assert_non_summary("false_result")

    def test_actual_closed_incomplete_receipt_has_successful_cli_validation(self):
        from adaptive_timing.receipt_check import main
        captured = self.capture("false_result")
        report = captured["snapshots"]["closed_adoption_error"]
        self.assertFalse(report["complete"])
        self.assertEqual(report["stop_reason"], "no_current_result")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actual-incomplete.json"
            path.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([str(path), "--json"])
        self.assertEqual(code, 0, output.getvalue())
        verdict = json.loads(output.getvalue())
        self.assertEqual(verdict["admission"], "accepted")
        self.assertEqual(verdict["consistency"], "consistent")
        self.assertEqual(verdict["completion"], "incomplete")

    def test_summary_looking_plain_mapping_is_not_a_numeric_adoption(self):
        self.assert_non_summary("summary_looking_mapping")

    def test_controlled_source_hash_unavailability_remains_explicit(self):
        captured = self.capture("unavailable_source_control")
        report = captured["snapshots"]["closed_unavailable_source"]
        files = report["source_identity"]["module_files"]
        self.assertEqual(files["runtime.py"], {"unavailable": "PermissionError"})
        self.assertEqual(len(files["revised_analysis.py"]["sha256"]), 64)
        self.assertTrue(report["complete"])
        self.consistent(captured)


if __name__ == "__main__":
    unittest.main()
