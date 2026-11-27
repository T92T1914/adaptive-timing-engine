"""The saved pass timing summary is the one its saved samples give."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("gpu_pass_timing_tables", ROOT / "tools" / "gpu_pass_timing_tables.py")
tables = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tables)


class PassTimingSummaryTests(unittest.TestCase):
    def test_percentile_is_nearest_rank(self):
        ordered = list(range(1, 1001))
        self.assertEqual([tables.percentile(ordered, q) for q in (500, 990, 999)], [500, 990, 999])
        self.assertEqual(tables.percentile([7], 999), 7)
        self.assertEqual(tables.percentile([1, 2, 3], 500), 2)
        self.assertIsNone(tables.percentile([], 500))

    def test_summary_separates_idle_starts_and_stopped_passes(self):
        condition = {"chunk_blocks": 1, "arrival": "poisson", "load": 0.9, "mode": "stop", "blocks": [
            {"requests": 3, "samples": [[100, 2000, tables.IDLE_AT_ARRIVAL], [4000, 500, tables.STOPPED]]},
            {"requests": 2, "samples": [[300, 3000, tables.IDLE_AT_ARRIVAL | tables.STOPPED]]},
        ]}
        row = tables.summarise(condition)
        self.assertEqual((row["requests"], row["starts"]), (5, 3))
        self.assertTrue(row["tail_rests_on_fewer_than_10000_starts"])
        self.assertEqual(row["start_delay"]["p50_ms"], 0.3)
        self.assertEqual(row["start_delay_idle_at_arrival"], {
            "count": 2, "p50_ms": 0.1, "p99_ms": 0.3, "p99_9_ms": 0.3, "max_ms": 0.3})
        self.assertEqual(row["completed_pass"]["count"], 1)
        self.assertEqual(row["stopped_pass"]["max_ms"], 3.0)


@unittest.skipUnless(tables.SUMMARY.exists(), "no saved pass timing run")
class SavedPassTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(tables.SUMMARY.read_text(encoding="utf-8"))
        cls.samples = tables.load_samples()

    def test_summary_names_the_samples_it_came_from(self):
        self.assertEqual(self.report["samples_file"], tables.SAMPLES.name)
        self.assertEqual(self.report["samples_sha256"],
                         hashlib.sha256(tables.SAMPLES.read_bytes()).hexdigest())

    def test_every_row_is_recomputed_from_the_samples(self):
        conditions = self.samples["conditions"]
        self.assertEqual(len(conditions), len(self.report["rows"]))
        for condition, row in zip(conditions, self.report["rows"]):
            saved = {key: value for key, value in row.items() if key != "blocks"}
            self.assertEqual(tables.summarise(condition), saved)

    def test_both_modes_cover_every_case_and_chunk_size(self):
        found = {(r["chunk_blocks"], r["arrival"], r["load"], r["mode"]) for r in self.report["rows"]}
        expected = {(size, arrival, load, mode) for size in (1, 2, 4, 8)
                    for arrival in ("poisson", "bursty") for load in (0.9, 1.5)
                    for mode in ("plain", "stop")}
        self.assertEqual(found, expected)

    def test_the_run_names_its_machine_and_load(self):
        for key in ("revision", "card", "driver", "toolkit", "torch", "started_utc", "finished_utc"):
            self.assertTrue(self.report[key], key)
        for row in self.report["rows"]:
            for block in row["blocks"]:
                for key in ("utilization_memory_clock_before", "utilization_memory_clock_after"):
                    self.assertEqual(len(block[key].split(",")), 3, block[key])


if __name__ == "__main__":
    unittest.main()
