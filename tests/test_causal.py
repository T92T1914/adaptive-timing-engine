"""Causality is an invariance of earlier decisions, not a policy name."""
from dataclasses import FrozenInstanceError, replace
import threading
import unittest

from adaptive_timing import (CausalScheduler, Event, Information, LatestWorker,
                             Policy, build_causal_plan, build_plan)
from tools.run_causal_study import run_case


def fact(event, at=0.0, *, sequence=0, version=1, kind="announce"):
    return Information(at, sequence, at, kind, event.id, version,
                       None if kind == "cancel" else event)


def step(session, at, facts):
    session.receive(tuple(facts), now=at)
    result = build_causal_plan(session.snapshot(at))
    assert session.install(result)
    return result.plan


class CausalTests(unittest.TestCase):
    def setUp(self):
        self.event = Event("a", 1, .95, 1.2, .1)
        self.policy = Policy(use_noise=False)

    def test_immutable_information_and_atomic_delivery_validation(self):
        session = CausalScheduler(self.policy)
        message = fact(self.event)
        with self.assertRaises(FrozenInstanceError):
            message.version = 2
        for changes in ({"observed_at": 1}, {"version": True}, {"event": []},
                        {"kind": "missing"}, {"kind": "cancel"}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(message, **changes)
        with self.assertRaises(ValueError):
            session.receive((message, fact(self.event, sequence=1, version=3, kind="update")), now=0)
        self.assertEqual(step(session, 0, (message,)).scheduled[0].event, self.event)

    def test_equal_time_order_and_cancellation_before_dispatch(self):
        session = CausalScheduler(self.policy)
        step(session, 0, (fact(self.event),))
        update = fact(replace(self.event, duration=.05), 1, version=2, kind="update", sequence=2)
        cancel = fact(self.event, 1, version=3, kind="cancel", sequence=3)
        self.assertEqual(step(session, 1, (cancel, update)).scheduled, ())
        self.assertEqual(session.poll(1), ())
        self.assertEqual(session.outcomes(), (("a", "canceled"),))
        with self.assertRaises(ValueError):
            session.receive((fact(self.event, 1, version=4, kind="update"),), now=1)

    def test_future_delivery_rejected_but_known_future_schedule_allowed(self):
        session = CausalScheduler(self.policy)
        with self.assertRaises(ValueError):
            session.receive((fact(self.event, 2),), now=0)
        plan = step(session, 0, (fact(self.event),))
        self.assertEqual(plan.scheduled[0].start, 1)
        self.assertEqual(session.poll(.9), ())

    def test_suffix_cannot_change_earlier_plans_or_dispatch(self):
        # Both worlds have the same prefix, including a known future event.
        # Later worlds change delivery order, targets, density, update and cancel.
        a = Event("a", .2, .15, .3, .02)
        b = Event("b", .8, .7, 1, .05)
        prefix = {0: (fact(a), fact(b, sequence=1)),
                  .1: (fact(replace(b, duration=.02), .1, version=2, kind="update"),)}
        late = Event("late", .31, .3, 1, .02)
        suffixes = ({.3: (fact(late, .3), fact(b, .3, sequence=1, version=3, kind="cancel"))},
                    {.3: (fact(replace(b, target=.9), .3, version=3, kind="update"),
                           fact(replace(late, target=.95, duration=.6), .3, sequence=1)),
                     .4: (fact(late, .4, version=2, kind="cancel"),)})
        traces = []
        for suffix in suffixes:
            session = CausalScheduler(seed=73)
            trace = []
            stream = prefix | suffix
            for tick in range(101):
                at = tick / 100
                if at in stream:
                    plan = step(session, at, stream[at])
                    if at < .3:
                        trace.append(("plan", at, plan))
                rows = session.poll(at)
                if at < .3:
                    trace.append(("dispatch", at, rows))
            traces.append(trace)
        self.assertEqual(*traces)
        # Positive control: the offline planner does respond to hidden suffix work.
        crowded = tuple(replace(late, id=f"x{i}", target=.25, earliest=.2) for i in range(20))
        first = build_plan((a, b), seed=73).scheduled[0]
        second = build_plan((a, b) + crowded, seed=73).scheduled[0]
        self.assertNotEqual(first.density, second.density)
        self.assertNotEqual(first.requested, second.requested)

    def test_update_cancel_boundary_changes_only_decisions_at_boundary(self):
        histories = []
        for kind in ("update", "cancel"):
            session = CausalScheduler(self.policy)
            before = step(session, 0, (fact(self.event),))
            earlier = session.poll(.9)
            after = step(session, 1, (fact(replace(self.event, target=1.1), 1,
                                         kind=kind, version=2),))
            histories.append((before, earlier, after))
        self.assertEqual(histories[0][:2], histories[1][:2])
        self.assertNotEqual(histories[0][2], histories[1][2])

    def test_empty_history_overload_and_reservation_survives_update(self):
        session = CausalScheduler(self.policy)
        self.assertEqual(build_causal_plan(session.snapshot(0)).plan.scheduled, ())
        a = Event("long", 1, 1, 3, 1)
        b = Event("next", 1.1, 1.1, 3, .1)
        step(session, .1, (fact(a, .1), fact(b, .1, sequence=1)))
        self.assertEqual(session.poll(1)[0].id, "long")
        update = fact(replace(b, target=1.2), 1.1, version=2, kind="update")
        plan = step(session, 1.1, (update,))
        self.assertGreaterEqual(plan.scheduled[0].start, 2 + self.policy.resource_gap)
        self.assertGreater(plan.scheduled[0].fatigue, 0)
        # Repeated snapshots do not turn projected effort into observed effort.
        self.assertEqual(build_causal_plan(session.snapshot(1.1)).plan, plan)
        overload = tuple(Event(str(i), 1, 1, 1.1, .1) for i in range(10))
        another = CausalScheduler(self.policy)
        plan = step(another, 0, tuple(fact(e, sequence=i) for i, e in enumerate(overload)))
        self.assertEqual((len(plan.scheduled), len(plan.rejected)), (1, 9))

    def test_stale_result_and_delayed_current_result(self):
        session = CausalScheduler(self.policy)
        session.receive((fact(self.event),), now=0)
        old = build_causal_plan(session.snapshot(0))
        session.receive((fact(self.event, .5, version=2, kind="cancel"),), now=.5)
        self.assertFalse(session.install(old))
        self.assertEqual(session.poll(2), ())
        session = CausalScheduler(self.policy)
        session.receive((fact(self.event),), now=0)
        delayed = build_causal_plan(session.snapshot(0))
        session.poll(2)
        self.assertTrue(session.install(delayed))
        self.assertEqual(session.poll(2)[0].status, "expired")
        self.assertFalse(session.install(delayed))

    def test_dispatch_invalidates_pending_result_and_cannot_be_undone(self):
        session = CausalScheduler(self.policy)
        step(session, 0, (fact(self.event),))
        pending = build_causal_plan(session.snapshot(.5))
        self.assertEqual(session.poll(1)[0].status, "dispatched")
        self.assertFalse(session.install(pending))
        status = session.receive((fact(self.event, 1.1, version=2, kind="cancel"),), now=1.1)
        self.assertEqual(status, ("ignored_terminal",))
        self.assertEqual(session.outcomes(), (("a", "dispatched"),))
        with self.assertRaises(ValueError):
            session.receive((fact(self.event, 1.2),), now=1.2)

    def test_latest_worker_uses_same_immutable_snapshot_lease(self):
        started, release = threading.Event(), threading.Event()
        session = CausalScheduler(self.policy)
        session.receive((fact(self.event),), now=0)
        def compute(request):
            started.set()
            if not release.wait(2):
                raise TimeoutError("test gate")
            return build_causal_plan(request)
        with LatestWorker(compute) as worker:
            worker.request(session.snapshot(0))
            self.assertTrue(started.wait(2))
            session.receive((fact(self.event, .5, version=2, kind="cancel"),), now=.5)
            release.set()
            self.assertTrue(worker.wait_idle(2))
            outcome = worker.take()
            self.assertIsNone(outcome.error)
            self.assertFalse(session.install(outcome.value))
        self.assertEqual(session.poll(1), ())

    def test_seed_is_keyed_by_identity_not_catalog_position(self):
        policy = replace(self.policy, use_noise=True, use_load=False, use_fatigue=False)
        a, b = CausalScheduler(policy, seed=42), CausalScheduler(policy, seed=42)
        isolated = step(a, 0, (fact(self.event),)).scheduled[0]
        other = replace(self.event, id="other", resource="another")
        with_other = step(b, 0, (fact(other, sequence=1), fact(self.event))).scheduled
        paired = next(row for row in with_other if row.event.id == self.event.id)
        self.assertEqual(isolated.requested, paired.requested)

    def test_study_accounting_on_small_independent_fixture(self):
        early = Event("early", .1, .1, .3, .01)
        canceled = Event("canceled", .5, .4, .7, .01)
        late = Event("late", .2, .1, .25, .05)
        messages = (fact(early), fact(canceled, sequence=1),
                    fact(canceled, .2, version=2, kind="cancel"), fact(late, .4))
        for mode in ("causal", "offline"):
            row = run_case(messages, 7, mode)
            self.assertEqual(sum(row[key] for key in
                                 ("rejected", "expired", "dispatched", "canceled", "unknown")), 3)
            self.assertIsNone(row["completed"])
            self.assertEqual(row["canceled"], 1)
            self.assertEqual(row["unknown"], 0)
            self.assertEqual(row["modeled_service_finished"], row["dispatched"])
            if mode == "causal":
                self.assertEqual(row["terminal"]["late"], "rejected")
            else:
                self.assertEqual(row["terminal"]["late"], "dispatched")


if __name__ == "__main__":
    unittest.main()
