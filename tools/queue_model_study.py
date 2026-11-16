"""Predict the worker's computed share and mean adopted age, then measure both.

The prediction is a closed form for a single compute slot with one waiting place
that a newer request replaces, and a result that counts only if no newer
request arrived while it ran. The measurement drives the real LatestWorker on a
virtual clock, so the counts do not depend on thread timing. Standard library only.

    python tools/queue_model_study.py --output reports/queue-model.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing import LatestWorker

LOADS = (0.3, 0.6, 0.9, 1.5, 3.0)
ARRIVALS = ("poisson", "periodic", "bursty")
SERVICES = ("fixed", "exponential")
SEEDS = (7, 42, 73, 101, 211)
FULL = {"requests": 3000, "seeds": SEEDS}
QUICK = {"requests": 200, "seeds": (7, 42)}
BURST_SIZE = 5
BURST_SPACING = 0.05
PHASE = 0.1234567
NOISE_FACTOR = 3.0


def predict(load: float, service: str) -> dict:
    """Closed form for Poisson arrivals at rate load per mean service time.

    p is the chance a compute sees no arrival while it runs. A busy period holds
    1/p computes, one adopted result and 1 + load/p arrivals on average. The
    age term uses how long the winning request waited, which is a truncated
    exponential, so it needs E[S exp(-load S)] for the service law S.
    """
    if service == "fixed":
        p = math.exp(-load)
        m = p
    elif service == "exponential":
        p = 1 / (1 + load)
        m = 1 / (1 + load) ** 2
    else:
        raise ValueError(f"unknown service law: {service}")
    return {
        "computed_share": 1 / (p + load),
        "adopted_share": p / (p + load),
        "mean_adopted_age": m / p + (1 - p) / load - m,
    }


def arrival_times(kind: str, load: float, count: int, rng: random.Random) -> list[float]:
    if kind == "poisson":
        clock, times = 0.0, []
        for _ in range(count):
            clock += rng.expovariate(load)
            times.append(clock)
        return times
    if kind == "periodic":
        return [PHASE + index / load for index in range(count)]
    if kind == "bursty":
        # Same long run rate as the other processes, in groups of BURST_SIZE.
        clock, times = 0.0, []
        while len(times) < count:
            clock += rng.expovariate(load / BURST_SIZE)
            for step in range(BURST_SIZE):
                times.append(clock + step * BURST_SPACING)
        return times[:count]
    raise ValueError(f"unknown arrival process: {kind}")


def wait_for(condition, what: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise RuntimeError(f"timed out waiting for {what}")
        time.sleep(0)


def measure(arrival: str, service: str, load: float, requests: int, seed: int) -> dict:
    rng = random.Random(f"{arrival}/{service}/{load}/{seed}")
    arrivals = arrival_times(arrival, load, requests, rng)
    service_rng = random.Random(f"service/{service}/{load}/{seed}")
    started = threading.Semaphore(0)
    finish = threading.Semaphore(0)

    def compute(payload):
        started.release()
        if not finish.acquire(timeout=10):
            raise TimeoutError("the study did not release a computation")
        return payload

    def duration() -> float:
        return 1.0 if service == "fixed" else service_rng.expovariate(1.0)

    ages, arrived_at = [], {}
    with LatestWorker(compute) as worker:
        running = None
        index = 0
        while index < len(arrivals) or running is not None:
            next_arrival = arrivals[index] if index < len(arrivals) else math.inf
            next_finish = running if running is not None else math.inf
            if next_arrival < next_finish:
                arrived_at[index] = next_arrival
                worker.request(index)
                if running is None:
                    if not started.acquire(timeout=10):
                        raise RuntimeError("worker did not start")
                    running = next_arrival + duration()
                index += 1
            else:
                queued = worker.stats().queued
                before = worker.stats().computed
                finish.release()
                wait_for(lambda: worker.stats().computed > before, "a computation to end")
                outcome = worker.take()
                if outcome is not None:
                    ages.append(next_finish - arrived_at[outcome.value])
                if queued:
                    if not started.acquire(timeout=10):
                        raise RuntimeError("worker did not start the waiting request")
                    running = next_finish + duration()
                else:
                    running = None
        stats = worker.stats()
    assert stats.submitted == requests
    assert stats.computed + stats.replaced == requests
    assert stats.computed - stats.stale == len(ages)
    return {"computed": stats.computed, "replaced": stats.replaced, "stale": stats.stale,
            "adopted": len(ages),
            "computed_share": stats.computed / requests,
            "adopted_share": len(ages) / requests,
            "mean_adopted_age": statistics.fmean(ages) if ages else None}


def summarize(values: list[float]) -> dict:
    spread = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return {"mean": statistics.fmean(values), "min": min(values), "max": max(values),
            "standard_error": spread}


def compare(predicted: float, runs: list[float | None]) -> dict:
    values = [value for value in runs if value is not None]
    if len(values) != len(runs):
        return {"measured": None, "difference": None, "verdict": "none adopted in at least one run"}
    measured = summarize(values)
    difference = measured["mean"] - predicted
    noise = NOISE_FACTOR * measured["standard_error"]
    verdict = "within noise" if abs(difference) <= max(noise, 1e-9) else "miss"
    return {"measured": measured, "difference": difference, "verdict": verdict}


def study(settings: dict) -> dict:
    rows = []
    for service in SERVICES:
        for arrival in ARRIVALS:
            for load in LOADS:
                runs = [measure(arrival, service, load, settings["requests"], seed)
                        for seed in settings["seeds"]]
                expected = predict(load, service)
                row = {"arrival": arrival, "service": service, "load": load,
                       "runs": runs, "predicted": expected}
                for name in ("computed_share", "adopted_share", "mean_adopted_age"):
                    row[name] = compare(expected[name], [run[name] for run in runs])
                rows.append(row)
    return {
        "schema_version": 1,
        "settings": {"requests_per_run": settings["requests"], "seeds": list(settings["seeds"]),
                     "loads": list(LOADS), "burst_size": BURST_SIZE,
                     "burst_spacing": BURST_SPACING, "phase": PHASE,
                     "noise_factor": NOISE_FACTOR,
                     "time_unit": "mean service time of one computation"},
        "note": "Virtual time. The worker is real, the service durations are assumed inputs, "
                "and nothing here is a latency bound or a measure of human performance.",
        "rows": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a short run for tests")
    args = parser.parse_args(argv)
    report = study(QUICK if args.quick else FULL)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
