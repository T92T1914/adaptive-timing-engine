"""Predict what each queue depth costs, from the measured chunk times, before any depth run.

    python tools/gpu_depth_predictions.py --output docs/evidence/gpu-depth-predictions.json

The input is docs/evidence/gpu-depth-chunk-times.json. A small event model replays the
worker, one calculation in progress and one request waiting, over the same generated
arrival schedules the depth runs will use. Each predicted pass draws one saved pass of
its chunk size and depth, so its chunk times and its number of waits stay together.

The model assumes the following, and each point is a place where a run can differ.

1. A launch takes no time, so the queue is always as full as the depth allows. Chunk k
   is checked and launched when chunk k - d finishes, and the first d chunks at the start.
   A depth of every chunk, or of at least the chunk count, therefore predicts no stop at
   all. The run asks between launches at those depths too, so a request that arrives
   while chunks are still being launched can stop such a pass there.
2. A check sees a newer request the moment it arrives. A stopped pass ends when the last
   chunk it launched finishes.
3. A waiting request starts the moment the worker is free. No start delay is added.
4. A pass that completes makes the waits of its drawn pass. A stopped pass makes the share
   of them that matches the checks it reached beyond the first d chunks, the check that
   found it stale included, since the pass waits before it asks. Every pass also makes
   the closing synchronize.
5. Chunk times are those of passes run back to back outside the worker with no stop.
6. Every request arrives exactly on its schedule. The run's arrival loop sleeps and can
   issue a request late, which can also change the shape of a burst.
7. A pass lasts as long as its chunk intervals on the card. In the chunk time run the host
   measured each pass a little longer than that sum, and the load unit is the host time,
   so the load in the model is slightly below the nominal one.

This file needs only the standard library.
"""
import argparse
from bisect import bisect_right
import importlib.util
from itertools import accumulate
import json
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "evidence" / "gpu-depth-chunk-times.json"

spec = importlib.util.spec_from_file_location("queue_model_study", ROOT / "tools" / "queue_model_study.py")
queue_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_model)

LOADS = (0.9, 1.5)
SEEDS = (7, 42, 73)
REQUESTS = 1000
REPEATS = 5
METRICS = ("computed_share", "adopted_share", "mean_adopted_age_ms", "stopped_share_of_requests",
           "stale_after_newer_ms_per_stop", "stopped_pass_ms", "stale_gpu_time_share",
           "synchronizes_per_pass")


def simulate(schedule, passes, depth, draw):
    """Replay one arrival schedule in ms. `draw` picks one saved pass for each calculation."""
    requests = len(schedule)
    free = 0.0
    index = 0
    computed = stopped = 0
    ages, tails, stopped_lengths, synchronizes = [], [], [], []
    busy = stale = 0.0
    while index < requests:
        start = max(free, schedule[index])
        latest = bisect_right(schedule, start, index) - 1
        arrived = schedule[latest]
        index = latest + 1
        newer = schedule[index] if index < requests else float("inf")
        saved = draw(passes)
        finished = [start + total for total in accumulate(saved["chunk_ms"])]
        count = len(finished)
        launched = count
        if depth is not None and depth < count:
            for oldest in range(count - depth):
                if finished[oldest] >= newer:
                    launched = oldest + depth
                    break
        end = finished[launched - 1]
        cut = launched < count
        computed += 1
        busy += end - start
        if depth is None or depth >= count:
            waits = saved["waits"]
        else:
            waits = saved["waits"]
            if cut:
                # The checks from chunk d up to the one that found the pass stale.
                waits *= (launched - depth + 1) / (count - depth)
        synchronizes.append(1 + waits)
        if cut:
            stopped += 1
            tails.append(end - newer)
            stopped_lengths.append(end - start)
        if not cut and newer > end:
            ages.append(end - arrived)
        else:
            stale += end - start
        free = end
    return {
        "computed_share": computed / requests, "adopted_share": len(ages) / requests,
        "mean_adopted_age_ms": statistics.fmean(ages) if ages else None,
        "stopped_share_of_requests": stopped / requests,
        "stale_after_newer_ms_per_stop": statistics.fmean(tails) if tails else None,
        "stopped_pass_ms": statistics.fmean(stopped_lengths) if stopped_lengths else None,
        "stale_gpu_time_share": stale / busy,
        "synchronizes_per_pass": statistics.fmean(synchronizes),
    }


def time_units(source):
    """The unit of load for each chunk size: the mean pass with every chunk queued."""
    return {row["chunk_blocks"]: row["summary"]["pass_host_mean_ms"]
            for row in source["rows"] if row["depth"] is None}


def predict_condition(row, unit_ms, arrival, load):
    results = []
    for seed in SEEDS:
        # The same schedule arrival_run.run_case builds for this arrival, load and seed.
        rng = random.Random(f"gpu/{arrival}/{load}/{seed}")
        schedule = [t * unit_ms for t in queue_model.arrival_times(arrival, load, REQUESTS, rng)]
        for repeat in range(REPEATS):
            draws = random.Random(
                f"depth/{row['chunk_blocks']}/{row['depth']}/{arrival}/{load}/{seed}/{repeat}")
            results.append(simulate(schedule, row["passes"], row["depth"], draws.choice))
    predicted = {}
    for name in METRICS:
        values = [result[name] for result in results if result[name] is not None]
        predicted[name] = ({"mean": statistics.fmean(values), "low": min(values), "high": max(values),
                            "runs": len(values)} if values else None)
    return {"chunk_blocks": row["chunk_blocks"], "chunks_per_pass": row["chunks_per_pass"],
            "depth": row["depth"], "arrival": arrival, "load": load, "predicted": predicted}


def predict(source):
    units = time_units(source)
    rows = [predict_condition(row, units[row["chunk_blocks"]], arrival, load)
            for row in source["rows"] for arrival in queue_model.ARRIVALS for load in LOADS]
    return {
        "schema_version": 1,
        "chunk_times_revision": source["revision"],
        "settings": {
            "requests_per_run": REQUESTS, "seeds": list(SEEDS), "draw_repeats": REPEATS,
            "loads": list(LOADS), "arrivals": list(queue_model.ARRIVALS),
            "time_unit_ms": {str(size): unit for size, unit in units.items()},
            "time_unit": "mean host time of a pass with every chunk queued, from the chunk time run",
        },
        "definitions": {
            "stale_after_newer_ms_per_stop": "for a stopped pass, the time from the newer request's "
                                             "arrival to the end of the pass, mean over stopped passes",
            "stopped_pass_ms": "whole length of a stopped pass, mean over stopped passes",
            "stale_gpu_time_share": "length of every pass whose result is not adopted over all pass "
                                    "length",
            "synchronizes_per_pass": "event waits plus the closing synchronize, mean over passes",
            "mean_adopted_age_ms": "from a request's arrival to the end of its adopted pass",
            "low_and_high": "smallest and largest of the single replays, one for each seed and draw",
        },
        "note": "Predictions from a model, made before any depth run. Nothing here is a measurement or "
                "a latency bound. The assumptions are listed in tools/gpu_depth_predictions.py.",
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = predict(json.loads(SOURCE.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
