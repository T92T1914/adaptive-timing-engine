"""Predict the worker's computed share and mean adopted age from a measured service time.

This reads the whole pass durations that examples/gpu_queue_study/measure_service.py
measured on the GPU and applies the closed forms of docs/queue-model.md with that
empirical distribution in place of an assumed one. It runs before any arrival run, and
the predictions it writes are committed first. Standard library only.

    python tools/gpu_queue_predictions.py --output docs/evidence/gpu-queue-predictions.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
SERVICE_TIMES = ROOT / "docs" / "evidence" / "gpu-service-time.json"
LOADS = (0.3, 0.6, 0.9, 1.5, 3.0)
ARRIVALS = ("poisson", "periodic", "bursty")


def predict(load: float, durations: list[float]) -> dict:
    """The closed form for Poisson arrivals and an empirical service distribution.

    Durations are rescaled to a mean of one, so load is the arrival rate per mean
    service time. p is the chance a computation sees no arrival while it runs, and m
    is the mean of S exp(-load S). A busy period holds 1/p computations, one adopted
    result and, by Wald's identity, 1/p units of GPU time, of which m/p belong to the
    adopted result. So the share of GPU time spent on results that are thrown away is
    1 - m.
    """
    if not durations or min(durations) <= 0:
        raise ValueError("durations must be positive and not empty")
    mean = statistics.fmean(durations)
    scaled = [d / mean for d in durations]
    p = statistics.fmean(math.exp(-load * s) for s in scaled)
    m = statistics.fmean(s * math.exp(-load * s) for s in scaled)
    return {
        "computed_share": 1 / (p + load),
        "adopted_share": p / (p + load),
        "mean_adopted_age": m / p + (1 - p) / load - m,
        "stale_gpu_time_share": 1 - m,
    }


def study(source: dict) -> dict:
    rows = []
    for size in source["rows"]:
        durations = size["pass_ms"]
        mean_ms = statistics.fmean(durations)
        for arrival in ARRIVALS:
            for load in LOADS:
                expected = predict(load, durations)
                expected["mean_adopted_age_ms"] = expected["mean_adopted_age"] * mean_ms
                rows.append({
                    "chunk_blocks": size["chunk_blocks"],
                    "arrival": arrival,
                    "load": load,
                    "predicted": expected,
                    "basis": "derived" if arrival == "poisson"
                    else "the Poisson form at the same long run rate, not derived for this process",
                })
    return {
        "schema_version": 1,
        "source": "docs/evidence/gpu-service-time.json",
        "card": source["card"],
        "torch": source["torch"],
        "mean_pass_ms": {str(r["chunk_blocks"]): statistics.fmean(r["pass_ms"]) for r in source["rows"]},
        "settings": {"loads": list(LOADS), "arrivals": list(ARRIVALS),
                     "time_unit": "mean pass duration of the chunk size",
                     "passes_per_chunk_size": source["passes_per_chunk_size"]},
        "note": "Written before any arrival run. One card, one machine, one model. Nothing here "
                "is a latency bound or a measure of human performance.",
        "rows": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=SERVICE_TIMES)
    args = parser.parse_args(argv)
    source = json.loads(args.source.read_text(encoding="utf-8"))
    report = study(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
