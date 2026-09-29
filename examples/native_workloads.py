"""Explicit optional native CPU example with snapshots made before submission."""
import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import math
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_timing.execution import RealExecutor
from adaptive_timing.runtime import Dispatch


def run():
    # The ordinary package, simulation and callable example need neither import.
    import numpy as np
    import heterogeneous_batch_runtime as native

    values = np.array([2, -3, 5, 0.25, -0.5], dtype=np.float64)
    mask = np.array([1, 0, 2, 255, 0], dtype=np.uint8)
    image = (np.arange(35).reshape(5, 7) ** 2 - 0.5).astype(np.float64)
    pixels = (np.arange(35).reshape(5, 7) % 6).astype(np.uint8)

    # Copies occur here on the owner thread, before accepted work can wait in
    # the queue. The native binding takes another snapshot when the call runs.
    owned_values, owned_mask, owned_image, owned_pixels = (
        array.copy(order="C") for array in (values, mask, image, pixels))
    for array in (owned_values, owned_mask, owned_image, owned_pixels):
        array.flags.writeable = False

    expected_sum = math.fsum(float(value) for value, selected in zip(values, mask) if selected)
    expected_histogram = np.zeros((3, 3, 256), dtype=np.uint64)
    for row in range(5):
        for column in range(7):
            expected_histogram[row // 2, column // 3, int(pixels[row, column])] += 1
    expected_stencil = image.copy()
    for row in range(1, 4):
        for column in range(1, 6):
            expected_stencil[row, column] = math.fsum(
                float(image[r, c]) / 9.0
                for r in range(row - 1, row + 2)
                for c in range(column - 1, column + 2))

    operations = {
        "masked_reduce": lambda cancel: native.masked_reduce(
            owned_values, owned_mask, backend="optimized", threads=2),
        "tile_histogram": lambda cancel: native.tile_histogram(
            owned_pixels, 2, 3, backend="optimized", threads=2),
        "stencil3x3": lambda cancel: native.stencil3x3(
            owned_image, backend="optimized", threads=2),
    }
    entered, release = threading.Event(), threading.Event()

    def gate(cancel):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("submission gate was not released")

    results = []
    with RealExecutor(max_workers=1, capacity=4) as executor:
        try:
            executor.submit(Dispatch("submission_gate", "cpu", 0, 0.1, "dispatched"), 1, gate)
            if not entered.wait(2):
                raise TimeoutError("submission gate did not start")
            for ident, operation in operations.items():
                executor.submit(Dispatch(ident, "cpu", 0, 0.1, "dispatched"), 1, operation)
            # These mutations happen while all native calls are still queued.
            # Correct output must use the separate snapshots captured above.
            values.fill(999)
            mask.fill(0)
            image.fill(999)
            pixels.fill(255)
        finally:
            release.set()
        while executor.outstanding:
            completed = executor.wait(1, timeout=5)
            if not completed:
                raise TimeoutError("native example did not complete")
            results.extend(completed)
        observations = executor.observations()

    by_id = {result.dispatch.id: result for result in results}
    if len(by_id) != 4 or not all(result.usable for result in results):
        raise AssertionError("an execution was stale, canceled or failed")
    if by_id["masked_reduce"].value != expected_sum:
        raise AssertionError("reduction did not match the independent sum")
    np.testing.assert_array_equal(by_id["tile_histogram"].value, expected_histogram)
    np.testing.assert_allclose(by_id["stencil3x3"].value, expected_stencil, rtol=1e-13, atol=1e-13)
    if not by_id["stencil3x3"].value.flags.owndata or not by_id["tile_histogram"].value.flags.owndata:
        raise AssertionError("native arrays do not own their completed outputs")

    result_records = []
    for result in results:
        record = asdict(result)
        value = record.pop("value")
        if isinstance(value, np.ndarray):
            record["output"] = {"shape": list(value.shape), "dtype": str(value.dtype),
                                "oracle_match": True, "owns_storage": True}
        else:
            record["output"] = value
        result_records.append(record)
    return {
        "schema_version": 1,
        "runtime_package_version": version("heterogeneous-batch-runtime"),
        "backend": "optimized CPU",
        "kernel_threads": 2,
        "executor_workers": 1,
        "input_snapshot": "owner thread before submission, then native binding at call entry",
        "caller_arrays_mutated_before_native_calls": True,
        "qualification": "Correctness and ownership example with an intentional queue gate, not a benchmark",
        "checks": {"masked_reduce": expected_sum, "tile_histogram_pixel_total": 35,
                   "partial_edge_tiles": True, "stencil_border_copied": True},
        "results": result_records,
        "observations": [asdict(row) for row in observations],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(run(), indent=2) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Saved native CPU completion observations to {args.output}")


if __name__ == "__main__":
    main()
