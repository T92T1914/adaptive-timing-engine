"""Optional integration has no effect on importing the default package."""
import builtins
import importlib.util
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/native_workloads.py"


class NativeExampleTests(unittest.TestCase):
    def test_loading_example_does_not_import_optional_dependencies(self):
        original_import = builtins.__import__

        def restricted(name, *args, **kwargs):
            if name.split(".")[0] in ("numpy", "heterogeneous_batch_runtime"):
                raise AssertionError("optional dependency imported without explicit execution")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=restricted):
            namespace = runpy.run_path(str(EXAMPLE))
        self.assertTrue(callable(namespace["run"]))

    @unittest.skipUnless(importlib.util.find_spec("heterogeneous_batch_runtime"),
                         "optional native runtime is not installed")
    def test_native_operations_use_submission_snapshots_and_complete(self):
        report = runpy.run_path(str(EXAMPLE))["run"]()
        self.assertEqual(report["checks"]["masked_reduce"], 7.25)
        self.assertEqual(report["checks"]["tile_histogram_pixel_total"], 35)
        self.assertTrue(report["caller_arrays_mutated_before_native_calls"])
        self.assertEqual(len(report["results"]), 4)
        self.assertEqual({row["status"] for row in report["results"]}, {"completed"})
        self.assertFalse(any(row["stale"] for row in report["results"]))
        self.assertEqual(sum(row["kind"] == "reconciled" for row in report["observations"]), 4)


if __name__ == "__main__":
    unittest.main()
