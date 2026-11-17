"""The tables in docs/gpu-queue-study.md are the saved arrival run, and the run kept its promises."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gpu_arrival_tables", ROOT / "tools" / "gpu_arrival_tables.py")
tables = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tables)

REPORT = json.loads((ROOT / "docs" / "evidence" / "gpu-arrival-runs.json").read_text(encoding="utf-8"))
PREDICTIONS = json.loads((ROOT / "docs" / "evidence" / "gpu-queue-predictions.json").read_text(encoding="utf-8"))
DOCUMENT = (ROOT / "docs" / "gpu-queue-study.md").read_text(encoding="utf-8")


class GpuArrivalTablesTests(unittest.TestCase):
    def test_the_document_holds_the_tables_of_the_saved_run(self):
        self.assertIn(tables.tables(REPORT).rstrip(), DOCUMENT)
        self.assertIn(tables.counts(REPORT), DOCUMENT)

    def test_every_case_was_run_and_predicted_before(self):
        self.assertEqual(len(REPORT["rows"]), len(PREDICTIONS["rows"]))
        for row, expected in zip(REPORT["rows"], PREDICTIONS["rows"]):
            self.assertEqual((row["chunk_blocks"], row["arrival"], row["load"]),
                             (expected["chunk_blocks"], expected["arrival"], expected["load"]))
            self.assertEqual(row["predicted"], expected["predicted"])

    def test_each_run_accounts_for_its_requests(self):
        for row in REPORT["rows"]:
            for run in row["runs"]:
                self.assertEqual(run["submitted"], REPORT["settings"]["requests_per_run"])
                self.assertEqual(run["submitted"], run["computed"] + run["replaced"])


if __name__ == "__main__":
    unittest.main()
