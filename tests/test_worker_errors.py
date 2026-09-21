"""Failures in a callback's diagnostic text must not strand the worker."""
import threading
import unittest

from adaptive_timing.worker import LatestWorker


class UnprintableError(Exception):
    def __str__(self):
        raise ValueError("diagnostic formatting failed")


class WorkerErrorTests(unittest.TestCase):
    def test_unprintable_failure_publishes_outcome_and_next_request_runs(self):
        escaped = []
        previous_hook = threading.excepthook
        threading.excepthook = escaped.append
        worker = LatestWorker(lambda value: self.compute(value))
        try:
            generation = worker.request(0)
            self.assertTrue(worker.wait_idle(timeout=1), "worker stranded after formatting failure")
            outcome = worker.take()
            self.assertEqual(outcome.generation, generation)
            self.assertIsNone(outcome.value)
            self.assertIn("UnprintableError", outcome.error)
            self.assertIn("unavailable", outcome.error)
            self.assertEqual(worker.stats().failures, 1)
            worker.request(3)
            self.assertTrue(worker.wait_idle())
            self.assertEqual(worker.take().value, 6)
            self.assertEqual(worker.stats().computed, 2)
            self.assertFalse(escaped)
        finally:
            worker.close(2)
            threading.excepthook = previous_hook

    @staticmethod
    def compute(value):
        if value == 0:
            raise UnprintableError()
        return value * 2

    def test_superseded_unprintable_failure_does_not_replace_latest_result(self):
        entered, release = threading.Event(), threading.Event()

        def compute(value):
            if value == 0:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test gate was not released")
                raise UnprintableError()
            return value

        with LatestWorker(compute) as worker:
            worker.request(0)
            try:
                self.assertTrue(entered.wait(2))
                latest = worker.request(42)
            finally:
                release.set()
            self.assertTrue(worker.wait_idle())
            outcome = worker.take()
            self.assertEqual((outcome.generation, outcome.value, outcome.error),
                             (latest, 42, None))
            self.assertEqual(worker.stats().stale, 1)
            self.assertEqual(worker.stats().failures, 1)
