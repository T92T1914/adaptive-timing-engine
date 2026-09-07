# Read the visual example

![A controlled worker experiment reduces 101 requests to two calculations and rejects one obsolete result.](adaptive-timing-example.png)

One calculation can run while one request waits. New requests replace the waiting request; an obsolete result is rejected when the active calculation finishes. The diagram shows event order, not elapsed time.

## Reproduce the values

Run from this repository using its documented Python environment.
Evidence was checked against commit `6e12229`.

```python
from adaptive_timing.experiment import worker_experiment

result = worker_experiment()
print(result["request_count"])
print(result["computed_payloads"])
print(result["stats"])
```

The first calculation is deliberately held with a thread event. Payloads 1
through 100 arrive while it is active. The waiting slot is replaced 99 times,
and only payloads 0 and 100 are calculated. Payload 0's obsolete result is
rejected. Payload numbers begin at zero; request revisions begin at one.
The current retained result is payload 100, revision 101.

The experiment does not establish a speedup, a CPU contention limit, or a
guarantee about operating system scheduling. The separate [timing policy
experiments](evidence/results.md) use synthetic workloads and are not a
validated model of human performance.

## Inspect the source

The [underlying values](visual-example-data.json) include the source and
conditions. A [vector copy](adaptive-timing-example.svg) is available for a closer look.
The figure is a visual explanation of the public implementation, not a
screenshot of an external application.
