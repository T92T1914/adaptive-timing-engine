# Wall clock study

The rest of this project runs on a virtual clock. This study measures what the pieces actually cost in elapsed time, on one machine. It is a measurement of that machine and that run. It gives no latency bound and says nothing about other hardware, other interpreters or human performance.

```sh
python tools/wall_clock_study.py --output reports/wall-clock.json
```

The retained run is [`docs/evidence/wall-clock.json`](evidence/wall-clock.json). The script uses only the standard library and `time.perf_counter_ns`.

## Environment of the retained run

| Item | Value |
| --- | --- |
| Interpreter | CPython 3.11.8, MSC v.1937, 64 bit, GIL enabled, inferred from the version because 3.11 has no free threaded build (the JSON records `gil_enabled` as null) |
| Platform | Windows build 10.0.26300, AMD64 (Windows 11, though the platform string in the JSON reads `Windows-10`) |
| Processor | AMD64 Family 25 Model 97, 16 logical CPUs |
| Timer | `time.perf_counter_ns`, monotonic, reported resolution 100 ns |
| Workload | the synthetic `steady` workload, seed 42, 60, 240 and 960 events |
| Load | Other programs ran on the computer. I did not control for them. |

## What is measured

1. Planning is one `build_plan` call, 200 samples per size.
2. The reference swap is one `Executor.install` of a finished plan, 5000 samples.
3. The first poll is one `Executor.poll` with the default budget of 256 on a fresh executor holding that plan, 2000 samples.
4. Adoption is the time from `Controller.request` until the poll that installs that request's plan, 200 samples per style. It includes the planning itself. The three styles differ only in how the owner thread waits: a tight loop of polls, a poll followed by `time.sleep(0)`, and a poll followed by `time.sleep(0.001)`. The sleep happens after each poll, so the sleeping styles add at least one sleep to the figure. Windows sleeps longer than requested, and at 60 events the 1 ms style took about 2.4 ms more than planning alone.

## Results

All values are microseconds, from the retained run. A sample count is in the JSON.

| Measure | Events | p50 | p95 | p99 | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| Planning | 60 | 611 | 820 | 1392 | 1509 |
| Planning | 240 | 2028 | 2986 | 3715 | 4961 |
| Planning | 960 | 8369 | 10675 | 12447 | 14164 |
| Reference swap | 60 | 0.3 | 0.3 | 0.4 | 51.7 |
| Reference swap | 240 | 0.3 | 0.3 | 0.4 | 126.2 |
| Reference swap | 960 | 0.2 | 0.3 | 0.4 | 5.0 |
| First poll, budget 256 | 60 | 52 | 100 | 136 | 469 |
| First poll, budget 256 | 240 | 212 | 407 | 474 | 852 |
| First poll, budget 256 | 960 | 227 | 438 | 516 | 1112 |
| Adoption, tight loop | 60 | 15533 | 16453 | 17304 | 19055 |
| Adoption, tight loop | 240 | 15533 | 16645 | 17288 | 17350 |
| Adoption, tight loop | 960 | 15695 | 17389 | 28115 | 28869 |
| Adoption, `sleep(0)` | 60 | 617 | 1078 | 1309 | 1745 |
| Adoption, `sleep(0)` | 240 | 2127 | 2797 | 3008 | 3251 |
| Adoption, `sleep(0)` | 960 | 8205 | 10510 | 11123 | 11908 |
| Adoption, `sleep(0.001)` | 60 | 3047 | 3405 | 4112 | 4863 |
| Adoption, `sleep(0.001)` | 240 | 3517 | 4122 | 4557 | 5317 |
| Adoption, `sleep(0.001)` | 960 | 9809 | 12459 | 13508 | 13728 |

## What the numbers show

1. Planning cost grows with the batch. It is the dominant cost for any plan of a few hundred events.
2. Installing a finished plan is a reference swap. Its median is a fraction of a microsecond at every size, and it does not grow with the batch. The few large maxima, up to 126 microseconds, are single samples and are not explained by this study.
3. A poll with the default budget stays below about 0.5 ms at p95 for 240 and 960 events. At 240 events the plan is smaller than the budget, so the poll drains it. At 960 events the plan is larger than the budget and the poll cost stayed close to the 240 event cost.
4. The tight loop is also bimodal. Its fastest samples took 7.8 ms to 13 ms against a median of 15.5 ms. How the owner thread waits matters more than the swap. With a tight loop, adoption took about 15.5 ms at the median for every size, even when planning took 0.6 ms. With `sleep(0)` it took about as long as planning plus a small overhead.

## What I did not establish

The 15.5 ms with a tight loop is consistent with the owner thread holding the interpreter while it spins, so that the planning thread waits for a thread switch. A short side probe on the same machine supports that reading. At 60 events the tight loop took a median of 15.6 ms with the default switch interval of 5 ms and 3.0 ms with `sys.setswitchinterval(0.0001)`. I did not separate this from the Windows timer tick, and I did not run the study on Linux or macOS or on a free threaded build, where the effect may differ. The probe is not in the retained results.

The study does not show an operating system wakeup bound, a hard deadline or a figure that carries over to another machine. The synthetic workloads stay synthetic. Treat the table as how this implementation behaved on one computer on one day.
