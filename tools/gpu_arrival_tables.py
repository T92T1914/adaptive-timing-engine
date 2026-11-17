"""Print the tables of docs/gpu-queue-study.md from the saved arrival run.

Every number in those tables comes from docs/evidence/gpu-arrival-runs.json, so the
document and the file cannot drift apart. Standard library only.

    python tools/gpu_arrival_tables.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "docs" / "evidence" / "gpu-arrival-runs.json"
ARRIVALS = ("poisson", "periodic", "bursty")


def cell(row: dict, name: str) -> str:
    """Predicted and measured, with a star when the measured mean is within noise."""
    entry = row[name]
    mark = "" if entry["verdict"] == "miss" else "\\*"
    return f"{row['predicted'][name]:.3f} | {entry['measured']['mean']:.3f}{mark}"


def tables(report: dict) -> str:
    out = []
    for arrival in ARRIVALS:
        out.append(f"### {arrival.capitalize()} arrivals\n")
        out.append("| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured "
                   "| Stale GPU time predicted | measured |")
        out.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in report["rows"]:
            if row["arrival"] != arrival:
                continue
            out.append(f"| {row['chunk_blocks']} | {row['load']} | {cell(row, 'computed_share')} "
                       f"| {cell(row, 'mean_adopted_age')} | {cell(row, 'stale_gpu_time_share')} |")
        out.append("")
    return "\n".join(out)


def counts(report: dict) -> str:
    rows = report["rows"]
    within = sum(1 for row in rows for name in ("computed_share", "mean_adopted_age", "stale_gpu_time_share")
                 if row[name]["verdict"] != "miss")
    total = len(rows) * 3
    mismatch = sum(abs(run["adopted_check_mismatch"]) for row in rows for run in row["runs"])
    return f"{within} of {total} comparisons are within noise, and the adopted check disagreed with the worker's own count {mismatch} times."


def main() -> int:
    report = json.loads(RUNS.read_text(encoding="utf-8"))
    print(counts(report))
    print()
    print(tables(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
