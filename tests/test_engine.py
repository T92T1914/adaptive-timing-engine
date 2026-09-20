from dataclasses import replace
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest

from adaptive_timing import (Controller, Event, Executor, LatestWorker, Plan,
                             PlanningRequest, Policy, build_plan)
from adaptive_timing.experiment import execution_case, run_case, workload, write_viewer


def event(id="a", target=1.0, resource="r", duration=0.01):
    return Event(id, target, max(0, target - 0.05), target + 0.2, duration, resource)


class PlanningTests(unittest.TestCase):
    def test_reproducible_and_input_order_independent(self):
        events = workload("burst")
        self.assertEqual(build_plan(events), build_plan(tuple(reversed(events))))

    def test_paired_draws_survive_policy_changes(self):
        events = workload("burst")
        full = build_plan(events)
        ablated = build_plan(events, replace(Policy(), use_load=False))
        by_id = {item.event.id:item for item in ablated.scheduled}
        for a in full.scheduled:
            b = by_id[a.event.id]
            self.assertAlmostEqual((a.requested-a.event.target)/a.sigma,
                                   (b.requested-b.event.target)/b.sigma)

    def test_other_resources_do_not_add_workload_pressure(self):
        events = workload("burst", 120)
        unrelated = tuple(replace(e, id="extra-"+e.id, resource="other") for e in events)
        actual = {s.event.id:s for s in build_plan(events + unrelated).scheduled}
        for item in build_plan(events).scheduled:
            self.assertEqual(item, actual[item.event.id])

    def test_long_rest_recovers_accumulated_effort(self):
        rows = {s.event.id:s for s in build_plan(workload("recovery", 120)).scheduled}
        self.assertLess(rows["task-0096"].fatigue, rows["task-0094"].fatigue / 10)

    def test_saturation_is_reported_not_silently_scheduled(self):
        plan = build_plan(workload("saturation"))
        self.assertGreater(len(plan.rejected), 50)
        self.assertEqual(len(plan.rejected) + len(plan.scheduled), 240)

    def test_infeasible_event_does_not_block_following_event(self):
        impossible = Event("too-long", 0.1, 0, 0.2, 5)
        short = event("short", 0.3, "worker-0")
        plan = build_plan((impossible, short))
        self.assertEqual([s.event.id for s in plan.scheduled], ["short"])

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            build_plan((event(), event()))

    def test_invalid_times_and_configuration(self):
        for value in (-1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                event(target=value)
        for key in ("comfortable_rate", "density_window", "recovery_seconds"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(Policy(), **{key:0})

    def test_randomized_window_resource_and_partition_invariants(self):
        for seed in range(25):
            rng = random.Random(seed)
            events = tuple(Event(str(i), t, max(0,t-rng.random()/10),
                                 t+rng.random()/10, rng.random()/30, str(i%3))
                           for i,t in enumerate(sorted(rng.random()*3 for _ in range(100))))
            plan = build_plan(events, seed=seed)
            available = {}
            ids = []
            for item in plan.scheduled:
                self.assertGreaterEqual(item.start+1e-12, item.event.earliest)
                self.assertGreaterEqual(item.start+1e-12, available.get(item.event.resource,0))
                self.assertLessEqual(item.finish, item.event.deadline+1e-12)
                available[item.event.resource] = item.finish + plan.resource_gap
                ids.append(item.event.id)
            ids.extend(item.event.id for item in plan.rejected)
            self.assertCountEqual(ids, [e.id for e in events])

    def test_malformed_plan_rejected_before_adoption(self):
        plan = build_plan((event(),))
        with self.assertRaises(ValueError):
            Plan((replace(plan.scheduled[0], finish=100),), (), 0)


class RuntimeTests(unittest.TestCase):
    def test_late_dispatch_preserves_resource_reservations(self):
        events = (Event("a",1,0.9,2,0.3), Event("b",1.4,1.3,2,0.2))
        executor = Executor()
        plan = build_plan(events, replace(Policy(),use_noise=False))
        executor.install(plan)
        first = executor.poll(1.3)
        self.assertEqual([item.id for item in first], ["a"])
        self.assertEqual(executor.poll(1.4), ())
        # Replanning cannot forget a service interval already emitted.
        executor.install(plan)
        self.assertEqual(executor.poll(1.5), ())
        second = executor.poll(1.603)
        self.assertEqual([item.id for item in second], ["b"])
        self.assertGreaterEqual(second[0].actual_start,first[0].finish+plan.resource_gap)

    def test_expired_event_is_not_emitted_as_success(self):
        executor = Executor()
        executor.install(build_plan((event(),)))
        self.assertEqual(executor.poll(2)[0].status, "expired")

    def test_budget_counts_duplicates_during_replanning(self):
        events = tuple(Event(str(i),1,1,2,0,str(i)) for i in range(50))
        executor = Executor()
        plan = build_plan(events, replace(Policy(),use_noise=False))
        executor.install(plan)
        self.assertEqual(len(executor.poll(1,budget=10)),10)
        executor.install(plan)
        self.assertEqual(executor.poll(1,budget=10),())
        self.assertEqual(executor.queued,40)
        self.assertEqual(len(executor.poll(1,budget=10)),10)

    def test_failed_install_keeps_existing_plan(self):
        executor = Executor()
        executor.install(build_plan((event(),)))
        with self.assertRaises(TypeError):
            executor.install(None)
        self.assertEqual(executor.poll(1.1)[0].id,"a")

    def test_backwards_clock_rejected(self):
        executor = Executor()
        executor.poll(1)
        with self.assertRaises(ValueError):
            executor.poll(0.5)

    def test_execution_records_never_overlap_under_irregular_polling(self):
        executor = Executor()
        events = workload("burst")
        executor.install(build_plan(events))
        results = []
        for tick in range(3000):
            results.extend(executor.poll(tick*0.031,budget=7))
        busy = {}
        for result in results:
            if result.status == "dispatched":
                self.assertGreaterEqual(result.actual_start+1e-12,busy.get(result.resource,0))
                busy[result.resource] = result.finish+Policy().resource_gap
        self.assertEqual(len({r.id for r in results}),len(results))
        self.assertEqual(executor.queued,0)


class ConcurrencyTests(unittest.TestCase):
    def test_obsolete_result_and_intermediate_requests_are_dropped(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def compute(value):
            calls.append(value)
            if value == 0:
                entered.set()
                self.assertTrue(release.wait(2))
            return value
        with LatestWorker(compute) as worker:
            worker.request(0)
            self.assertTrue(entered.wait(2))
            for i in range(1,101):
                worker.request(i)
            self.assertEqual(worker.stats().queued,1)
            self.assertIsNone(worker.take())
            release.set()
            self.assertTrue(worker.wait_idle(2))
            self.assertEqual(worker.take().value,100)
            self.assertIsNone(worker.take())
            self.assertEqual(calls,[0,100])
            self.assertEqual(worker.stats().stale,1)
            self.assertEqual(worker.stats().replaced,99)

    def test_ready_result_invalidated_at_request_time(self):
        entered, release = threading.Event(), threading.Event()
        def compute(value):
            if value == 2:
                entered.set()
                release.wait(2)
            return value
        with LatestWorker(compute) as worker:
            worker.request(1)
            self.assertTrue(worker.wait_idle())
            worker.request(2)
            self.assertTrue(entered.wait(2))
            self.assertIsNone(worker.take())
            release.set()

    def test_failure_is_visible_and_worker_recovers(self):
        def compute(value):
            return 10/value
        with LatestWorker(compute) as worker:
            worker.request(0)
            self.assertTrue(worker.wait_idle())
            self.assertIn("ZeroDivisionError",worker.take().error)
            worker.request(2)
            self.assertTrue(worker.wait_idle())
            self.assertEqual(worker.take().value,5)

    def test_none_is_a_successful_result(self):
        with LatestWorker(lambda value:None) as worker:
            worker.request(1)
            self.assertTrue(worker.wait_idle())
            self.assertIsNone(worker.take().error)

    def test_close_rejects_requests_and_prevents_publication(self):
        entered, release = threading.Event(), threading.Event()
        def compute(value):
            entered.set()
            release.wait(2)
            return value
        worker = LatestWorker(compute)
        worker.request(1)
        self.assertTrue(entered.wait(2))
        self.assertFalse(worker.close())
        with self.assertRaises(RuntimeError):
            worker.request(2)
        release.set()
        self.assertTrue(worker.close(2))
        self.assertIsNone(worker.take())

    def test_controller_preserves_schedule_on_failed_computation(self):
        def fail(request):
            raise ValueError("invalid input")
        controller = Controller(fail)
        try:
            controller.executor.install(build_plan((event(),)))
            controller.request(PlanningRequest((event(),)))
            self.assertTrue(controller.worker.wait_idle())
            self.assertEqual(controller.poll(1.1)[0].id,"a")
            self.assertIn("invalid input",controller.last_error)
        finally:
            controller.close(2)

    def test_controller_rejects_cross_thread_submission(self):
        controller = Controller()
        errors = []
        def submit():
            try:
                controller.request(PlanningRequest((event(),)))
            except RuntimeError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=submit)
        thread.start()
        thread.join(2)
        controller.close(2)
        self.assertEqual(len(errors),1)

    def test_missing_plan_reports_failure_and_keeps_previous_schedule(self):
        calls = 0

        def planner(request):
            nonlocal calls
            calls += 1
            if calls == 2:
                return None
            return build_plan(request.events)

        controller = Controller(planner)
        try:
            original = controller.request(PlanningRequest((event(),)))
            self.assertTrue(controller.worker.wait_idle())
            controller.poll(0.0)
            self.assertEqual(controller.installed_revision, original)

            controller.request(PlanningRequest(()))
            self.assertTrue(controller.worker.wait_idle())
            self.assertEqual(controller.poll(1.1)[0].id, "a")
            self.assertIsNotNone(controller.last_error)
            self.assertEqual(controller.installed_revision, original)

            replacement = controller.request(PlanningRequest(()))
            self.assertTrue(controller.worker.wait_idle())
            controller.poll(1.1)
            self.assertEqual(controller.installed_revision, replacement)
            self.assertIsNone(controller.last_error)
        finally:
            controller.close(2)

    def test_controller_adopts_latest_revision(self):
        controller = Controller()
        try:
            revision = controller.request(PlanningRequest((event(),)))
            self.assertTrue(controller.worker.wait_idle())
            self.assertEqual(controller.poll(1.1)[0].id,"a")
            self.assertEqual(controller.installed_revision,revision)
        finally:
            controller.close(2)

    def test_invalid_poll_preserves_pending_outcome_for_retry(self):
        controller = Controller()
        try:
            revision = controller.request(PlanningRequest((event(),)))
            self.assertTrue(controller.worker.wait_idle())
            with self.assertRaises(ValueError):
                controller.poll(1.1, budget=0)
            self.assertEqual(controller.installed_revision, 0)
            self.assertEqual(controller.poll(1.1)[0].id, "a")
            self.assertEqual(controller.installed_revision, revision)
        finally:
            controller.close(2)


class EvidenceTests(unittest.TestCase):
    def test_execution_accounts_for_every_admitted_task(self):
        for scenario in ("steady", "saturation"):
            for period in (0.002, 0.04, 0.15):
                with self.subTest(scenario=scenario, period=period):
                    row = execution_case(scenario, period, 120)
                    self.assertEqual(row["admitted"], row["dispatched"] + row["expired"])
        delayed = execution_case("steady", 0.15, 120)
        self.assertGreater(delayed["expired"], 0)

    def test_comparison_measurements_reproduce_except_clock(self):
        a = run_case("burst","full",42,60)
        b = run_case("burst","full",42,60)
        a.pop("planning_ms")
        b.pop("planning_ms")
        self.assertEqual(a,b)

    def test_export_does_not_allow_event_text_to_close_script(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"report.html"
            write_viewer({"value":"</script><script>alert(1)</script>"},output)
            html = output.read_text(encoding="utf-8")
            payload = html.split('<script type="application/json" id="data">')[1].split('</script>')[0]
            self.assertNotIn("<",payload)
            self.assertEqual(json.loads(payload)["value"],"</script><script>alert(1)</script>")


if __name__ == "__main__":
    unittest.main()
