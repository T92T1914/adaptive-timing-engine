"""Clock admission, canonical finish arithmetic and planner boundaries."""
from dataclasses import replace
import math
import unittest

from adaptive_timing import (CausalRequest, CausalScheduler, Event, Executor,
                             Information, Plan, Policy, Scheduled,
                             build_causal_plan, build_plan)


ORIGINS = (0.0, 1.0, 1_000.0, 1_000_000.0, 10_000_000.0,
           100_000_000.0, 1_000_000_000.0, 1_700_000_000.0,
           10_000_000_000.0)
DURATIONS = (0.017, 0.05, 0.125)
EXACT_POLICY = Policy(use_noise=False, resource_gap=0.0)


def plans(events, *, now=0.0, policy=EXACT_POLICY):
    yield "direct", build_plan(events, policy, now=now)
    request = CausalRequest(1, now, events, (), policy, 42)
    yield "causal", build_causal_plan(request).plan


def scheduled(event, start):
    return Scheduled(event, start, start, start + event.duration,
                     0.0, 0.0, 0.0, False)


class ClockDomainTests(unittest.TestCase):
    def test_resolution_covers_tolerated_late_poll_across_binade(self):
        deadline = math.nextafter(1.0, -math.inf)
        duration = math.ulp(deadline)
        with self.assertRaisesRegex(ValueError, "clock resolution"):
            Event("late-collapse", 0.5, 0.5, deadline, duration)
        event = Event("late-resolvable", 0.5, 0.5, deadline, 2 * duration)
        executor = Executor()
        executor.install(build_plan((event,), EXACT_POLICY))
        row, = executor.poll(1.0)
        self.assertEqual(row.status, "dispatched")
        self.assertGreater(row.finish, row.actual_start)

    def test_subtraction_bound_remains_conservative_without_optimizing_admission(self):
        origin = float(2 ** 53)
        event = Event("conservative", origin + 2, origin + 2, origin + 4, 3.0)
        for path, plan in plans((event,), now=origin):
            with self.subTest(path=path):
                self.assertEqual(plan.scheduled, ())
                self.assertEqual(len(plan.rejected), 1)

    def test_translated_fractional_events_use_canonical_finish_in_both_paths(self):
        for origin in ORIGINS:
            for duration in DURATIONS:
                event = Event("translated", origin + 1.0, origin + 0.75,
                              origin + 1.5, duration)
                for path, plan in plans((event,), now=origin):
                    with self.subTest(origin=origin, duration=duration, path=path):
                        self.assertEqual(plan.rejected, ())
                        self.assertEqual(len(plan.scheduled), 1)
                        row, = plan.scheduled
                        self.assertIs(row.event, event)
                        self.assertEqual(row.start, event.target)
                        self.assertEqual(row.finish, row.start + duration)
                        self.assertGreaterEqual(row.start, event.earliest)
                        self.assertLessEqual(row.finish, event.deadline)

    def test_rounded_deadline_subtraction_backs_off_before_admission(self):
        origin = float(2 ** 53)
        event = Event("rounded-upper", origin + 6.0, origin,
                      origin + 6.0, 3.0)
        for path, plan in plans((event,), now=origin):
            with self.subTest(path=path):
                self.assertEqual(plan.rejected, ())
                row, = plan.scheduled
                self.assertEqual(row.start, origin + 2.0)
                self.assertEqual(row.finish, origin + 4.0)
                self.assertEqual(row.finish, row.start + event.duration)
                self.assertLessEqual(row.finish, event.deadline)

    def test_backed_off_start_below_earliest_is_explicitly_rejected(self):
        origin = float(2 ** 53)
        event = Event("no-rounded-slot", origin + 6.0, origin + 4.0,
                      origin + 6.0, 3.0)
        for path, plan in plans((event,), now=origin):
            with self.subTest(path=path):
                self.assertEqual(plan.scheduled, ())
                self.assertEqual(len(plan.rejected), 1)
                self.assertIs(plan.rejected[0].event, event)
                self.assertIn("feasible", plan.rejected[0].reason)

    def test_positive_duration_below_window_resolution_is_rejected_at_event(self):
        origin = float(2 ** 53)
        with self.assertRaisesRegex(ValueError, "(?i)(clock|resolution)"):
            Event("unresolved", origin, origin, origin + 6.0, 1.0)

    def test_duration_resolution_covers_the_whole_window_before_late_dispatch(self):
        with self.assertRaisesRegex(ValueError, "(?i)(clock|resolution)"):
            Event("early-target-wide-window", 1.0, 0.0, float(2 ** 53) + 6.0, 1.0)

    def test_exact_resolution_and_zero_duration_remain_supported(self):
        origin = float(2 ** 53)
        for duration in (0.0, 2.0):
            event = Event("resolvable", origin + 2.0, origin,
                          origin + 6.0, duration)
            for path, plan in plans((event,), now=origin):
                with self.subTest(duration=duration, path=path):
                    self.assertEqual(plan.rejected, ())
                    row, = plan.scheduled
                    self.assertEqual(row.start, origin + 2.0)
                    self.assertEqual(row.finish, row.start + duration)

    def test_resource_gap_resolution_is_checked_without_widening_overlap(self):
        origin = float(2 ** 53)
        event = Event("gap-resolution", origin + 2.0, origin,
                      origin + 6.0, 2.0)
        for path in ("direct", "causal"):
            policy = Policy(use_noise=False, resource_gap=1.0)
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "(?i)resource_gap.*(clock|resolution)"):
                    if path == "direct":
                        build_plan((event,), policy, now=origin)
                    else:
                        request = CausalRequest(1, origin, (event,), (), policy, 42)
                        build_causal_plan(request)
        with self.assertRaisesRegex(ValueError, "(?i)resource_gap.*(clock|resolution)"):
            Plan((scheduled(event, event.target),), (), 1.0)
        for gap in (0.0, 2.0):
            policy = Policy(use_noise=False, resource_gap=gap)
            for path, plan in plans((event,), now=origin, policy=policy):
                with self.subTest(gap=gap, path=path):
                    self.assertEqual(plan.rejected, ())
                    self.assertEqual(plan.resource_gap, gap)
                    self.assertEqual(plan.scheduled[0].finish, origin + 4.0)

    def test_manual_plan_rejects_one_ulp_noncanonical_finish_near_zero(self):
        event = Event("manual", 1.0, 0.5, 2.0, 0.125)
        row = scheduled(event, 1.0)
        self.assertEqual(Plan((row,), (), 0.0).scheduled, (row,))
        for direction in (-math.inf, math.inf):
            wrong_finish = math.nextafter(row.finish, direction)
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                Plan((replace(row, finish=wrong_finish),), (), 0.0)

    def test_manual_large_origin_plan_accepts_canonical_forward_arithmetic(self):
        event = Event("manual-large", 100_000_001.0, 100_000_000.0,
                      100_000_002.0, 0.017)
        row = scheduled(event, event.target)
        self.assertEqual(Plan((row,), (), 0.0).scheduled, (row,))

    def test_event_finite_window_and_duration_controls_remain_intact(self):
        event = Event("finite", 1.0, 0.5, 2.0, 0.125)
        for field in ("target", "earliest", "deadline", "duration"):
            for invalid in (-1.0, math.inf, -math.inf, math.nan):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError):
                        replace(event, **{field: invalid})
        for changes in ({"earliest": 1.5}, {"deadline": 0.75}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(event, **changes)

    def test_manual_plan_finite_and_deadline_controls_remain_intact(self):
        event = Event("manual-controls", 1.0, 0.5, 2.0, 0.125)
        row = scheduled(event, 1.0)
        for field in ("start", "finish"):
            for invalid in (-1.0, math.inf, -math.inf, math.nan):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError):
                        Plan((replace(row, **{field: invalid}),), (), 0.0)
        with self.assertRaises(ValueError):
            Plan((scheduled(event, 2.0),), (), 0.0)
        with self.assertRaises(ValueError):
            Plan((scheduled(event, 0.25),), (), 0.0)

    def test_expired_impossible_and_narrow_windows_keep_their_dispositions(self):
        cases = (
            (Event("expired", 0.5, 0.0, 1.0, 0.125), 2.0, False),
            (Event("too-long", 0.5, 0.0, 1.0, 2.0), 0.0, False),
            (Event("narrow-refused", 0.95, 0.95, 1.0, 0.125), 0.0, False),
            (Event("narrow-fit", 1.0, 0.875, 1.0, 0.125), 0.0, True),
        )
        for event, now, accepted in cases:
            for path, plan in plans((event,), now=now):
                with self.subTest(event=event.id, path=path):
                    self.assertEqual(len(plan.scheduled), int(accepted))
                    self.assertEqual(len(plan.rejected), int(not accepted))
                    if accepted:
                        row, = plan.scheduled
                        self.assertEqual((row.start, row.finish), (0.875, 1.0))
                    else:
                        self.assertIs(plan.rejected[0].event, event)

    def test_manual_overlap_and_resource_gap_controls_remain_intact(self):
        first = Event("a", 0.0, 0.0, 2.0, 0.5, "serial")
        second = Event("b", 0.25, 0.0, 2.0, 0.5, "serial")
        with self.assertRaises(ValueError):
            Plan((scheduled(first, 0.0), scheduled(second, 0.25)), (), 0.0)
        with self.assertRaises(ValueError):
            Plan((scheduled(first, 0.0), scheduled(second, 0.5)), (), 0.125)
        rows = (scheduled(first, 0.0), scheduled(second, 0.625))
        self.assertEqual(Plan(rows, (), 0.125).scheduled, rows)
        other = replace(second, resource="independent")
        concurrent = (scheduled(first, 0.0), scheduled(other, 0.25))
        self.assertEqual(Plan(concurrent, (), 0.0).scheduled, concurrent)

    def test_sequential_work_at_large_origin_preserves_resource_gap(self):
        origin = 1_700_000_000.0
        policy = Policy(use_noise=False, resource_gap=0.125)
        first = Event("first", origin + 1.0, origin, origin + 4.0, 0.125)
        second = Event("second", origin + 1.0, origin, origin + 4.0, 0.125)
        for path, plan in plans((first, second), now=origin, policy=policy):
            with self.subTest(path=path):
                self.assertEqual(plan.rejected, ())
                a, b = plan.scheduled
                self.assertEqual((a.event.id, b.event.id), ("first", "second"))
                self.assertEqual(b.start, a.finish + policy.resource_gap)
                self.assertLessEqual(b.finish, second.deadline)

    def test_empty_plans_remain_valid_at_declared_origins(self):
        for origin in ORIGINS + (float(2 ** 53),):
            for path, plan in plans((), now=origin):
                with self.subTest(origin=origin, path=path):
                    self.assertEqual(plan, Plan((), (), 0.0))

    def test_executor_dispatches_large_origin_once_and_expires_late_work(self):
        origin = 100_000_000.0
        event = Event("actual-virtual-dispatch", origin + 1.0, origin,
                      origin + 2.0, 0.017)
        plan = build_plan((event,), EXACT_POLICY, now=origin)
        executor = Executor()
        executor.install(plan)
        row, = executor.poll(event.target)
        self.assertEqual((row.id, row.status), (event.id, "dispatched"))
        self.assertEqual(row.finish, row.actual_start + event.duration)
        self.assertLessEqual(row.finish, event.deadline)
        executor.install(plan)
        self.assertEqual(executor.poll(event.target), ())

        late = Executor()
        late.install(plan)
        expired, = late.poll(event.deadline)
        self.assertEqual((expired.id, expired.status), (event.id, "expired"))

    def test_causal_session_dispatches_large_origin_through_public_lifecycle(self):
        origin = 100_000_000.0
        event = Event("causal-dispatch", origin + 1.0, origin,
                      origin + 2.0, 0.017)
        session = CausalScheduler(EXACT_POLICY)
        information = Information(origin, 0, origin, "announce", event.id, 1, event)
        self.assertEqual(session.receive((information,), now=origin), ("applied",))
        result = build_causal_plan(session.snapshot(origin))
        self.assertTrue(session.install(result))
        row, = session.poll(event.target)
        self.assertEqual((row.id, row.status), (event.id, "dispatched"))
        self.assertEqual(row.finish, row.actual_start + event.duration)
        self.assertEqual(session.outcomes(), ((event.id, "dispatched"),))
        self.assertEqual(session.poll(event.target), ())


if __name__ == "__main__":
    unittest.main()
