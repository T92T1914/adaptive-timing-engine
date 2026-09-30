"""Inspect retained CUDA application output using a separate scalar host oracle."""
import argparse
import json
from pathlib import Path


def validate(record):
    assert record["schema_version"] == 1
    inputs = record["inputs"]
    for image in inputs.values():
        assert len(image) == 11 and all(len(row) == 17 for row in image)
        assert all(type(value) is int and 0 <= value <= 255 for row in image for value in row)
    def histogram(image):
        bins = [[[0] * 256 for _ in range(4)] for _ in range(3)]
        for r, row in enumerate(image):
            for c, value in enumerate(row):
                bins[r // 4][c // 5][value] += 1
        return bins
    rows = {row["dispatch"]["id"]: row for row in record["results"]}
    assert len(rows) == len(record["results"]) == 7
    executed = ["obsolete-snapshot", "current-repeat", "running-cancel-request", "shutdown-running"]
    for sequence, ident in enumerate(executed, 1):
        row = rows[ident]
        assert row["status"] == "completed"
        assert row["value"]["request_id"] == sequence
        image = inputs["original" if ident == "obsolete-snapshot" else "current"]
        assert row["value"]["value"] == histogram(image), f"full histogram differs for {ident}"
        assert all(value >= 0 for value in row["value"]["timing"].values())
        assert row["submitted_at"] <= row["started_at"] <= row["completed_at"] <= row["reconciled_at"]
    assert rows["obsolete-snapshot"]["stale"] and not rows["obsolete-snapshot"]["cancellation_requested"]
    assert not rows["current-repeat"]["stale"] and not rows["current-repeat"]["cancellation_requested"]
    for ident in ("running-cancel-request", "shutdown-running"):
        assert rows[ident]["cancellation_requested"]
    for ident in ("queued-cancel", "shutdown-queued"):
        assert rows[ident]["status"] == "canceled" and rows[ident]["started_at"] is None
        assert rows[ident]["value"] is None
    assert rows["synthetic-host-failure"]["status"] == "failed"
    assert "synthetic host exception" in rows["synthetic-host-failure"]["error"]
    observed = record["observations"]
    assert [row["at"] for row in observed] == sorted(row["at"] for row in observed)
    for ident, row in rows.items():
        events = [event for event in observed if event["id"] == ident]
        assert events[0]["kind"] == "submitted" and events[-1]["kind"] == "reconciled"
        assert events[-1]["stale"] == row["stale"]
        if ident in executed:
            assert sum(event["kind"] == "completed" for event in events) == 1
    assert all(record["checks"].values())
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    record = json.loads(args.trace.read_text())
    rows = validate(record)
    print("ID | physical outcome | cancellation requested | obsolete")
    for ident, row in rows.items():
        print(f'{ident} | {row["status"]} | {row["cancellation_requested"]} | {row["stale"]}')
    print("All four full GPU outputs match the separate scalar oracle. Seven outcomes reconciled.")
    print("Host and device clocks remain separate. No benchmark or GPU sanitizer pass is inferred.")


if __name__ == "__main__":
    main()
