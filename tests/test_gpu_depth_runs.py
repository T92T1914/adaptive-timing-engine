"""The depth runs are summarised the way the predictions define each metric."""
import ast
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "gpu_queue_study"
PREDICTIONS = ROOT / "docs" / "evidence" / "gpu-depth-predictions.json"

spec = importlib.util.spec_from_file_location("gpu_depth_run_tables", ROOT / "tools" / "gpu_depth_run_tables.py")
tables = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tables)


def record(index, arrived, start, end, next_arrived, adopted=False, stopped=False, waits=0, launched=8):
    return {"index": index, "arrived": arrived, "next_arrived": next_arrived, "start": start, "end": end,
            "adopted": adopted, "stopped": stopped, "waits": waits, "launched": launched}


class DepthSampleTests(unittest.TestCase):
    def test_samples_keep_start_order_delay_length_newer_and_flags(self):
        passes = [
            # Listed out of start order, so the sort is part of what is tested.
            record(2, 1.0100, 1.0102, 1.0130, None, adopted=True, waits=3),
            record(0, 1.0000, 1.0001, 1.0040, 1.0020, stopped=True, waits=1, launched=3),
            record(1, 1.0020, 1.0040, 1.0080, 1.0090),
        ]
        self.assertEqual(tables.samples_from(passes), [
            [100, 3900, 1900, 1, 3, tables.STOPPED | tables.IDLE_AT_ARRIVAL],
            [2000, 4000, 5000, 0, 8, 0],
            [200, 2800, None, 3, 8, tables.ADOPTED | tables.IDLE_AT_ARRIVAL],
        ])

    def test_run_metrics_follow_the_prediction_definitions(self):
        samples = [
            # delay, pass, newer, waits, launched, flags. Times in microseconds.
            [100, 4000, 1000, 2, 3, tables.STOPPED],
            [500, 6000, 7000, 4, 8, 0],
            [300, 2000, None, 0, 8, tables.ADOPTED],
        ]
        metrics = tables.run_metrics(samples, requests=5)
        self.assertEqual(set(metrics), set(tables.METRICS))
        self.assertEqual(metrics["computed_share"], 3 / 5)
        self.assertEqual(metrics["adopted_share"], 1 / 5)
        self.assertEqual(metrics["mean_adopted_age_ms"], 2.3)
        self.assertEqual(metrics["stopped_share_of_requests"], 1 / 5)
        self.assertEqual(metrics["stale_after_newer_ms_per_stop"], 3.0)
        self.assertEqual(metrics["stopped_pass_ms"], 4.0)
        self.assertEqual(metrics["stale_gpu_time_share"], 10000 / 12000)
        self.assertEqual(metrics["synchronizes_per_pass"], (3 + 5 + 1) / 3)

    def test_a_run_with_no_stop_has_no_stop_lengths(self):
        metrics = tables.run_metrics([[0, 1000, None, 0, 4, tables.ADOPTED]], requests=1)
        self.assertIsNone(metrics["stale_after_newer_ms_per_stop"])
        self.assertIsNone(metrics["stopped_pass_ms"])
        self.assertEqual(metrics["stale_gpu_time_share"], 0.0)

    def test_summary_pools_the_runs_and_keeps_each_value(self):
        condition = {"chunk_blocks": 4, "chunks_per_pass": 8, "depth": 2, "arrival": "poisson", "load": 0.9,
                     "runs": [
                         {"requests": 2, "samples": [[100, 2000, 500, 1, 2, tables.STOPPED | tables.IDLE_AT_ARRIVAL],
                                                     [900, 3000, None, 6, 8, tables.ADOPTED]]},
                         {"requests": 1, "samples": [[300, 2500, None, 6, 8, tables.ADOPTED | tables.IDLE_AT_ARRIVAL]]},
                     ]}
        row = tables.summarise(condition)
        self.assertEqual((row["requests"], row["starts"]), (3, 3))
        self.assertTrue(row["tail_rests_on_fewer_than_10000_starts"])
        self.assertEqual(row["measured"]["stopped_pass_ms"], {"mean": 2.0, "runs": [2.0, None]})
        self.assertEqual(row["measured"]["computed_share"], {"mean": 1.0, "runs": [1.0, 1.0]})
        self.assertEqual(row["start_delay"]["p50_ms"], 0.3)
        self.assertEqual(row["start_delay_idle_at_arrival"]["count"], 2)

    def test_the_metrics_are_those_of_the_predictions(self):
        saved = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
        for row in saved["rows"]:
            self.assertEqual(tuple(row["predicted"]), tables.METRICS)


