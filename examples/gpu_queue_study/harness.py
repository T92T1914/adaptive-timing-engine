"""Timing harness. Every timed region is closed by a synchronize, so the clock
stops when the GPU finishes and not when the launch returns."""
import time

import torch


def timed(function, *args):
    """Return the result of `function` and its elapsed seconds, GPU work included."""
    torch.cuda.synchronize()
    start = time.perf_counter()
    result = function(*args)
    torch.cuda.synchronize()
    return result, time.perf_counter() - start


def service_times(chunk_list, activations, runs, warmup=20):
    """Time `runs` full passes of the chunk list and return one duration per chunk per pass."""
    from model import run_chunk
    for _ in range(warmup):
        value = activations
        for chunk in chunk_list:
            value = run_chunk(chunk, value)
    torch.cuda.synchronize()
    durations = [[] for _ in chunk_list]
    for _ in range(runs):
        value = activations
        for index, chunk in enumerate(chunk_list):
            value, seconds = timed(run_chunk, chunk, value)
            durations[index].append(seconds)
    return durations
