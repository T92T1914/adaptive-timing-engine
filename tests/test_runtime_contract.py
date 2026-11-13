"""Pins what heterogeneous-batch-runtime relies on at its installed commit.

That package imports Dispatch and OwnerExecutor and subclasses OwnerExecutor
through submit_owned, _assert_owner, _resource and _close_resource. The hooks
are underscore names, so this test is what makes them a stated contract. Change
them only together with the runtime's pin.
"""
import dataclasses
import threading
import unittest

from adaptive_timing.execution import OwnerExecutor
from adaptive_timing.runtime import Dispatch


class DispatchContractTests(unittest.TestCase):
    def test_dispatch_fields_and_order(self):
        self.assertTrue(dataclasses.is_dataclass(Dispatch))
        self.assertEqual([f.name for f in dataclasses.fields(Dispatch)],
                         ["id", "resource", "actual_start", "finish", "status"])
        record = Dispatch("a", "r", 0, 1, "dispatched")
        self.assertEqual((record.id, record.resource, record.status), ("a", "r", "dispatched"))


class OwnerHookContractTests(unittest.TestCase):
    def test_subclass_hooks_keep_their_meaning(self):
        events = []

        class Resource:
            def close(self):
                events.append(("raw close", threading.get_ident()))

        class Subclass(OwnerExecutor):
            def _close_resource(self):
                events.append(("hook close", threading.get_ident(), self._resource is not None))
                try:
                    super()._close_resource()
                finally:
                    self._resource = None

        execution = Subclass(Resource, capacity=2)
        try:
            execution._assert_owner()
            seen = []

            def operation(resource, cancel):
                seen.append((isinstance(resource, Resource), isinstance(cancel, threading.Event),
                             threading.get_ident()))
                return 5

            execution.submit_owned(Dispatch("job", "owned", 0, 1, "dispatched"), 1, operation)
            result, = execution.wait(1, timeout=2)
            self.assertEqual((result.status, result.value), ("completed", 5))
            self.assertEqual(seen[0][:2], (True, True))
            # The operation and the close hook run on the one owning worker,
            # which is not the creating thread.
            self.assertNotEqual(seen[0][2], threading.get_ident())
        finally:
            execution.close()
        self.assertEqual([e[0] for e in events], ["hook close", "raw close"])
        self.assertTrue(events[0][2])
        self.assertEqual(events[0][1], events[1][1])
        self.assertIsNone(execution._resource)

    def test_assert_owner_refuses_other_threads(self):
        class Plain:
            def close(self):
                pass

        execution = OwnerExecutor(Plain, capacity=1)
        failures = []

        def other():
            try:
                execution._assert_owner()
            except RuntimeError as error:
                failures.append(str(error))

        try:
            thread = threading.Thread(target=other)
            thread.start()
            thread.join(2)
        finally:
            execution.close()
        self.assertEqual(len(failures), 1)


if __name__ == "__main__":
    unittest.main()
