"""Measure the service time distribution of the chunked calculation on the GPU.

Run from this folder inside the GPU environment:

    python measure_service.py --output ../../docs/evidence/gpu-service-time.json
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess

import torch

import harness
import model

RUNS = 300


def percentile(ordered, share):
    """Linear interpolation between order statistics."""
    position = (len(ordered) - 1) * share
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def smi(query):
    out = subprocess.run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=30, check=True)
    return out.stdout.strip().splitlines()[0]


def measure(runs):
    blocks, inputs = model.build()
    rows = []
    for size in model.CHUNK_SIZES:
        chunk_list = model.chunks(blocks, size)
        per_chunk = harness.service_times(chunk_list, inputs, runs)
        values = sorted(v * 1000.0 for chunk in per_chunk for v in chunk)
        rows.append({
            "chunk_blocks": size,
            "chunks_per_pass": len(chunk_list),
            "count": len(values),
            "median_ms": percentile(values, 0.5),
            "p5_ms": percentile(values, 0.05),
            "p95_ms": percentile(values, 0.95),
            "mean_ms": statistics.fmean(values),
        })
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=RUNS)
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device")
    before = smi("utilization.gpu,memory.used")
    rows = measure(args.runs)
    after = smi("utilization.gpu,memory.used")
    report = {
        "note": "One card, one machine, one model. Every timed region is closed by a synchronize. Nothing here is a latency bound.",
        "card": torch.cuda.get_device_name(0),
        "driver": smi("driver_version"),
        "toolkit": torch.version.cuda,
        "torch": torch.__version__,
        "model": {"width": model.WIDTH, "blocks": model.BLOCKS, "batch": model.BATCH,
                  "seed": model.SEED, "dtype": "float32"},
        "passes_per_chunk_size": args.runs,
        "gpu_utilization_and_memory_before": before,
        "gpu_utilization_and_memory_after": after,
        "rows": rows,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
