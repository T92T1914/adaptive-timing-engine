"""Summarise the depth runs and set each condition beside its prediction.

    python tools/gpu_depth_run_tables.py

The runs are in docs/evidence/gpu-depth-runs.json, and one sample for every calculation
that started is in docs/evidence/gpu-depth-runs-samples.json.gz. The predictions are
docs/evidence/gpu-depth-predictions.json. A measured mean misses when it differs from the
predicted mean by more than 25 percent of the predicted mean. A prediction of zero misses
any measurement above zero, and a metric that one side has and the other lacks is a miss
too. The percentiles are nearest rank. This file needs only the standard library, so the
comparison can be checked on a machine with no GPU.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "docs" / "evidence" / "gpu-depth-runs.json"
SAMPLES = ROOT / "docs" / "evidence" / "gpu-depth-runs-samples.json.gz"
PREDICTIONS = ROOT / "docs" / "evidence" / "gpu-depth-predictions.json"

# The metrics of tools/gpu_depth_predictions.py, in its order.
METRICS = ("computed_share", "adopted_share", "mean_adopted_age_ms", "stopped_share_of_requests",
           "stale_after_newer_ms_per_stop", "stopped_pass_ms", "stale_gpu_time_share",
           "synchronizes_per_pass")
MISS_SHARE = 0.25

# Flags of one sample.
ADOPTED = 1
STOPPED = 2
IDLE_AT_ARRIVAL = 4

QUANTILES = (("p50", 500), ("p99", 990), ("p99_9", 999))
TAIL_COUNT = 10_000
KEYS = ("chunk_blocks", "chunks_per_pass", "depth", "arrival", "load")


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


def samples_from(passes):
    """Turn the pass records of arrival_run.run_case into samples in start order.

    A sample is [delay_us, pass_us, newer_us, waits, launched, flags] in whole
    microseconds. delay_us runs from the request's arrival to the start of its pass,
    pass_us from that start to the return of the closing synchronize, and newer_us from
    the start to the arrival of the next request, or None for the last request. A start
    counts as idle at arrival when no earlier pass was still between its own start and end
    timestamps when the request arrived.
    """
    out = []
    previous_end = None
    for item in sorted(passes, key=lambda record: record["start"]):
        flags = (ADOPTED if item["adopted"] else 0) | (STOPPED if item["stopped"] else 0)
        if previous_end is None or previous_end <= item["arrived"]:
            flags |= IDLE_AT_ARRIVAL
        newer = None if item["next_arrived"] is None else round((item["next_arrived"] - item["start"]) * 1e6)
        out.append([round((item["start"] - item["arrived"]) * 1e6), round((item["end"] - item["start"]) * 1e6),
                    newer, item["waits"], item["launched"], flags])
        previous_end = item["end"]
    return out


def mean_or_none(values):
    return statistics.fmean(values) if values else None


def run_metrics(samples, requests):
    """The metrics of one run, defined as in the predictions, from its samples."""
    adopted = [s for s in samples if s[5] & ADOPTED]
    stopped = [s for s in samples if s[5] & STOPPED]
    busy = sum(s[1] for s in samples)
    return {
        "computed_share": len(samples) / requests,
        "adopted_share": len(adopted) / requests,
        "mean_adopted_age_ms": mean_or_none([(s[0] + s[1]) / 1000.0 for s in adopted]),
        "stopped_share_of_requests": len(stopped) / requests,
        # A stopped pass always has a newer request, since that request is what stopped it.
        "stale_after_newer_ms_per_stop": mean_or_none([(s[1] - s[2]) / 1000.0 for s in stopped]),
        "stopped_pass_ms": mean_or_none([s[1] / 1000.0 for s in stopped]),
        "stale_gpu_time_share": (sum(s[1] for s in samples if not s[5] & ADOPTED) / busy) if busy else None,
        "synchronizes_per_pass": mean_or_none([1 + s[3] for s in samples]),
    }


def summarise(condition):
    """One row from the runs of one condition. Each run holds its requests and samples."""
    per_run = [run_metrics(run["samples"], run["requests"]) for run in condition["runs"]]
    samples = [sample for run in condition["runs"] for sample in run["samples"]]
    row = {key: condition[key] for key in KEYS}
    row["requests"] = sum(run["requests"] for run in condition["runs"])
    row["starts"] = len(samples)
    row["tail_rests_on_fewer_than_10000_starts"] = len(samples) < TAIL_COUNT
    measured = {}
    for name in METRICS:
        values = [metrics[name] for metrics in per_run]
        present = [value for value in values if value is not None]
        measured[name] = {"mean": statistics.fmean(present), "runs": values} if present else None
    row["measured"] = measured
    row["start_delay"] = spread([s[0] for s in samples])
    row["start_delay_idle_at_arrival"] = spread([s[0] for s in samples if s[5] & IDLE_AT_ARRIVAL])
    return row


def compare(predicted, measured):
    """Verdict for one metric. Each side is the dict saved for it, or None when it has no value."""
    if predicted is None and measured is None:
        return {"predicted": None, "measured": None, "relative": None, "verdict": "neither", "miss": False}
    if predicted is None or measured is None:
        verdict = "none predicted" if predicted is None else "none measured"
        return {"predicted": None if predicted is None else predicted["mean"],
                "measured": None if measured is None else measured["mean"],
                "relative": None, "verdict": verdict, "miss": True}
    expected, found = predicted["mean"], measured["mean"]
    if expected:
        relative = (found - expected) / abs(expected)
        miss = abs(relative) > MISS_SHARE
    else:
        relative = None
        miss = found != 0
    return {"predicted": expected, "measured": found, "relative": relative,
            "verdict": "miss" if miss else "within 25 percent", "miss": miss}


def comparisons(report, predictions):
    """One row for every condition, in the order of the predictions."""
    found = {tuple(row[key] for key in KEYS): row for row in report["rows"]}
    rows = []
    for expected in predictions["rows"]:
        key = tuple(expected[name] for name in KEYS)
        row = found[key]
        rows.append({**{name: expected[name] for name in KEYS},
                     "metrics": {name: compare(expected["predicted"][name], row["measured"][name])
                                 for name in METRICS},
                     "start_delay": row["start_delay"], "starts": row["starts"]})
    return rows


def predictions_digest(predictions):
    """SHA-256 of the predictions as canonical JSON, so line endings do not change it."""
    canonical = json.dumps(predictions, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def load_samples(path=SAMPLES):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def cell(result, digits):
    def show(value):
        return "none" if value is None else f"{value:.{digits}f}"
    return f"{show(result['predicted'])} / {show(result['measured'])}{' *' if result['miss'] else ''}"


STOP_COLUMNS = (("stopped_share_of_requests", "Stopped share of requests", 3),
                ("stale_after_newer_ms_per_stop", "Stale after the newer request per stop (ms)", 3),
                ("stopped_pass_ms", "Stopped pass (ms)", 3),
                ("synchronizes_per_pass", "Synchronizes per pass", 2))
ADOPTION_COLUMNS = (("computed_share", "Computed share", 3), ("adopted_share", "Adopted share", 3),
                    ("mean_adopted_age_ms", "Adopted age (ms)", 3),
                    ("stale_gpu_time_share", "Stale share of GPU time", 3))


def table(rows, columns, start_delay=False):
    head = ["Chunk blocks", "Depth", "Arrivals", "Load"] + [title for _, title, _ in columns]
    if start_delay:
        head += ["Starts", "Start delay p50 / p99 / p99.9 (ms)"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in rows:
        cells = [row["chunk_blocks"], "all" if row["depth"] is None else row["depth"], row["arrival"],
                 f"{row['load']:.1f}"]
        cells += [cell(row["metrics"][name], digits) for name, _, digits in columns]
        if start_delay:
            delay = row["start_delay"]
            cells += [row["starts"], " / ".join("none" if delay[f"{name}_ms"] is None
                                                else f"{delay[name + '_ms']:.3f}" for name, _ in QUANTILES)]
        lines.append("| " + " | ".join(str(value) for value in cells) + " |")
    return "\n".join(lines)


def miss_counts(rows):
    return {name: sum(row["metrics"][name]["miss"] for row in rows) for name in METRICS}


def main():
    report = json.loads(RUNS.read_text(encoding="utf-8"))
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    rows = comparisons(report, predictions)
    print("Each cell is predicted / measured. A star marks a miss over 25 percent.\n")
    print(table(rows, STOP_COLUMNS))
    print()
    print(table(rows, ADOPTION_COLUMNS, start_delay=True))
    print()
    for name, count in miss_counts(rows).items():
        print(f"{name}: {count} of {len(rows)} missed")


if __name__ == "__main__":
    main()
