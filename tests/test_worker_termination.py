"""A callback must not leave a dead worker accepting requests."""
import gc
import threading
import unittest
import weakref
from unittest.mock import patch

from adaptive_timing import Controller, Event, LatestWorker, PlanningRequest, Policy, build_plan


class ExitWhileFormatting(ValueError):
    def __str__(self):
        raise SystemExit("diagnostic formatter exited")


class WorkerTerminationTests(unittest.TestCase):
    def test_callback_exit_is_visible_and_the_next_request_runs(self):
        for error_type in (SystemExit, KeyboardInterrupt, GeneratorExit, ExitWhileFormatting):
            with self.subTest(error_type=error_type.__name__):
                escaped = []

                def compute(value):
                    if value == 0:
                        raise error_type("callback stopped")
                    return value * 2

                with patch("threading.excepthook", escaped.append), LatestWorker(compute) as worker:
                    revision = worker.request(0)
                    self.assertTrue(worker.wait_idle(1), "callback stranded the worker")
                    outcome = worker.take()
                    self.assertEqual(outcome.generation, revision)
                    self.assertIsNone(outcome.value)
                    self.assertIn(error_type.__name__, outcome.error)
                    if error_type is ExitWhileFormatting:
                        self.assertIn("unavailable", outcome.error)
                    self.assertEqual(worker.stats().failures, 1)
                    self.assertFalse(worker.stats().running)
                    worker.request(7)
                    self.assertTrue(worker.wait_idle(1))
                    self.assertEqual(worker.take().value, 14)
                    self.assertEqual(worker.stats().computed, 2)
                    self.assertEqual(escaped, [])

    def test_superseded_exit_does_not_discard_the_waiting_request(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def compute(value):
            calls.append(value)
            if value == 0:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test gate was not released")
                raise SystemExit("obsolete calculation exited")
            return value

        with LatestWorker(compute) as worker:
            worker.request(0)
            try:
                self.assertTrue(entered.wait(1))
                worker.request(1)
                latest = worker.request(2)
            finally:
                release.set()
            self.assertTrue(worker.wait_idle(1))
            outcome = worker.take()
            self.assertEqual((outcome.generation, outcome.value, outcome.error), (latest, 2, None))
            self.assertEqual(calls, [0, 2])
            stats = worker.stats()
            self.assertEqual((stats.computed, stats.failures, stats.stale, stats.replaced),
                             (2, 1, 1, 1))

    def test_close_during_callback_exit_finishes_cleanup_without_publication(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def compute(value):
            calls.append(value)
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test gate was not released")
            raise SystemExit("callback exited during shutdown")

        worker = LatestWorker(compute)
        try:
            worker.request(1)
            self.assertTrue(entered.wait(1))
            worker.request(2)
            self.assertFalse(worker.close())
            release.set()
            self.assertTrue(worker.close(1))
            self.assertTrue(worker.wait_idle(0))
            self.assertIsNone(worker.take())
            self.assertEqual(calls, [1])
            self.assertFalse(worker.stats().running)
            self.assertEqual(worker.stats().failures, 1)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                worker.request(3)
        finally:
            release.set()
            worker.close(1)

    def test_exited_callback_releases_its_payload(self):
        class Payload:
            pass

        def compute(payload):
            raise SystemExit("cannot use payload")

        with LatestWorker(compute) as worker:
            payload = Payload()
            reference = weakref.ref(payload)
            worker.request(payload)
            del payload
            self.assertTrue(worker.wait_idle(1))
            self.assertIn("SystemExit", worker.take().error)
            gc.collect()
            self.assertIsNone(reference())

    def test_owner_thread_interrupt_is_not_suppressed(self):
        with self.assertRaises(KeyboardInterrupt):
            with LatestWorker(lambda value: value) as worker:
                raise KeyboardInterrupt("owner stopped")
        self.assertTrue(worker.close(1))
        with self.assertRaisesRegex(RuntimeError, "closed"):
            worker.request(1)

    def test_controller_keeps_active_plan_then_recovers_after_callback_exit(self):
        policy = Policy(use_noise=False)
        original = Event("original", 1, 1, 2, 0.1)
        replacement = Event("replacement", 3, 3, 4, 0.1)

        def planner(request):
            if not request.events:
                raise SystemExit("planner rejected empty work")
            return build_plan(request.events, request.policy)

        controller = Controller(planner)
        try:
            controller.executor.install(build_plan((original,), policy))
            controller.request(PlanningRequest((), policy))
            self.assertTrue(controller.worker.wait_idle(1))
            self.assertEqual(controller.poll(1)[0].id, "original")
            self.assertEqual(controller.installed_revision, 0)
            self.assertIn("SystemExit", controller.last_error)
            latest = controller.request(PlanningRequest((replacement,), policy))
            self.assertTrue(controller.worker.wait_idle(1))
            self.assertEqual(controller.poll(3)[0].id, "replacement")
            self.assertEqual(controller.installed_revision, latest)
            self.assertIsNone(controller.last_error)
        finally:
            controller.close(1)
