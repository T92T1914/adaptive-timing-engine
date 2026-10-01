"""Real NumPy snapshot checks with an inert owner, never a native CUDA import."""
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ImportError:
    np = None

from adaptive_timing.runtime import Dispatch


@unittest.skipIf(np is None, "explicit optional NumPy snapshot checks")
class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        received = self.received

        class Context:
            def __init__(self, *args, **kwargs):
                self.owner = threading.get_ident()

            def run(self, image):
                assert threading.get_ident() == self.owner
                received.append(image)
                return np.bincount(np.asarray(image).ravel(), minlength=256)

            def close(self):
                assert threading.get_ident() == self.owner

        runtime = ModuleType("heterogeneous_batch_runtime")
        runtime.__path__ = []
        cuda = ModuleType("heterogeneous_batch_runtime.cuda")
        cuda.CudaHistogramContext = Context
        spec = importlib.util.spec_from_file_location(
            "adaptive_timing._snapshot_test",
            Path(__file__).resolve().parents[1] / "adaptive_timing/cuda.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
                "heterogeneous_batch_runtime": runtime,
                "heterogeneous_batch_runtime.cuda": cuda}):
            spec.loader.exec_module(self.module)
            self.executor = self.module.CudaHistogramExecutor(2, 2, 1, 1, capacity=1)
        self.addCleanup(self.executor.close)

    def assert_submission_snapshot(self, image):
        expected = np.zeros(256, dtype=np.int64)
        expected[3], expected[7] = 2, 2
        entered, release = threading.Event(), threading.Event()

        def gate(cancel):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("host snapshot gate")

        try:
            self.executor.submit_image(
                Dispatch("snapshot", "histogram", 0, 1, "dispatched"),
                1, image, gate=gate)
            self.assertTrue(entered.wait(1))
            # Submit has returned, but the owner has not consumed its input.
            image.fill(9)
            release.set()
            result, = self.executor.wait(1, timeout=1)
            self.assertTrue(result.usable)
            np.testing.assert_array_equal(result.value, expected)
            self.assertFalse(np.shares_memory(self.received[0], image))
            self.assertIs(type(self.received[0]), np.ndarray)
        finally:
            release.set()

    def test_ordinary_array_keeps_submission_values(self):
        self.assert_submission_snapshot(np.array([[3, 7], [7, 3]], dtype=np.uint8))

    def test_subclass_returning_itself_cannot_alias_submission(self):
        class SelfCopy(np.ndarray):
            def copy(self, *, order="C"):
                return self

        self.assert_submission_snapshot(
            np.array([[3, 7], [7, 3]], dtype=np.uint8).view(SelfCopy))

    def test_subclass_returning_shared_view_cannot_alias_submission(self):
        class ViewCopy(np.ndarray):
            def copy(self, *, order="C"):
                return self.view(np.ndarray)

        self.assert_submission_snapshot(
            np.array([[3, 7], [7, 3]], dtype=np.uint8).view(ViewCopy))

    def test_validation_reentrant_close_rejects_before_admission(self):
        executor = self.executor

        class ClosingDtype(np.ndarray):
            @property
            def dtype(self):
                executor.close()
                return np.ndarray.dtype.__get__(self)

        image = np.zeros((2, 2), dtype=np.uint8).view(ClosingDtype)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.executor.submit_image(
                Dispatch("never", "histogram", 0, 1, "dispatched"), 1, image)
        self.assertEqual(self.executor.outstanding, 0)
        self.assertEqual(self.executor.observations(), ())
        self.assertEqual(self.received, [])

    def test_allocator_reentrant_close_cannot_bypass_final_admission(self):
        image = np.zeros((2, 2), dtype=np.uint8)
        original = np.asarray

        def close_during_copy(value):
            base = original(value)
            self.executor.close()
            return base

        # A controlled allocator seam exercises the post-copy check. No caller
        # copy override is trusted or required to execute after normalization.
        with patch.object(self.module.np, "asarray", side_effect=close_during_copy):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                self.executor.submit_image(
                    Dispatch("never", "histogram", 0, 1, "dispatched"), 1, image)
        self.assertEqual(self.executor.outstanding, 0)
        self.assertEqual(self.executor.observations(), ())
        self.assertEqual(self.received, [])


if __name__ == "__main__":
    unittest.main()
