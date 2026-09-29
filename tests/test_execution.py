"""Real work lifecycle regressions use gates rather than timing assumptions."""
from concurrent.futures import Future, ThreadPoolExecutor
import gc
import threading
import unittest
import weakref
from unittest.mock import patch

from adaptive_timing import Event, Executor, Policy, build_plan
from adaptive_timing.execution import OperationCancelled, RealExecutor
from adaptive_timing.runtime import Dispatch


def dispatch(ident, resource="cpu"):
    return Dispatch(ident, resource, 0.0, 0.001, "dispatched")


class ExitWhileFormatting(ValueError):
    def __str__(self):
        raise SystemExit("bad formatter")


class ExecutionTests(unittest.TestCase):
    def test_virtual_dispatch_is_not_physical_completion_or_resource_release(self):
        entered, release, second_started, other_started = (threading.Event() for _ in range(4))

        def first(cancel):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test gate")
            return 21

        virtual = Executor()
        virtual.install(build_plan((Event("a", 0, 0, 1, 0.001),
                                    Event("b", 0.01, 0.01, 1, 0.001)), Policy(use_noise=False)))
        first_dispatch = virtual.poll(0)[0]
        second_dispatch = virtual.poll(0.02)[0]
        self.assertEqual(second_dispatch.status, "dispatched")
        executor = RealExecutor(max_workers=2)
        try:
            executor.submit(first_dispatch, 1, first)
            self.assertTrue(entered.wait(1))
            executor.submit(second_dispatch, 1, lambda cancel: second_started.set())
            executor.submit(dispatch("c", "other"), 1, lambda cancel: other_started.set())
            self.assertTrue(other_started.wait(1))
            executor.wait(1, timeout=1)
            self.assertFalse(second_started.is_set())
            self.assertIn(first_dispatch.resource, executor.reserved_resources)
            release.set()
            outcomes = []
            while executor.outstanding:
                rows = executor.wait(1, timeout=1)
                self.assertTrue(rows)
                outcomes.extend(rows)
            self.assertTrue(second_started.is_set())
            first_result = next(row for row in outcomes if row.dispatch.id == "a")
            self.assertEqual(first_result.value, 21)
            self.assertTrue(first_result.usable)
            self.assertLessEqual(first_result.submitted_at, first_result.started_at)
            self.assertLessEqual(first_result.started_at, first_result.completed_at)
            self.assertLessEqual(first_result.completed_at, first_result.reconciled_at)
        finally:
            release.set()
            executor.close()

    def test_stale_completion_retains_value_and_physical_success(self):
        with RealExecutor() as executor:
            executor.submit(dispatch("old"), 1, lambda cancel: 42)
            result, = executor.wait(2, timeout=1)
            self.assertEqual((result.status, result.value), ("completed", 42))
            self.assertTrue(result.stale)
            self.assertFalse(result.usable)
            self.assertFalse(result.cancellation_requested)
            self.assertEqual(executor.reconcile(2), ())
            observations = executor.observations()
            self.assertEqual([row.kind for row in observations],
                             ["submitted", "started", "completed", "reconciled"])
            self.assertTrue(observations[-1].stale)
            self.assertEqual(executor.observations(), ())

    def test_running_cancellation_request_does_not_cancel_or_release(self):
        entered, release = threading.Event(), threading.Event()

        def operation(cancel):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test gate")
            return False

        executor = RealExecutor()
        try:
            executor.submit(dispatch("a"), 1, operation)
            self.assertTrue(entered.wait(1))
            self.assertFalse(executor.cancel("a"))
            self.assertEqual(executor.reconcile(1), ())
            self.assertEqual(executor.reserved_resources, ("cpu",))
            release.set()
            result, = executor.wait(1, timeout=1)
            self.assertEqual(result.status, "completed")
            self.assertIs(result.value, False)
            self.assertTrue(result.cancellation_requested)
            self.assertFalse(result.usable)
            self.assertNotIn("cancel_confirmed", [row.kind for row in executor.observations()])
        finally:
            release.set()
            executor.close()

    def test_cooperative_cancellation_is_acknowledged_only_after_cleanup(self):
        entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()

        def operation(cancel):
            entered.set()
            if not cancel.wait(2):
                raise TimeoutError("no cancellation")
            if not release.wait(2):
                raise TimeoutError("cleanup blocked")
            cleanup.set()
            raise OperationCancelled()

        executor = RealExecutor()
        try:
            executor.submit(dispatch("a"), 1, operation)
            self.assertTrue(entered.wait(1))
            self.assertFalse(executor.cancel("a"))
            self.assertEqual(executor.reconcile(2), ())
            self.assertEqual(executor.reserved_resources, ("cpu",))
            release.set()
            result, = executor.wait(2, timeout=1)
            self.assertTrue(cleanup.is_set())
            self.assertEqual(result.status, "canceled")
            self.assertTrue(result.stale)
            self.assertIsNotNone(result.started_at)
            self.assertEqual(executor.reserved_resources, ())
        finally:
            release.set()
            executor.close()

    def test_cancel_after_completed_future_does_not_relabel_success(self):
        with RealExecutor() as executor:
            executor.submit(dispatch("a"), 1, lambda cancel: "finished")
            # Inspect the actual completion boundary, before owner reconciliation.
            executor._jobs["a"].future.result(timeout=1)
            self.assertFalse(executor.cancel("a"))
            result, = executor.reconcile(1)
            self.assertEqual((result.status, result.value), ("completed", "finished"))
            self.assertTrue(result.cancellation_requested)
            self.assertFalse(result.usable)
            self.assertNotIn("cancel_confirmed", [row.kind for row in executor.observations()])

    def test_queued_cancellation_does_not_call_operation(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def first(cancel):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test gate")

        executor = RealExecutor()
        try:
            executor.submit(dispatch("a"), 1, first)
            self.assertTrue(entered.wait(1))
            executor.submit(dispatch("b"), 1, lambda cancel: calls.append("b"))
            self.assertTrue(executor.cancel("b"))
            self.assertTrue(executor.cancel("b"))
            result, = executor.reconcile(1)
            self.assertEqual(result.status, "canceled")
            self.assertIsNone(result.started_at)
            self.assertEqual(calls, [])
            self.assertEqual(executor.reserved_resources, ("cpu",))
            self.assertEqual([r.kind for r in executor.observations() if r.id == "b"],
                             ["submitted", "cancel_requested", "cancel_confirmed", "reconciled"])
        finally:
            release.set()
            executor.close()

    def test_capacity_and_session_identity_preserve_state_on_rejection(self):
        with RealExecutor(capacity=1) as executor:
            executor.submit(dispatch("a"), 1, lambda cancel: 10)
            executor._jobs["a"].future.result(timeout=1)
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                executor.submit(dispatch("b"), 1, lambda cancel: 20)
            self.assertEqual(executor.outstanding, 1)
            self.assertEqual(executor.reconcile(1)[0].value, 10)
            with self.assertRaisesRegex(ValueError, "twice"):
                executor.submit(dispatch("a"), 1, lambda cancel: 30)
            executor.submit(dispatch("b"), 1, lambda cancel: 20)
            self.assertEqual(executor.wait(1, timeout=1)[0].value, 20)

    def test_cancel_at_pool_boundary_confirms_a_future_that_never_started(self):
        with RealExecutor() as executor:
            future = Future()
            with patch.object(executor._pool, "submit", return_value=future):
                executor.submit(dispatch("a"), 1, lambda cancel: self.fail("canceled work ran"))
            self.assertTrue(executor.cancel("a"))
            self.assertTrue(future.cancelled())
            result, = executor.reconcile(1)
            self.assertEqual(result.status, "canceled")
            self.assertIsNone(result.started_at)
            self.assertEqual(executor.reserved_resources, ())

    def test_failures_release_resource_and_preserve_the_next_operation(self):
        for error in (ValueError, SystemExit, KeyboardInterrupt, ExitWhileFormatting,
                      OperationCancelled):
            with self.subTest(error=error.__name__), RealExecutor() as executor:
                def failing(cancel):
                    raise error("failure")

                executor.submit(dispatch("a"), 1, failing)
                executor.submit(dispatch("b"), 1, lambda cancel: 42)
                results = []
                while executor.outstanding:
                    rows = executor.wait(1, timeout=1)
                    self.assertTrue(rows)
                    results.extend(rows)
                self.assertEqual([row.status for row in results], ["failed", "completed"])
                self.assertIn(error.__name__, results[0].error)
                self.assertEqual(results[1].value, 42)

    def test_submission_failure_is_recorded_and_reconciled(self):
        for error in (RuntimeError("unavailable"), ExitWhileFormatting("formatter exited")):
            with self.subTest(error=type(error).__name__), RealExecutor() as executor:
                with patch.object(executor._pool, "submit", side_effect=error):
                    executor.submit(dispatch("a"), 1, lambda cancel: 0)
                result, = executor.reconcile(1)
                self.assertEqual(result.status, "failed")
                self.assertIsNone(result.started_at)
                self.assertIn("unavailable", result.error)
                self.assertEqual(executor.reserved_resources, ())

    def test_post_enqueue_thread_start_failure_cannot_invoke_rejected_work(self):
        entered, release, next_started = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def first(cancel):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test gate")
            calls.append("first")

        executor = RealExecutor(max_workers=2)
        try:
            executor.submit(dispatch("first", "first-resource"), 1, first)
            self.assertTrue(entered.wait(1))
            # This is the real CPython enqueue path. Only its later thread
            # startup fails, so the rejected work item remains in the queue.
            with patch("threading.Thread.start", side_effect=RuntimeError("thread unavailable")):
                executor.submit(dispatch("rejected", "reused-resource"), 1,
                                lambda cancel: calls.append("forbidden"))
            failed, = executor.reconcile(1)
            self.assertEqual((failed.dispatch.id, failed.status), ("rejected", "failed"))
            self.assertNotIn("reused-resource", executor.reserved_resources)

            def following(cancel):
                calls.append("following")
                next_started.set()

            # Keep the original worker as the sole queue consumer. The reused
            # resource belongs to following when it reaches the rejected item.
            with patch.object(executor._pool, "_adjust_thread_count"):
                executor.submit(dispatch("following", "reused-resource"), 1, following)
            self.assertIn("reused-resource", executor.reserved_resources)
            release.set()
            self.assertTrue(next_started.wait(1))
            while executor.outstanding:
                self.assertTrue(executor.wait(1, timeout=1))
            self.assertEqual(calls, ["first", "following"])
            rejected_rows = [row.kind for row in executor.observations() if row.id == "rejected"]
            self.assertEqual(rejected_rows, ["submitted", "failed", "reconciled"])
        finally:
            release.set()
            executor.close()

    def test_owner_interrupt_during_submit_preserves_failure_and_still_propagates(self):
        with RealExecutor() as executor:
            with patch.object(executor._pool, "submit", side_effect=KeyboardInterrupt("stop")):
                with self.assertRaises(KeyboardInterrupt):
                    executor.submit(dispatch("a"), 1, lambda cancel: self.fail("interrupted work ran"))
            failed, = executor.reconcile(1)
            self.assertEqual(failed.status, "failed")
            self.assertIn("KeyboardInterrupt", failed.error)
            self.assertEqual(executor.reserved_resources, ())

    def test_nonwaiting_shutdown_retains_inputs_until_physical_completion(self):
        entered, release = threading.Event(), threading.Event()

        class OwnedOperation:
            def __call__(self, cancel):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test gate")
                return 42

        executor = RealExecutor()
        operation = OwnedOperation()
        reference = weakref.ref(operation)
        executor.submit(dispatch("a"), 1, operation)
        del operation
        try:
            self.assertTrue(entered.wait(1))
            executor.submit(dispatch("b"), 1, lambda cancel: self.fail("queued work ran"))
            executor.close(wait_for_completion=False)
            queued, = executor.reconcile(1)
            self.assertEqual((queued.dispatch.id, queued.status), ("b", "canceled"))
            gc.collect()
            self.assertIsNotNone(reference())
            self.assertEqual(executor.reserved_resources, ("cpu",))
            with self.assertRaisesRegex(RuntimeError, "closed"):
                executor.submit(dispatch("c"), 1, lambda cancel: None)
            release.set()
            result, = executor.wait(1, timeout=1)
            self.assertEqual(result.status, "completed")
            self.assertTrue(result.cancellation_requested)
            executor.close()
            gc.collect()
            self.assertIsNone(reference())
            self.assertEqual(executor.outstanding, 0)
        finally:
            release.set()
            executor.close()

    def test_invalid_calls_do_not_consume_a_finished_result(self):
        with RealExecutor() as executor:
            executor.submit(dispatch("a"), 1, lambda cancel: 42)
            for generation in (0, -1, True, 1.5):
                with self.assertRaises(ValueError):
                    executor.reconcile(generation)
            for timeout in (-1, float("nan"), float("inf"), True):
                with self.assertRaises(ValueError):
                    executor.wait(1, timeout=timeout)
            self.assertEqual(executor.wait(1, timeout=1)[0].value, 42)

    def test_waiting_shutdown_waits_for_cooperative_cleanup(self):
        entered, cleaned = threading.Event(), threading.Event()

        def operation(cancel):
            entered.set()
            if not cancel.wait(2):
                raise TimeoutError("shutdown did not request cancellation")
            cleaned.set()
            raise OperationCancelled()

        executor = RealExecutor()
        executor.submit(dispatch("a"), 1, operation)
        self.assertTrue(entered.wait(1))
        executor.close()
        self.assertTrue(cleaned.is_set())
        self.assertEqual(executor.reconcile(1)[0].status, "canceled")

    def test_invalid_submission_never_consumes_identity_or_capacity(self):
        with RealExecutor(capacity=1) as executor:
            for record, generation, operation in (
                (Dispatch("a", "cpu", 0, 1, "expired"), 1, lambda cancel: 1),
                (dispatch("a"), 0, lambda cancel: 1),
                (dispatch("a"), 1, None),
                (dispatch("a", ""), 1, lambda cancel: 1),
            ):
                with self.assertRaises((ValueError, TypeError)):
                    executor.submit(record, generation, operation)
                self.assertEqual(executor.outstanding, 0)
                self.assertEqual(executor.observations(), ())
            executor.submit(dispatch("a"), 1, lambda cancel: 42)
            self.assertEqual(executor.wait(1, timeout=1)[0].value, 42)

    def test_non_owner_cannot_mutate_or_consume_state(self):
        with RealExecutor() as executor, ThreadPoolExecutor(max_workers=1) as foreign:
            for call in (lambda: executor.submit(dispatch("a"), 1, lambda cancel: 1),
                         lambda: executor.cancel("a"), lambda: executor.reconcile(1),
                         lambda: executor.wait(1), executor.close, executor.observations,
                         lambda: executor.outstanding, lambda: executor.reserved_resources):
                with self.assertRaisesRegex(RuntimeError, "owner thread"):
                    foreign.submit(call).result(timeout=1)
            self.assertEqual(executor.outstanding, 0)


if __name__ == "__main__":
    unittest.main()
