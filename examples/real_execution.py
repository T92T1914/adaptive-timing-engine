"""Run two real CPU callables from virtual dispatches and retain observations."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_timing import Event, Executor, Policy, build_plan
from adaptive_timing.execution import RealExecutor


def run():
    generation = 1
    events = (Event("first", 0, 0, 1, 0.1), Event("second", 1, 1, 2, 0.1))
    inputs = {"first": tuple(range(1, 11)), "second": tuple(range(11, 21))}
    virtual = Executor()
    virtual.install(build_plan(events, Policy(use_noise=False)))
    results = []
    with RealExecutor() as execution:
        for virtual_now in (0.0, 1.0):
            for dispatched in virtual.poll(virtual_now):
                if dispatched.status != "dispatched":
                    raise RuntimeError("example unexpectedly expired")
                values = inputs[dispatched.id]
                # Capture immutable input by value. Returning sum is physical
                # completion for this synchronous CPU operation.
                execution.submit(dispatched, generation,
                                 lambda cancel, values=values: sum(values))
        while execution.outstanding:
            completed = execution.wait(generation, timeout=2)
            if not completed:
                raise TimeoutError("example CPU work did not complete")
            results.extend(completed)
        observations = execution.observations()
    if {row.dispatch.id: row.value for row in results if row.usable} != {"first": 55, "second": 155}:
        raise AssertionError("unexpected completed result")
    return {
        "schema_version": 1,
        "mode": "real synchronous CPU callables from virtual dispatches",
        "clock": "monotonic wall clock, separate from virtual Dispatch times",
        "qualification": "Execution example, not a timing benchmark or a real deadline guarantee",
        "results": [asdict(row) for row in results],
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
        print(f"Saved actual completion observations to {args.output}")


if __name__ == "__main__":
    main()
