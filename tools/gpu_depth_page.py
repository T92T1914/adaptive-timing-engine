"""Print the tables of docs/gpu-queue-depth.md from the saved runs.

    python tools/gpu_depth_page.py

Every miss of tools/gpu_depth_run_tables.py gets one of two marks.

A  The condition queues every chunk, so the model predicts no stop and the run stopped
   passes there. This contradicts the first assumption of the predictions, that a launch
   takes no time. It covers only the three metrics about stops.
?  Not explained.

The page also reports a replay of the same model, with the chunk times of each condition
stretched so that their mean pass equals the median completed pass measured in that
condition. It was made after the runs and takes the pass length from the run it is set
beside. One common length for every condition brings more misses within 25 percent, as
the control table shows, so the replay is an observation and gives no miss a reason. Why
the passes were longer is not explained.

The last table sizes part of the first study with the pass times that pass timing measured
later inside the worker. This file needs only the standard library.
"""
import importlib.util
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runs_tool = sibling("gpu_depth_run_tables")
model = sibling("gpu_depth_predictions")
first_model = sibling("gpu_queue_predictions")

KEYS = runs_tool.KEYS
METRICS = runs_tool.METRICS
STOP_METRICS = ("stopped_share_of_requests", "stale_after_newer_ms_per_stop", "stopped_pass_ms")
TITLES = {name: title for name, title, _ in runs_tool.STOP_COLUMNS + runs_tool.ADOPTION_COLUMNS}
DEPTHS = (1, 2, 4, 8, None)


