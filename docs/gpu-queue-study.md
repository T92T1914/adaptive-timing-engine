# GPU queue study

The [queue model](queue-model.md) predicts what `LatestWorker` computes and keeps when requests arrive faster than one calculation finishes. That study ran on a virtual clock with assumed service times. This one puts a real calculation under the worker: a small PyTorch model on an RTX 4090, cut into chunks, with arrivals generated in real time. I measured the service time first, wrote the predictions from it, committed them, and only then ran the arrivals.

Most of the predictions miss, some by a wide margin. Two causes explain nearly all of it, and I found both only after the run.

## What was run

The calculation is a fixed stack of eight matrix blocks, split into chunks of 1, 2, 4 or 8 blocks. The code is in [`examples/gpu_queue_study`](../examples/gpu_queue_study) with its own pinned environment. The package itself still imports only the standard library.

1. [`gpu-service-time.json`](evidence/gpu-service-time.json) holds 300 passes per chunk size. Every timed region is closed by a synchronize, so the clock stops when the GPU finishes and not when the launch returns. A pass is the sum of its chunk timings.
2. [`gpu-queue-predictions.json`](evidence/gpu-queue-predictions.json) applies the closed forms of the queue model to that measured distribution. It was committed before any arrival run (pull request 45).
3. [`gpu-arrival-runs.json`](evidence/gpu-arrival-runs.json) is the arrival run. For each chunk size, arrival process and load, the real worker handled 1000 requests for each of three seeds. Arrivals are scheduled in units of the predicted mean pass, so a load of 0.6 means 0.6 arrivals per predicted mean pass. A computation counts as adopted when no newer request exists at the moment it ends.
4. [`gpu-pass-diagnostic.json`](evidence/gpu-pass-diagnostic.json) was made after the arrival run, to look into the misses. It changes no prediction.

Environment: NVIDIA GeForce RTX 4090, driver 621.24, CUDA toolkit 13.0, `torch` 2.14.1+cu130, Windows. The card also drives the desktop. The service time file records 41 percent utilization before its measurement, and I took the arrival run after four hours without keyboard or mouse input.

```sh
python tools/gpu_arrival_tables.py
```

That command prints every table below from the saved file.

## The first table

Computed share is the fraction of requests that start computing. Mean adopted age is measured in predicted mean passes. Stale GPU time is the share of busy GPU time spent on results that were thrown away. A star marks a measured mean within three standard errors of the prediction. Otherwise the cell is a miss.

7 of 180 comparisons are within noise, and the adopted check disagreed with the worker's own count 18 times.

For periodic and bursty arrivals the prediction is the Poisson form at the same long run rate. It is not derived for those processes, and the committed predictions say so.

### Poisson arrivals

| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured | Stale GPU time predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3 | 0.953 | 0.902 | 1.070 | 0.645 | 0.305 | 0.177 |
| 1 | 0.6 | 0.853 | 0.807 | 1.081 | 0.652 | 0.506 | 0.320 |
| 1 | 0.9 | 0.743 | 0.717 | 1.063 | 0.659 | 0.640 | 0.459 |
| 1 | 1.5 | 0.561 | 0.582 | 0.995 | 0.662 | 0.797 | 0.640 |
| 1 | 3.0 | 0.322 | 0.329\* | 0.845 | 0.574 | 0.935 | 0.863 |
| 2 | 0.3 | 0.953 | 0.888 | 1.065 | 0.775 | 0.309 | 0.211 |
| 2 | 0.6 | 0.851 | 0.778 | 1.073 | 0.769 | 0.509 | 0.372 |
| 2 | 0.9 | 0.742 | 0.670 | 1.055 | 0.790 | 0.641 | 0.512 |
| 2 | 1.5 | 0.560 | 0.523 | 0.992 | 0.761 | 0.795 | 0.715 |
| 2 | 3.0 | 0.322 | 0.276 | 0.858 | 0.698 | 0.934 | 0.903\* |
| 4 | 0.3 | 0.954 | 0.879 | 1.072 | 0.842 | 0.302 | 0.221 |
| 4 | 0.6 | 0.854 | 0.763 | 1.090 | 0.839 | 0.501 | 0.398 |
| 4 | 0.9 | 0.745 | 0.648 | 1.078 | 0.848 | 0.635 | 0.547 |
| 4 | 1.5 | 0.563 | 0.493 | 1.025 | 0.833 | 0.793 | 0.715 |
| 4 | 3.0 | 0.323 | 0.253 | 0.892 | 0.724 | 0.936 | 0.912 |
| 8 | 0.3 | 0.956 | 0.870 | 1.087 | 0.922 | 0.290 | 0.237 |
| 8 | 0.6 | 0.859 | 0.739 | 1.120 | 0.948 | 0.488 | 0.437 |
| 8 | 0.9 | 0.750 | 0.616 | 1.122 | 0.959 | 0.625 | 0.576 |
| 8 | 1.5 | 0.567 | 0.455 | 1.079 | 0.907 | 0.790 | 0.745 |
| 8 | 3.0 | 0.324 | 0.218 | 0.930 | 0.748 | 0.940 | 0.926 |

