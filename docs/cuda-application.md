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
python tools/check_cuda_trace.py reports/cuda-application.json
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
and C contiguous layout. Array subclasses are accepted, but copying first
uses a base NumPy array so an overridden copy cannot retain shared caller data.
The submission copy finishes before successful return.
Caller mutation after that return cannot change admitted data. The caller must
exclude external native writers during the copy. The GIL alone does not provide
that exclusion. A rejected submission is not admitted and may be retried with
the same ID. Capacity includes running, queued and completed unreconciled work.
Full, closed or invalid submissions are rejected before allocating the image
snapshot. Admission is checked again after copying, and a copy failure consumes
neither the ID nor capacity. The first check does not reserve a slot.
Capacity bounds retained image jobs, not lifetime ID history or all process memory.

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

## Retained execution

The [full actual trace](cuda-application-trace.json) records the runtime source
`fa7afb4b798a1481ffa34a85248ba63d2ea029a2` and this application's source
`3ce1b6571899faed99fc7a2eabdf5862cf5639ad`. Its published LF-byte SHA-256 is
`da83bf0e4fdd0190fbcdb936b785d175e8a6a18befc2530424dc3b42d43065f9`.
The trace retains both synthetic input arrays and all four completed GPU output
arrays. The independent scalar checker inspects those arrays and seven physical
outcomes without NumPy, CUDA or rerunning work. Its regression also rejects a
changed bin even when total pixel count remains constant.

Windows fresh CPU and optional CUDA environments passed the Adaptive suite.
The native runtime's [verification report](https://github.com/T92T1914/heterogeneous-batch-runtime/blob/main/docs/python-cuda-results.md)
records actual RTX 4090 contracts, fresh packages, successful hosted Clang host
TSan and a separate WSL TSan initialization restriction. It preserves both
results and makes no GPU sanitizer claim. The existing Inter/Clair/Obscur
explorers and all historical study artifacts are unchanged. This application
adds inspectable JSON and a command-line checker without a presentation redesign.
