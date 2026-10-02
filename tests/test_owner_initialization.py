"""Failed owner initialization still retires a created resource on its worker."""
from concurrent.futures import Future
import gc
import threading
import unittest
from unittest.mock import patch
import weakref

from adaptive_timing.execution import OwnerExecutor


class OwnerInitializationTests(unittest.TestCase):
    def interrupted_factory(self, error, *, close_error=False, subclass=False):
        calls, references = [], []
        entered, release = threading.Event(), threading.Event()
        original_result = Future.result
        interrupted = False
        before = {thread.ident for thread in threading.enumerate()}

        class Resource:
            def __init__(self):
                calls.append(("create", threading.get_ident()))
                references.append(weakref.ref(self))

            def close(self):
                calls.append(("close", threading.get_ident()))
                if close_error:
                    raise ValueError("inert resource cleanup failed")

            def __del__(self):
                calls.append(("destroy", threading.get_ident()))

        def factory():
            resource = Resource()
            entered.set()
            if not release.wait(2):
                raise TimeoutError("factory gate")
            return resource

        def interrupted_result(future, timeout=None):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                self.assertTrue(entered.wait(1))
                release.set()
                raise error
            return original_result(future, timeout)

        class ConsumerExecutor(OwnerExecutor):
            def _close_resource(self):
                calls.append(("consumer_close", threading.get_ident()))
                super()._close_resource()

        executor_type = ConsumerExecutor if subclass else OwnerExecutor
        try:
            with (patch.object(Future, "result", interrupted_result),
                  self.assertRaises(type(error)) as caught):
                executor_type(factory)
            self.assertIs(caught.exception, error)
        finally:
            release.set()
        gc.collect()
        self.assertEqual([name for name, _ in calls].count("create"), 1)
        self.assertEqual([name for name, _ in calls].count("close"), 1)
        self.assertEqual([name for name, _ in calls].count("destroy"), 1)
        self.assertEqual(len({ident for _, ident in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())
        self.assertIsNone(references[0]())
        self.assertFalse(any(thread.ident not in before
                             and thread.name.startswith("timing-execution")
                             for thread in threading.enumerate()))
        return calls

    def test_caller_interrupt_after_creation_closes_and_releases_on_worker(self):
        for error_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(error_type=error_type):
                self.interrupted_factory(error_type("caller stopped initialization"))

    def test_cleanup_failure_keeps_initial_interrupt_and_worker_reference_release(self):
        error = KeyboardInterrupt("caller stopped initialization")
        self.interrupted_factory(error, close_error=True)
        self.assertTrue(any("ValueError: inert resource cleanup failed" in note
                            for note in error.__notes__))

    def test_partial_initialization_uses_consumer_close_hook(self):
        calls = self.interrupted_factory(SystemExit("owner stopped"), subclass=True)
        self.assertEqual([name for name, _ in calls].count("consumer_close"), 1)

    def test_submit_failure_cannot_construct_a_rejected_resource_later(self):
        from concurrent.futures import ThreadPoolExecutor
        original_submit = ThreadPoolExecutor.submit
        first = True
        calls = []

        def enqueue_then_fail(pool, function, *args, **kwargs):
            nonlocal first
            future = original_submit(pool, function, *args, **kwargs)
            if first:
                first = False
                raise RuntimeError("submit failed after enqueue")
            return future

        with (patch.object(ThreadPoolExecutor, "submit", enqueue_then_fail),
              self.assertRaisesRegex(RuntimeError, "submit failed after enqueue")):
            OwnerExecutor(lambda: calls.append("created"))
        self.assertEqual(calls, [])

    def test_factory_failure_does_not_call_cleanup_hook_for_missing_resource(self):
        calls = []

        class ConsumerExecutor(OwnerExecutor):
            def _close_resource(self):
                calls.append("close hook")
                super()._close_resource()

        error = ValueError("factory failed before returning a resource")

        def factory():
            raise error

        with self.assertRaises(ValueError) as caught:
            ConsumerExecutor(factory)
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
