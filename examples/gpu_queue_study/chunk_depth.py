"""Measure the time of each chunk when at most d chunks are queued on the stream.

A chunk time here is the interval on the card between the event after the previous chunk
and the event after this one, so any time the stream sat idle waiting for the next launch
is part of it. The first chunk's interval starts at an event recorded before the pass. No
pass goes stale. Passes run back to back in this script and not inside the worker. Run
from this folder:

    python chunk_depth.py --output ../../docs/evidence/gpu-depth-chunk-times.json
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import sys
import time

import torch

import model
from arrival_run import ROOT, smi
from depth import run_depth

sys.path.insert(0, str(ROOT / "tools"))
from gpu_depth_tables import summarise  # noqa: E402

# None queues every chunk of the pass.
DEPTHS = (1, 2, 4, 8, None)
FULL = {"sizes": model.CHUNK_SIZES, "rounds": 6, "passes_per_round": 50, "warmup": 20}
QUICK = {"sizes": (4,), "rounds": 2, "passes_per_round": 5, "warmup": 3}
ORDER_SEED = 29
READING = "utilization.gpu,memory.used,clocks.sm"


def new_event():
    event = torch.cuda.Event(enable_timing=True)
    event.record()
    return event


def one_pass(chunk_list, inputs, depth):
    """Return the chunk intervals in ms, the host time of the pass in ms and the waits."""
    torch.cuda.synchronize()
    start = time.perf_counter()
    first = new_event()
    _, events, waits = run_depth(chunk_list, inputs, depth, model.run_chunk, new_event)
    torch.cuda.synchronize()
    host = (time.perf_counter() - start) * 1000.0
    intervals = []
    previous = first
    for event in events:
        intervals.append(previous.elapsed_time(event))
        previous = event
    return intervals, host, waits


def study(settings):
    blocks, inputs = model.build()
    chunked = {size: model.chunks(blocks, size) for size in settings["sizes"]}
    for _ in range(settings["warmup"]):
        for chunk_list in chunked.values():
            one_pass(chunk_list, inputs, None)
    cases = [(size, depth) for size in settings["sizes"] for depth in DEPTHS]
    passes = {case: [] for case in cases}
    readings = []
    order = random.Random(ORDER_SEED)
    for number in range(settings["rounds"]):
        shuffled = order.sample(cases, len(cases))
        for size, depth in shuffled:
            before = smi(READING)
            for _ in range(settings["passes_per_round"]):
                intervals, host, waits = one_pass(chunked[size], inputs, depth)
                passes[(size, depth)].append({"round": number, "chunk_ms": [round(v, 6) for v in intervals],
                                              "host_ms": round(host, 6), "waits": waits})
            readings.append({"round": number, "chunk_blocks": size, "depth": depth,
                             "utilization_memory_clock_before": before,
                             "utilization_memory_clock_after": smi(READING)})
    rows = [{"chunk_blocks": size, "chunks_per_pass": len(chunked[size]), "depth": depth,
             "summary": summarise(passes[(size, depth)]), "passes": passes[(size, depth)]}
            for size, depth in cases]
    return rows, readings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a short run for tests")
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device")
    settings = QUICK if args.quick else FULL
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True, timeout=30).stdout.strip()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, readings = study(settings)
    source = json.loads((ROOT / "docs" / "evidence" / "gpu-service-time.json").read_text(encoding="utf-8"))
    report = {
        "schema_version": 1, "revision": revision,
        "card": torch.cuda.get_device_name(0), "driver": smi("driver_version"), "toolkit": source["toolkit"],
        "torch": torch.__version__, "torch_cuda": torch.version.cuda, "python": sys.version.split()[0],
        "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {**{key: settings[key] for key in ("rounds", "passes_per_round", "warmup")},
                     "depths": list(DEPTHS), "depth_null": "every chunk of the pass is queued",
                     "order_seed": ORDER_SEED,
                     "chunk_time": "interval on the card between the events after consecutive chunks, "
                                   "idle time between launches included. The first chunk of a pass "
                                   "starts at an event recorded on an idle stream",
                     "desktop_load": "a working desktop. Utilization, memory and core clock are read "
                                     "before and after every round of a case"},
        "note": "One card, one machine, one model. No pass goes stale and no pass runs inside the worker. "
                "Nothing here is a latency bound.",
        "readings": readings, "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
