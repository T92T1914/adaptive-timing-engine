"""Consumed and superseded batches must not remain in an idle worker frame."""
import gc
import unittest
import weakref

from adaptive_timing.worker import LatestWorker


class Batch:
    pass


class WorkerRetentionTests(unittest.TestCase):
    def test_consumed_input_and_output_are_released_before_wait_idle_returns(self):
        with LatestWorker(lambda payload: Batch()) as worker:
            payload = Batch()
            payload_ref = weakref.ref(payload)
            worker.request(payload)
            del payload
            self.assertTrue(worker.wait_idle())
            outcome = worker.take()
            output_ref = weakref.ref(outcome.value)
            del outcome
            gc.collect()
            self.assertIsNone(payload_ref())
            self.assertIsNone(output_ref())
            worker.request(Batch())
            self.assertTrue(worker.wait_idle())
            self.assertIsNotNone(worker.take().value)

    def test_pending_output_is_retained_until_taken(self):
        refs = []
        def compute(payload):
            value = Batch()
            refs.append(weakref.ref(value))
            return value
        with LatestWorker(compute) as worker:
            worker.request(None)
            self.assertTrue(worker.wait_idle())
            gc.collect()
            self.assertIsNotNone(refs[0]())
            outcome = worker.take()
            self.assertIs(outcome.value, refs[0]())
            del outcome
            gc.collect()
            self.assertIsNone(refs[0]())

    def test_failed_payload_is_also_released(self):
        def compute(payload):
            raise ValueError('invalid batch')
        with LatestWorker(compute) as worker:
            payload = Batch()
            ref = weakref.ref(payload)
            worker.request(payload)
            del payload
            self.assertTrue(worker.wait_idle())
            self.assertIn('ValueError', worker.take().error)
            gc.collect()
            self.assertIsNone(ref())
