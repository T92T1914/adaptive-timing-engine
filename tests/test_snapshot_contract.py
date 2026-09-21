"""Typed snapshots must not retain ordinary mutable lookalike records."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from adaptive_timing import (Controller, Event, Executor, Plan, PlanningRequest,
                             Policy, build_plan)
from adaptive_timing.model import Rejected


class SnapshotContractTests(unittest.TestCase):
    def setUp(self):
        self.event = Event("original", 1.0, 1.0, 10.0, 0.1)
        self.policy = Policy(use_noise=False)
        self.plan = build_plan((self.event,), self.policy)

    def test_plan_rejects_mutable_schedule_and_nested_event(self):
        item = self.plan.scheduled[0]
        mutable_item = SimpleNamespace(**vars(item))
        mutable_event = SimpleNamespace(**vars(self.event))
        for candidate in (mutable_item, replace(item, event=mutable_event)):
            with self.subTest(candidate=candidate), self.assertRaises(TypeError):
                Plan((candidate,), (), 0.0)

    def test_plan_rejects_mutable_rejection_and_nested_event(self):
        rejected = Rejected(self.event, "no feasible interval")
        for candidate in (SimpleNamespace(**vars(rejected)),
                          replace(rejected, event=SimpleNamespace(**vars(self.event)))):
            with self.subTest(candidate=candidate), self.assertRaises(TypeError):
                Plan((), (candidate,), 0.0)

    def test_request_rejects_mutable_nested_snapshot_fields(self):
        for changes in ({"events": (SimpleNamespace(**vars(self.event)),)},
                        {"policy": SimpleNamespace(**vars(self.policy))},
                        {"seed": [42]}):
            with self.subTest(changes=changes), self.assertRaises(TypeError):
                replace(PlanningRequest((self.event,), self.policy), **changes)

    def test_invalid_submission_keeps_ready_revision(self):
        controller = Controller()
        try:
            request = PlanningRequest((self.event,), self.policy)
            revision = controller.request(request)
            self.assertTrue(controller.worker.wait_idle())
            with self.assertRaises(TypeError):
                controller.request(SimpleNamespace(**vars(request)))
            self.assertEqual(controller.revision, revision)
            self.assertEqual(controller.poll(1.0)[0].id, "original")
            self.assertEqual(controller.installed_revision, revision)
        finally:
            controller.close(2)

    def test_mutable_planner_result_cannot_replace_active_schedule(self):
        mutable = SimpleNamespace(**vars(self.plan.scheduled[0]))
        calls = 0

        def planner(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return Plan((mutable,), (), 0.0)
            return build_plan(request.events, request.policy, seed=request.seed)

        controller = Controller(planner)
        try:
            controller.executor.install(self.plan)
            controller.request(PlanningRequest((self.event,), self.policy))
            self.assertTrue(controller.worker.wait_idle())
            # Without a type boundary, this alters the ready plan so it can
            # dispatch before the event's earliest permitted start.
            mutable.start = 0.0
            self.assertEqual(controller.poll(0.0), ())
            self.assertIn("TypeError", controller.last_error)
            self.assertEqual(controller.installed_revision, 0)
            self.assertEqual(controller.poll(1.0)[0].id, "original")
            replacement = replace(self.event, id="replacement", target=2.0, earliest=2.0)
            revision = controller.request(PlanningRequest((replacement,), self.policy))
            self.assertTrue(controller.worker.wait_idle())
            self.assertEqual(controller.poll(2.0)[0].id, "replacement")
            self.assertEqual(controller.installed_revision, revision)
            self.assertIsNone(controller.last_error)
        finally:
            controller.close(2)

    def test_valid_explicit_plan_and_negative_integer_seed_remain_supported(self):
        request = PlanningRequest((self.event,), self.policy, seed=-42)
        plan = build_plan(request.events, request.policy, seed=request.seed)
        rejected = Rejected(replace(self.event, id="rejected"), "deliberate exclusion")
        explicit = Plan(plan.scheduled, (rejected,), plan.resource_gap)
        executor = Executor()
        executor.install(explicit)
        self.assertEqual(executor.poll(1.0)[0].id, "original")
        self.assertEqual(executor.queued, 0)
