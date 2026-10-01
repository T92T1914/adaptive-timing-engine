"""Admission rejects optional snapshots before copying, using inert host doubles."""
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from adaptive_timing.runtime import Dispatch


class Image:
    def __init__(self, values=(1, 2, 3, 4)):
        self.dtype = "uint8"
        self.ndim = 2
        self.shape = (2, 2)
        self.flags = SimpleNamespace(c_contiguous=True)
        self.values = list(values)
        self.copy_calls = 0
        self.fail_copy = False
        self.on_copy = None

    def copy(self, *, order):
        self.copy_calls += 1
        if self.fail_copy:
            raise MemoryError("snapshot must not be allocated")
        if self.on_copy is not None:
            self.on_copy()
        assert order == "C"
        return Image(tuple(self.values))


def dispatch(ident):
    return Dispatch(ident, "cuda-histogram", 0, 1, "dispatched")


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls

        class Context:
            def __init__(self, *args, **kwargs):
                self.owner = threading.get_ident()

            def run(self, image):
                if threading.get_ident() != self.owner:
                    raise AssertionError("resource used outside its owner")
                calls.append(tuple(image.values))
                return tuple(image.values)

            def close(self):
                if threading.get_ident() != self.owner:
                    raise AssertionError("resource closed outside its owner")

        numpy = ModuleType("numpy")
        numpy.ndarray = Image
        numpy.dtype = lambda name: name
        class Allocator:
            # Controlled allocation seam for admission, allocation failure and
            # reentrancy. The real NumPy ownership contract has separate tests.
            def __init__(self, image):
                self.image = image

            def copy(self, *, order):
                return self.image.copy(order=order)

        numpy.asarray = Allocator
        runtime = ModuleType("heterogeneous_batch_runtime")
        runtime.__path__ = []
        cuda = ModuleType("heterogeneous_batch_runtime.cuda")
        cuda.CudaHistogramContext = Context
        # Execute the actual adapter in a distinct module. Its doubles do not
        # replace an imported production module or require optional packages.
        spec = importlib.util.spec_from_file_location(
            "adaptive_timing._admission_test",
            Path(__file__).resolve().parents[1] / "adaptive_timing/cuda.py")
        module = importlib.util.module_from_spec(spec)
        self.modules = patch.dict(sys.modules, {
            "numpy": numpy, "heterogeneous_batch_runtime": runtime,
            "heterogeneous_batch_runtime.cuda": cuda})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec.loader.exec_module(module)
        self.executor = module.CudaHistogramExecutor(2, 2, 1, 1, capacity=1)
        self.addCleanup(self.executor.close)

    def test_full_capacity_rejects_before_copy_then_retry_owns_snapshot(self):
        entered, release = threading.Event(), threading.Event()

        def gate(cancel):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("host gate")

        self.addCleanup(release.set)
        first = Image()
        self.executor.submit_image(dispatch("first"), 1, first, gate=gate)
        try:
            self.assertTrue(entered.wait(1))
            first.values[:] = [9] * 4
            rejected = Image((5, 6, 7, 8))
            rejected.fail_copy = True
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                self.executor.submit_image(dispatch("retry"), 1, rejected)
            self.assertEqual(rejected.copy_calls, 0)
            self.assertEqual(self.executor.outstanding, 1)
            self.assertEqual(self.executor.reserved_resources, ("cuda-histogram",))
            release.set()
            old, = self.executor.wait(2, timeout=1)
            self.assertEqual(old.value, (1, 2, 3, 4))
            self.assertTrue(old.stale)
            self.assertFalse(old.usable)
            rejected.fail_copy = False
            self.executor.submit_image(dispatch("retry"), 2, rejected)
            current, = self.executor.wait(2, timeout=1)
            self.assertEqual(current.value, (5, 6, 7, 8))
            self.assertTrue(current.usable)
            self.assertEqual(rejected.copy_calls, 1)
            self.assertEqual(self.calls, [(1, 2, 3, 4), (5, 6, 7, 8)])
            self.assertEqual([row.id for row in self.executor.observations()
                              if row.kind == "submitted"], ["first", "retry"])
        finally:
            release.set()

    def test_closed_executor_rejects_before_copy(self):
        self.executor.close()
        image = Image()
        image.fail_copy = True
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.executor.submit_image(dispatch("closed"), 1, image)
        self.assertEqual(image.copy_calls, 0)
        self.assertEqual(self.executor.observations(), ())

    def test_duplicate_identity_rejects_before_copy(self):
        self.executor.submit_image(dispatch("same"), 1, Image())
        self.executor.wait(1, timeout=1)
        image = Image()
        image.fail_copy = True
        with self.assertRaisesRegex(ValueError, "twice"):
            self.executor.submit_image(dispatch("same"), 1, image)
        self.assertEqual(image.copy_calls, 0)

    def test_invalid_generation_and_dispatch_reject_before_copy(self):
        for record, generation in (
                (dispatch("same"), 0),
                (Dispatch("same", "cuda-histogram", 0, 1, "expired"), 1),
                (Dispatch("", "cuda-histogram", 0, 1, "dispatched"), 1)):
            with self.subTest(record=record, generation=generation):
                image = Image()
                image.fail_copy = True
                with self.assertRaises(ValueError):
                    self.executor.submit_image(record, generation, image)
                self.assertEqual(image.copy_calls, 0)
                self.assertEqual(self.executor.outstanding, 0)
                self.assertEqual(self.executor.observations(), ())

    def test_copy_failure_does_not_consume_identity_or_capacity(self):
        image = Image()
        image.fail_copy = True
        with self.assertRaisesRegex(MemoryError, "snapshot"):
            self.executor.submit_image(dispatch("retry"), 1, image)
        self.assertEqual(self.executor.outstanding, 0)
        self.assertEqual(self.executor.observations(), ())
        image.fail_copy = False
        self.executor.submit_image(dispatch("retry"), 1, image)
        self.assertTrue(self.executor.wait(1, timeout=1)[0].usable)

    def test_copy_reentrancy_cannot_bypass_final_admission_check(self):
        image = Image()
        image.on_copy = self.executor.close
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.executor.submit_image(dispatch("never"), 1, image)
        self.assertEqual(image.copy_calls, 1)
        self.assertEqual(self.executor.outstanding, 0)
        self.assertEqual(self.executor.observations(), ())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