class DepthCompareTests(unittest.TestCase):
    def side(self, value):
        return {"mean": value}

    def test_a_miss_is_more_than_25_percent_of_the_prediction(self):
        self.assertFalse(tables.compare(self.side(2.0), self.side(2.5))["miss"])
        self.assertFalse(tables.compare(self.side(2.0), self.side(1.5))["miss"])
        result = tables.compare(self.side(2.0), self.side(2.6))
        self.assertTrue(result["miss"])
        self.assertAlmostEqual(result["relative"], 0.3)
        self.assertTrue(tables.compare(self.side(2.0), self.side(1.4))["miss"])

    def test_a_prediction_of_zero_misses_anything_above_zero(self):
        self.assertFalse(tables.compare(self.side(0.0), self.side(0.0))["miss"])
        result = tables.compare(self.side(0.0), self.side(0.001))
        self.assertTrue(result["miss"])
        self.assertIsNone(result["relative"])

    def test_a_value_on_one_side_only_is_a_miss(self):
        self.assertEqual(tables.compare(None, self.side(0.4))["verdict"], "none predicted")
        self.assertTrue(tables.compare(None, self.side(0.4))["miss"])
        self.assertEqual(tables.compare(self.side(0.4), None)["verdict"], "none measured")
        self.assertFalse(tables.compare(None, None)["miss"])

    def test_the_digest_ignores_key_order_and_sees_values(self):
        one = {"b": [1, 2.5], "a": {"x": None, "y": 3}}
        reordered = {"a": {"y": 3, "x": None}, "b": [1, 2.5]}
        self.assertNotEqual(list(one), list(reordered))
        self.assertEqual(tables.predictions_digest(one), tables.predictions_digest(reordered))
        changed = {"b": [1, 2.25], "a": {"x": None, "y": 3}}
        self.assertNotEqual(tables.predictions_digest(one), tables.predictions_digest(changed))


class DepthRunImportTests(unittest.TestCase):
    def imports(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names}
        names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        return names

    def test_the_tables_need_only_the_standard_library(self):
        allowed = set(sys.stdlib_module_names)
        self.assertLessEqual(self.imports(ROOT / "tools" / "gpu_depth_run_tables.py"), allowed)

    def test_nothing_in_the_package_imports_the_depth_run(self):
        for path in (ROOT / "adaptive_timing").glob("*.py"):
            self.assertNotIn("depth_run", self.imports(path), path.name)


