# GPU queue study

This folder holds the calculation I put under the worker's queue model. It is a small fixed PyTorch model cut into chunks, plus a timing harness. The engine package does not import any of it, and the package still needs only the standard library.

## Files

1. `model.py` builds 32 blocks of a 2048 wide linear layer and GELU from a seeded generator, and groups them into chunks of 1, 2, 4 or 8 blocks.
2. `harness.py` times one chunk at a time. Every timed region is closed by `torch.cuda.synchronize()`, so a timing includes the work on the GPU and not only the launch.
3. `requirements.txt` pins the `torch` build I use.

## Environment

The environment is separate from the one the tests use. I made it with `python -m venv` and installed the pinned build for CUDA 13.0 from the PyTorch index.

```sh
python -m pip install -r requirements.txt --index-url https://download.pytorch.org/whl/cu130
```

## Can CUDA remove work already placed on a stream

No call that I found does. The CUDA Runtime API page for [stream management](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__STREAM.html) lists no function that cancels or clears queued work. `cudaStreamDestroy` returns at once and the stream's resources are released "once the device has completed all work in" the stream, so it lets pending work finish. `cudaStreamQuery` only reports whether the work is done. I read that page and did not read the event, execution control or driver API pages, so this is the answer for the stream calls and not a proof about every interface.

That is why the cooperative stop in later steps has the calculation look at the current generation between chunks. A chunk already placed on the stream runs to its end, and the next one is not launched.
