"""Measure what the arrival run assumed and did not check: how long one pass takes.

The predictions use the sum of per chunk timings, each closed by its own synchronize.
The worker runs a whole pass and synchronizes once. This script times both, and also
times the passes inside the worker under a Poisson load of 0.6, so the three can be
set side by side. It was written after the arrival run and does not change any
prediction. Run it from this folder in the GPU environment:

    python pass_diagnostic.py --output ../../docs/evidence/gpu-pass-diagnostic.json
"""
import argparse
import json
from pathlib import Path
import random
import statistics
import sys
import time

import torch

import model
from arrival_run import ROOT, PREDICTIONS, queue_model, run_case, wait_until

sys.path.insert(0, str(ROOT))
from adaptive_timing import LatestWorker  # noqa: E402

PASSES = 300
REQUESTS = 1000
LOAD = 0.6


def whole_pass_ms(chunk_list, inputs):
    out = []
    for _ in range(PASSES):
        torch.cuda.synchronize()
        start = time.perf_counter()
        value = inputs
        for chunk in chunk_list:
            value = model.run_chunk(chunk, value)
        torch.cuda.synchronize()
        out.append((time.perf_counter() - start) * 1000.0)
    return out


def in_worker_ms(chunk_list, inputs, mean_pass, seed):
    rng = random.Random(f"gpu/diagnostic/{seed}")
    schedule = [t * mean_pass for t in queue_model.arrival_times("poisson", LOAD, REQUESTS, rng)]
    durations = []
    late = []

    def compute(index):
        start = time.perf_counter()
        value = inputs
        for chunk in chunk_list:
            value = model.run_chunk(chunk, value)
        torch.cuda.synchronize()
        if index >= 0:
            durations.append((time.perf_counter() - start) * 1000.0)

    with LatestWorker(compute) as worker:
        worker.request(-1)
        worker.wait_idle(timeout=60)
        origin = time.perf_counter()
        for index, offset in enumerate(schedule):
            wait_until(origin + offset)
            late.append((time.perf_counter() - (origin + offset)) * 1000.0)
            worker.request(index)
        worker.wait_idle(timeout=60)
    return durations, late


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device")
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    blocks, inputs = model.build()
    for _ in range(20):
        value = inputs
        for chunk in model.chunks(blocks, 1):
            value = model.run_chunk(chunk, value)
    torch.cuda.synchronize()
    rows = []
    for size in model.CHUNK_SIZES:
        chunk_list = model.chunks(blocks, size)
        mean_pass = predictions["mean_pass_ms"][str(size)] / 1000.0
        alone = whole_pass_ms(chunk_list, inputs)
        inside, late = in_worker_ms(chunk_list, inputs, mean_pass, 7)
        rows.append({
            "chunk_blocks": size,
            "assumed_mean_pass_ms": mean_pass * 1000.0,
            "whole_pass_alone": {"count": len(alone), "mean_ms": statistics.fmean(alone),
                                 "median_ms": statistics.median(alone)},
            "whole_pass_in_worker": {"count": len(inside), "mean_ms": statistics.fmean(inside),
                                     "median_ms": statistics.median(inside)},
            "arrival_lateness_ms": {"mean": statistics.fmean(late), "p95": sorted(late)[int(0.95 * len(late))],
                                    "max": max(late), "share_over_1ms": sum(x > 1.0 for x in late) / len(late)},
        })
    # A suspect for the misses: the thread that generates arrivals spins and keeps the
    # interpreter lock, so the worker starts late. Rerun two cases with a shorter switch interval.
    chunk_list = model.chunks(blocks, 2)
    mean_pass = predictions["mean_pass_ms"]["2"] / 1000.0
    interval = {}
    for label, value in (("default_5ms", sys.getswitchinterval()), ("shorter_0.2ms", 0.0002)):
        sys.setswitchinterval(value)
        interval[label] = {
            f"{arrival}_{load}": run_case(chunk_list, inputs, arrival, load, mean_pass, REQUESTS, 7)["computed_share"]
            for arrival, load in (("periodic", 3.0), ("bursty", 0.6), ("poisson", 0.6), ("periodic", 0.9))}
    report = {"switch_interval_check": {"chunk_blocks": 2, "requests": REQUESTS, "seed": 7,
                                        "computed_share": interval},
              "note": "Made after the arrival run. One card, one machine. Nothing here changes a prediction.",
              "card": torch.cuda.get_device_name(0), "torch": torch.__version__,
              "load": LOAD, "requests": REQUESTS, "seed": 7, "rows": rows}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