### Periodic arrivals

| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured | Stale GPU time predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3 | 0.953 | 1.000 | 1.070 | 0.657 | 0.305 | 0.000 |
| 1 | 0.6 | 0.853 | 1.000 | 1.081 | 0.632 | 0.506 | 0.000 |
| 1 | 0.9 | 0.743 | 1.000 | 1.063 | 0.633 | 0.640 | 0.274 |
| 1 | 1.5 | 0.561 | 0.812 | 0.995 | 0.405 | 0.797 | 0.923 |
| 1 | 3.0 | 0.322 | 0.410 | 0.845 | 0.795\* | 0.935 | 0.998 |
| 2 | 0.3 | 0.953 | 1.000 | 1.065 | 0.735 | 0.309 | 0.000 |
| 2 | 0.6 | 0.851 | 1.000 | 1.073 | 0.749 | 0.509 | 0.000 |
| 2 | 0.9 | 0.742 | 1.000 | 1.055 | 0.613 | 0.641 | 0.693 |
| 2 | 1.5 | 0.560 | 0.714 | 0.992 | 0.527 | 0.795 | 0.993 |
| 2 | 3.0 | 0.322 | 0.003 | 0.858 | 1.246\* | 0.934 | 0.998 |
| 4 | 0.3 | 0.954 | 1.000 | 1.072 | 0.771 | 0.302 | 0.000 |
| 4 | 0.6 | 0.854 | 1.000 | 1.090 | 0.824 | 0.501 | 0.011 |
| 4 | 0.9 | 0.745 | 0.998 | 1.078 | 0.667 | 0.635 | 0.784 |
| 4 | 1.5 | 0.563 | 0.640 | 1.025 | 1.280\* | 0.793 | 0.998 |
| 4 | 3.0 | 0.323 | 0.002 | 0.892 | 0.740\* | 0.936 | 0.998 |
| 8 | 0.3 | 0.956 | 1.000 | 1.087 | 0.907 | 0.290 | 0.000 |
| 8 | 0.6 | 0.859 | 1.000 | 1.120 | 0.932 | 0.488 | 0.123 |
| 8 | 0.9 | 0.750 | 0.970 | 1.122 | 0.658 | 0.625 | 0.907 |
| 8 | 1.5 | 0.567 | 0.609 | 1.079 | 1.052\* | 0.790 | 0.999 |
| 8 | 3.0 | 0.324 | 0.002 | 0.930 | 0.673 | 0.940 | 0.998 |

### Bursty arrivals

| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured | Stale GPU time predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3 | 0.953 | 0.193 | 1.070 | 0.639 | 0.305 | 0.039 |
| 1 | 0.6 | 0.853 | 0.190 | 1.081 | 0.639 | 0.506 | 0.080 |
| 1 | 0.9 | 0.743 | 0.184 | 1.063 | 0.662 | 0.640 | 0.114 |
| 1 | 1.5 | 0.561 | 0.170 | 0.995 | 0.638 | 0.797 | 0.205 |
| 1 | 3.0 | 0.322 | 0.148 | 0.845 | 0.638 | 0.935 | 0.379 |
| 2 | 0.3 | 0.953 | 0.193 | 1.065 | 0.731 | 0.309 | 0.060 |
| 2 | 0.6 | 0.851 | 0.188 | 1.073 | 0.767 | 0.509 | 0.056 |
| 2 | 0.9 | 0.742 | 0.181 | 1.055 | 0.764 | 0.641 | 0.133 |
| 2 | 1.5 | 0.560 | 0.168 | 0.992 | 0.751 | 0.795 | 0.254 |
| 2 | 3.0 | 0.322 | 0.143 | 0.858 | 0.753 | 0.934 | 0.440 |
| 4 | 0.3 | 0.954 | 0.192 | 1.072 | 0.803 | 0.302 | 0.064 |
| 4 | 0.6 | 0.854 | 0.187 | 1.090 | 0.832 | 0.501 | 0.068 |
| 4 | 0.9 | 0.745 | 0.180 | 1.078 | 0.810 | 0.635 | 0.162 |
| 4 | 1.5 | 0.563 | 0.167 | 1.025 | 0.822 | 0.793 | 0.264 |
| 4 | 3.0 | 0.323 | 0.140 | 0.892 | 0.815 | 0.936 | 0.484 |
| 8 | 0.3 | 0.956 | 0.192 | 1.087 | 0.911 | 0.290 | 0.064 |
| 8 | 0.6 | 0.859 | 0.186 | 1.120 | 0.914 | 0.488 | 0.091 |
| 8 | 0.9 | 0.750 | 0.178 | 1.122 | 0.911 | 0.625 | 0.167 |
| 8 | 1.5 | 0.567 | 0.165 | 1.079 | 0.896 | 0.790 | 0.272 |
| 8 | 3.0 | 0.324 | 0.135 | 0.930 | 0.880 | 0.940 | 0.462 |

