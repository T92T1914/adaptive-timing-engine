"""Measure what the calculation saves by stopping itself between chunks, and what that costs.

The calculation looks at the worker's generation after each chunk and returns early when a
newer request exists. This is not cancellation. The engine never interrupts the calculation,
and work already placed on the GPU is not removed. The chunk in flight finishes first.

Three modes run the same arrivals on the same worker (see arrival_run.run_case). The plain mode
is the first study. The synced mode adds a synchronize after every chunk and stops nothing, so
the synchronize cost is separate from the effect of stopping. Run from this folder:

    python cooperative_stop.py --output ../../docs/evidence/gpu-cooperative-stop.json
"""
import argparse
import json
from pathlib import Path
import statistics

import torch

import model
from arrival_run import MODES, PREDICTIONS, ROOT, queue_model, run_case  # noqa: F401

FULL = {"requests": 1000, "seeds": (7, 42, 73), "sizes": model.CHUNK_SIZES,
        "cases": (("poisson", 0.9), ("poisson", 1.5), ("bursty", 0.9), ("bursty", 1.5))}
QUICK = {"requests": 40, "seeds": (7,), "sizes": (1, 2, 4), "cases": (("poisson", 0.9),)}
KEYS = ("computed_share", "adopted_share", "mean_adopted_age", "stale_gpu_seconds", "busy_gpu_seconds",
        "mean_adopted_pass_ms", "stopped")


def mean_of(runs, key):
    values = [run[key] for run in runs if run[key] is not None]
    return statistics.fmean(values) if values else None


def study(settings, mean_pass_ms, source):
    blocks, inputs = model.build()
    for _ in range(3):
        value = inputs
        for chunk in model.chunks(blocks, 1):
            value = model.run_chunk(chunk, value)
    torch.cuda.synchronize()
    rows = []
    for size in settings["sizes"]:
        chunk_list = model.chunks(blocks, size)
        mean_pass = mean_pass_ms[str(size)] / 1000.0
        for arrival, load in settings["cases"]:
            row = {"chunk_blocks": size, "chunks_per_pass": len(chunk_list), "arrival": arrival, "load": load,
                   "modes": {}}
            for mode in MODES:
                runs = [run_case(chunk_list, inputs, arrival, load, mean_pass, settings["requests"], seed, mode)
                        for seed in settings["seeds"]]
                row["modes"][mode] = {"runs": runs, "means": {key: mean_of(runs, key) for key in KEYS}}
            rows.append(row)
    return {
        "schema_version": 1,
        "card": source["card"], "driver": source["driver"], "toolkit": source["toolkit"], "torch": source["torch"],
        "settings": {"requests_per_run": settings["requests"], "seeds": list(settings["seeds"]),
                     "cases": [list(case) for case in settings["cases"]], "modes": list(MODES),
                     "time_unit": "mean pass duration of the chunk size, from the first study",
                     "arrival_wait": "sleep only, no spin",
                     "arrivals": "generated in real time, not from any person"},
        "note": "The calculation stops itself between chunks. It is not forced cancellation, and work already "
                "placed on the GPU is not removed. One card, one machine, one model, generated arrivals. "
                "Nothing here is a latency bound or a measure of human performance.",
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
    report = study(QUICK if args.quick else FULL, predictions["mean_pass_ms"], source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
