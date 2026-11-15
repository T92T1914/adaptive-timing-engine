"""The wall clock study runs and its retained results keep their shape."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wall_clock_study", ROOT / "tools/wall_clock_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)

MEASURES = ("planning", "reference_swap", "first_poll_budget_256",
            "adoption_tight_loop", "adoption_sleep_0", "adoption_sleep_1ms")


def check_shape(test, report, counts):
    test.assertEqual(sorted(report["sizes"]), sorted(str(size) for size in study.SIZES))
    for size, block in report["sizes"].items():
        for name in MEASURES:
            row = block[name]
            ordered = [row[key] for key in ("min_us", "p50_us", "p95_us", "p99_us", "max_us")]
            test.assertEqual(ordered, sorted(ordered), (size, name))
            test.assertGreater(row["samples"], 0)
        test.assertEqual(block["planning"]["samples"], counts["plan"])
        test.assertEqual(block["reference_swap"]["samples"], counts["swap"])
        # Adopting a finished plan is far cheaper than computing it.
        test.assertLess(block["reference_swap"]["p50_us"], block["planning"]["p50_us"])
    for key in ("python", "platform", "timer", "logical_cpus"):
        test.assertIn(key, report["environment"])


class WallClockStudyTests(unittest.TestCase):
    def test_quick_run_writes_the_expected_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "wall.json"
            self.assertEqual(study.main(["--quick", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
        check_shape(self, report, study.QUICK)

    def test_environment_reports_the_actual_gil_state(self):
        for state in (True, False):
            with mock.patch.object(study.sys, "_is_gil_enabled", lambda state=state: state, create=True):
                self.assertIs(study.environment()["gil_enabled"], state)
        with mock.patch.object(study, "sys", types.SimpleNamespace(version="3.11 test")):
            self.assertIsNone(study.environment()["gil_enabled"])

    def test_retained_results_keep_the_documented_shape(self):
        path = ROOT / "docs/evidence/wall-clock.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        check_shape(self, report, study.FULL)
        self.assertIn("Not a latency bound", report["note"])

    def test_retained_hosted_results_keep_the_documented_shape(self):
        folder = ROOT / "docs/wall-clock-hosted"
        expected = {"ubuntu-latest-3.13": True, "macos-latest-3.13": True,
                    "ubuntu-latest-3.14t": False}
        self.assertEqual(sorted(path.name for path in folder.iterdir()), sorted(expected))
        for name, gil in expected.items():
            report = json.loads((folder / name / "wall-clock.json").read_text(encoding="utf-8"))
            runner = json.loads((folder / name / "runner.json").read_text(encoding="utf-8"))
            check_shape(self, report, study.FULL)
            self.assertIs(report["environment"]["gil_enabled"], gil, name)
            self.assertIs(report["environment"]["gil_enabled_at_start"], gil, name)
            self.assertEqual(runner["GITHUB_RUN_ID"], "43608063433")
            for block in report["sizes"].values():
                self.assertEqual(set(block["gil_enabled_after"].values()), {gil}, name)


if __name__ == "__main__":
    unittest.main()
