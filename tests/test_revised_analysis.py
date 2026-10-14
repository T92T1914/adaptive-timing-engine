"""Revised input is checked against actual work and controlled lifecycle gates."""
from collections import Counter
from hashlib import sha256
import threading
import unittest
from unittest.mock import patch

from adaptive_timing.execution import OperationCancelled
from adaptive_timing.execution import RealExecutor
from adaptive_timing.revised_analysis import AnalysisSession, Summary, analyze, demonstration
from adaptive_timing.worker import LatestWorker


class ManualClock:
    def __init__(self, origin=100_000_000.0):
        self.value = origin

    def __call__(self):
        return self.value

    def advance(self, seconds=0.01):
        self.value += seconds
        return self.value


def expected(values):
    counts = Counter(values)
    return Summary(len(values), sum(values),
                   tuple(counts[value] for value in range(256)),
                   sha256(bytes(values)).hexdigest())


class RevisedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for session in reversed(self.sessions):
            session.close()

    def session(self, **kwargs):
        session = AnalysisSession(**kwargs)
        self.sessions.append(session)
        return session

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def wait_gate(self, gate):
        self.assertTrue(gate.wait(2), "actual operation did not reach its gate")

    def dispatch_ready(self, session):
        self.assertTrue(session._planner.wait_idle(2), "planner did not settle")
        # Delay owner reconciliation while the actual submitted callable runs.
        with patch.object(session._execution, "wait", return_value=()):
            return session.step()

    def await_physical(self, session, ident):
        job = session._execution._jobs[ident]
        self.assertIsNotNone(job.future)
        return job.future.result(timeout=2)

    def row(self, session, revision):
        return next(row for row in session.report()["revisions"]
                    if row["revision"] == revision)

    def gated_analysis(self, marker):
        entered, release = threading.Event(), self.gate()
        calls = []

        def operation(values, cancellation, progress):
            calls.append(values)

            def observed(count):
                progress(count)
                if values and values[0] == marker and count == 1024:
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test computation gate was not released")

            return analyze(values, cancellation, observed)

        return operation, entered, release, calls

    def test_analysis_has_independent_count_sum_histogram_and_digest(self):
        values = (0, 255, 1, 255, 17, 0, 128, 128, 128)
        progress = []
        actual = analyze(values, threading.Event(), progress.append)
        self.assertEqual(actual, expected(values))
        self.assertEqual(actual.count, 9)
        self.assertEqual(actual.total, 912)
        self.assertEqual(actual.histogram[128], 3)
        self.assertEqual(progress, [9])

    def test_empty_input_is_real_empty_summary(self):
        progress = []
        result = analyze((), threading.Event(), progress.append)
        self.assertEqual(result, expected(()))
        self.assertEqual(result.digest,
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertFalse(any(result.histogram))
        self.assertEqual(sum(progress), 0)

    def test_analysis_progress_is_actual_bounded_cumulative_work(self):
        values = tuple(range(256)) * 10
        progress = []
        result = analyze(values, threading.Event(), progress.append)
        self.assertEqual(result, expected(values))
        self.assertEqual(progress[-1], len(values))
        self.assertTrue(all(0 < right - left <= 1024
                            for left, right in zip([0] + progress, progress)))

    def test_cancellation_preserves_partial_physical_work(self):
        cancellation = threading.Event()
        progress = []

        def stop_after_first_chunk(count):
            progress.append(count)
            cancellation.set()

        with self.assertRaises(OperationCancelled):
            analyze(tuple(range(256)) * 12, cancellation, stop_after_first_chunk)
        self.assertEqual(progress, [1024])

    def test_preexisting_cancellation_runs_no_chunk(self):
        cancellation = threading.Event()
        cancellation.set()
        progress = []
        with self.assertRaises(OperationCancelled):
            analyze((1, 2, 3), cancellation, progress.append)
        self.assertEqual(progress, [])

    def test_real_current_result_uses_large_origin_relative_clock(self):
        clock = ManualClock()
        values = (5, 9, 1, 9, 255, 0)
        session = self.session(clock=clock)
        revision = session.offer(values)
        self.dispatch_ready(session)
        report = session.finish()
        self.assertTrue(report["complete"])
        self.assertEqual(session.current, expected(values))
        self.assertEqual(session.revision, revision)
        self.assertEqual(report["metrics"]["physical_values"], len(values))
        self.assertEqual(report["metrics"]["usable"], 1)
        self.assertEqual(report["metrics"]["stale"], 0)

    def test_configuration_has_finite_session_and_capacity_bounds(self):
        for changes in ({"capacity": 0}, {"capacity": 3}, {"capacity": True},
                        {"max_revisions": 0}, {"max_revisions": 65},
                        {"max_revisions": True}, {"pending_seconds": 0},
                        {"pending_seconds": float("inf")},
                        {"pending_seconds": float("nan")},
                        {"pending_seconds": True}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                self.session(**changes)

    def test_invalid_input_does_not_advance_current_revision(self):
        clock = ManualClock()
        session = self.session(clock=clock)
        revision = session.offer((1, 2, 3))
        for values in ([1, 2], (True,), (1.0,), (-1,), (256,),
                       (1,) * 65_537, "123", None):
            clock.advance()
            with self.subTest(input_type=type(values).__name__), self.assertRaises((TypeError, ValueError)):
                session.offer(values)
            self.assertEqual(session.revision, revision)
            self.assertEqual(session.report()["metrics"]["offered"], 1)

    def test_session_revision_cap_rejects_without_replacing_accepted_work(self):
        clock = ManualClock()
        session = self.session(clock=clock, max_revisions=1)
        revision = session.offer((4, 5))
        clock.advance()
        with self.assertRaises((RuntimeError, ValueError)):
            session.offer((8, 9))
        self.assertEqual(session.revision, revision)
        self.dispatch_ready(session)
        self.assertTrue(session.finish()["complete"])
        self.assertEqual(session.current, expected((4, 5)))

    def test_capacity_refusal_retains_exact_dispatch_until_reconciliation(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation, capacity=1)
        first = (7,) * 2048
        second = (3, 5, 3, 9)
        old_revision = session.offer(first)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        old_id = self.row(session, old_revision)["job_id"]
        self.assertEqual(self.row(session, old_revision)["processed"], 1024)
        clock.advance()
        new_revision = session.offer(second)
        self.dispatch_ready(session)
        new_id = self.row(session, new_revision)["job_id"]
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(session.pending_revision, new_revision)
        self.assertEqual(self.row(session, new_revision)["disposition"], "pending_admission")
        self.assertEqual(session.report()["metrics"]["refused"], 1)
        for _ in range(3):
            self.assertEqual(session.step(), ())
        self.assertEqual(session.report()["metrics"]["refused"], 1)
        self.assertEqual(calls, [first])

        release.set()
        self.await_physical(session, old_id)
        held_resources = session._execution.reserved_resources
        self.assertEqual(session._execution.outstanding, 1)
        self.assertEqual(len(held_resources), 1)
        self.assertEqual(session.pending_revision, new_revision)
        old_result, = session.step()
        self.assertEqual(old_result.dispatch.id, old_id)
        self.assertEqual(old_result.value, expected(first))
        self.assertTrue(old_result.stale)
        self.assertFalse(old_result.usable)
        self.assertIsNone(session.pending_revision)
        self.assertEqual(self.row(session, new_revision)["job_id"], new_id)
        self.await_physical(session, new_id)
        new_result, = session.step()
        self.assertEqual(new_result.dispatch.id, new_id)
        self.assertTrue(new_result.usable)
        self.assertEqual(session.current, expected(second))
        self.assertEqual(calls, [first, second])
        self.assertEqual(session._execution.outstanding, 0)
        self.assertEqual(session._execution.reserved_resources, ())
        report = session.report()
        self.assertEqual(report["metrics"]["admitted"], 2)
        self.assertEqual(report["metrics"]["physical_values"], len(first) + len(second))
        self.assertEqual(report["metrics"]["stale"], 1)

    def test_newer_pending_revision_has_explicit_superseded_disposition(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation)
        first = (7,) * 2048
        session.offer(first)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        old_id = self.row(session, 1)["job_id"]
        clock.advance()
        skipped = session.offer((2, 2))
        self.dispatch_ready(session)
        skipped_id = self.row(session, skipped)["job_id"]
        self.assertEqual(session.pending_revision, skipped)
        clock.advance()
        latest = session.offer((9, 9, 9))
        self.assertEqual(self.row(session, skipped)["disposition"], "superseded_before_admission")
        self.dispatch_ready(session)
        self.assertEqual(session.pending_revision, latest)
        latest_id = self.row(session, latest)["job_id"]
        self.assertEqual(len({old_id, skipped_id, latest_id}), 3)
        release.set()
        self.await_physical(session, old_id)
        session.step()
        self.await_physical(session, latest_id)
        session.step()
        self.assertEqual(calls, [first, (9, 9, 9)])
        self.assertEqual(session.current, expected((9, 9, 9)))
        self.assertEqual(self.row(session, skipped)["processed"], 0)
        self.assertEqual(session.report()["metrics"]["admitted"], 2)

    def test_expired_pending_dispatch_never_executes(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation, pending_seconds=0.25)
        first = (7,) * 2048
        session.offer(first)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        old_id = self.row(session, 1)["job_id"]
        clock.advance()
        pending = session.offer((8, 8))
        self.dispatch_ready(session)
        self.assertEqual(session.pending_revision, pending)
        clock.advance(0.5)
        session.step()
        self.assertIsNone(session.pending_revision)
        self.assertEqual(self.row(session, pending)["disposition"], "expired_before_admission")
        self.assertEqual(session._execution.outstanding, 1)
        release.set()
        self.await_physical(session, old_id)
        session.step()
        self.assertEqual(calls, [first])
        self.assertIsNone(session.current)
        self.assertEqual(self.row(session, pending)["processed"], 0)
        self.assertFalse(session.report()["complete"])

    def test_cancel_pending_dispatch_keeps_running_ownership(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation)
        first = (7,) * 2048
        session.offer(first)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        old_id = self.row(session, 1)["job_id"]
        clock.advance()
        pending = session.offer((8, 8))
        self.dispatch_ready(session)
        held = session._execution.reserved_resources
        self.assertTrue(session.cancel(pending))
        self.assertEqual(self.row(session, pending)["disposition"], "canceled_before_admission")
        self.assertEqual(session._execution.outstanding, 1)
        self.assertEqual(session._execution.reserved_resources, held)
        release.set()
        self.await_physical(session, old_id)
        session.step()
        self.assertEqual(calls, [first])
        self.assertIsNone(session.current)

    def test_cancel_before_plan_admission_never_calls_analyzer(self):
        calls = []

        def operation(values, cancellation, progress):
            calls.append(values)
            return analyze(values, cancellation, progress)

        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation)
        revision = session.offer((1, 2, 3))
        clock.advance()
        self.assertTrue(session.cancel(revision))
        self.dispatch_ready(session)
        self.assertEqual(calls, [])
        self.assertEqual(self.row(session, revision)["disposition"], "canceled_before_admission")
        self.assertEqual(session._execution.outstanding, 0)

    def test_planner_mailbox_replaces_waiting_input_without_physical_work(self):
        clock = ManualClock()
        session = self.session(clock=clock)
        entered, release = threading.Event(), self.gate()
        original = session._planner._compute
        requests = []

        def gated_plan(request):
            requests.append(request)
            if len(requests) == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test planning gate was not released")
            return original(request)

        with patch.object(session._planner, "_compute", gated_plan):
            first = session.offer((1,))
            self.wait_gate(entered)
            clock.advance()
            second = session.offer((2,))
            clock.advance()
            latest = session.offer((3, 3, 3))
            self.assertEqual(session._planner.stats().replaced, 1)
            self.assertEqual(session._execution.outstanding, 0)
            release.set()
            self.dispatch_ready(session)
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.row(session, first)["disposition"], "superseded_before_admission")
        self.assertEqual(self.row(session, second)["disposition"], "superseded_before_admission")
        latest_id = self.row(session, latest)["job_id"]
        self.await_physical(session, latest_id)
        result, = session.step()
        self.assertEqual(result.generation, latest)
        self.assertEqual(session.current, expected((3, 3, 3)))
        self.assertEqual(session.report()["metrics"]["physical_values"], 3)
        self.assertEqual(session._planner.stats().stale, 1)

    def test_completed_result_is_obsolete_if_input_changes_before_reconciliation(self):
        clock = ManualClock()
        session = self.session(clock=clock)
        first = session.offer((1, 2, 3))
        self.dispatch_ready(session)
        first_id = self.row(session, first)["job_id"]
        self.await_physical(session, first_id)
        self.assertEqual(session._execution.outstanding, 1)
        clock.advance()
        latest = session.offer((8, 8))
        result, = session.step()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.value, expected((1, 2, 3)))
        self.assertTrue(result.stale)
        self.assertFalse(result.usable)
        self.assertIsNone(session.current)
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, latest)["job_id"])
        session.step()
        self.assertEqual(session.current, expected((8, 8)))

    def test_offer_after_adoption_clears_old_current_value(self):
        clock = ManualClock()
        session = self.session(clock=clock)
        first = session.offer((1, 2, 3))
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, first)["job_id"])
        session.step()
        self.assertEqual(session.current, expected((1, 2, 3)))
        clock.advance()
        latest = session.offer((8, 8))
        self.assertIsNone(session.current)
        self.assertFalse(session.report()["complete"])
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, latest)["job_id"])
        session.step()
        self.assertEqual(session.current, expected((8, 8)))
        self.assertEqual(session.report()["metrics"]["admitted"], 2)

    def test_physical_queue_cancellation_never_calls_waiting_operation(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation, capacity=2)
        first = (7,) * 2048
        session.offer(first)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        first_id = self.row(session, 1)["job_id"]
        clock.advance()
        queued = session.offer((3, 3))
        self.dispatch_ready(session)
        self.assertEqual(session._execution.outstanding, 2)
        self.assertEqual(self.row(session, queued)["disposition"], "admitted")
        self.assertTrue(session.cancel(queued))
        canceled, = session.step()
        self.assertEqual(canceled.status, "canceled")
        self.assertIsNone(canceled.started_at)
        self.assertEqual(self.row(session, queued)["processed"], 0)
        self.assertEqual(session._execution.outstanding, 1)
        self.assertEqual(len(session._execution.reserved_resources), 1)
        release.set()
        self.await_physical(session, first_id)
        session.step()
        self.assertEqual(calls, [first])
        self.assertEqual(session._execution.reserved_resources, ())

    def test_running_cancellation_acknowledges_only_after_actual_cleanup(self):
        entered, cancellation_seen = threading.Event(), threading.Event()
        cleanup, release = threading.Event(), self.gate()

        def operation(values, cancellation, progress):
            analyze(values[:1024], threading.Event(), progress)
            entered.set()
            if not cancellation.wait(5):
                raise TimeoutError("test cancellation was not requested")
            cancellation_seen.set()
            if not release.wait(5):
                raise TimeoutError("test cleanup gate was not released")
            cleanup.set()
            raise OperationCancelled()

        session = self.session(analyzer=operation)
        revision = session.offer((4,) * 2048)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        ident = self.row(session, revision)["job_id"]
        self.assertEqual(self.row(session, revision)["processed"], 1024)
        self.assertFalse(session.cancel(revision))
        self.wait_gate(cancellation_seen)
        self.assertFalse(cleanup.is_set())
        self.assertEqual(session.step(), ())
        self.assertEqual(session._execution.outstanding, 1)
        self.assertEqual(len(session._execution.reserved_resources), 1)
        release.set()
        self.await_physical(session, ident)
        result, = session.step()
        self.assertTrue(cleanup.is_set())
        self.assertEqual(result.status, "canceled")
        self.assertTrue(result.cancellation_requested)
        self.assertFalse(result.usable)
        self.assertEqual(session._execution.reserved_resources, ())
        self.assertEqual(session.report()["metrics"]["physical_values"], 1024)
        self.assertIsNone(session.current)

    def test_operation_failure_preserves_error_and_allows_healthy_successor(self):
        def operation(values, cancellation, progress):
            if values == (13,):
                raise ValueError("declared analysis failure")
            return analyze(values, cancellation, progress)

        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation)
        failed = session.offer((13,))
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, failed)["job_id"])
        result, = session.step()
        self.assertEqual(result.status, "failed")
        self.assertIn("ValueError: declared analysis failure", result.error)
        self.assertIsNone(session.current)
        self.assertEqual(session._execution.reserved_resources, ())
        clock.advance()
        healthy = session.offer((3, 4, 5))
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, healthy)["job_id"])
        result, = session.step()
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.usable)
        self.assertEqual(session.current, expected((3, 4, 5)))
        self.assertNotEqual(self.row(session, failed)["job_id"],
                            self.row(session, healthy)["job_id"])

    def test_owner_thread_rejects_mutation_and_inspection_from_another_thread(self):
        session = self.session()
        revision = session.offer((1, 2, 3))
        actions = (lambda: session.offer((4,)), session.step, session.report,
                   lambda: session.cancel(revision), session.close,
                   lambda: session.current, lambda: session.revision,
                   lambda: session.pending_revision)
        errors = []

        def foreign_owner():
            for action in actions:
                try:
                    action()
                except BaseException as error:
                    errors.append(error)

        thread = threading.Thread(target=foreign_owner, name="analysis-owner-check")
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), len(actions))
        self.assertTrue(all(isinstance(error, RuntimeError) for error in errors))
        self.assertTrue(all("owner" in str(error) for error in errors))
        self.assertEqual(session.revision, revision)
        self.assertEqual(session.report()["metrics"]["offered"], 1)

    def test_clock_refusal_preserves_adopted_revision(self):
        clock = ManualClock()
        session = self.session(clock=clock)
        revision = session.offer((1, 2, 3))
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, revision)["job_id"])
        session.step()
        with self.assertRaises(ValueError):
            session.offer((9,))
        self.assertEqual(session.revision, revision)
        self.assertEqual(session.current, expected((1, 2, 3)))
        clock.advance(-1)
        with self.assertRaises(ValueError):
            session.step()
        self.assertEqual(session.current, expected((1, 2, 3)))
        self.assertEqual(session.report()["metrics"]["offered"], 1)

    def test_close_drains_owned_workers_results_and_resources(self):
        operation, entered, release, calls = self.gated_analysis(7)
        session = self.session(analyzer=operation)
        revision = session.offer((7,) * 2048)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        planner_thread = session._planner._thread
        execution_threads = tuple(session._execution._pool._threads)
        self.assertTrue(execution_threads)
        release.set()
        session.close()
        self.assertFalse(planner_thread.is_alive())
        self.assertTrue(all(not thread.is_alive() for thread in execution_threads))
        self.assertEqual(session._execution.outstanding, 0)
        self.assertEqual(session._execution.reserved_resources, ())
        self.assertIsNone(session.pending_revision)
        report = session.report()
        self.assertEqual(len(report["results"]), 1)
        self.assertIn(self.row(session, revision)["disposition"], ("completed", "canceled"))
        self.assertEqual(calls, [(7,) * 2048])
        self.assertEqual(session._execution.observations(), ())
        later = session.report()
        for snapshot in (report, later):
            identity = snapshot["source_identity"]
            self.assertIn("captured_monotonic", identity)
            snapshot["source_identity"] = {
                key: value for key, value in identity.items()
                if key != "captured_monotonic"
            }
        self.assertEqual(later, report)
        session.close()
        with self.assertRaises(RuntimeError):
            session.offer((2,))

    def test_finish_timeout_still_waits_for_cancellation_cleanup(self):
        entered, cleaned = threading.Event(), threading.Event()

        def operation(values, cancellation, progress):
            analyze(values[:1024], threading.Event(), progress)
            entered.set()
            if not cancellation.wait(5):
                raise TimeoutError("finish did not request cancellation")
            cleaned.set()
            raise OperationCancelled()

        session = self.session(analyzer=operation)
        revision = session.offer((4,) * 2048)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        report = session.finish(timeout=0)
        self.assertFalse(report["complete"])
        self.assertEqual(report["stop_reason"], "timeout")
        self.assertTrue(cleaned.is_set())
        self.assertEqual(session._execution.outstanding, 0)
        self.assertEqual(session._execution.reserved_resources, ())
        self.assertEqual(report["metrics"]["physical_values"], 1024)
        self.assertEqual(self.row(session, revision)["disposition"], "canceled")

    def test_observations_are_retained_once_after_executor_drain(self):
        session = self.session()
        revision = session.offer((4, 5, 6))
        self.dispatch_ready(session)
        ident = self.row(session, revision)["job_id"]
        self.await_physical(session, ident)
        session.step()
        report = session.report()
        observations = report["observations"]
        self.assertEqual([row["kind"] for row in observations if row["id"] == ident],
                         ["submitted", "started", "completed", "reconciled"])
        self.assertEqual(session._execution.observations(), ())
        session.step()
        self.assertEqual(session.report()["observations"], observations)
        self.assertEqual(len(session.report()["results"]), 1)

    def test_causal_lease_changes_do_not_change_application_data_revision(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation)
        values = (7,) * 2048
        revision = session.offer(values)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        ident = self.row(session, revision)["job_id"]
        first_request = session._scheduler.snapshot(0.0)
        second_request = session._scheduler.snapshot(0.0)
        self.assertGreater(second_request.generation, first_request.generation)
        self.assertNotEqual(second_request.generation, revision)
        self.assertEqual(session.revision, revision)
        release.set()
        self.await_physical(session, ident)
        result, = session.step()
        self.assertEqual(result.generation, revision)
        self.assertFalse(result.stale)
        self.assertTrue(result.usable)
        self.assertEqual(session.current, expected(values))
        self.assertEqual(calls, [values])
        mapping = session.report()["clock"]
        self.assertEqual(mapping["session_origin"], clock.value)
        self.assertEqual(self.row(session, revision)["offered_at"], 0.0)
        self.assertIn("separate clock domain", mapping["physical_units"])
        self.assertIn("submission", mapping["deadline"])

    def test_failed_planning_does_not_dispatch_old_catalog_on_healthy_successor(self):
        calls = []

        def operation(values, cancellation, progress):
            calls.append(values)
            return analyze(values, cancellation, progress)

        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation)
        with patch.object(session._planner, "_compute", side_effect=ValueError("declared planner failure")):
            failed = session.offer((13, 13))
            self.dispatch_ready(session)
        failed_id = self.row(session, failed)["job_id"]
        self.assertEqual(self.row(session, failed)["disposition"], "planning_failed")
        self.assertIn("declared planner failure", self.row(session, failed)["error"])
        self.assertEqual(calls, [])
        self.assertEqual(session._execution.outstanding, 0)

        clock.advance()
        healthy = session.offer((2, 4, 6))
        self.dispatch_ready(session)
        healthy_id = self.row(session, healthy)["job_id"]
        self.assertNotEqual(failed_id, healthy_id)
        self.await_physical(session, healthy_id)
        result, = session.step()
        self.assertEqual(result.dispatch.id, healthy_id)
        self.assertTrue(result.usable)
        self.assertEqual(session.current, expected((2, 4, 6)))
        self.assertEqual(calls, [(2, 4, 6)])
        self.assertEqual(self.row(session, failed)["disposition"], "planning_failed")
        self.assertEqual(self.row(session, failed)["processed"], 0)
        self.assertEqual(len(session.report()["results"]), 1)
        self.assertEqual(session.report()["metrics"]["admitted"], 1)

    def test_execution_constructor_failure_leaves_no_planner_thread(self):
        created = []

        def new_planner(*args, **kwargs):
            worker = LatestWorker(*args, **kwargs)
            created.append(worker)
            self.addCleanup(worker.close, None)
            return worker

        before = {thread.ident for thread in threading.enumerate()
                  if thread.name == "latest-planner"}
        with patch("adaptive_timing.revised_analysis.LatestWorker", side_effect=new_planner), \
                patch("adaptive_timing.revised_analysis.RealExecutor",
                      side_effect=RuntimeError("declared executor construction failure")):
            with self.assertRaisesRegex(RuntimeError, "declared executor construction failure"):
                AnalysisSession()
        self.assertTrue(all(not worker._thread.is_alive() for worker in created))
        after = {thread.ident for thread in threading.enumerate()
                 if thread.name == "latest-planner"}
        self.assertEqual(after - before, set())

    def test_planner_constructor_failure_closes_already_created_executor(self):
        created = []

        def new_execution(*args, **kwargs):
            executor = RealExecutor(*args, **kwargs)
            created.append(executor)
            self.addCleanup(executor.close)
            return executor

        with patch("adaptive_timing.revised_analysis.RealExecutor", side_effect=new_execution), \
                patch("adaptive_timing.revised_analysis.LatestWorker",
                      side_effect=RuntimeError("declared mailbox construction failure")):
            with self.assertRaisesRegex(RuntimeError, "declared mailbox construction failure"):
                AnalysisSession()
        self.assertEqual(len(created), 1)
        executor, = created
        self.assertTrue(executor._closed)
        self.assertTrue(executor._pool._shutdown)
        self.assertEqual(executor.outstanding, 0)
        self.assertEqual(executor.reserved_resources, ())

    def test_false_return_is_physical_success_with_explicit_adoption_error(self):
        session = self.session(analyzer=lambda values, cancellation, progress: False)
        revision = session.offer((1, 2, 3))
        self.dispatch_ready(session)
        self.await_physical(session, self.row(session, revision)["job_id"])
        result, = session.step()
        self.assertEqual(result.status, "completed")
        self.assertIs(result.value, False)
        self.assertIsNone(result.error)
        self.assertTrue(result.usable)
        self.assertIsNone(session.current)
        self.assertEqual(self.row(session, revision)["disposition"], "completed")
        self.assertIn("Summary", self.row(session, revision)["adoption_error"])
        report = session.finish()
        self.assertFalse(report["complete"])
        self.assertEqual(report["stop_reason"], "no_current_result")
        self.assertEqual(report["results"][0]["status"], "completed")
        self.assertIs(report["results"][0]["value"], False)
        self.assertEqual(report["metrics"]["physical_completed"], 1)

    def test_physically_accepted_queue_survives_submission_cutoff(self):
        operation, entered, release, calls = self.gated_analysis(7)
        clock = ManualClock()
        session = self.session(clock=clock, analyzer=operation, capacity=2,
                               pending_seconds=0.25)
        first = (7,) * 2048
        old = session.offer(first)
        self.dispatch_ready(session)
        self.wait_gate(entered)
        old_id = self.row(session, old)["job_id"]
        clock.advance()
        current_values = (3, 4, 5)
        current = session.offer(current_values)
        self.dispatch_ready(session)
        current_id = self.row(session, current)["job_id"]
        self.assertEqual(self.row(session, current)["disposition"], "admitted")
        self.assertEqual(session._execution.outstanding, 2)
        self.assertIsNone(session._execution._jobs[current_id].future)
        clock.advance(0.5)
        self.assertEqual(session.step(), ())
        self.assertEqual(self.row(session, current)["disposition"], "admitted")
        self.assertEqual(session._execution.outstanding, 2)
        self.assertEqual(calls, [first])

        release.set()
        self.await_physical(session, old_id)
        old_result, = session.step()
        self.assertTrue(old_result.stale)
        self.await_physical(session, current_id)
        current_result, = session.step()
        self.assertEqual(current_result.dispatch.id, current_id)
        self.assertTrue(current_result.usable)
        self.assertEqual(current_result.status, "completed")
        self.assertEqual(session.current, expected(current_values))
        self.assertEqual(calls, [first, current_values])
        self.assertGreater(self.row(session, current)["adopted_at"],
                           self.row(session, current)["deadline"])
        self.assertEqual(session._execution.outstanding, 0)
        self.assertEqual(session._execution.reserved_resources, ())

    def test_default_clock_demonstration_finishes_actual_revised_work(self):
        report = demonstration()
        self.assertTrue(report["complete"])
        self.assertTrue(report["closed"])
        self.assertTrue(report["planner_stopped"])
        self.assertEqual(report["outstanding"], 0)
        self.assertEqual(report["reserved_resources"], [])
        metrics = report["metrics"]
        self.assertEqual(metrics["offered"], 3)
        self.assertEqual(metrics["admitted"], 2)
        self.assertEqual(metrics["physical_completed"], 2)
        self.assertEqual(metrics["stale"], 1)
        self.assertEqual(metrics["usable"], 1)
        self.assertEqual(metrics["physical_values"], 10_240)
        second = report["revisions"][1]
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["disposition"], "superseded_before_admission")
        self.assertNotIn("admitted_at", second)
        self.assertEqual(second["processed"], 0)
        current = report["current"]
        self.assertEqual(current["count"], 2048)
        self.assertEqual(current["total"], 142_336)
        self.assertEqual(current["histogram"][7], 512)
        self.assertEqual(current["histogram"][8], 1024)
        self.assertEqual(current["histogram"][255], 512)
        self.assertEqual(sum(current["histogram"]), 2048)


if __name__ == "__main__":
    unittest.main()
