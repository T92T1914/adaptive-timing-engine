"""The GPU example stays outside the package and its timed regions close with a synchronize."""
import ast
import importlib.util
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
    def test_nothing_in_the_package_or_the_tools_imports_the_example(self):
        for folder in ("adaptive_timing", "tools"):
            for path in (ROOT / folder).glob("*.py"):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("gpu_queue_study", text, path.name)

    def test_the_requirements_pin_one_exact_torch_build(self):
        lines = [l for l in (EXAMPLE / "requirements.txt").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^torch==\d+\.\d+\.\d+\+cu\d+$")

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
        self.assertIn("nothing here is a latency bound", report["note"].lower())


if __name__ == "__main__":
    unittest.main()
