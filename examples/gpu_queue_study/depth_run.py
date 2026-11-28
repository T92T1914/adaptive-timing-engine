"""Run the worker over every queue depth and record what each prediction is set beside.

The predictions in docs/evidence/gpu-depth-predictions.json come from the chunk times
alone and were committed before any depth run. This script takes its conditions, seeds,
request count and time unit from that file, so every run answers a prediction as
written. Each pass is depth.run_depth inside the real LatestWorker, through the depth mode
of arrival_run.run_case, and the arrival loop sleeps and never spins. Run from this folder
in the GPU environment:

    python depth_run.py --output ../../docs/evidence/gpu-depth-runs.json

One sample for every calculation that started goes beside it in a file ending in
-samples.json.gz. From the repository root, python tools/gpu_depth_run_tables.py prints
each condition beside its prediction.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import torch

import model
from arrival_run import DEPTH_MODE, ROOT, run_case, smi

sys.path.insert(0, str(ROOT / "tools"))
from gpu_depth_run_tables import KEYS, predictions_digest, samples_from, summarise  # noqa: E402

PREDICTIONS = ROOT / "docs" / "evidence" / "gpu-depth-predictions.json"
QUICK = {"sizes": (4,), "depths": (2, None), "arrivals": ("poisson",), "loads": (0.9,), "seeds": (7,),
         "requests": 40}
ORDER_SEED = 37
READING = "utilization.gpu,memory.used,clocks.sm"
WORKER_KEYS = ("submitted", "computed", "replaced", "stale", "adopted", "failures", "adopted_check_mismatch")


def full_settings(predictions):
    """Every condition the predictions hold, with their seeds and request count."""
    settings = predictions["settings"]
    rows = predictions["rows"]
    return {"sizes": tuple(sorted({row["chunk_blocks"] for row in rows})),
            "depths": tuple(dict.fromkeys(row["depth"] for row in rows)),
            "arrivals": tuple(settings["arrivals"]), "loads": tuple(settings["loads"]),
            "seeds": tuple(settings["seeds"]), "requests": settings["requests_per_run"]}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
                          timeout=30).stdout.strip()


def study(settings, predictions):
    blocks, inputs = model.build()
    for _ in range(3):
        value = inputs
        for chunk in model.chunks(blocks, 1):
            value = model.run_chunk(chunk, value)
    torch.cuda.synchronize()
    chunked = {size: model.chunks(blocks, size) for size in settings["sizes"]}
    units = predictions["settings"]["time_unit_ms"]
    wanted = [(row["chunk_blocks"], row["depth"], row["arrival"], row["load"]) for row in predictions["rows"]]
    conditions = [case for case in wanted
                  if case[0] in settings["sizes"] and case[1] in settings["depths"]
                  and case[2] in settings["arrivals"] and case[3] in settings["loads"]]
    # A shuffled order spreads any drift of the desktop load over the depths.
    order = random.Random(ORDER_SEED).sample(conditions, len(conditions))
    done = {}
    for number, (size, depth, arrival, load) in enumerate(order):
        mean_pass = units[str(size)] / 1000.0
        runs = []
        for seed in settings["seeds"]:
            passes = []
            before = smi(READING)
            result = run_case(chunked[size], inputs, arrival, load, mean_pass, settings["requests"], seed,
                              DEPTH_MODE, depth=depth, passes=passes)
            after = smi(READING)
            runs.append({"seed": seed, "requests": settings["requests"],
                         "utilization_memory_clock_before": before, "utilization_memory_clock_after": after,
                         "arrival_lateness_ms": result["arrival_lateness_ms"],
                         "worker": {key: result[key] for key in WORKER_KEYS},
                         "samples": samples_from(passes)})
        done[(size, depth, arrival, load)] = {"chunk_blocks": size, "chunks_per_pass": len(chunked[size]),
                                              "depth": depth, "arrival": arrival, "load": load,
                                              "order": number, "runs": runs}
        print(f"{number + 1}/{len(order)} size {size} depth {depth} {arrival} {load}", flush=True)
    return [done[case] for case in conditions]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a short run for tests")
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device")
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    source = json.loads((ROOT / "docs" / "evidence" / "gpu-service-time.json").read_text(encoding="utf-8"))
    settings = QUICK if args.quick else full_settings(predictions)
    revision = git("rev-parse", "HEAD")
    tree_clean = git("status", "--porcelain", "--untracked-files=no") == ""
    predictions_commit, predictions_committed = git(
        "log", "-1", "--format=%H%n%cI", "--", "docs/evidence/gpu-depth-predictions.json").splitlines()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conditions = study(settings, predictions)
    samples = args.output.with_name(args.output.stem + "-samples.json.gz")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    kept = [{**{key: condition[key] for key in KEYS},
             "runs": [{key: run[key] for key in ("seed", "requests", "samples")} for run in condition["runs"]]}
            for condition in conditions]
    raw = json.dumps({"schema_version": 1, "conditions": kept}, separators=(",", ":")).encode()
    samples.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    rows = []
    for condition in conditions:
        row = summarise(condition)
        row["order"] = condition["order"]
        row["runs"] = [{key: value for key, value in run.items() if key != "samples"} for run in condition["runs"]]
        rows.append(row)
    report = {
        "schema_version": 1, "revision": revision, "tree_clean": tree_clean,
        "predictions_commit": predictions_commit, "predictions_committed": predictions_committed,
        "predictions_digest": predictions_digest(predictions),
        "card": torch.cuda.get_device_name(0), "driver": smi("driver_version"),
        "toolkit": source["toolkit"], "torch": torch.__version__, "torch_cuda": torch.version.cuda,
        "python": sys.version.split()[0], "switch_interval_seconds": sys.getswitchinterval(),
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {"sizes": list(settings["sizes"]), "depths": list(settings["depths"]),
                     "arrivals": list(settings["arrivals"]), "loads": list(settings["loads"]),
                     "seeds": list(settings["seeds"]), "requests_per_run": settings["requests"],
                     "order_seed": ORDER_SEED,
                     "depth_null": "every chunk of the pass is queued",
                     "time_unit_ms": predictions["settings"]["time_unit_ms"],
                     "time_unit": "the time unit of the predictions, the mean host time of a pass with every "
                                  "chunk queued in the chunk time run",
                     "schedule_key": "gpu/{arrival}/{load}/{seed}, as in the predictions",
                     "arrival_wait": "sleep only, no spin",
                     "arrivals_source": "generated in real time, not from any person",
                     "sample": "[delay_us, pass_us, newer_us, waits, launched, flags], see "
                               "tools/gpu_depth_run_tables.py",
                     "start_delay": "from just before the request call to the first line of the calculation, "
                                    "any wait behind an earlier pass included",
                     "pass_time": "from the first line of the calculation to the return of its closing "
                                  "synchronize",
                     "percentile": "nearest rank",
                     "desktop_load": "a working desktop. Utilization, memory and core clock are read before "
                                     "and after every run as utilization.gpu,memory.used,clocks.sm"},
        "samples_file": samples.name, "samples_sha256": hashlib.sha256(samples.read_bytes()).hexdigest(),
        "note": "One card, one machine, one model, generated arrivals. A stopped pass stops itself between "
                "launches, and the chunks it already queued run to their end. Nothing here is a latency bound, "
                "forced cancellation or a measure of human performance.",
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
