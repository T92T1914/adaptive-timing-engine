"""Summarise the saved pass and start delay samples and print the table.

    python tools/gpu_pass_timing_tables.py

The samples are in docs/evidence/gpu-pass-timing-samples.json.gz, one pair of whole
microseconds and a flag for every calculation that started. This file needs only the
standard library, so the summary can be checked on a machine with no GPU.
"""
import gzip
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "docs" / "evidence" / "gpu-pass-timing.json"
SAMPLES = ROOT / "docs" / "evidence" / "gpu-pass-timing-samples.json.gz"

# Flags of one sample.
STOPPED = 1
IDLE_AT_ARRIVAL = 2

QUANTILES = (("p50", 500), ("p99", 990), ("p99_9", 999))
TAIL_COUNT = 10_000


def percentile(ordered, per_mille):
    """Nearest rank: the smallest value with at least that share at or below it."""
    if not ordered:
        return None
    return ordered[max(1, math.ceil(len(ordered) * per_mille / 1000)) - 1]


def spread(values_us):
    ordered = sorted(values_us)
    result = {"count": len(ordered)}
    for name, per_mille in QUANTILES:
        value = percentile(ordered, per_mille)
        result[f"{name}_ms"] = None if value is None else value / 1000.0
    result["max_ms"] = ordered[-1] / 1000.0 if ordered else None
    return result


def summarise(condition):
    """One row from every block of one condition. A sample is [delay_us, pass_us, flags]."""
    samples = [sample for block in condition["blocks"] for sample in block["samples"]]
    row = {key: condition[key] for key in ("chunk_blocks", "arrival", "load", "mode")}
    row["requests"] = sum(block["requests"] for block in condition["blocks"])
    row["starts"] = len(samples)
    row["tail_rests_on_fewer_than_10000_starts"] = len(samples) < TAIL_COUNT
    row["start_delay"] = spread([delay for delay, _, _ in samples])
    # A request that found the worker idle waited for no earlier pass.
    row["start_delay_idle_at_arrival"] = spread(
        [delay for delay, _, flags in samples if flags & IDLE_AT_ARRIVAL])
    row["completed_pass"] = spread([length for _, length, flags in samples if not flags & STOPPED])
    row["stopped_pass"] = spread([length for _, length, flags in samples if flags & STOPPED])
    return row


def load_samples(path=SAMPLES):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def table(rows):
    lines = ["| Chunk blocks | Arrivals | Load | Mode | Starts | Start delay p50 / p99 / p99.9 (ms) "
             "| Idle start delay p50 / p99 / p99.9 (ms) | Completed pass p50 / p99 / p99.9 (ms) |",
             "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        cells = [row["chunk_blocks"], row["arrival"], f"{row['load']:.1f}", row["mode"], row["starts"]]
        for key in ("start_delay", "start_delay_idle_at_arrival", "completed_pass"):
            cells.append(" / ".join("none" if row[key][f"{name}_ms"] is None
                                    else f"{row[key][name + '_ms']:.3f}" for name, _ in QUANTILES))
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    return "\n".join(lines)


def main():
    report = json.loads(SUMMARY.read_text(encoding="utf-8"))
    print(table(report["rows"]))


if __name__ == "__main__":
    main()
