"""Measure wall clock cost of planning, the reference swap, polling and adoption.

The numbers describe one machine, one interpreter and one run. They are not a
latency bound. Standard library only. Timer is time.perf_counter_ns.

    python tools/wall_clock_study.py --output reports/wall-clock.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing import Controller, Executor, PlanningRequest, Policy, build_plan
from adaptive_timing.experiment import percentile, workload

SIZES = (60, 240, 960)
SCENARIO = "steady"
SEED = 42
FULL = {"plan": 200, "swap": 5000, "poll": 2000, "adopt": 200}
ADOPTION_MODES = ("adoption_tight_loop", "adoption_sleep_0", "adoption_sleep_1ms")
QUICK = {"plan": 3, "swap": 20, "poll": 20, "adopt": 3}


def summary(nanoseconds: list[int]) -> dict:
    micros = [value / 1000 for value in nanoseconds]
    return {"samples": len(micros),
            "min_us": round(min(micros), 3),
            "p50_us": round(percentile(micros, 0.50), 3),
            "p95_us": round(percentile(micros, 0.95), 3),
            "p99_us": round(percentile(micros, 0.99), 3),
            "max_us": round(max(micros), 3)}


def measure_planning(events, repeats):
    policy = Policy()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        build_plan(events, policy, seed=SEED, now=0.0)
        samples.append(time.perf_counter_ns() - start)
    return samples


def measure_swap(plan, repeats):
    executor = Executor()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        executor.install(plan)
        samples.append(time.perf_counter_ns() - start)
    return samples


def measure_poll(plan, repeats):
    """One poll with the default budget. Plans under the budget are drained by it."""
    samples = []
    horizon = plan.scheduled[-1].start + 1.0 if plan.scheduled else 1.0
    for _ in range(repeats):
        executor = Executor()
        executor.install(plan)
        start = time.perf_counter_ns()
        executor.poll(horizon)
        samples.append(time.perf_counter_ns() - start)
    return samples


def measure_adoption(events, repeats, pause):
    """Time from request to the poll that installs its plan.

    pause is None for a tight loop, otherwise the seconds slept between polls.
    A zero pause still gives up the interpreter to the planning thread.

    The wait includes the planning itself, so it is compared with the planning
    samples for the same size, not with the swap alone.
    """
    request = PlanningRequest(events, Policy(), SEED, 0.0)
    samples = []
    controller = Controller()
    now = 0.0
    try:
        for _ in range(repeats):
            before = controller.installed_revision
            start = time.perf_counter_ns()
            revision = controller.request(request)
            while controller.installed_revision < revision:
                now += 1e-6
                controller.poll(now, budget=1)
                if pause is not None:
                    time.sleep(pause)
                if time.perf_counter_ns() - start > 30_000_000_000:
                    raise RuntimeError("adoption did not finish within 30 seconds")
            samples.append(time.perf_counter_ns() - start)
            assert controller.installed_revision > before
    finally:
        controller.close(2.0)
    return samples


def environment() -> dict:
    gil = getattr(sys, "_is_gil_enabled", None)
    info = time.get_clock_info("perf_counter")
    return {"python": sys.version.replace("\n", " "),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "gil_enabled": None if gil is None else bool(gil),
            "timer": "time.perf_counter_ns",
            "timer_resolution_s": info.resolution,
            "timer_monotonic": info.monotonic}


def study(counts: dict) -> dict:
    results = {}
    for size in SIZES:
        events = workload(SCENARIO, size)
        plan = build_plan(events, Policy(), seed=SEED, now=0.0)
        measure_planning(events, 2)
        results[str(size)] = {
            "events": size,
            "planned": len(plan.scheduled),
            "planning": summary(measure_planning(events, counts["plan"])),
            "reference_swap": summary(measure_swap(plan, counts["swap"])),
            "first_poll_budget_256": summary(measure_poll(plan, counts["poll"])),
            "adoption_tight_loop": summary(measure_adoption(events, counts["adopt"], None)),
            "adoption_sleep_0": summary(measure_adoption(events, counts["adopt"], 0)),
            "adoption_sleep_1ms": summary(measure_adoption(events, counts["adopt"], 0.001)),
        }
    return {"scenario": SCENARIO, "seed": SEED, "sizes": results,
            "environment": environment(),
            "samples_per_measure": counts,
            "note": "One machine, one interpreter, one run. Not a latency bound."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="few samples, for tests")
    args = parser.parse_args(argv)
    report = study(QUICK if args.quick else FULL)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
