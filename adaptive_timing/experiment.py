"""Generated workloads and paired policy comparisons; no captured user data."""
from __future__ import annotations

from dataclasses import asdict, replace
import gzip
import json
import math
from pathlib import Path
import platform
import statistics
import threading
import time

from .model import Event, Policy, build_plan
from .runtime import Executor
from .worker import LatestWorker

SCENARIOS = ("steady", "burst", "recovery", "saturation")
VARIANTS = {
    "full": Policy(),
    "no_load": replace(Policy(), use_load=False),
    "no_fatigue": replace(Policy(), use_fatigue=False),
    "no_noise": replace(Policy(), use_noise=False),
}


def workload(name: str, count: int = 240) -> tuple[Event, ...]:
    if name not in SCENARIOS:
        raise ValueError(f"unknown workload: {name}")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive integer")
    target = 0.5
    events = []
    for i in range(count):
        phase = (i % 120) / 120
        step = 0.18
        if name in ("burst", "recovery") and 0.2 <= phase < 0.8:
            step = 0.032
        if name == "saturation":
            step = 0.004
        if name == "recovery" and i % 120 == 96:
            target += 6.0
        target += step
        # Deliberately synthetic service tasks on two arbitrary resources.
        events.append(Event(f"task-{i:04d}", target, max(0, target - 0.055),
                            target + 0.075, 0.017, f"resource-{i % 2}"))
    return tuple(events)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = fraction * (len(ordered) - 1)
    lo = int(index)
    hi = min(len(ordered) - 1, lo + 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def run_case(scenario: str, variant: str, seed: int, count: int = 240) -> dict:
    events = workload(scenario, count)
    started = time.perf_counter()
    plan = build_plan(events, VARIANTS[variant], seed=seed)
    planning_ms = 1000 * (time.perf_counter() - started)
    trace = []
    for item in plan.scheduled:
        row = asdict(item)
        row.update(row.pop("event"))
        row["status"] = "planned"
        trace.append(row)
    for item in plan.rejected:
        row = asdict(item.event)
        row.update(status="rejected", reason=item.reason)
        trace.append(row)
    trace.sort(key=lambda row: (row["target"], row["id"]))
    offsets = [1000 * abs(item.start - item.event.target) for item in plan.scheduled]
    return {
        "scenario": scenario, "variant": variant, "seed": seed, "events": count,
        "planned": len(plan.scheduled), "rejected": len(plan.rejected),
        "constrained": sum(item.constrained for item in plan.scheduled),
        "absolute_offset_p50_ms": percentile(offsets, 0.5),
        "absolute_offset_p95_ms": percentile(offsets, 0.95),
        "mean_sigma_ms": statistics.fmean(item.sigma * 1000 for item in plan.scheduled)
        if plan.scheduled else 0.0,
        "planning_ms": planning_ms, "trace": trace,
    }


def worker_experiment() -> dict:
    started = threading.Event()
    release = threading.Event()
    calls = []

    def compute(value):
        calls.append(value)
        if value == 0:
            started.set()
            if not release.wait(5):
                raise TimeoutError("experiment release was not signaled")
        return value

    with LatestWorker(compute) as worker:
        worker.request(0)
        if not started.wait(2):
            raise RuntimeError("worker did not start")
        request_us = []
        for value in range(1, 101):
            begin = time.perf_counter_ns()
            worker.request(value)
            request_us.append((time.perf_counter_ns() - begin) / 1000)
        queued_while_busy = worker.stats().queued
        empty = Executor()
        poll_us = []
        for i in range(2000):
            begin = time.perf_counter_ns()
            empty.poll(i / 1000)
            poll_us.append((time.perf_counter_ns() - begin) / 1000)
        release.set()
        if not worker.wait_idle(2):
            raise RuntimeError("worker did not finish")
        result = worker.take()
        assert result is not None and result.error is None and result.value == 100
        assert calls == [0, 100]
        return {
            "request_count": 101, "computed_payloads": calls,
            "queued_while_busy": queued_while_busy,
            "stats": asdict(worker.stats()),
            "request_p50_us": percentile(request_us, 0.5),
            "request_p95_us": percentile(request_us, 0.95),
            "empty_poll_p50_us": percentile(poll_us, 0.5),
            "empty_poll_p95_us": percentile(poll_us, 0.95),
            "conditions": "First compute deliberately gated by threading.Event; 100 replacements; "
            "2000 empty-executor polls. This is not a CPU-contention or real-time deadline benchmark.",
        }


def experiment(count: int = 240, seeds: tuple[int, ...] = (7, 42, 73)) -> dict:
    cases = [run_case(scenario, variant, seed, count)
             for scenario in SCENARIOS for variant in VARIANTS for seed in seeds]
    return {
        "schema_version": 1,
        "conditions": {"python": platform.python_version(), "platform": platform.platform(),
                       "event_count_per_case": count, "seeds": seeds,
                       "policy": asdict(Policy()), "time_units": "seconds unless named otherwise",
                       "data": "generated synthetic workloads; no calibrated human data"},
        "cases": cases, "worker": worker_experiment(),
        "execution": [execution_case(name, period, count) for name in SCENARIOS
                      for period in (0.002, 0.04, 0.15)],
    }


def execution_case(scenario: str, period: float, count: int) -> dict:
    events = workload(scenario, count)
    plan = build_plan(events, seed=42)
    executor = Executor()
    executor.install(plan)
    emitted = []
    durations = []
    for tick in range(math.ceil(max(e.deadline for e in events) / period) + 2):
        start = time.perf_counter_ns()
        emitted.extend(executor.poll(tick * period, budget=32))
        durations.append((time.perf_counter_ns() - start) / 1000)
    assert executor.queued == 0
    return {"scenario": scenario, "seed": 42, "poll_interval_ms": period*1000,
            "poll_budget": 32, "admitted": len(plan.scheduled),
            "dispatched": sum(row.status == "dispatched" for row in emitted),
            "expired": sum(row.status == "expired" for row in emitted),
            "poll_p95_us": percentile(durations, 0.95),
            "conditions": "Virtual clock, full policy, no external I/O. CPU time includes record "
            "collection; polling intervals are simulated, not measured OS wakeups."}


def write_report(report: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    summary = report | {"cases": [{k:v for k,v in case.items() if k != "trace"}
                                  for case in report["cases"]]}
    (directory / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (directory / "traces.json.gz").write_bytes(gzip.compress(
        json.dumps(report, separators=(",", ":")).encode("utf-8"), mtime=0))
    lines = ["# Reproducible measurements", "", "Generated by `python -m adaptive_timing benchmark`.", "",
             "Synthetic workloads. Each policy receives identical events and per-event random draws.", "",
             "| Workload | Policy | Seed | Planned | Rejected | Constrained | Offset p95 (ms) |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for case in report["cases"]:
        lines.append(f'| {case["scenario"]} | {case["variant"]} | {case["seed"]} | '
                     f'{case["planned"]} | {case["rejected"]} | {case["constrained"]} | '
                     f'{case["absolute_offset_p95_ms"]:.3f} |')
    lines += ["", "A rejected event has no feasible service interval under the selected ordering.",
              "Lower timing variation is not automatically better: the policy is intended to make",
              "variation and constraints inspectable, not maximize a score or prove human realism.", "",
              "Full traces are in `traces.json.gz`; summary measurements are in `results.json`.", "",
              "## Polling delays and deadline expiration", "",
              "Virtual clock, full policy, seed 42. Each poll processes at most 32 candidates.", "",
              "| Workload | Poll interval (ms) | Admitted | Dispatched | Expired |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for row in report["execution"]:
        lines.append(f'| {row["scenario"]} | {row["poll_interval_ms"]:g} | {row["admitted"]} | '
                     f'{row["dispatched"]} | {row["expired"]} |')
    lines += ["", "These are simulated polling delays, not operating-system latency measurements.", "",
              "## Background computation", "", "```json", json.dumps(report["worker"], indent=2),
              "```", "", "## Conditions", "", "```json", json.dumps(report["conditions"], indent=2), "```", ""]
    (directory / "results.md").write_text("\n".join(lines), encoding="utf-8")


def write_viewer(report: dict, output: Path) -> None:
    template = Path(__file__).with_name("viewer.html").read_text(encoding="utf-8")
    # Even a caller-supplied event name must not end a script element.
    payload = json.dumps(report, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__REPORT_DATA__", payload), encoding="utf-8")
