"""Print the cooperative stop table from docs/evidence/gpu-cooperative-stop.json.

    python tools/gpu_stop_tables.py
"""
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "evidence" / "gpu-cooperative-stop.json"
MODES = ("plain", "synced", "stop")


def readings(report):
    found = []
    for row in report["rows"]:
        for mode in row["modes"].values():
            for run in mode["runs"]:
                for key in ("gpu_utilization_and_memory_before", "gpu_utilization_and_memory_after"):
                    found.append(tuple(float(part) for part in run[key].split(",")))
    return found


def main():
    report = json.loads(SOURCE.read_text(encoding="utf-8"))
    found = readings(report)
    utilization = [item[0] for item in found]
    memory = [item[1] for item in found]
    print(f"{len(found)} readings, utilization {min(utilization):.0f} to {max(utilization):.0f} percent, "
          f"median {statistics.median(utilization):.0f}, memory {min(memory):.0f} to {max(memory):.0f} MiB")
    print("| Chunk blocks | Arrivals | Load | Stale GPU s: plain | synced | stop "
          "| Stale share: plain | synced | stop | Pass ms: plain | synced | stop | Stopped per run |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in report["rows"]:
        means = {mode: row["modes"][mode]["means"] for mode in MODES}
        share = {mode: means[mode]["stale_gpu_seconds"] / means[mode]["busy_gpu_seconds"] for mode in MODES}
        cells = [row["chunk_blocks"], row["arrival"], f"{row['load']:.1f}"]
        cells += [f"{means[mode]['stale_gpu_seconds']:.2f}" for mode in MODES]
        cells += [f"{share[mode]:.2f}" for mode in MODES]
        cells += [f"{means[mode]['mean_adopted_pass_ms']:.2f}" for mode in MODES]
        cells.append(f"{means['stop']['stopped']:.0f}")
        print("| " + " | ".join(str(cell) for cell in cells) + " |")


if __name__ == "__main__":
    main()
