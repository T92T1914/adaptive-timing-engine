"""Run the real LatestWorker over the GPU calculation and set the counts beside the predictions.

Arrivals are generated in real time from the same schedules as tools/queue_model_study.py,
scaled by the mean pass duration of the chunk size. Run it from this folder in the GPU
environment after docs/evidence/gpu-queue-predictions.json is committed:

    python arrival_run.py --output ../../docs/evidence/gpu-arrival-runs.json
"""
import argparse
import importlib.util
import json
from pathlib import Path
import random
import statistics
import sys
import time

import torch

import model

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from adaptive_timing import LatestWorker  # noqa: E402

spec = importlib.util.spec_from_file_location("queue_model_study", ROOT / "tools" / "queue_model_study.py")
queue_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_model)

PREDICTIONS = ROOT / "docs" / "evidence" / "gpu-queue-predictions.json"
FULL = {"requests": 1000, "seeds": (7, 42, 73), "sizes": model.CHUNK_SIZES, "loads": queue_model.LOADS}
QUICK = {"requests": 40, "seeds": (7,), "sizes": (1,), "loads": (0.6,)}
METRICS = ("computed_share", "adopted_share", "mean_adopted_age", "stale_gpu_time_share")


def wait_until(target):
    """Sleep until the target and never spin, so the worker can take the interpreter lock.

    The first version slept until 1.5 ms before the arrival and spun for the rest, which
    probably starved the worker thread (docs/gpu-queue-study.md). An arrival may now be
    issued late. Each run records how late, so the schedule's distortion is part of the result.
    """
    remaining = target - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)


MODES = ("plain", "synced", "stop")


def run_case(chunk_list, inputs, arrival, load, mean_pass, requests, seed, mode="plain"):
    """Run one case. The mode changes only how a pass treats the chunks.

    plain: one synchronize at the end of the pass, as in the first study.
    synced: a synchronize after every chunk and no stop, to price the synchronize alone.
    stop: a synchronize after every chunk, then a look at the worker's generation. A stale
        pass returns at that point. This is the calculation stopping itself. Work already
        placed on the GPU is not removed, and the chunk in flight finishes first.
    """
    rng = random.Random(f"gpu/{arrival}/{load}/{seed}")
    schedule = [t * mean_pass for t in queue_model.arrival_times(arrival, load, requests, rng)]
    arrived = [0.0] * requests
    late = []
    runs = []
    holder = {}

    def compute(index):
        start = time.perf_counter()
        value = inputs
        stopped = False
        for position, chunk in enumerate(chunk_list):
            value = model.run_chunk(chunk, value)
            if mode != "plain":
                torch.cuda.synchronize()
            if mode == "stop" and position + 1 < len(chunk_list) and holder["worker"].stats().generation != index + 2:
                stopped = True
                break
        torch.cuda.synchronize()
        end = time.perf_counter()
        # Adopted when no newer request exists at the moment the computation ends.
        adopted = (not stopped) and holder["worker"].stats().generation == index + 2
        if index >= 0:
            runs.append((index, start, end, adopted, stopped))

    with LatestWorker(compute) as worker:
        holder["worker"] = worker
        # The first call on a new thread sets up its cuBLAS handle, so one warm request
        # runs before the schedule and is left out of every count.
        worker.request(-1)
        worker.wait_idle(timeout=60)
        origin = time.perf_counter()
        for index, offset in enumerate(schedule):
            wait_until(origin + offset)
            arrived[index] = time.perf_counter()
            late.append((arrived[index] - (origin + offset)) * 1000.0)
            worker.request(index)
        worker.wait_idle(timeout=60)
        stats = worker.stats()
    busy = sum(end - start for _, start, end, _, _ in runs)
    stale_time = sum(end - start for _, start, end, adopted, _ in runs if not adopted)
    ages = [end - arrived[index] for index, _, end, adopted, _ in runs if adopted]
    adopted_pass = [end - start for _, start, end, adopted, _ in runs if adopted]
    adopted_count = len(ages)
    return {
        "submitted": stats.submitted - 1, "computed": stats.computed - 1, "replaced": stats.replaced,
        "stale": stats.stale, "adopted": adopted_count,
        # The check at the end of a computation and the worker's own check are two lock
        # acquisitions. A mismatch is a request that slipped between them.
        "adopted_check_mismatch": (stats.computed - 1 - stats.stale) - adopted_count,
        "computed_share": (stats.computed - 1) / requests,
        "adopted_share": adopted_count / requests,
        "mean_adopted_age": (statistics.fmean(ages) / mean_pass) if ages else None,
        "stale_gpu_time_share": stale_time / busy if busy else None,
        "stopped": sum(1 for *_, stopped in runs if stopped),
        "stale_gpu_seconds": stale_time,
        "busy_gpu_seconds": busy,
        "mean_adopted_pass_ms": statistics.fmean(adopted_pass) * 1000.0 if adopted_pass else None,
        "arrival_lateness_ms": {"mean": statistics.fmean(late), "p95": sorted(late)[int(0.95 * len(late))],
                                "max": max(late), "share_over_1ms": sum(x > 1.0 for x in late) / len(late)},
    }


def study(settings, predictions, source):
    blocks, inputs = model.build()
    for _ in range(3):
        value = inputs
        for chunk in model.chunks(blocks, 1):
            value = model.run_chunk(chunk, value)
    torch.cuda.synchronize()
    predicted = {(r["chunk_blocks"], r["arrival"], r["load"]): r["predicted"] for r in predictions["rows"]}
    rows = []
    for size in settings["sizes"]:
        chunk_list = model.chunks(blocks, size)
        mean_pass = predictions["mean_pass_ms"][str(size)] / 1000.0
        for arrival in queue_model.ARRIVALS:
            for load in settings["loads"]:
                runs = [run_case(chunk_list, inputs, arrival, load, mean_pass, settings["requests"], seed)
                        for seed in settings["seeds"]]
                expected = predicted[(size, arrival, load)]
                row = {"chunk_blocks": size, "arrival": arrival, "load": load,
                       "runs": runs, "predicted": expected}
                for name in METRICS:
                    row[name] = queue_model.compare(expected[name], [run[name] for run in runs])
                rows.append(row)
    return {
        "schema_version": 1,
        "card": source["card"], "driver": source["driver"], "toolkit": source["toolkit"],
        "torch": source["torch"],
        "settings": {"requests_per_run": settings["requests"], "seeds": list(settings["seeds"]),
                     "loads": list(settings["loads"]), "noise_factor": queue_model.NOISE_FACTOR,
                     "time_unit": "mean pass duration of the chunk size",
                     "arrivals": "generated in real time, not from any person",
                     "arrival_wait": "sleep only, no spin"},
        "note": "One card, one machine, one model, generated arrivals. Nothing here is a latency bound "
                "or a measure of human performance.",
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a short run for tests")
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device")
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    source = json.loads((ROOT / "docs" / "evidence" / "gpu-service-time.json").read_text(encoding="utf-8"))
    report = study(QUICK if args.quick else FULL, predictions, source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
