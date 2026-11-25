# GPU queue study

The [queue model](queue-model.md) predicts what `LatestWorker` computes and keeps when requests arrive faster than one calculation finishes. That study ran on a virtual clock with assumed service times. This one puts a real calculation under the worker: a small PyTorch model on an RTX 4090, cut into chunks, with arrivals generated in real time. I measured the service time first, wrote the predictions from it, committed them, and only then ran the arrivals.

Most of the predictions miss, some by a wide margin. The first run used an arrival loop that spun, and the worker thread was probably starved. I replaced the spin with a sleep and ran the whole table again, and this document holds the second run. Some misses stay unexplained, and the document says which.

## What was run

The calculation is a fixed stack of 32 matrix blocks, split into chunks of 1, 2, 4 or 8 blocks. The code is in [`examples/gpu_queue_study`](../examples/gpu_queue_study) with its own pinned environment. The package itself still imports only the standard library.

1. [`gpu-service-time.json`](evidence/gpu-service-time.json) holds 300 passes per chunk size. Every timed region is closed by a synchronize, so the clock stops when the GPU finishes and not when the launch returns. A pass is the sum of its chunk timings.
2. [`gpu-queue-predictions.json`](evidence/gpu-queue-predictions.json) applies the closed forms of the queue model to that measured distribution. It was committed before any arrival run (pull request 45).
3. [`gpu-arrival-runs.json`](evidence/gpu-arrival-runs.json) is the arrival run, taken with the sleeping arrival loop. For each chunk size, arrival process and load, the real worker handled 1000 requests for each of three seeds. Arrivals are scheduled in units of the predicted mean pass, so a load of 0.6 means 0.6 arrivals per predicted mean pass. A computation counts as adopted when no newer request exists at the moment it ends.
4. [`gpu-pass-diagnostic.json`](evidence/gpu-pass-diagnostic.json) was made after the first arrival run, which used the spinning loop, to look into its misses. It changes no prediction, and its pass timings came from that loop.

Environment: NVIDIA GeForce RTX 4090, driver 621.24, CUDA toolkit 13.0, `torch` 2.14.1+cu130, Windows. The card also drives the desktop, and other programs held part of it during the run. `nvidia-smi` was read just before and just after every one of the 180 runs, and the readings are in the saved file. Utilization ranged from 10 to 99 percent with a mean of 51 percent, and memory used from 7,079 to 7,378 MiB. That memory includes this process, and the reading taken right after a run includes that run, so neither measures the load from other programs alone. Windows reported more than 11 hours since the last keyboard or mouse input when I started the run at 18:57 on November 22, 2026. A reading of input idle time at 19:21 showed input at about 19:19, close to the end of the run, so I cannot say which cases it touched. The last ones in the file are the bursty rows at chunk size 8.

```sh
python tools/gpu_arrival_tables.py
```

That command prints every table below from the saved file.

## The table

Computed share is the fraction of requests that start computing. Mean adopted age is measured in predicted mean passes. Stale GPU time is the share of busy GPU time spent on results that were thrown away. A star marks a measured mean within three standard errors of the prediction. Otherwise the cell is a miss.

9 of 180 comparisons are within noise. The adopted check and the worker's own stale count differed by 17 requests in total, spread over 15 of 180 runs.

For periodic and bursty arrivals the prediction is the Poisson form at the same long run rate. It is not derived for those processes, and the committed predictions say so.

The first run, with the spinning loop, had 7 of 180 within noise and a periodic computed share of 0.002 at load 3.0 for chunk sizes 2, 4 and 8. It stays in the history of this file. This table replaces it, and the sentences below describe this table unless they say otherwise.

### Poisson arrivals

| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured | Stale GPU time predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3 | 0.953 | 0.974 | 1.070 | 0.636 | 0.305 | 0.157 |
| 1 | 0.6 | 0.853 | 0.956 | 1.081 | 0.457 | 0.506 | 0.210 |
| 1 | 0.9 | 0.743 | 0.921 | 1.063 | 0.481 | 0.640 | 0.316 |
| 1 | 1.5 | 0.561 | 0.854 | 0.995 | 0.487 | 0.797 | 0.446 |
| 1 | 3.0 | 0.322 | 0.645 | 0.845 | 0.519 | 0.935 | 0.689 |
| 2 | 0.3 | 0.953 | 0.970 | 1.065 | 0.659 | 0.309 | 0.164 |
| 2 | 0.6 | 0.851 | 0.944 | 1.073 | 0.538 | 0.509 | 0.238 |
| 2 | 0.9 | 0.742 | 0.904 | 1.055 | 0.558 | 0.641 | 0.350 |
| 2 | 1.5 | 0.560 | 0.819 | 0.992 | 0.590 | 0.795 | 0.506 |
| 2 | 3.0 | 0.322 | 0.586 | 0.858 | 0.619 | 0.934 | 0.748 |
| 4 | 0.3 | 0.954 | 0.966 | 1.072 | 0.752 | 0.302 | 0.181 |
| 4 | 0.6 | 0.854 | 0.936 | 1.090 | 0.580 | 0.501 | 0.251 |
| 4 | 0.9 | 0.745 | 0.890 | 1.078 | 0.608 | 0.635 | 0.368 |
| 4 | 1.5 | 0.563 | 0.796 | 1.025 | 0.647 | 0.793 | 0.540 |
| 4 | 3.0 | 0.323 | 0.554 | 0.892 | 0.674 | 0.936 | 0.773 |
| 8 | 0.3 | 0.956 | 0.965 | 1.087 | 0.783 | 0.290 | 0.191 |
| 8 | 0.6 | 0.859 | 0.930 | 1.120 | 0.652 | 0.488 | 0.284 |
| 8 | 0.9 | 0.750 | 0.874 | 1.122 | 0.687 | 0.625 | 0.398 |
| 8 | 1.5 | 0.567 | 0.770 | 1.079 | 0.736 | 0.790 | 0.582 |
| 8 | 3.0 | 0.324 | 0.516 | 0.930 | 0.753 | 0.940 | 0.819 |

### Periodic arrivals

| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured | Stale GPU time predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3 | 0.953 | 1.000 | 1.070 | 0.633 | 0.305 | 0.000 |
| 1 | 0.6 | 0.853 | 1.000 | 1.081 | 0.435 | 0.506 | 0.000 |
| 1 | 0.9 | 0.743 | 1.000 | 1.063 | 0.408 | 0.640 | 0.000 |
| 1 | 1.5 | 0.561 | 0.998 | 0.995 | 0.406 | 0.797 | 0.004 |
| 1 | 3.0 | 0.322 | 0.775 | 0.845 | 0.487 | 0.935 | 0.997 |
| 2 | 0.3 | 0.953 | 1.000 | 1.065 | 0.773 | 0.309 | 0.000 |
| 2 | 0.6 | 0.851 | 1.000 | 1.073 | 0.476 | 0.509 | 0.000 |
| 2 | 0.9 | 0.742 | 1.000 | 1.055 | 0.481 | 0.641 | 0.000 |
| 2 | 1.5 | 0.560 | 1.000 | 0.992 | 0.477 | 0.795 | 0.001 |
| 2 | 3.0 | 0.322 | 0.652 | 0.858 | 0.780\* | 0.934 | 0.999 |
| 4 | 0.3 | 0.954 | 1.000 | 1.072 | 0.772 | 0.302 | 0.000 |
| 4 | 0.6 | 0.854 | 1.000 | 1.090 | 0.521 | 0.501 | 0.000 |
| 4 | 0.9 | 0.745 | 1.000 | 1.078 | 0.521 | 0.635 | 0.000 |
| 4 | 1.5 | 0.563 | 1.000 | 1.025 | 0.515 | 0.793 | 0.005 |
| 4 | 3.0 | 0.323 | 0.601 | 0.892 | 0.720\* | 0.936 | 0.998 |
| 8 | 0.3 | 0.956 | 1.000 | 1.087 | 0.787 | 0.290 | 0.000 |
| 8 | 0.6 | 0.859 | 1.000 | 1.120 | 0.580 | 0.488 | 0.000 |
| 8 | 0.9 | 0.750 | 1.000 | 1.122 | 0.578 | 0.625 | 0.000 |
| 8 | 1.5 | 0.567 | 1.000 | 1.079 | 0.569 | 0.790 | 0.085 |
| 8 | 3.0 | 0.324 | 0.540 | 0.930 | 0.856\* | 0.940 | 0.998 |

### Bursty arrivals

