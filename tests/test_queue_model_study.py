"""The queue model study runs, its formulas keep their limits and its retained results hold."""
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("queue_model_study", ROOT / "tools/queue_model_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def retained():
    return json.loads((ROOT / "docs/evidence/queue-model.json").read_text(encoding="utf-8"))


class QueueModelStudyTests(unittest.TestCase):
    def test_formulas_keep_their_limits(self):
        for service in study.SERVICES:
            light = study.predict(1e-6, service)
            self.assertAlmostEqual(light["computed_share"], 1.0, places=4)
            self.assertAlmostEqual(light["adopted_share"], 1.0, places=4)
            self.assertAlmostEqual(light["mean_adopted_age"], 1.0, places=2)
            heavy = study.predict(200.0, service)
            self.assertAlmostEqual(heavy["computed_share"], 1 / 200, places=3)
            for load in study.LOADS:
                row = study.predict(load, service)
                self.assertLessEqual(row["adopted_share"], row["computed_share"])
                self.assertGreater(row["mean_adopted_age"], 0)

    def test_fixed_service_matches_the_derivation(self):
        load = 1.5
        p = math.exp(-load)
        row = study.predict(load, "fixed")
        self.assertAlmostEqual(row["computed_share"], 1 / (p + load))
        self.assertAlmostEqual(row["mean_adopted_age"], (1 - p) * (1 + 1 / load))

    def test_unknown_names_are_refused(self):
        with self.assertRaises(ValueError):
            study.predict(1.0, "uniform")
        with self.assertRaises(ValueError):
            study.arrival_times("steady", 1.0, 3, study.random.Random(1))

    def test_quick_run_writes_the_expected_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "queue.json"
            self.assertEqual(study.main(["--quick", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(report["rows"]),
                         len(study.SERVICES) * len(study.ARRIVALS) * len(study.LOADS))
        for row in report["rows"]:
            for run in row["runs"]:
                self.assertEqual(run["computed"] + run["replaced"], study.QUICK["requests"])
                self.assertEqual(run["computed"] - run["stale"], run["adopted"])

    def test_retained_poisson_rows_agree_with_the_model(self):
        report = retained()
        self.assertIn("nothing here is a latency bound", report["note"])
        poisson = [row for row in report["rows"] if row["arrival"] == "poisson"]
        self.assertEqual(len(poisson), len(study.SERVICES) * len(study.LOADS))
        for row in poisson:
            for name in ("computed_share", "adopted_share", "mean_adopted_age"):
                self.assertEqual(row[name]["verdict"], "within noise",
                                 (row["service"], row["load"], name))

    def test_retained_misses_are_the_ones_the_document_names(self):
        rows = {(r["arrival"], r["service"], r["load"]): r for r in retained()["rows"]}
        for load in (1.5, 3.0):
            row = rows[("periodic", "fixed", load)]
            self.assertEqual([run["adopted"] for run in row["runs"]], [1] * len(row["runs"]))
            self.assertEqual(row["adopted_share"]["verdict"], "miss")
        for load in (0.3, 0.6, 0.9):
            row = rows[("periodic", "fixed", load)]
            self.assertEqual([run["computed"] for run in row["runs"]],
                             [study.FULL["requests"]] * len(row["runs"]))
        for (arrival, _, load), row in rows.items():
            if arrival == "bursty" and load <= 0.9:
                self.assertLess(row["computed_share"]["measured"]["mean"], 0.5)


if __name__ == "__main__":
    unittest.main()
