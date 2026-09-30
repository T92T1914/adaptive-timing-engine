import threading
import unittest

from adaptive_timing.execution import OwnerExecutor
from adaptive_timing.runtime import Dispatch


def dispatch(ident):
    return Dispatch(ident, "owned", 0, 1, "dispatched")


class OwnerTests(unittest.TestCase):
    def test_owner_cleanup_outstanding_failure_cancel_and_stale(self):
        calls = []
        entered, release = threading.Event(), threading.Event()

        class Resource:
            def __init__(self):
                self.owner = threading.get_ident()
                calls.append(("create", self.owner))

            def close(self):
                calls.append(("close", threading.get_ident()))
                self_check = self.owner == threading.get_ident()
                if not self_check:
                    raise AssertionError("wrong close owner")

        execution = OwnerExecutor(Resource, capacity=2)

        def work(resource, cancel):
            self.assertEqual(resource.owner, threading.get_ident())
            entered.set()
            if not release.wait(2):
                raise TimeoutError("gate")
            calls.append(("complete", threading.get_ident()))
            return 17

        try:
            execution.submit_owned(dispatch("running"), 1, work)
            self.assertTrue(entered.wait(1))
            execution.submit_owned(dispatch("queued"), 1,
                lambda resource, cancel: self.fail("canceled queued work ran"))
            with self.assertRaises(RuntimeError):
                execution.submit_owned(dispatch("full"), 1, lambda r, c: 0)
            self.assertTrue(execution.cancel("queued"))
            self.assertFalse(execution.cancel("running"))
            queued, = execution.reconcile(2)
            self.assertEqual(queued.status, "canceled")
            self.assertEqual(execution.outstanding, 1)
            release.set()
            result, = execution.wait(2, timeout=2)
            self.assertEqual((result.status, result.value), ("completed", 17))
            self.assertTrue(result.stale)
            self.assertFalse(result.usable)
            failed = threading.Event()
            def fail(resource, cancel):
                failed.set()
                raise ValueError("synthetic host failure")
            execution.submit_owned(dispatch("failure"), 2, fail)
            self.assertTrue(failed.wait(1))
            execution.close()
            failure, = execution.reconcile(2)
            self.assertEqual(failure.status, "failed")
            self.assertEqual(execution.outstanding, 0)
            self.assertEqual(len({owner for _, owner in calls}), 1)
            self.assertEqual(calls[-1][0], "close")
        finally:
            release.set()
            execution.close()

    def test_factory_error_and_context_exception(self):
        def failure():
            raise ValueError("factory error")
        with self.assertRaisesRegex(ValueError, "factory error"):
            OwnerExecutor(failure)
        closed = []
        class Resource:
            def close(self):
                closed.append(threading.get_ident())
        with self.assertRaisesRegex(ValueError, "body"):
            with OwnerExecutor(Resource):
                raise ValueError("body")
        self.assertEqual(len(closed), 1)
