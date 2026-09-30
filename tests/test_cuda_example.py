import builtins
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


class CudaExampleTests(unittest.TestCase):
    def test_optional_example_has_no_import_side_effect(self):
        original = builtins.__import__
        def restricted(name, *args, **kwargs):
            if name.split(".")[0] in ("numpy", "heterogeneous_batch_runtime"):
                raise AssertionError("optional import before explicit run")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=restricted):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/cuda_histogram.py"))
        self.assertTrue(callable(namespace["run"]))