| Chunk blocks | Load | Computed share predicted | measured | Mean adopted age predicted | measured | Stale GPU time predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3 | 0.953 | 0.368 | 1.070 | 1.803 | 0.305 | 0.509 |
| 1 | 0.6 | 0.853 | 0.365 | 1.081 | 1.366 | 0.506 | 0.515 |
| 1 | 0.9 | 0.743 | 0.357 | 1.063 | 1.053\* | 0.640 | 0.530 |
| 1 | 1.5 | 0.561 | 0.350 | 0.995 | 0.696 | 0.797 | 0.556 |
| 1 | 3.0 | 0.322 | 0.316\* | 0.845 | 0.620 | 0.935 | 0.603 |
| 2 | 0.3 | 0.953 | 0.365 | 1.065 | 2.038 | 0.309 | 0.508 |
| 2 | 0.6 | 0.851 | 0.362 | 1.073 | 1.535 | 0.509 | 0.513\* |
| 2 | 0.9 | 0.742 | 0.352 | 1.055 | 1.141\* | 0.641 | 0.527 |
| 2 | 1.5 | 0.560 | 0.342 | 0.992 | 0.796 | 0.795 | 0.560 |
| 2 | 3.0 | 0.322 | 0.299 | 0.858 | 0.768 | 0.934 | 0.610 |
| 4 | 0.3 | 0.954 | 0.364 | 1.072 | 2.056 | 0.302 | 0.513 |
| 4 | 0.6 | 0.854 | 0.361 | 1.090 | 1.606 | 0.501 | 0.515 |
| 4 | 0.9 | 0.745 | 0.347 | 1.078 | 1.209 | 0.635 | 0.530 |
| 4 | 1.5 | 0.563 | 0.337 | 1.025 | 0.856 | 0.793 | 0.560 |
| 4 | 3.0 | 0.323 | 0.291 | 0.892 | 0.829 | 0.936 | 0.618 |
| 8 | 0.3 | 0.956 | 0.361 | 1.087 | 2.212 | 0.290 | 0.514 |
| 8 | 0.6 | 0.859 | 0.360 | 1.120 | 1.683 | 0.488 | 0.519 |
| 8 | 0.9 | 0.750 | 0.347 | 1.122 | 1.254\* | 0.625 | 0.534 |
| 8 | 1.5 | 0.567 | 0.327 | 1.079 | 0.977 | 0.790 | 0.574 |
| 8 | 3.0 | 0.324 | 0.280 | 0.930 | 0.932\* | 0.940 | 0.637 |

## What the sleeping loop changed

The arrival thread now sleeps until each arrival and never spins. An arrival can therefore be late, and every run records how late. Across the 180 runs the mean lateness was at most 0.35 ms, the 95th percentile at most 0.60 ms and at most 2 percent of arrivals were more than 1 ms late. One run had a single arrival 23.9 ms late (periodic, chunk size 1, load 1.5). That is small against a pass of a few milliseconds, though it matters more at load 3.0, where the spacing is 1.5 to 2.1 ms.

The change moved the results a long way.

1. Periodic arrivals at load 3.0 now give a computed share of 0.775, 0.652, 0.601 and 0.540 for chunk sizes 1, 2, 4 and 8, against 0.322 predicted. The first run gave 0.410, 0.003, 0.002 and 0.002. That is what the starvation account expected: with the spin gone the worker gets the interpreter lock. It does not prove the account, because I changed one thing and did not measure the worker's start delay.
2. Poisson computed shares moved from mostly below the prediction to above it in all 20 cells.
3. Bursty computed shares moved from the range 0.14 to 0.19 to between 0.28 and 0.37.

## Why the misses happen

### The pass inside the worker is shorter than the number the predictions assume

The predictions read the sum of per chunk timings, each with its own synchronize. The worker runs a whole pass and synchronizes once. The diagnostic timed both under the first run's spinning loop, with 300 passes per size and 1000 requests inside the worker under Poisson arrivals at load 0.6.