@unittest.skipUnless(importlib.util.find_spec("torch"), "torch is not installed here")
class DepthRunCardTests(unittest.TestCase):
    def setUp(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("no CUDA device")
        sys.path.insert(0, str(EXAMPLE))
        self.addCleanup(sys.path.remove, str(EXAMPLE))

    def test_the_depth_mode_records_waits_launches_and_every_next_arrival(self):
        import arrival_run
        import model
        blocks, inputs = model.build()
        chunk_list = model.chunks(blocks, 1)
        passes = []
        # A high load replaces many requests before they can start.
        arrival_run.run_case(chunk_list, inputs, "poisson", 3.0, 0.0024, 60, 7, arrival_run.DEPTH_MODE,
                             depth=4, passes=passes)
        started = {item["index"]: item["arrived"] for item in passes}
        replaced_next = [item for item in passes if item["index"] + 1 < 60 and item["index"] + 1 not in started]
        self.assertTrue(replaced_next)
        for item in replaced_next:
            # The next request arrived after this one and before the next request that started,
            # so the record holds the replaced request and not the next one to start.
            later = min(index for index in started if index > item["index"])
            self.assertLess(item["arrived"], item["next_arrived"])
            self.assertLess(item["next_arrived"], started[later])
        for item in passes:
            self.assertEqual(item["next_arrived"] is None, item["index"] == 59)
            self.assertEqual(item["stopped"], item["launched"] < len(chunk_list))
            self.assertLessEqual(item["waits"], len(chunk_list) - 4)
            self.assertLessEqual(item["start"], item["end"])
            if item["adopted"]:
                self.assertFalse(item["stopped"])

    def test_the_other_modes_keep_their_launches_and_make_no_waits(self):
        import arrival_run
        import model
        blocks, inputs = model.build()
        chunk_list = model.chunks(blocks, 4)
        for mode in arrival_run.MODES:
            passes = []
            arrival_run.run_case(chunk_list, inputs, "poisson", 0.9, 0.0024, 30, 7, mode, passes=passes)
            self.assertTrue(passes, mode)
            for item in passes:
                self.assertEqual(item["waits"], 0, mode)
                self.assertEqual(item["launched"] == len(chunk_list), not item["stopped"], mode)
        self.assertNotIn(arrival_run.DEPTH_MODE, arrival_run.MODES)

    def test_an_unknown_mode_is_refused(self):
        import arrival_run
        with self.assertRaises(ValueError):
            arrival_run.run_case([], None, "poisson", 0.9, 0.0024, 1, 7, "forced")

    def test_a_quick_depth_run_saves_a_summary_of_its_own_samples(self):
        import depth_run
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "depth.json"
            self.assertEqual(depth_run.main(["--quick", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            saved = tables.load_samples(output.with_name(report["samples_file"]))
        self.assertEqual([row["depth"] for row in report["rows"]], [2, None])
        for row in report["rows"]:
            self.assertEqual([run["worker"]["failures"] for run in row["runs"]], [0] * len(row["runs"]))
            self.assertEqual(sum(run["worker"]["computed"] for run in row["runs"]), row["starts"])
        for condition, row in zip(saved["conditions"], report["rows"]):
            summary = {key: value for key, value in row.items() if key not in ("order", "runs")}
            self.assertEqual(tables.summarise(condition), summary)
            for run in condition["runs"]:
                for delay, length, newer, waits, launched, flags in run["samples"]:
                    self.assertEqual(bool(flags & tables.STOPPED), launched < row["chunks_per_pass"])
                    if row["depth"] is None:
                        self.assertEqual(waits, 0)
                    else:
                        self.assertLessEqual(waits, row["chunks_per_pass"] - row["depth"])
        self.assertEqual(report["predictions_digest"],
                         tables.predictions_digest(json.loads(PREDICTIONS.read_text(encoding="utf-8"))))


class SavedDepthRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(tables.RUNS.read_text(encoding="utf-8"))
        cls.predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
        cls.samples = tables.load_samples()

    def test_the_run_names_its_machine_and_load(self):
        for key in ("revision", "card", "driver", "toolkit", "torch", "torch_cuda", "python",
                    "started_utc", "finished_utc"):
            self.assertTrue(self.report[key], key)
        self.assertTrue(self.report["tree_clean"])
        for row in self.report["rows"]:
            for run in row["runs"]:
                for key in ("utilization_memory_clock_before", "utilization_memory_clock_after"):
                    self.assertEqual(len(run[key].split(",")), 3)

    def test_the_predictions_were_committed_before_the_run_and_have_not_changed(self):
        committed = datetime.fromisoformat(self.report["predictions_committed"])
        started = datetime.fromisoformat(self.report["started_utc"])
        self.assertLess(committed, started)
        self.assertEqual(self.report["predictions_commit"], "e8c52d5368e98c939da870ac1ecd8c3c3727e6c2")
        self.assertEqual(self.report["predictions_digest"], tables.predictions_digest(self.predictions))

    def test_every_prediction_has_its_three_runs_in_the_predicted_unit(self):
        settings = self.predictions["settings"]
        found = [tuple(row[key] for key in tables.KEYS) for row in self.report["rows"]]
        expected = [tuple(row[key] for key in tables.KEYS) for row in self.predictions["rows"]]
        self.assertEqual(found, expected)
        self.assertEqual(self.report["settings"]["time_unit_ms"], settings["time_unit_ms"])
        for row in self.report["rows"]:
            self.assertEqual([run["seed"] for run in row["runs"]], settings["seeds"])
            self.assertEqual({run["requests"] for run in row["runs"]}, {settings["requests_per_run"]})
        self.assertEqual(sorted(row["order"] for row in self.report["rows"]), list(range(len(expected))))

    def test_the_samples_file_is_the_one_the_report_names(self):
        self.assertEqual(self.report["samples_file"], tables.SAMPLES.name)
        self.assertEqual(hashlib.sha256(tables.SAMPLES.read_bytes()).hexdigest(), self.report["samples_sha256"])

    def test_the_summaries_follow_from_the_saved_samples(self):
        self.assertEqual(len(self.samples["conditions"]), len(self.report["rows"]))
        for condition, row in zip(self.samples["conditions"], self.report["rows"]):
            summary = {key: value for key, value in row.items() if key not in ("order", "runs")}
            self.assertEqual(tables.summarise(condition), summary)

    def test_the_samples_keep_the_depth_bound_and_the_worker_counts(self):
        for condition, row in zip(self.samples["conditions"], self.report["rows"]):
            count, limit = row["chunks_per_pass"], row["depth"]
            for run, saved in zip(condition["runs"], row["runs"]):
                self.assertEqual(saved["worker"]["computed"], len(run["samples"]))
                self.assertEqual(saved["worker"]["failures"], 0)
                adopted = sum(1 for sample in run["samples"] if sample[5] & tables.ADOPTED)
                self.assertEqual(saved["worker"]["adopted"], adopted)
                for delay, length, newer, waits, launched, flags in run["samples"]:
                    self.assertEqual(bool(flags & tables.STOPPED), launched < count)
                    self.assertFalse(flags & tables.STOPPED and flags & tables.ADOPTED)
                    self.assertLessEqual(waits, 0 if limit is None else max(0, count - limit))
                    if flags & tables.STOPPED:
                        self.assertIsNotNone(newer)


if __name__ == "__main__":
    unittest.main()
