"""Time every pass inside the worker and how long each calculation waited to start.

The first study measured neither, so some of its misses have no measured cause. This
runs the same worker and the same sleeping arrival loop in the plain and stop modes
(see arrival_run.run_case) and keeps, for every calculation that started, the time
from its request's arrival to its start and the time from its start to its end. Both
are read inside the example with time.perf_counter, so the engine needs no new method.
A condition runs in blocks until it holds 10000 starts or reaches the block limit.
Run from this folder:

    python pass_timing.py --output ../../docs/evidence/gpu-pass-timing.json

The samples go beside the summary in a file ending in -samples.json.gz.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import torch

import model
from arrival_run import PREDICTIONS, ROOT, run_case, smi

sys.path.insert(0, str(ROOT / "tools"))
from gpu_pass_timing_tables import IDLE_AT_ARRIVAL, STOPPED, TAIL_COUNT, summarise  # noqa: E402

CASES = (("poisson", 0.9), ("poisson", 1.5), ("bursty", 0.9), ("bursty", 1.5))
MODES = ("plain", "stop")
FULL = {"sizes": model.CHUNK_SIZES, "cases": CASES, "block_requests": 5000, "target_starts": TAIL_COUNT,
        "max_blocks": 8}
QUICK = {"sizes": (2,), "cases": (("poisson", 0.9),), "block_requests": 60, "target_starts": 60,
         "max_blocks": 2}
FIRST_SEED = 1000
READING = "utilization.gpu,memory.used,clocks.sm"


def samples_from(trace):
    """Turn (arrived, start, end, adopted, stopped) rows into [delay_us, pass_us, flags]."""
    out = []
    previous_end = None
    for arrived, start, end, _, stopped in sorted(trace, key=lambda row: row[1]):
        flags = STOPPED if stopped else 0
        if previous_end is None or previous_end <= arrived:
            flags |= IDLE_AT_ARRIVAL
        out.append([round((start - arrived) * 1e6), round((end - start) * 1e6), flags])
        previous_end = end
    return out


def run_condition(settings, chunk_list, inputs, arrival, load, mean_pass, mode):
    blocks = []
    starts = 0
    while starts < settings["target_starts"] and len(blocks) < settings["max_blocks"]:
        seed = FIRST_SEED + len(blocks)
        trace = []
        before = smi(READING)
        result = run_case(chunk_list, inputs, arrival, load, mean_pass, settings["block_requests"],
                          seed, mode, trace)
        after = smi(READING)
        samples = samples_from(trace)
        starts += len(samples)
        blocks.append({"seed": seed, "requests": settings["block_requests"],
                       "utilization_memory_clock_before": before, "utilization_memory_clock_after": after,
                       "arrival_lateness_ms": result["arrival_lateness_ms"], "samples": samples})
    return blocks


def study(settings, predictions, source):
    blocks, inputs = model.build()
    for _ in range(3):
        value = inputs
        for chunk in model.chunks(blocks, 1):
            value = model.run_chunk(chunk, value)
    torch.cuda.synchronize()
    conditions = []
    for size in settings["sizes"]:
        chunk_list = model.chunks(blocks, size)
        mean_pass = predictions["mean_pass_ms"][str(size)] / 1000.0
        for arrival, load in settings["cases"]:
            for mode in MODES:
                condition = {"chunk_blocks": size, "arrival": arrival, "load": load, "mode": mode}
                condition["blocks"] = run_condition(settings, chunk_list, inputs, arrival, load,
                                                    mean_pass, mode)
                conditions.append(condition)
                row = summarise(condition)
                print(f"{size} {arrival} {load} {mode}: {row['starts']} starts", flush=True)
    return conditions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a short run for tests")
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device")
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    source = json.loads((ROOT / "docs" / "evidence" / "gpu-service-time.json").read_text(encoding="utf-8"))
    settings = QUICK if args.quick else FULL
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True, timeout=30).stdout.strip()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conditions = study(settings, predictions, source)
    samples = args.output.with_name(args.output.stem + "-samples.json.gz")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"schema_version": 1, "conditions": conditions}, separators=(",", ":")).encode()
    samples.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    rows = []
    for condition in conditions:
        row = summarise(condition)
        row["blocks"] = [{key: value for key, value in block.items() if key != "samples"}
                         for block in condition["blocks"]]
        rows.append(row)
    report = {
        "schema_version": 1, "revision": revision,
        "card": torch.cuda.get_device_name(0), "driver": smi("driver_version"),
        "toolkit": source["toolkit"], "torch": torch.__version__, "torch_cuda": torch.version.cuda,
        "python": sys.version.split()[0], "switch_interval_seconds": sys.getswitchinterval(),
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {**{key: settings[key] for key in ("block_requests", "target_starts", "max_blocks")},
                     "first_seed": FIRST_SEED, "cases": [list(case) for case in settings["cases"]],
                     "modes": list(MODES),
                     "time_unit": "mean pass duration of the chunk size, from the first study",
                     "arrival_wait": "sleep only, no spin",
                     "arrivals": "generated in real time, not from any person",
                     "start_delay": "from just before the request call to the first line of the calculation, "
                                    "any wait behind an earlier pass included",
                     "pass_time": "from the first line of the calculation to the return of its last "
                                  "synchronize",
                     "percentile": "nearest rank",
                     "desktop_load": "a working desktop. Utilization, memory and core clock are read "
                                     "before and after every block as utilization.gpu,memory.used,clocks.sm"},
        "samples_file": samples.name, "samples_sha256": hashlib.sha256(samples.read_bytes()).hexdigest(),
        "note": "One card, one machine, one model, generated arrivals. Nothing here is a latency bound "
                "or a measure of human performance.",
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
