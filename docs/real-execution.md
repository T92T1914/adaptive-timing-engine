# Optional real execution

`RealExecutor` runs synchronous Python callables and records when work actually
starts and finishes. It accepts the existing `Dispatch` record without changing
the virtual executor, the causal policy or either retained comparison study.
The default demonstration still runs entirely as a simulation.

```sh
python examples/real_execution.py --output reports/real-execution.json
python -m unittest discover -s tests -p test_execution.py -v
```

The example submits two immutable input tuples from virtual dispatches and
checks their real CPU sums. Its JSON includes the dispatch assumptions and
separate monotonic wall clock observations. It is an execution example, not a
benchmark. The virtual clock is advanced by the example rather than paced
against elapsed wall time. None of those observations establishes an operating
system wakeup bound or a real deadline guarantee.

## Ownership and completion

The caller uses `submit`, `cancel`, `reconcile` and `observations` on the thread
that created the executor. A thread pool runs only the supplied operations.
`max_workers` bounds active operations, and `capacity` bounds all accepted work
that has not yet been reconciled. A full executor rejects another submission
without consuming its ID. The caller can reconcile and retry that dispatch.

Worker admission has a separate gate. A thread pool can enqueue work before
thread startup raises an exception. The gate opens only after submission
returns successfully. If submission fails, an existing worker may remove the
queued item later, but it cannot invoke that rejected callable after the
resource has been released. Error formatting also cannot interrupt cleanup.

Each named resource has one reservation. If the virtual plan estimates that a
task finishes after 10 milliseconds but its callable is still running, another
callable for that resource remains queued. Other resources may run when a
worker is available. A reservation is conservatively retained until the owner
reconciles the completed future. Slower reconciliation can delay subsequent
work, but cannot release a resource before the operation returns.

An operation receives a `threading.Event` for cancellation. It must hold owned
inputs or references whose contents remain stable until it finishes. A closure
retains a reference, but does not make a mutable array immutable. The executor
retains the callable through completion and reconciliation. Results belong to
the caller after reconciliation. An event ID cannot be reused during a session.

This first adapter accepts synchronous callables only. An accelerator wrapper
must wait for its submitted device work and finish any required copy before
returning. That also applies when cancellation or an error occurs. Returning a
launch handle while device work continues violates the adapter contract. No
GPU backend is imported, required or claimed by this module.

### Explicit native CPU example

With the separately built `heterogeneous-batch-runtime` package installed in
the selected environment, run:

```sh
python examples/native_workloads.py --output reports/native-execution.json
```

This optional example runs real `masked_reduce`, `tile_histogram` and
`stencil3x3` calls through one executor worker, with two native kernel threads.
NumPy and the runtime are imported only when the example is explicitly run.
They remain absent from the default package dependencies.

The owner makes private array copies before submission. A thread gate holds
the queue while the caller's original arrays are changed, then releases the
three operations. Independent small oracles check the reduction, all histogram
bins including partial edge tiles, and the stencil's interior and copied border.
The returned arrays must own their storage. A failed or stale result fails the
example rather than producing a successful receipt.

The native Python binding takes its own additional snapshot when the callable
actually starts. That later copy alone would not preserve submission values
if the callable captured mutable caller arrays. These are two distinct copy
boundaries. The example's copies and intentional queue gate belong in the
reported execution path and are not evidence of a performance improvement.
This integration uses the synchronous CPU interface. It does not establish GPU
execution or make future asynchronous backends safe without synchronization.

## Distinct states

| Observation or result | Meaning |
| --- | --- |
| `Dispatch.status == "dispatched"` | The virtual scheduler issued the event. It does not prove submission to this executor. |
| `submitted` | The real executor accepted the callable and owns its pending work. |
| `started` | A worker entered the callable. |
| `cancel_requested` | The owner requested cancellation. Work may still be running. |
| `cancel_confirmed` | Queued work cannot start, or a running callable acknowledged the request after cleanup. |
| `completed` | The synchronous callable returned a value. `False` is an ordinary successful value. |
| `failed` | Submission or execution failed. The error is retained in the result. |
| `reconciled` | The owner consumed the terminal result and released its reservation. |

`ExecutionResult.status` records `completed`, `failed` or `canceled` independently
from `stale` and `cancellation_requested`. A stale result retains its value and
physical outcome for inspection. It does not overwrite current work. `usable`
is true only for a successful result in the supplied current generation with
no cancellation request. The owner must adopt that result before advancing
the generation. The executor does not update a scheduler behind the caller.

Cancellation before a callable starts can be confirmed immediately. Once it
starts, cancellation is cooperative. A callable acknowledges by raising
`OperationCancelled` only after its cleanup and any external work have finished.
Returning normally after a cancellation request remains physical completion.
A request arriving after completion does not retroactively make the operation
canceled, and its returned value is no longer marked usable.

`close()` stops submission, requests cancellation and waits for worker cleanup.
`close(wait_for_completion=False)` returns without waiting. It does not stop a
running callable or release its resource. Keep the executor and reconcile until
`outstanding` is zero. Python also waits for these worker threads at process exit.
An operation that ignores cancellation and never returns can block shutdown.
There is no forced thread termination or claim of bounded shutdown latency.

## Observation limits

`submitted_at`, `started_at`, `completed_at` and `reconciled_at` use
`time.monotonic()` in this process. Their origin is not a UTC timestamp, and they
cannot be subtracted from virtual `Dispatch.actual_start` or `Dispatch.finish`.
The difference between completion and reconciliation is owner observation
delay. It is separate from callable duration and queue delay. Those durations
can overlap across resources and must not be summed into elapsed wall time.

Drain `observations()` to collect transitions. Draining observations does not
consume results or start queued work. The observation buffer and issued ID set
grow with the session. Start a fresh executor for an independent session and
drain observations regularly. This is not a hard real time logging system.

The tests use explicit thread gates to cover same resource serialization,
independent resources, stale success, completion racing with cancellation,
cooperative cleanup, queued cancellation, exceptions, queue capacity, owner
thread enforcement and retained inputs during nonwaiting shutdown. They do not
establish device performance, accelerator recovery or universal throughput.
