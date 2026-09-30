"""Inspect actual Python submission, synchronous CUDA completion and reconciliation."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(*, runtime_revision=None, adaptive_revision=None):
    import numpy as np
    from adaptive_timing.cuda import CudaHistogramExecutor
    from adaptive_timing.runtime import Dispatch

    def dispatch(ident):
        return Dispatch(ident, "cuda-histogram", 0, 1, "dispatched")

    def expected(image):
        bins = np.zeros((3, 4, 256), dtype=np.uint64)
        for r in range(3):
            for c in range(4):
                bins[r, c] = np.bincount(image[r*4:(r+1)*4, c*5:(c+1)*5].ravel(), minlength=256)
        return bins

    image = np.random.default_rng(91).integers(0, 256, (11, 17), dtype=np.uint8)
    original = image.copy()
    outcomes, observations, checks = [], [], {}
    entered, release = threading.Event(), threading.Event()

    def gate(cancel):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("inspection host gate")

    def collect(executor, generation):
        while executor.outstanding:
            rows = executor.wait(generation, timeout=5)
            if not rows:
                raise TimeoutError("bounded application did not complete")
            outcomes.extend(rows)

    executor = CudaHistogramExecutor(11, 17, 4, 5, capacity=2)
    try:
        executor.submit_image(dispatch("obsolete-snapshot"), 1, image, gate=gate)
        if not entered.wait(5):
            raise TimeoutError("callable did not enter host gate")
        # submit_image returned after the copy. The gate prevents CUDA reading
        # before mutation, so equality below verifies actual submission ownership.
        image.fill(7)
        checks["mutated_after_submission_return"] = True
        executor.submit_image(dispatch("queued-cancel"), 1, image)
        try:
            executor.submit_image(dispatch("backpressure"), 1, image)
        except RuntimeError as exc:
            if "capacity" not in str(exc):
                raise
            checks["bounded_backpressure"] = True
        else:
            raise AssertionError("capacity did not reject")
        checks["queued_cancel_confirmed"] = executor.cancel("queued-cancel")
        release.set()
        collect(executor, 2)
        old = next(row for row in outcomes if row.dispatch.id == "obsolete-snapshot")
        np.testing.assert_array_equal(old.value["value"], expected(original))
        if old.usable or not old.stale or old.status != "completed":
            raise AssertionError("obsolete completion was adopted")
        checks["obsolete_physically_completed_not_current"] = True
        executor.submit_image(dispatch("current-repeat"), 2, image)
        collect(executor, 2)
        current = outcomes[-1]
        np.testing.assert_array_equal(current.value["value"], expected(image))
        if not current.usable:
            raise AssertionError("current result unusable")
        entered.clear()
        release.clear()
        executor.submit_image(dispatch("running-cancel-request"), 2, image, gate=gate)
        if not entered.wait(5):
            raise TimeoutError("cancel gate")
        if executor.cancel("running-cancel-request"):
            raise AssertionError("running callable falsely canceled")
        if executor.outstanding != 1 or not executor.reserved_resources:
            raise AssertionError("live reservation released")
        release.set()
        collect(executor, 2)
        running = outcomes[-1]
        np.testing.assert_array_equal(running.value["value"], expected(image))
        if running.status != "completed" or not running.cancellation_requested or running.usable:
            raise AssertionError("cancel request changed physical outcome")
        checks["running_cancel_retains_storage_until_completion"] = True
        failed = threading.Event()
        def synthetic_failure(cancel):
            failed.set()
            raise ValueError("synthetic host exception, not GPU failure")
        executor.submit(dispatch("synthetic-host-failure"), 2, synthetic_failure)
        if not failed.wait(5):
            raise TimeoutError("failure gate")
        collect(executor, 2)
        if outcomes[-1].status != "failed":
            raise AssertionError("exception outcome missing")
        entered.clear()
        def shutdown_gate(cancel):
            entered.set()
            if not cancel.wait(5):
                raise TimeoutError("shutdown cancellation request")
        executor.submit_image(dispatch("shutdown-running"), 2, image, gate=shutdown_gate)
        if not entered.wait(5):
            raise TimeoutError("shutdown gate")
        executor.submit_image(dispatch("shutdown-queued"), 2, image)
        executor.close()
        outcomes.extend(executor.reconcile(2))
        shutdown = next(row for row in outcomes if row.dispatch.id == "shutdown-running")
        np.testing.assert_array_equal(shutdown.value["value"], expected(image))
        if shutdown.status != "completed" or executor.outstanding or executor.reserved_resources:
            raise AssertionError("shutdown did not drain and reconcile")
        checks["explicit_shutdown_drained_outstanding_work"] = True
        observations.extend(executor.observations())
    finally:
        release.set()
        executor.close()

    records = []
    for row in outcomes:
        record = asdict(row)
        if row.value is not None:
            record["value"]["value"] = row.value["value"].tolist()
        records.append(record)
    return {
        "schema_version": 1,
        "mode": "actual Python to reusable CUDA histogram, synthetic images",
        "clock": "host monotonic seconds for observations, CUDA event milliseconds for device stages, Dispatch times are virtual metadata",
        "qualification": "Correctness and lifecycle trace, not a benchmark. Host gates precede CUDA calls. Cancellation overlap with a GPU kernel is not claimed. Host asynchronous admission provides no GPU concurrency or transfer overlap.",
        "checks": checks,
        "provenance": {"runtime_source_revision": runtime_revision, "adaptive_source_revision": adaptive_revision},
        "inputs": {"original": original.tolist(), "current": image.tolist()},
        "results": records,
        "observations": [asdict(row) for row in observations],
        "cleanup": "explicit close completed on the resource owner before reconciliation returned zero outstanding jobs",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-revision")
    parser.add_argument("--adaptive-revision")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(runtime_revision=args.runtime_revision,
        adaptive_revision=args.adaptive_revision), indent=2) + "\n", encoding="utf-8")
    print(f"Saved actual application trace to {args.output}")


if __name__ == "__main__":
    main()
