"""Execute the committed bounded protocol once into a new directory."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing import CausalScheduler, Executor, Information, Policy, build_causal_plan, build_plan
from adaptive_timing.experiment import SCENARIOS, percentile, workload

COUNT = 120
SEEDS = (7, 42, 73)
PATTERNS = ("rolling", "revisions")


def stream(scenario: str, pattern: str, count: int = COUNT):
    messages = []
    for index, event in enumerate(workload(scenario, count)):
        late = pattern == "revisions" and index % 11 == 0
        changes = [(event.target + (.06 if late else -.08), "announce", event)]
        if pattern == "revisions" and not late:
            if index % 10 == 0:
                changes.append((event.target - .02, "update",
                                replace(event, target=event.target + .03, deadline=event.deadline + .03)))
            if index % 13 == 0:
                changes.append((event.target - .01, "cancel", None))
        for version, (at, kind, snapshot) in enumerate(changes, 1):
            tick = max(0, math.ceil((at - 1e-10) * 100))
            messages.append(Information(tick / 100, len(messages), max(0, tick / 100 - .005),
                                        kind, event.id, version, snapshot))
    return tuple(sorted(messages, key=lambda row: (row.available_at, row.sequence)))


def mean(values):
    return statistics.fmean(values) if values else None


def quantile(values, fraction):
    return percentile(values, fraction) if values else None


def run_case(messages, seed, mode):
    groups = {}
    final = {}
    canceled = set()
    deadlines = []
    for row in messages:
        groups.setdefault(round(row.available_at * 100), []).append(row)
        if row.event:
            final[row.id] = row.event
            deadlines.append(row.event.deadline)
        else:
            canceled.add(row.id)
            final.pop(row.id, None)
    end_tick = math.ceil((max(deadlines + [row.available_at for row in messages]) + .2) * 100)
    horizon = end_tick / 100
    plans, dispatches, changes = [], [], []
    planned, admitted, evaluated = 0, set(), 0
    calculation_ms = 0.0
    executor = Executor() if mode == "offline" else CausalScheduler(seed=seed)
    statuses = {event_id: "canceled" for event_id in canceled} if mode == "offline" else {}

    def record(plan, at, events_evaluated):
        nonlocal planned, evaluated
        planned += len(plan.scheduled)
        evaluated += events_evaluated
        admitted.update(row.event.id for row in plan.scheduled)
        plans.append({"at": at, "plan": asdict(plan)})

    if mode == "offline":
        started = time.perf_counter()
        plan = build_plan(tuple(final.values()), seed=seed)
        calculation_ms += 1000 * (time.perf_counter() - started)
        executor.install(plan)
        record(plan, 0.0, len(final))
        statuses.update({row.event.id: "rejected" for row in plan.rejected})
    # Only the driver sees this complete stream. The causal session receives one group.
    for tick in range(end_tick + 1):
        at = tick / 100
        if mode == "causal" and tick in groups:
            facts = tuple(groups[tick])
            applied = executor.receive(facts, now=at)
            changes.extend({"at": at, "id": row.id, "version": row.version, "status": status}
                           for row, status in zip(facts, applied))
            request = executor.snapshot(at)
            started = time.perf_counter()
            result = build_causal_plan(request)
            calculation_ms += 1000 * (time.perf_counter() - started)
            if not executor.install(result):
                raise RuntimeError("synchronous current result was not installed")
            record(result.plan, at, len(request.events))
        if tick % 2 == 0 or tick == end_tick:
            for row in executor.poll(at, budget=32):
                dispatches.append(asdict(row))
                statuses[row.id] = row.status
    if mode == "causal":
        statuses = dict(executor.outcomes())
    ids = sorted({row.id for row in messages})
    terminal = {event_id: statuses.get(event_id, "unknown") for event_id in ids}
    # Offset uses the event version installed when dispatch occurred, not a later update.
    latest = {}
    event_at_dispatch = {}
    timeline = [(row["at"], 0, row) for row in plans]
    timeline += [(row["actual_start"], 1, row) for row in dispatches if row["status"] == "dispatched"]
    for at, kind, row in sorted(timeline, key=lambda item: (item[0], item[1])):
        if kind == 0:
            latest = {item["event"]["id"]: item["event"] for item in row["plan"]["scheduled"]}
        else:
            event_at_dispatch[row["id"]] = latest[row["id"]]
    dispatched = [row for row in dispatches if row["status"] == "dispatched"]
    offsets = {row["id"]: abs(row["actual_start"] - event_at_dispatch[row["id"]]["target"]) * 1000
               for row in dispatched}
    per_resource = {resource: sum(row["finish"] - row["actual_start"] for row in dispatched
                                 if row["resource"] == resource)
                    for resource in sorted({row.event.resource for row in messages if row.event})}
    admission_rows = [row for decision in plans for row in decision["plan"]["scheduled"]]
    counts = {status: sum(value == status for value in terminal.values())
              for status in ("rejected", "expired", "dispatched", "canceled", "unknown")}
    if sum(counts.values()) != len(ids):
        raise RuntimeError("terminal states do not partition event IDs")
    return {"mode": mode, "seed": seed, "events": len(ids), "horizon_seconds": horizon,
            "planned": planned, "admitted": len(admitted), **counts, "completed": None,
            "modeled_service_finished": sum(row["finish"] <= horizon for row in dispatched),
            "ignored_terminal": sum(row["status"] == "ignored_terminal" for row in changes),
            "offset_p50_ms": quantile(list(offsets.values()), .5),
            "offset_p95_ms": quantile(list(offsets.values()), .95),
            "busy_seconds": sum(per_resource.values()),
            "resource_utilization": {key: value / horizon for key, value in per_resource.items()},
            "mean_plan_density": mean([row["density"] for row in admission_rows]),
            "mean_plan_fatigue": mean([row["fatigue"] for row in admission_rows]),
            "mean_plan_sigma_ms": mean([row["sigma"] * 1000 for row in admission_rows]),
            "event_evaluations": evaluated, "calculations": len(plans), "calculation_ms": calculation_ms,
            "offsets_by_id_ms": offsets, "terminal": terminal, "plans": plans,
            "execution": dispatches, "changes": changes}


def run(checkpoint=None):
    cases, pairs, streams = [], [], {}
    for scenario in SCENARIOS:
        for pattern in PATTERNS:
            key = f"{scenario}/{pattern}"
            messages = stream(scenario, pattern)
            streams[key] = [asdict(row) for row in messages]
            for seed in SEEDS:
                pair = [run_case(messages, seed, mode) | {"scenario": scenario, "pattern": pattern}
                        for mode in ("offline", "causal")]
                cases.extend(pair)
                offline, causal = pair
                shared = sorted(set(offline["offsets_by_id_ms"]) & set(causal["offsets_by_id_ms"]))
                pairs.append({"scenario": scenario, "pattern": pattern, "seed": seed,
                    "shared_dispatched": len(shared),
                    "shared_mean_offset_difference_ms": mean([causal["offsets_by_id_ms"][key]
                        - offline["offsets_by_id_ms"][key] for key in shared]),
                    **{f"{field}_difference": causal[field] - offline[field]
                       for field in ("admitted", "rejected", "expired", "dispatched", "canceled",
                                     "unknown", "busy_seconds", "event_evaluations")}})
                if checkpoint:
                    checkpoint({"streams": streams, "cases": cases, "pairs": pairs})
    return {"streams": streams, "cases": cases, "pairs": pairs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise SystemExit("Refusing to evaluate an uncommitted checkout")
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {"schema_version": 1, "evaluated_revision": revision,
        "protocol_sha256_lf": hashlib.sha256((ROOT / "docs/causal-protocol.md").read_text(encoding="utf-8").encode()).hexdigest(),
        "conditions": {"python": platform.python_version(), "platform": platform.platform(),
            "policy": asdict(Policy()), "event_count": COUNT, "seeds": SEEDS,
            "scenarios": SCENARIOS, "patterns": PATTERNS, "poll_interval_seconds": .02,
            "poll_budget": 32, "processes": 1,
            "scope": "Synthetic virtual execution, no observed external completion or OS latency."}}
    (args.output / "run-state.json").write_text(json.dumps(metadata | {"status": "running"}, indent=2) + "\n", encoding="utf-8")
    try:
        def checkpoint(partial):
            (args.output / "partial.json").write_text(json.dumps(metadata | partial,
                separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
        report = metadata | run(checkpoint)
        (args.output / "raw.json").write_text(json.dumps(report, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
        excluded = {"plans", "execution", "changes", "terminal", "offsets_by_id_ms"}
        summary = {key: value for key, value in report.items() if key != "streams"}
        summary["cases"] = [{key: value for key, value in case.items() if key not in excluded} for case in report["cases"]]
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except BaseException as exc:
        (args.output / "run-state.json").write_text(json.dumps(metadata | {"status": "failed", "error": str(exc)}, indent=2) + "\n", encoding="utf-8")
        raise
    (args.output / "run-state.json").write_text(json.dumps(metadata | {"status": "completed"}, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(report['pairs'])} pairs at {revision} in {args.output}")


if __name__ == "__main__":
    main()