## Why the misses happen

### The pass is shorter than the number the predictions assume

The predictions read the sum of per chunk timings, each with its own synchronize. The worker runs a whole pass and synchronizes once. The diagnostic times both, with 300 passes per size and 1000 requests inside the worker under Poisson arrivals at load 0.6.

| Chunk blocks | Assumed mean pass (ms) | Whole pass alone (ms) | Whole pass inside the worker (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 6.23 | 4.54 | 4.14 |
| 2 | 5.27 | 4.54 | 4.17 |
| 4 | 4.87 | 4.39 | 4.21 |
| 8 | 4.38 | 4.49 | 4.23 |

This explains the mean adopted age. An adopted result takes at least one pass, so its age is bounded below by a pass. At chunk size 1 the measured age at low load is 0.645 predicted passes, which is about 4.0 ms, and a whole pass inside the worker takes about 4.1 ms. Every age prediction is in units of the longer assumed pass, so the measured ages sit below them. The gap is largest at chunk size 1 (6.23 ms assumed) and smallest at size 8 (4.38 ms assumed), which matches the age misses shrinking with chunk size.

It also moves the load. With a shorter real pass, a nominal load of 0.6 is a real load of about 0.4 at chunk size 1. That alone would raise the computed share above the prediction, and the next cause hides it.

### The thread that generates arrivals starves the worker

My arrival loop sleeps until about 2 ms before the next arrival and spins for the rest. A spinning Python thread keeps the interpreter lock and gives it up only at the switch interval, 5 ms by default. The worker needs the lock to start its calculation and to read the result back, so it starts late or not at all.

The evidence is a pattern and one test. Periodic arrivals at load 3.0 are spaced under 2 ms apart for chunk sizes 2, 4 and 8, so the loop never sleeps, and the worker computed 0.3 percent of requests or less. At chunk size 1 the spacing is 2.08 ms, the loop does sleep, and the share is 0.410. Bursty arrivals put five requests 0.3 ms apart, and the worker computes about one in five, the share you get if it only ever starts after the whole burst has arrived. The test reran four cases at chunk size 2 with the switch interval at 0.2 ms, one seed and 1000 requests each:

| Case | Computed share, 5 ms interval | 0.2 ms interval |
| --- | ---: | ---: |
| Periodic, load 3.0 | 0.002 | 0.355 |
| Bursty, load 0.6 | 0.194 | 0.368 |
| Poisson, load 0.6 (predicted 0.851) | 0.771 | 0.899 |
| Periodic, load 0.9 | 1.000 | 1.000 |

The two collapsed cases recover most of the way. The Poisson case moves from below the prediction to above it, which is the direction the shorter pass predicts. I did not rerun the full table with the shorter interval, so the numbers above stay as the first run produced them.

### Periodic arrivals that never interrupt

A periodic arrival with spacing longer than a pass never lands inside a computation, so nothing is replaced and no result is stale. That is the computed share of 1.000 in the periodic rows at loads 0.3 and 0.6, with a stale share of 0.000 in six of the eight. The Poisson form predicts computed shares near 0.95 and 0.85 and stale shares near 0.3 and 0.5 for them, because it assumes random arrivals. This is the same miss the virtual clock study found, and the explanation is the same.

At periodic loads 0.9 and 1.5 the spacing is close to a pass, so small variation in the pass decides whether an arrival interrupts it. Those cells swing between the two regimes and I do not explain them further.

### Not explained

1. The stale GPU time of the Poisson rows is below the prediction at every load and chunk size. The shorter pass lowers the real load and so lowers the stale share, which points the right way, but I did not size it.
2. The seven cells marked as within noise, all in periodic rows or at load 3.0, are not evidence that the model holds there. With the worker starved the way it was, I take them as chance.
3. The adopted check at the end of a computation and the worker's own stale count are two lock acquisitions. They disagreed 18 times across 180 runs, a request slipping in between. It is small, and I did not trace each case.

## Limits

One card, one machine, one model, arrivals generated by a script. The service time distribution comes from 300 passes per size. The diagnostic is one seed and one load. Nothing here is a latency bound, a claim about preempting GPU work or a measure of anyone's performance.

The cooperative stop, where the calculation checks the current generation between chunks and returns early, is the next step and is not in this document.
