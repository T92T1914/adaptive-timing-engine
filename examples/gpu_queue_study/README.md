# GPU queue study

This folder holds the calculation I put under the worker's queue model. It is a small fixed PyTorch model cut into chunks, plus a timing harness. The engine package does not import any of it, and the default modules of the package still need only the standard library.

## Files

1. `model.py` builds 32 blocks of a 2048 wide linear layer and GELU from a seeded generator, and groups them into chunks of 1, 2, 4 or 8 blocks.
2. `harness.py` times one chunk at a time. Every timed region is closed by `torch.cuda.synchronize()`, so a timing includes the work on the GPU and not only the launch.
3. `requirements.txt` pins the `torch` build I use.
4. `measure_service.py` times every chunk of every chunk size and writes [`docs/evidence/gpu-service-time.json`](../../docs/evidence/gpu-service-time.json). It holds the card, the driver, the toolkit, the `torch` build, and the count, median, 5th and 95th percentile for each chunk size. It also keeps the duration of every whole pass, which is the sum of that pass's chunk timings, so the queue model can read the distribution of one complete calculation.
5. `arrival_run.py` runs the real worker over the calculation with generated arrivals and writes [`gpu-arrival-runs.json`](../../docs/evidence/gpu-arrival-runs.json). It reads the committed predictions, and its arrival loop sleeps and never spins.
6. `pass_diagnostic.py` times whole passes alone and inside the worker, and reruns four cases at chunk size 2 under two interpreter switch intervals. It made [`gpu-pass-diagnostic.json`](../../docs/evidence/gpu-pass-diagnostic.json) while the arrival loop still spun. It now imports the sleeping loop, so a run today does not repeat those conditions.
7. `cooperative_stop.py` runs three modes on the same arrivals and writes [`gpu-cooperative-stop.json`](../../docs/evidence/gpu-cooperative-stop.json). In the stop mode the calculation returns early between chunks when its request is stale.
8. `pass_timing.py` times every pass inside the worker and the delay from a request's arrival to the start of its calculation, in the plain and stop modes, and writes [`gpu-pass-timing.json`](../../docs/evidence/gpu-pass-timing.json) with its [samples](../../docs/evidence/gpu-pass-timing-samples.json.gz). The table prints with `python tools/gpu_pass_timing_tables.py` from the repository root. The study document does not cover this run yet.
9. `depth.py` is a pass that keeps at most d chunks queued on its stream. Between launches it drops finished work with an event query, which does not wait, waits for the oldest chunk only when the queue is full, and then lets a stale pass stop. Chunks already queued still run.
10. `chunk_depth.py` times every chunk at depths of 1, 2, 4, 8 and the whole pass, back to back outside the worker with no stale pass, and writes [`gpu-depth-chunk-times.json`](../../docs/evidence/gpu-depth-chunk-times.json). A chunk time is the interval on the card between the events after consecutive chunks, so idle time between launches is part of it. The first chunk of a pass is different, since its interval starts at an event recorded on an idle stream and so holds the launch from idle. The summaries pool it with the rest and take a percentile as the value at index `int(share * count)` of the sorted times. The saved passes keep launch order. `python tools/gpu_depth_tables.py` prints the table from the repository root.

Read the pass timing results with these points in mind. Only requests that started have a sample, so a replaced request adds nothing to the start delay. A start is counted as idle at arrival when the previous pass had taken its end timestamp before the request arrived, though the worker may still have been finishing its bookkeeping. A condition adds blocks until it holds enough starts, so the plain and stop modes share the arrivals of their common seeds only, and the conditions ran in a fixed order. The card readings are taken between blocks on a working desktop. They include this study's own process and its last block, so they do not measure the load of other programs, and the core clock they show is not the clock during a pass. The toolkit version is copied from the service time results, and `torch_cuda` is read during the run.

The [study document](../../docs/gpu-queue-study.md) holds the results, the misses and the limits. The arrival and stop tables print from the saved files with `python tools/gpu_arrival_tables.py` and `python tools/gpu_stop_tables.py`, run from the repository root.

The retained run of `measure_service.py` was one pass of 300 repeats per chunk size on the RTX 4090 while the desktop was idle but its usual background programs held about 40 percent of the card. That load widens the upper percentile, and the median is the steadier number. It is one run on one machine.

## Environment

The environment is separate from the one the tests use. I made it with `python -m venv` and installed the pinned build for CUDA 13.0 from the PyTorch index.

```sh
python -m pip install -r requirements.txt --index-url https://download.pytorch.org/whl/cu130
```

## Can CUDA remove work already placed on a stream

No call that I found does. The CUDA Runtime API page for [stream management](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__STREAM.html) lists no function that cancels or clears queued work. `cudaStreamDestroy` returns at once and the stream's resources are released "once the device has completed all work in" the stream, so it lets pending work finish. `cudaStreamQuery` only reports whether the work is done. I read that page and did not read the event, execution control or driver API pages, so this is the answer for the stream calls and not a proof about every interface.

That is why the cooperative stop in `cooperative_stop.py` has the calculation look at the current generation between chunks. A chunk already placed on the stream runs to its end, and the next one is not launched.
