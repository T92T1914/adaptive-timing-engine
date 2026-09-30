# Optional owned CUDA application

`examples/cuda_histogram.py` connects Python input to an existing reusable
histogram in Heterogeneous Batch Runtime. It is explicit optional execution.
Importing the default package or loading the example does not import NumPy,
the native runtime or CUDA. Simulation, planner APIs and historical studies
retain their existing behavior.

Install the runtime using its [optional CUDA build instructions](https://github.com/T92T1914/heterogeneous-batch-runtime/blob/main/docs/python-cuda.md), then install this package in that environment:

```sh
python -m pip install .
python examples/cuda_histogram.py --output reports/cuda-application.json
```

The example uses only synthetic images. It checks every output bin against an
independent NumPy bincount oracle. Its deterministic host gates demonstrate a
verified submission snapshot, changed caller input, repeated results, queued
cancellation, a cancellation request while the callable is running, an obsolete
completed generation, backpressure, a synthetic host exception and explicit
shutdown with outstanding work. Host gates precede CUDA invocation. They do not
claim that cancellation happened during a GPU kernel, device failure or reset.

`CudaHistogramExecutor` is a narrow adapter over `OwnerExecutor`, which uses one
worker. Factory, repeated operations, close and reference release run on that
worker. The runtime wrapper owns one dedicated native CUDA thread with the
selected device current. Thread-affine methods are never dispatched to an
arbitrary pool worker. `OwnerExecutor` requires waiting shutdown and closes its
resource after admitted running work returns. It preserves RealExecutor's IDs,
physical states and creating-thread reconciliation.

`submit_image` requires an exact uint8 NumPy array with the configured shape
and C contiguous layout. The submission copy finishes before successful return.
Caller mutation after that return cannot change admitted data. The caller must
exclude external native writers during the copy. The GIL alone does not provide
that exclusion. A rejected submission is not admitted and may be retried with
the same ID. Capacity includes running, queued and completed unreconciled work.
It bounds retained image jobs, not lifetime ID history or all process memory.

The synchronous CUDA call returns only after upload, computation, download and
stream synchronization. A cancellation request cannot free that live snapshot
or release its resource reservation. A queued job may be confirmed canceled
without invoking CUDA. A running job retains its physical completed or failed
outcome. The owner adopts a result only if `usable` is true in the current
generation. A physically completed obsolete result remains inspectable but is
not current application state.

Trace observations use host monotonic seconds. Device stages use CUDA event
milliseconds, and native host totals include the native synchronous call.
Dispatch times remain virtual planner metadata. The trace records complete
histogram outputs and cancellation/stale states. No overlapping intervals are
summed into a fabricated latency. This is an application correctness trace,
not a timing benchmark or a GPU speedup claim.

Use an explicit `with` block or `close()`, then reconcile remaining outcomes.
Do not drop the adapter as a shutdown strategy. Its worker and resource have to
finish normally. Exceptions retain physical failure evidence and close still
runs on the owner. Arbitrary interpreter termination and hardware loss are not
supported recovery cases. Host ownership tests and host sanitizers do not
establish GPU safety. GPU counters and WDDM sanitizer initialization are separate
unpassed diagnostic gates.