| Chunk blocks | Assumed mean pass (ms) | Whole pass alone (ms) | Whole pass inside the worker (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 6.23 | 4.54 | 4.14 |
| 2 | 5.27 | 4.54 | 4.17 |
| 4 | 4.87 | 4.39 | 4.21 |
| 8 | 4.38 | 4.49 | 4.23 |

The sleeping run suggests the pass inside the worker is shorter still. A periodic arrival spaced wider than a pass starts a computation at once, so the adopted age at periodic load 1.5 is close to one pass. It is 0.406, 0.477, 0.515 and 0.569 assumed passes for chunk sizes 1, 2, 4 and 8, which is 2.5 ms at every size, against 4.1 to 4.2 ms in the diagnostic. At periodic load 0.3 the age for chunk size 1 is about 3.9 ms. A pass that gets faster as the load rises fits a card whose clocks settle while it stays busy. I did not test that, and I did not repeat the diagnostic with the sleeping loop, so the 2.5 ms figure is an inference from ages and not a timing.

If it holds, it explains the direction of the mean adopted age misses. Every age prediction is in units of the assumed pass, and the assumed pass is longer than the real one at every size. Measured Poisson ages sit below the prediction in all 20 cells, and the gap is widest at chunk size 1, where the assumed pass is longest. The ratio of 2.5 ms to 6.23 ms is 0.40 and the periodic age at chunk size 1 is 0.41, so for the periodic case the figures agree. For Poisson I did not size the effect.

A shorter real pass also lowers the real load. A nominal load of 0.6 is well under 0.6 in real terms, which would lift the computed share above the prediction. The Poisson measurements do sit above it in all 20 cells. I did not recompute the predictions at the lowered load, so I cannot say how much of the gap that accounts for.

### The first run's spinning loop

The first run's arrival loop slept until 1.5 ms before the next arrival and spun for the rest. A spinning Python thread keeps the interpreter lock and gives it up only at the switch interval, 5 ms by default. The worker needs the lock to launch its calculation, so a worker that woke during a spin could start late. The diagnostic's switch interval test reran four cases at chunk size 2 with the interval at 0.2 ms, one seed and 1000 requests each, and the periodic load 3.0 computed share rose from 0.002 to 0.355. The full rerun above is the stronger test of the same idea, because it removed the spin itself. The diagnostic file is kept as the record that led to the change, and its numbers describe the spinning loop.

### Periodic arrivals that never interrupt

A periodic arrival with spacing longer than a pass never lands inside a computation, so nothing is replaced and no result is stale. With the sleeping loop that now holds in every periodic row at loads 0.3 to 1.5, with a computed share of at least 0.998 and a stale share of at most 0.085, and under 0.006 in all but one of them. The Poisson form predicts computed shares from 0.56 to 0.95 and stale shares from 0.3 to 0.8 for those rows, because it assumes random arrivals. This is the same miss the virtual clock study found, and the explanation is the same. The first run's swings at load 1.5 are gone, which fits the shorter pass, since the spacing at load 1.5 is longer than a pass of 2.5 ms.

At load 3.0 the spacing is shorter than a pass, so arrivals do interrupt, and the stale share is 0.997 to 0.999. The computed share there is between 0.54 and 0.775 against 0.322 predicted. I did not explain why it sits above the prediction, though a shorter pass would lift it.

### Bursty arrivals

Each burst is five requests 0.05 passes apart, so the whole burst lands inside one pass. A worker that starts on the first request of a burst finishes it stale, then computes the latest one, which is two computations for five requests. That gives a computed share near 0.4 and a stale share near 0.5 of the busy time. The measured computed share is 0.28 to 0.37 and the stale share is 0.51 to 0.64, so the fit is reasonable, and I wrote it after I saw the numbers. Mean adopted age at low load is 1.8 to 2.2 passes, above the prediction, which fits a result that waits out one stale pass and then its own.

### Not explained

1. The stale GPU time of the Poisson rows is below the prediction in all 20 cells. A shorter real pass lowers the real load and so the stale share, which points the right way, but I did not size it.
2. The nine cells marked as within noise are scattered across the periodic load 3.0 rows and a few bursty cells. They are not evidence that the model holds there. I take them as chance crossings of trends that are wrong elsewhere.
3. The adopted check at the end of a computation and the worker's own stale count are two lock acquisitions. The two counts differed by 17 requests in total, spread over 15 of the 180 runs, which fits a request slipping in between. It is small, and I did not trace each case.
4. Why the pass inside the worker gets shorter as load rises, if it does.

## Limits

One card, one machine, one model, arrivals generated by a script, and one run of the full table with three seeds per cell. The card was shared with the desktop, and its use was read before and after each run and not controlled. Input arrived near the end of the run. The service time distribution comes from 300 passes per size, and the passes inside the worker were probably shorter than that. The diagnostic is one seed and one load and belongs to the spinning loop. Nothing here is a latency bound, a claim about preempting GPU work or a measure of anyone's performance.

## The cooperative stop

This is the calculation stopping itself. After each chunk it looks at the worker's current generation and returns early when a newer request exists. It is not forced cancellation. The engine never interrupts the calculation, the chunk in flight always finishes, and work already placed on the GPU is not removed. The code is [`cooperative_stop.py`](../examples/gpu_queue_study/cooperative_stop.py), it adds no public method to the engine, and the saved results are in [`gpu-cooperative-stop.json`](evidence/gpu-cooperative-stop.json).

Three modes ran the same generated arrivals on the same worker. `plain` is the first study. `synced` adds a synchronize after every chunk and stops nothing, so the cost of the synchronize is kept apart from the effect of stopping. `stop` adds the synchronize and the early return. Each cell is the mean of three seeds with 1000 requests each, Poisson and bursty arrivals at loads 0.9 and 1.5, with the sleeping arrival loop. Stale GPU seconds are per run. Because the modes keep the card busy for different lengths of time, the stale share of busy time is the fairer comparison. Pass ms is the mean duration of an adopted pass. A load of 0.9 or 1.5 is set from the mean pass of `plain`. The `synced` and `stop` passes are longer, so the same arrivals put a higher real load on them, and that confounds any comparison of absolute stale seconds between modes.

| Chunk blocks | Arrivals | Load | Stale GPU s: plain | synced | stop | Stale share: plain | synced | stop | Pass ms: plain | synced | stop | Stopped per run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | poisson | 0.9 | 0.64 | 1.27 | 0.81 | 0.28 | 0.40 | 0.27 | 2.42 | 3.53 | 3.85 | 394 |
| 1 | poisson | 1.5 | 0.89 | 1.45 | 0.89 | 0.42 | 0.54 | 0.37 | 2.44 | 3.35 | 3.44 | 495 |
| 1 | bursty | 0.9 | 0.86 | 1.23 | 0.49 | 0.55 | 0.57 | 0.26 | 4.58 | 6.55 | 9.65 | 473 |
| 1 | bursty | 1.5 | 0.66 | 0.82 | 0.41 | 0.58 | 0.60 | 0.33 | 3.47 | 4.17 | 6.01 | 476 |
| 2 | poisson | 0.9 | 0.75 | 1.10 | 0.64 | 0.33 | 0.41 | 0.27 | 2.48 | 3.08 | 3.09 | 358 |
| 2 | poisson | 1.5 | 0.98 | 1.30 | 0.82 | 0.48 | 0.56 | 0.40 | 2.46 | 2.99 | 3.01 | 489 |
| 2 | bursty | 0.9 | 0.71 | 0.91 | 0.39 | 0.54 | 0.57 | 0.27 | 3.86 | 4.73 | 7.10 | 406 |
| 2 | bursty | 1.5 | 0.56 | 0.65 | 0.35 | 0.57 | 0.59 | 0.35 | 3.01 | 3.33 | 4.71 | 410 |
| 4 | poisson | 0.9 | 0.78 | 1.00 | 0.59 | 0.35 | 0.41 | 0.27 | 2.46 | 2.79 | 2.83 | 330 |
| 4 | poisson | 1.5 | 1.03 | 1.24 | 0.81 | 0.51 | 0.57 | 0.41 | 2.46 | 2.80 | 2.81 | 471 |
| 4 | bursty | 0.9 | 0.68 | 0.85 | 0.39 | 0.54 | 0.56 | 0.29 | 3.71 | 4.54 | 6.25 | 343 |
| 4 | bursty | 1.5 | 0.53 | 0.57 | 0.31 | 0.58 | 0.59 | 0.35 | 2.79 | 2.90 | 3.94 | 374 |
| 8 | poisson | 0.9 | 0.83 | 0.96 | 0.63 | 0.38 | 0.41 | 0.30 | 2.46 | 2.67 | 2.68 | 303 |
| 8 | poisson | 1.5 | 1.05 | 1.18 | 0.82 | 0.55 | 0.59 | 0.45 | 2.45 | 2.65 | 2.69 | 421 |
| 8 | bursty | 0.9 | 0.64 | 0.73 | 0.33 | 0.54 | 0.56 | 0.32 | 3.48 | 3.81 | 4.43 | 254 |
| 8 | bursty | 1.5 | 0.48 | 0.51 | 0.31 | 0.57 | 0.58 | 0.40 | 2.61 | 2.69 | 3.15 | 308 |

A second table shows what the stop did to the results that were kept, from the same runs.

| Chunk blocks | Arrivals | Load | Adopted share: plain | synced | stop | Mean adopted age: plain | synced | stop |
|---|---|---|---|---|---|---|---|---|
| 1 | poisson | 0.9 | 0.66 | 0.53 | 0.56 | 0.44 | 0.68 | 0.63 |
| 1 | poisson | 1.5 | 0.50 | 0.37 | 0.43 | 0.46 | 0.66 | 0.56 |
| 1 | bursty | 0.9 | 0.16 | 0.14 | 0.15 | 1.23 | 1.83 | 1.58 |
| 1 | bursty | 1.5 | 0.14 | 0.13 | 0.14 | 0.89 | 1.12 | 0.99 |
| 2 | poisson | 0.9 | 0.61 | 0.52 | 0.57 | 0.54 | 0.69 | 0.60 |
| 2 | poisson | 1.5 | 0.43 | 0.35 | 0.41 | 0.56 | 0.71 | 0.59 |
| 2 | bursty | 0.9 | 0.16 | 0.15 | 0.15 | 1.21 | 1.57 | 1.40 |
| 2 | bursty | 1.5 | 0.14 | 0.13 | 0.14 | 0.91 | 1.05 | 0.93 |
| 4 | poisson | 0.9 | 0.58 | 0.53 | 0.58 | 0.59 | 0.68 | 0.60 |
| 4 | poisson | 1.5 | 0.40 | 0.34 | 0.41 | 0.62 | 0.71 | 0.60 |
| 4 | bursty | 0.9 | 0.16 | 0.15 | 0.15 | 1.26 | 1.61 | 1.39 |
| 4 | bursty | 1.5 | 0.14 | 0.14 | 0.15 | 0.92 | 0.98 | 0.86 |
| 8 | poisson | 0.9 | 0.54 | 0.51 | 0.55 | 0.66 | 0.72 | 0.65 |
| 8 | poisson | 1.5 | 0.35 | 0.32 | 0.38 | 0.68 | 0.75 | 0.66 |
| 8 | bursty | 0.9 | 0.15 | 0.15 | 0.16 | 1.34 | 1.50 | 1.13 |
| 8 | bursty | 1.5 | 0.14 | 0.14 | 0.15 | 0.95 | 1.00 | 0.83 |

What the tables show:

1. The stale share of busy GPU time is lower with the stop than in `plain` and than in `synced` in all 16 cells, though at one block per chunk and Poisson load 0.9 the gap to `plain` is 0.28 against 0.27, which three seeds cannot separate from chance. The largest drops are in bursty arrivals, from 0.54 to 0.58 down to 0.26 to 0.40. In Poisson arrivals the drop is smaller, from 0.28 to 0.55 down to 0.27 to 0.45.
2. Stale GPU seconds do not always fall against `plain`. At one block per chunk and Poisson load 0.9 they rise from 0.64 to 0.81, and at load 1.5 they are equal at 0.89. Against `synced`, which pays the same synchronize cost, they fall in every cell.
3. The synchronize costs more the smaller the chunk. Poisson load 0.9 shows the mean adopted pass growing from `plain` to `synced` by 1.11 ms at 1 block per chunk, 0.60 at 2, 0.33 at 4 and 0.21 at 8. That is about 35 microseconds per added synchronize at 1 block. The difference may include lost overlap between launches as well as the call itself, and I did not separate the two.
4. Between 254 and 495 of the 1000 requests in a run ended early. The share rises with load and falls as chunks get larger, which fits a larger chunk leaving fewer places to stop, though I did not test that.
5. The stop does not make the kept results fresher than `plain` in general. Against `plain`, the adopted share is lower or equal in 10 of the 16 cells, and the largest drop is at 1 block with Poisson arrivals, from 0.66 to 0.56 at load 0.9. The mean adopted age is higher in 10 of the 16, for example from 0.44 to 0.63, and lower in the other six, which are 4 blocks at load 1.5 and all four 8 block cells. It beats `synced` on mean adopted age in all 16 cells. The stop mostly pays back the penalty of the synchronize, and what it clearly removes is stale GPU time.

Not explained: in bursty arrivals the adopted pass is longer with the stop than with `synced`, for example 9.65 ms against 6.55 ms at 1 block per chunk and load 0.9. A stop shortens the stale passes, so the passes that remain to be adopted are the ones that start late in a burst, and I expect that selection plays a part, but I did not test it.

Limits of this table: the card was shared with the desktop and was measured in that state, at my normal desktop load with no game running. `nvidia-smi` was read just before and just after every run. Across the 288 readings, utilization ranged from 4 to 79 percent with a median of 41, and memory used from 6,832 to 7,030 MiB. The readings are in the saved file and are not controlled for. The table comes from one full run, since a first run of the same script lacked the readings and was discarded, and it holds one card, one machine, one model and generated arrivals. It says nothing about a latency bound or about anyone's performance.

```sh
python tools/gpu_stop_tables.py
```

