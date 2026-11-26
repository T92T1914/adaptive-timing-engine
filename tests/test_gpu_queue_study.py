"""The GPU example stays outside the package and its timed regions close with a synchronize."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "gpu_queue_study"


def calls(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            yield ast.unparse(child.func)


class GpuExampleTests(unittest.TestCase):
    def test_the_study_states_the_block_count_of_the_saved_run(self):
        evidence = ROOT / "docs" / "evidence" / "gpu-service-time.json"
        blocks = json.loads(evidence.read_text(encoding="utf-8"))["model"]["blocks"]
        study = (ROOT / "docs" / "gpu-queue-study.md").read_text(encoding="utf-8")
        self.assertIn(f"a fixed stack of {blocks} matrix blocks", study)

    def test_the_study_states_the_pass_that_sets_the_stop_loads(self):
        evidence = ROOT / "docs" / "evidence"
        predictions = json.loads((evidence / "gpu-queue-predictions.json").read_text(encoding="utf-8"))
        report = json.loads((evidence / "gpu-cooperative-stop.json").read_text(encoding="utf-8"))
        assumed = predictions["mean_pass_ms"]
        for row in report["rows"]:
            runs = row["modes"]["plain"]["runs"]
            measured = 1000 * sum(run["busy_gpu_seconds"] for run in runs) / sum(run["computed"] for run in runs)
            self.assertLess(measured, assumed[str(row["chunk_blocks"])])
        values = [f"{assumed[size]:.2f}" for size in ("1", "2", "4", "8")]
        study = (ROOT / "docs" / "gpu-queue-study.md").read_text(encoding="utf-8")
        self.assertIn(f"the assumed pass of the first study, {values[0]}, {values[1]}, {values[2]} "
                      f"and {values[3]} ms at 1, 2, 4 and 8 blocks per chunk", study)
        self.assertNotIn("set from the mean pass of `plain`", study)

    def test_the_study_separates_mean_and_single_run_stop_counts(self):
        evidence = ROOT / "docs" / "evidence" / "gpu-cooperative-stop.json"
        modes = [row["modes"]["stop"] for row in json.loads(evidence.read_text(encoding="utf-8"))["rows"]]
        means = [mode["means"]["stopped"] for mode in modes]
        runs = [run["stopped"] for mode in modes for run in mode["runs"]]
        study = (ROOT / "docs" / "gpu-queue-study.md").read_text(encoding="utf-8")
        self.assertIn(f"Between {min(means):.0f} and {max(means):.0f} of the 1000 requests in a run ended early "
                      f"on the mean of three seeds, and single runs ranged from {min(runs)} to {max(runs)}.", study)

    def test_the_study_states_the_predicted_range_of_the_quiet_periodic_rows(self):
        evidence = ROOT / "docs" / "evidence" / "gpu-queue-predictions.json"
        shares = [row["predicted"]["computed_share"]
                  for row in json.loads(evidence.read_text(encoding="utf-8"))["rows"]
                  if row["arrival"] == "periodic" and row["load"] <= 1.5]
        study = (ROOT / "docs" / "gpu-queue-study.md").read_text(encoding="utf-8")
        self.assertIn(f"predicts computed shares from {min(shares):.2f} to {max(shares):.2f}", study)

    def test_the_documents_limit_the_standard_library_claim_to_the_default_modules(self):
        for path in (ROOT / "docs" / "gpu-queue-study.md", EXAMPLE / "README.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("the default modules of the package", text.lower(), path.name)
            self.assertNotIn("package itself still imports only the standard library", text, path.name)
            self.assertNotIn("the package still needs only the standard library", text, path.name)

    def test_nothing_in_the_package_or_the_tools_imports_the_example(self):
        for folder in ("adaptive_timing", "tools"):
            for path in (ROOT / folder).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    for name in names:
                        self.assertNotIn(name.split(".")[0], {"gpu_queue_study", "harness", "measure_service", "torch"},
                                         path.name)

    def test_the_requirements_pin_one_exact_torch_build(self):
        lines = [l for l in (EXAMPLE / "requirements.txt").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^torch==\d+\.\d+\.\d+\+cu\d+$")

    def test_the_example_readme_names_every_script(self):
        text = (EXAMPLE / "README.md").read_text(encoding="utf-8")
        missing = [path.name for path in sorted(EXAMPLE.glob("*.py")) if f"`{path.name}`" not in text]
        self.assertEqual(missing, [])

    def test_every_timed_region_is_closed_by_a_synchronize(self):
        tree = ast.parse((EXAMPLE / "harness.py").read_text(encoding="utf-8"))
        timed = 0
        for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            names = list(calls(function))
            if any(name.endswith("perf_counter") for name in names):
                timed += 1
                self.assertGreaterEqual(names.count("torch.cuda.synchronize"), 2, function.name)
                self.assertEqual(names.count("time.perf_counter"), 2, function.name)
        self.assertGreaterEqual(timed, 1)

    def test_the_example_is_not_a_package_member(self):
        self.assertFalse((EXAMPLE / "__init__.py").exists())


@unittest.skipUnless(importlib.util.find_spec("torch"), "torch is not installed here")
class GpuExampleRunTests(unittest.TestCase):
    def setUp(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("no CUDA device")
        sys.path.insert(0, str(EXAMPLE))
        self.addCleanup(sys.path.remove, str(EXAMPLE))

    def test_chunking_does_not_change_the_result(self):
        import torch
        import model
        blocks, inputs = model.build()
        results = []
        for size in (1, 8):
            value = inputs
            for chunk in model.chunks(blocks, size):
                value = model.run_chunk(chunk, value)
            results.append(value)
        torch.testing.assert_close(results[0], results[1], rtol=1e-3, atol=1e-3)

    def test_a_quick_arrival_run_keeps_the_worker_counts_consistent(self):
        import json
        import tempfile
        import arrival_run
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "arrivals.json"
            self.assertEqual(arrival_run.main(["--quick", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(report["rows"]), 3)
        for row in report["rows"]:
            for run in row["runs"]:
                self.assertEqual(run["computed"] + run["replaced"], run["submitted"])
                self.assertEqual(run["submitted"], arrival_run.QUICK["requests"])
                self.assertEqual(run["computed"] - run["stale"], run["adopted"] + run["adopted_check_mismatch"])
                self.assertTrue(0 <= run["adopted_share"] <= run["computed_share"] <= 1)
                self.assertGreaterEqual(run["arrival_lateness_ms"]["max"], run["arrival_lateness_ms"]["mean"])
        self.assertEqual(report["settings"]["arrival_wait"], "sleep only, no spin")

    def test_a_quick_cooperative_stop_run_stops_only_stale_passes(self):
        import json
        import tempfile
        import cooperative_stop
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stop.json"
            self.assertEqual(cooperative_stop.main(["--quick", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(report["rows"]), 3)
        for row in report["rows"]:
            self.assertEqual(set(row["modes"]), {"plain", "synced", "stop"})
            for mode, block in row["modes"].items():
                for run in block["runs"]:
                    self.assertEqual(run["computed"] + run["replaced"], run["submitted"])
                    self.assertLessEqual(run["stopped"], run["stale"])
                    if mode != "stop":
                        self.assertEqual(run["stopped"], 0)

    def test_the_retained_stop_results_hold_card_readings_and_real_stops(self):
        import json
        report = json.loads((ROOT / "docs/evidence/gpu-cooperative-stop.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted({row["chunk_blocks"] for row in report["rows"]}), [1, 2, 4, 8])
        for row in report["rows"]:
            for mode, block in row["modes"].items():
                for run in block["runs"]:
                    self.assertIn("gpu_utilization_and_memory_before", run)
                    self.assertIn("gpu_utilization_and_memory_after", run)
                    if mode == "stop":
                        self.assertGreater(run["stopped"], 0)
                    else:
                        self.assertEqual(run["stopped"], 0)

    def test_a_chunk_size_that_does_not_divide_is_refused(self):
        import model
        blocks, _ = model.build()
        with self.assertRaises(ValueError):
            model.chunks(blocks, 5)


class GpuServiceTimeResultsTests(unittest.TestCase):
    def test_the_retained_results_name_their_environment_and_keep_their_order(self):
        import json
        report = json.loads((ROOT / "docs/evidence/gpu-service-time.json").read_text(encoding="utf-8"))
        for key in ("card", "driver", "toolkit", "torch"):
            self.assertTrue(report[key], key)
        self.assertEqual([r["chunk_blocks"] for r in report["rows"]], [1, 2, 4, 8])
        for row in report["rows"]:
            self.assertEqual(row["count"], row["chunks_per_pass"] * report["passes_per_chunk_size"])
            self.assertLessEqual(row["p5_ms"], row["median_ms"])
            self.assertLessEqual(row["median_ms"], row["p95_ms"])
            self.assertEqual(len(row["pass_ms"]), report["passes_per_chunk_size"])
            self.assertTrue(all(value > 0 for value in row["pass_ms"]))
        self.assertIn("nothing here is a latency bound", report["note"].lower())


if __name__ == "__main__":
    unittest.main()