def load(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def key_of(row):
    return tuple(row[name] for name in KEYS)


def label(depth):
    return "all" if depth is None else str(depth)


def queues_every_chunk(row):
    return row["depth"] is None or row["depth"] >= row["chunks_per_pass"]


def completed_passes_ms(condition):
    return [sample[1] / 1000.0 for run in condition["runs"] for sample in run["samples"]
            if not sample[5] & runs_tool.STOPPED]


def stretched(source_row, target_ms):
    """The saved chunk times scaled by one factor so their mean pass is target_ms."""
    factor = target_ms / statistics.fmean(sum(saved["chunk_ms"]) for saved in source_row["passes"])
    return {**source_row, "passes": [{"chunk_ms": [value * factor for value in saved["chunk_ms"]],
                                      "waits": saved["waits"]} for saved in source_row["passes"]]}


def inputs():
    return {"runs": load("gpu-depth-runs.json"), "predictions": load("gpu-depth-predictions.json"),
            "chunk_times": load("gpu-depth-chunk-times.json"),
            "samples": runs_tool.load_samples()}


def replay(data, average=statistics.median, fixed_ms=None):
    """The predictions again with the completed pass of each condition as its pass length.

    With fixed_ms every condition gets that one length instead, which is the control.
    """
    units = model.time_units(data["chunk_times"])
    source = {(row["chunk_blocks"], row["depth"]): row for row in data["chunk_times"]["rows"]}
    samples = {key_of(item): item for item in data["samples"]["conditions"]}
    result = {}
    for row in data["predictions"]["rows"]:
        size, depth = row["chunk_blocks"], row["depth"]
        length = fixed_ms if fixed_ms is not None else average(completed_passes_ms(samples[key_of(row)]))
        again = model.predict_condition(stretched(source[(size, depth)], length), units[size],
                                        row["arrival"], row["load"])
        result[key_of(row)] = again["predicted"]
    return result


def marked_rows(data, replayed):
    """The comparison rows with a mark on every miss and what the replay did in every cell."""
    rows = runs_tool.comparisons(data["runs"], data["predictions"])
    measured = {key_of(row): row["measured"] for row in data["runs"]["rows"]}
    for row in rows:
        key = key_of(row)
        for name, result in row["metrics"].items():
            held = not runs_tool.compare(replayed[key][name], measured[key][name])["miss"]
            result["replay_holds"] = held
            if not result["miss"]:
                result["mark"] = ""
            elif queues_every_chunk(row) and name in STOP_METRICS and result["predicted"] in (0, None):
                result["mark"] = "A"
            else:
                result["mark"] = "?"
    return rows


def replay_reaches(result):
    """A miss outside A that the replay brings within 25 percent."""
    return result["miss"] and result["mark"] != "A" and result["replay_holds"]


def reason_counts(rows, mean_rows):
    out = {}
    for name in METRICS:
        results = [row["metrics"][name] for row in rows]
        out[name] = {
            "missed": sum(r["miss"] for r in results),
            **{mark: sum(r["mark"] == mark for r in results) for mark in ("A", "?")},
            "replay_within": sum(map(replay_reaches, results)),
            "replay_within_with_mean": sum(replay_reaches(row["metrics"][name]) for row in mean_rows),
            "held_then_replay_missed": sum(not r["miss"] and not r["replay_holds"] for r in results),
        }
    return out


def reason_table(counts):
    lines = ["| Metric | Missed of 120 | A | Not explained | Within 25 percent in the replay, outside A "
             "| The same with the mean pass instead | Held before, missed by the replay |",
             "|---|---|---|---|---|---|---|"]
    keys = ("missed", "A", "?", "replay_within", "replay_within_with_mean", "held_then_replay_missed")
    for name in METRICS:
        lines.append(f"| {TITLES[name]} | " + " | ".join(str(counts[name][key]) for key in keys) + " |")
    totals = [sum(counts[name][key] for name in METRICS) for key in keys]
    lines.append("| All | " + " | ".join(str(value) for value in totals) + " |")
    return "\n".join(lines)


def common_length_ms(data):
    """The median of every completed pass of the depth runs together."""
    return statistics.median(value for condition in data["samples"]["conditions"]
                             for value in completed_passes_ms(condition))


def control_counts(rows):
    """How many misses outside A a replay brings within 25 percent, and how many cells it misses."""
    results = [result for row in rows for result in row["metrics"].values()]
    return {"within": sum(map(replay_reaches, results)),
            "replay_missed": sum(not r["replay_holds"] for r in results)}


def controls(data, own_rows):
    """The replay with each condition's own pass beside replays with one length for all."""
    common = common_length_ms(data)
    out = [("The median completed pass of each condition", control_counts(own_rows))]
    for title, length in ((f"{common:.3f} ms for all, the median of every completed pass", common),
                          ("3.500 ms for all", 3.5), ("4.500 ms for all", 4.5)):
        out.append((title, control_counts(marked_rows(data, replay(data, fixed_ms=length)))))
    return out


def control_table(entries):
    lines = ["| Pass length in the replay | Misses outside A brought within 25 percent "
             "| Cells of 960 the replay misses |", "|---|---|---|"]
    for title, counts in entries:
        lines.append(f"| {title} | {counts['within']} | {counts['replay_missed']} |")
    return "\n".join(lines)


def comparison_table(rows, columns, start_delay=False):
    """The table of gpu_depth_run_tables with the mark after each star."""
    plain = runs_tool.table(rows, columns, start_delay).split("\n")
    out = plain[:2]
    for line, row in zip(plain[2:], rows):
        cells = line.split(" | ")
        for position, (name, _, _) in enumerate(columns, 4):
            mark = row["metrics"][name]["mark"]
            if mark:
                end = " |" if cells[position].endswith(" |") else ""
                cells[position] = cells[position].removesuffix(" |") + f" {mark}" + end
        out.append(" | ".join(cells))
    return "\n".join(out)


def trade(data):
    """Each chunk size and depth over its six arrival conditions."""
    comparisons = runs_tool.comparisons(data["runs"], data["predictions"])
    samples = {key_of(item): item for item in data["samples"]["conditions"]}
    source = {(row["chunk_blocks"], row["depth"]): row for row in data["chunk_times"]["rows"]}
    out = []
    for size in sorted({row["chunk_blocks"] for row in comparisons}):
        for depth in DEPTHS:
            group = [row for row in comparisons if (row["chunk_blocks"], row["depth"]) == (size, depth)]
            entry = {"chunk_blocks": size, "depth": depth, "chunks_per_pass": group[0]["chunks_per_pass"],
                     "back_to_back_ms": statistics.fmean(
                         sum(saved["chunk_ms"]) for saved in source[(size, depth)]["passes"])}
            lengths = [statistics.median(completed_passes_ms(samples[key_of(row)])) for row in group]
            entry["back_to_back_host_ms"] = source[(size, depth)]["summary"]["pass_host_mean_ms"]
            entry["above_host"] = sum(value > entry["back_to_back_host_ms"] for value in lengths)
            entry["completed_pass_ms"] = (min(lengths), max(lengths))
            entry["fewest_completed"] = min(len(completed_passes_ms(samples[key_of(row)])) for row in group)
            for name in ("stale_after_newer_ms_per_stop", "synchronizes_per_pass", "stale_gpu_time_share",
                         "mean_adopted_age_ms"):
                for side in ("predicted", "measured"):
                    values = [row["metrics"][name][side] for row in group
                              if row["metrics"][name][side] is not None]
                    entry[f"{name}_{side}"] = ((statistics.median(values), min(values), max(values),
                                                len(values)) if values else None)
            out.append(entry)
    return out


def trade_table(entries):
    lines = ["| Chunk blocks | Depth | Synchronizes per pass | Stale after the newer request per stop (ms) "
             "| Stale share of GPU time | Adopted age (ms) |", "|---|---|---|---|---|---|"]
    columns = (("synchronizes_per_pass", 2), ("stale_after_newer_ms_per_stop", 3),
               ("stale_gpu_time_share", 3), ("mean_adopted_age_ms", 3))
    for entry in entries:
        cells = [str(entry["chunk_blocks"]), label(entry["depth"])]
        for name, digits in columns:
            value = entry[f"{name}_measured"]
            cells.append("none" if value is None else
                         f"{value[0]:.{digits}f} ({value[1]:.{digits}f} to {value[2]:.{digits}f})")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def pass_table(entries):
    lines = ["| Chunk blocks | Depth | Pass in the model, card time (ms) | Back to back pass, host time (ms) "
             "| Median completed pass in the worker, host time, six conditions (ms) "
             "| Fewest completed passes in one condition |", "|---|---|---|---|---|---|"]
    for entry in entries:
        low, high = entry["completed_pass_ms"]
        lines.append(f"| {entry['chunk_blocks']} | {label(entry['depth'])} | {entry['back_to_back_ms']:.3f} "
                     f"| {entry['back_to_back_host_ms']:.3f} | {low:.3f} to {high:.3f} "
                     f"| {entry['fewest_completed']} |")
    return "\n".join(lines)


def late_stops(data):
    """Where every chunk is queued, how long after the start a request still stopped a pass.

    A stop needs the newer request before the last look of the pass, so launches were
    still going on when that request arrived.
    """
    newer, medians = [], []
    for condition in data["samples"]["conditions"]:
        if not queues_every_chunk(condition):
            continue
        found = [sample[2] / 1000.0 for run in condition["runs"] for sample in run["samples"]
                 if sample[5] & runs_tool.STOPPED]
        if found:
            newer += found
            medians.append(statistics.median(found))
    return {"conditions": len(medians), "stops": len(newer), "median_ms": statistics.median(newer),
            "low_median_ms": min(medians), "high_median_ms": max(medians), "max_ms": max(newer)}


def first_study_sizing():
    """The Poisson cells of the first study again at the load the measured pass implies.

    The pass is the plain pass inside the worker from gpu-pass-timing.json, taken on a
    later day than the first study's runs, at the two loads it covers.
    """
    timing = load("gpu-pass-timing.json")
    samples = runs_tool.load_samples(EVIDENCE / timing["samples_file"])
    committed = load("gpu-queue-predictions.json")
    arrivals = load("gpu-arrival-runs.json")
    out = []
    for condition in samples["conditions"]:
        if condition["mode"] != "plain" or condition["arrival"] != "poisson":
            continue
        size, nominal = condition["chunk_blocks"], condition["load"]
        passes = [sample[1] / 1000.0 for block in condition["blocks"] for sample in block["samples"]]
        mean = statistics.fmean(passes)
        assumed = committed["mean_pass_ms"][str(size)]
        real = nominal * mean / assumed
        again = first_model.predict(real, passes)
        # The first study states ages in assumed passes.
        again["mean_adopted_age"] *= mean / assumed
        match = (size, "poisson", nominal)
        before = next(row for row in committed["rows"]
                      if (row["chunk_blocks"], row["arrival"], row["load"]) == match)["predicted"]
        row = next(row for row in arrivals["rows"]
                   if (row["chunk_blocks"], row["arrival"], row["load"]) == match)
        metrics = {name: (before[name], again[name], statistics.fmean(run[name] for run in row["runs"]))
                   for name in ("computed_share", "mean_adopted_age", "stale_gpu_time_share")}
        out.append({"chunk_blocks": size, "load": nominal, "assumed_ms": assumed, "pass_ms": mean,
                    "real_load": real, "metrics": metrics})
    return sorted(out, key=lambda item: (item["chunk_blocks"], item["load"]))


def sizing_table(entries):
    lines = ["| Chunk blocks | Load | Assumed pass (ms) | Measured mean pass (ms) | Load in measured passes "
             "| Computed share | Mean adopted age (assumed passes) | Stale GPU time |",
             "|---|---|---|---|---|---|---|---|"]
    for item in entries:
        cells = [str(item["chunk_blocks"]), f"{item['load']:.1f}", f"{item['assumed_ms']:.2f}",
                 f"{item['pass_ms']:.2f}", f"{item['real_load']:.3f}"]
        cells += [" / ".join(f"{value:.3f}" for value in item["metrics"][name])
                  for name in ("computed_share", "mean_adopted_age", "stale_gpu_time_share")]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def reading_ranges():
    """Lowest and highest utilization and memory read around the runs of each study used here."""
    out = {}
    for name, runs_key in (("gpu-depth-chunk-times.json", None), ("gpu-depth-runs.json", "runs"),
                           ("gpu-pass-timing.json", "blocks")):
        report = load(name)
        items = report["readings"] if runs_key is None else [run for row in report["rows"]
                                                             for run in row[runs_key]]
        readings = [[float(part) for part in item[f"utilization_memory_clock_{side}"].split(",")]
                    for item in items for side in ("before", "after")]
        out[name] = {"readings": len(readings),
                     **{field: (min(r[i] for r in readings), max(r[i] for r in readings))
                        for i, field in enumerate(("utilization", "memory", "clock"))}}
    return out


def facts():
    """The figures the text of the page states outside its tables."""
    data = inputs()
    replayed = replay(data)
    rows = marked_rows(data, replayed)
    entries = trade(data)
    sizing = first_study_sizing()
    timing = load("gpu-pass-timing.json")
    runs = data["runs"]

    def span(values):
        values = list(values)
        return (min(values), max(values))

    def direction(name, replay_missed_only=False):
        found = [row["metrics"][name] for row in rows]
        found = [r for r in found if r["miss"] and r["relative"] is not None
                 and not (replay_missed_only and r["replay_holds"])]
        above = sum(r["measured"] > r["predicted"] for r in found)
        return {"above": above, "below": len(found) - above}

    periodic = [row for row in rows if row["arrival"] == "periodic" and row["load"] == 0.9
                and row["metrics"]["adopted_share"]["predicted"] >= 0.53]
    again = [replayed[key_of(row)]["adopted_share"]["mean"] for row in periodic]
    out = {
        "periodic_load_0.9": {
            "conditions": len(periodic),
            "predicted": span(row["metrics"]["adopted_share"]["predicted"] for row in periodic),
            "measured": span(row["metrics"]["adopted_share"]["measured"] for row in periodic),
            "replay_below_0.07": sum(value < 0.07 for value in again),
            "replay_otherwise": span(value for value in again if value >= 0.07)},
        "stale_after_newer_direction": direction("stale_after_newer_ms_per_stop"),
        "synchronize_direction": direction("synchronizes_per_pass"),
        "synchronize_direction_replay_missed": direction("synchronizes_per_pass", replay_missed_only=True),
        "stale_share_medians": span(entry["stale_gpu_time_share_measured"][0] for entry in entries),
        "conditions_above_host_back_to_back": sum(entry["above_host"] for entry in entries),
        "starts": {"total": sum(row["starts"] for row in runs["rows"]),
                   "per_condition": span(row["starts"] for row in runs["rows"])},
        "adopted_check_mismatches": sum(abs(run["worker"]["adopted_check_mismatch"])
                                        for row in runs["rows"] for run in row["runs"]),
        "start_delay_ms": {name: span(row["start_delay"][name] for row in runs["rows"])
                           for name in ("p50_ms", "p99_ms", "p99_9_ms")},
        "sizing_gap_percent": {
            name: {side: span(100 * abs(e["metrics"][name][i] - e["metrics"][name][2]) / e["metrics"][name][2]
                              for e in sizing) for i, side in enumerate(("committed", "at_measured_load"))}
            for name in ("computed_share", "mean_adopted_age", "stale_gpu_time_share")},
        "pass_timing_completed_p50_ms": {
            f"{mode} {arrival}": span(row["completed_pass"]["p50_ms"] for row in timing["rows"]
                                      if (row["mode"], row["arrival"]) == (mode, arrival))
            for mode in ("plain", "stop") for arrival in ("poisson", "bursty")},
        "revisions": {"chunk_times": data["chunk_times"]["revision"], "predictions": runs["predictions_commit"],
                      "depth_runs": runs["revision"], "pass_timing": timing["revision"]},
    }
    return out


def tables():
    """Every table of the page, by name, in the order the page holds them."""
    data = inputs()
    rows = marked_rows(data, replay(data))
    mean_rows = marked_rows(data, replay(data, statistics.fmean))
    entries = trade(data)
    return {
        "trade": trade_table(entries),
        "reasons": reason_table(reason_counts(rows, mean_rows)),
        "control": control_table(controls(data, rows)),
        "pass": pass_table(entries),
        "stops": comparison_table(rows, runs_tool.STOP_COLUMNS),
        "adoption": comparison_table(rows, runs_tool.ADOPTION_COLUMNS, start_delay=True),
        "sizing": sizing_table(first_study_sizing()),
    }


def main():
    for name, table in tables().items():
        print(f"[{name}]\n{table}\n")
    print("[late stops]", json.dumps(late_stops(inputs())))
    print("[readings]", json.dumps(reading_ranges()))
    print("[facts]", json.dumps(facts()))


if __name__ == "__main__":
    sys.exit(main())
