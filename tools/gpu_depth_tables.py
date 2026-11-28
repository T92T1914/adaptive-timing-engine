"""Summarise the saved chunk times by queue depth and print the table.

    python tools/gpu_depth_tables.py

The percentiles are the value at index int(share * count) of the sorted chunk times. The
first chunk of every pass is pooled with the rest. This file needs only the standard
library.
"""
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "evidence" / "gpu-depth-chunk-times.json"


def summarise(passes):
    """Summary of the saved passes of one chunk size and depth."""
    intervals = sorted(value for item in passes for value in item["chunk_ms"])
    hosts = [item["host_ms"] for item in passes]
    return {
        "passes": len(passes), "chunk_count": len(intervals),
        "chunk_mean_ms": statistics.fmean(intervals), "chunk_median_ms": statistics.median(intervals),
        "chunk_p5_ms": intervals[int(0.05 * len(intervals))],
        "chunk_p95_ms": intervals[int(0.95 * len(intervals))],
        "pass_host_mean_ms": statistics.fmean(hosts), "pass_host_median_ms": statistics.median(hosts),
        # Each pass also ends in one synchronize that the depth does not change.
        "synchronizes_per_pass": 1 + statistics.fmean(item["waits"] for item in passes),
    }


def table(rows):
    lines = ["| Chunk blocks | Depth | Chunk mean (ms) | Chunk median (ms) | Chunk p95 (ms) "
             "| Mean pass on the host (ms) | Synchronizes per pass |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        s = row["summary"]
        cells = [row["chunk_blocks"], "all" if row["depth"] is None else row["depth"],
                 f"{s['chunk_mean_ms']:.4f}", f"{s['chunk_median_ms']:.4f}", f"{s['chunk_p95_ms']:.4f}",
                 f"{s['pass_host_mean_ms']:.3f}", f"{s['synchronizes_per_pass']:.2f}"]
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    return "\n".join(lines)


def main():
    print(table(json.loads(SOURCE.read_text(encoding="utf-8"))["rows"]))


if __name__ == "__main__":
    main()
