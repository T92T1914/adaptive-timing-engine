"""A shutdown diagnostic must not carry the worker's resource to the caller."""
import gc
import threading
import unittest
import weakref
from concurrent.futures import Future
from unittest.mock import patch

from adaptive_timing.execution import OwnerExecutor
from adaptive_timing.runtime import Dispatch


def close_failure(executor):
    try:
        executor.close()
    except BaseException as error:  # noqa: BLE001 - distinguish caller and resource interruptions.
        return error
    raise AssertionError("the resource close failure was hidden")


class OwnerShutdownTests(unittest.TestCase):
    def resource_factory(self, fail):
        calls, references = [], []

        class Resource:
            def __init__(self):
                self.owner = threading.get_ident()
                calls.append(("create", self.owner))
                references.append(weakref.ref(self))

            def close(self):
                calls.append(("close", threading.get_ident()))
                if self.owner != threading.get_ident():
                    raise AssertionError("wrong resource close thread")
                fail(self)

            def __del__(self):
                calls.append(("destroy", threading.get_ident()))

        return Resource, calls, references

    def assert_retired(self, executor, calls, references):
        gc.collect()
        self.assertIsNone(references[0]())
        self.assertEqual([name for name, _ in calls], ["create", "close", "destroy"])
        self.assertEqual(len({ident for _, ident in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())
        self.assertIsNone(executor._resource)
        self.assertFalse(executor._resource_initialized)
        self.assertTrue(executor._resource_closed)
        self.assertTrue(all(not worker.is_alive() for worker in executor._pool._threads))

    def test_retained_close_error_does_not_retain_resource_or_worker_traceback(self):
        def fail(resource):
            raise ValueError("close failed")

        factory, calls, references = self.resource_factory(fail)
        executor = OwnerExecutor(factory)
        error = close_failure(executor)
        self.assert_retired(executor, calls, references)
        self.assertIs(type(error), RuntimeError)
        self.assertEqual(str(error), "Owner resource cleanup failed: ValueError: close failed")
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        frames = []
        traceback = error.__traceback__
        while traceback is not None:
            frames.append(traceback.tb_frame.f_code.co_name)
            traceback = traceback.tb_next
        self.assertNotIn("_close_resource", frames)
        self.assertNotIn("_retire_resource", frames)
        executor.close()
        self.assert_retired(executor, calls, references)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            executor.submit_owned(Dispatch("after", "owned", 0, 1, "dispatched"),
                                  1, lambda resource, cancel: None)

    def test_chained_cleanup_traceback_locals_are_released_on_worker(self):
        class ResourceCloseError(ValueError):
            def __str__(self):
                return "owned close failed"

        def inner_failure(resource):
            error = ValueError("nested close failure", resource)
            raise error

        def fail(resource):
            try:
                inner_failure(resource)
            except ValueError as cause:
                error = ResourceCloseError("close failed", resource)
                error.resource = resource
                raise error from cause

        factory, calls, references = self.resource_factory(fail)
        executor = OwnerExecutor(factory)
        error = close_failure(executor)
        self.assertIs(type(error), RuntimeError)
        self.assertEqual(str(error),
                         "Owner resource cleanup failed: ResourceCloseError: owned close failed")
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assert_retired(executor, calls, references)

    def test_close_base_exceptions_and_unprintable_error_still_retire_on_worker(self):
        class UnprintableError(BaseException):
            def __str__(self):
                raise SystemExit("diagnostic formatting failed")

        for error_type in (KeyboardInterrupt, SystemExit, UnprintableError):
            with self.subTest(error_type=error_type.__name__):
                def fail(resource, error_type=error_type):
                    raise error_type("resource stopped cleanup")

                factory, calls, references = self.resource_factory(fail)
                executor = OwnerExecutor(factory)
                error = close_failure(executor)
                self.assertIs(type(error), RuntimeError)
                suffix = ("diagnostic unavailable" if error_type is UnprintableError
                          else "resource stopped cleanup")
                self.assertEqual(str(error),
                    f"Owner resource cleanup failed: {error_type.__name__}: {suffix}")
                self.assert_retired(executor, calls, references)

    def test_consumer_close_override_can_fail_before_clearing_its_resource(self):
        def fail(resource):
            raise ValueError("consumer close failed")

        factory, calls, references = self.resource_factory(fail)
        hook_threads = []

        class ConsumerExecutor(OwnerExecutor):
            def _close_resource(self):
                hook_threads.append(threading.get_ident())
                self._resource.close()

        executor = ConsumerExecutor(factory)
        error = close_failure(executor)
        self.assertIs(type(error), RuntimeError)
        self.assertIn("ValueError: consumer close failed", str(error))
        self.assert_retired(executor, calls, references)
        self.assertEqual(hook_threads, [calls[0][1]])
        executor.close()
        self.assertEqual(hook_threads, [calls[0][1]])

    def test_failed_close_waits_for_running_work_and_preserves_queued_cancellation(self):
        def fail(resource):
            raise ValueError("close after work failed")

        factory, calls, references = self.resource_factory(fail)
        executor = OwnerExecutor(factory, capacity=2)
        entered, completed = threading.Event(), threading.Event()

        def work(resource, cancel):
            self.assertEqual(resource.owner, threading.get_ident())
            entered.set()
            if not cancel.wait(2):
                raise TimeoutError("shutdown cancellation gate")
            completed.set()
            return 17

        executor.submit_owned(Dispatch("running", "owned", 0, 1, "dispatched"), 1, work)
        self.assertTrue(entered.wait(1))
        executor.submit_owned(Dispatch("queued", "owned", 0, 1, "dispatched"), 1,
                              lambda resource, cancel: self.fail("queued work ran"))
        error = close_failure(executor)
        self.assertIs(type(error), RuntimeError)
        self.assertTrue(completed.is_set())
        self.assert_retired(executor, calls, references)
        rows = {row.dispatch.id: row for row in executor.reconcile(1)}
        self.assertEqual((rows["running"].status, rows["running"].value), ("completed", 17))
        self.assertTrue(rows["running"].cancellation_requested)
        self.assertFalse(rows["running"].usable)
        self.assertEqual(rows["queued"].status, "canceled")
        self.assertEqual(executor.outstanding, 0)

    def test_caller_interruption_during_close_keeps_original_interrupt(self):
        entered, release = threading.Event(), threading.Event()

        def close(resource):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("resource close gate")

        factory, calls, references = self.resource_factory(close)
        executor = OwnerExecutor(factory)
        interruption = KeyboardInterrupt("caller stopped waiting for close")

        def interrupted_result(future, timeout=None):
            self.assertTrue(entered.wait(1))
            release.set()
            raise interruption

        try:
            with patch.object(Future, "result", interrupted_result):
                error = close_failure(executor)
        finally:
            release.set()
        self.assertIs(error, interruption)
        self.assert_retired(executor, calls, references)
        executor.close()


if __name__ == "__main__":
    unittest.main()
