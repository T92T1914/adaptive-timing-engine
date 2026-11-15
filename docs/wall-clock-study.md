# Wall clock study

The rest of this project runs on a virtual clock. This study measures what the pieces actually cost in elapsed time. The first run was on one Windows computer, and runs on hosted Linux and macOS are further down. Each is a measurement of that machine and that run. It gives no latency bound and says nothing about other hardware, other interpreters or human performance.

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

## Hosted runs on Linux and macOS

The workflow [`wall-clock-study.yml`](../.github/workflows/wall-clock-study.yml) runs the same script on GitHub hosted runners. It runs on dispatch and when the workflow or the script changes. Each job uploads the study JSON and a `runner.json` that names the runner image. The retained files are in [`docs/wall-clock-hosted`](wall-clock-hosted), one folder per job, from run 43608063433 at revision dce1636.

| Job | Operating system image | Interpreter | Logical CPUs | GIL during the study |
| --- | --- | --- | ---: | --- |
| Linux, 3.13 | ubuntu26, image 20261111.196.1 | CPython 3.13.18 | 4 | on |
| macOS, 3.13 | macos26, image 20261102.0473.1 | CPython 3.13.17 | 3 | on |
| Linux, 3.14t | ubuntu26, image 20261111.196.1 | CPython 3.14.10 free threading build | 4 | off at the start and after every measure |

Each cell is the median and then the 95th percentile, in microseconds. The sample counts match the retained Windows run. The runners are shared virtual machines and the jobs ran one after another, so other tenants may have shared the host.

| Runner | Measure | 60 events | 240 events | 960 events |
| --- | --- | ---: | ---: | ---: |
| Linux, 3.13 | Planning | 577 / 598 | 2296 / 2325 | 9404 / 9571 |
| Linux, 3.13 | First poll | 68 / 76 | 267 / 279 | 293 / 303 |
| Linux, 3.13 | Adoption, tight loop | 5765 / 5878 | 7516 / 7650 | 19920 / 20164 |
| Linux, 3.13 | Adoption, `sleep(0)` | 663 / 693 | 2407 / 2481 | 9738 / 9867 |
| Linux, 3.13 | Adoption, `sleep(0.001)` | 2147 / 2155 | 3488 / 3561 | 10648 / 11064 |
| macOS, 3.13 | Planning | 704 / 1633 | 4012 / 5920 | 14610 / 22423 |
| macOS, 3.13 | First poll | 45 / 131 | 192 / 476 | 214 / 614 |
| macOS, 3.13 | Adoption, tight loop | 24196 / 28068 | 25422 / 31901 | 36901 / 61829 |
| macOS, 3.13 | Adoption, `sleep(0)` | 1514 / 2543 | 3932 / 7581 | 14859 / 21534 |
| macOS, 3.13 | Adoption, `sleep(0.001)` | 10346 / 13334 | 10162 / 12211 | 20059 / 26691 |
| Linux, 3.14t | Planning | 562 / 580 | 2228 / 2276 | 9190 / 9440 |
| Linux, 3.14t | First poll | 68 / 76 | 268 / 279 | 288 / 303 |
| Linux, 3.14t | Adoption, tight loop | 688 / 994 | 2471 / 2901 | 10107 / 10520 |
| Linux, 3.14t | Adoption, `sleep(0)` | 740 / 1047 | 2582 / 3067 | 10203 / 10738 |
| Linux, 3.14t | Adoption, `sleep(0.001)` | 2150 / 2161 | 4333 / 4353 | 12147 / 13407 |

What these runs show:

1. The tight loop penalty is not specific to Windows. On Linux with the GIL on, adoption in a tight loop took 5.8 ms at 60 events where planning took 0.6 ms. That is close to the default switch interval of 5 ms. The gap stayed near 5.2 ms at 240 events and grew to 10.5 ms at 960 events, so on Linux it is not the flat 15.5 ms of the Windows run.
2. On macOS the tight loop took 24 ms to 37 ms at the median, with 95th percentiles up to 62 ms. The macOS runner had three CPUs and its planning figures also spread more than Linux, so I read part of that as host noise.
3. On the free threaded build the tight loop stayed within about 0.9 ms of planning at every size, and its median was at or below the `sleep(0)` median. That agrees with the reading that the penalty comes from the owner thread holding the GIL while it spins. It does not prove it. The free threaded job also ran a different Python version than the Linux 3.13 job, and I did not run the same version with the GIL forced on or off.
4. Planning, the first poll and the two sleeping styles keep the ordering of the Windows run on every runner. The reference swap stays a fraction of a microsecond. Its rows are in the JSON files and not in the table.
5. The same workflow gave different figures on a second run. In an earlier run at revision 80aff16 the Linux tight loop at 960 events had a median of 27.1 ms against 19.9 ms here. I did not retain that run. Treat any one hosted figure as good to a few tens of percent at best.
6. The hosted figures differ from the retained Windows figures and from each other. They do not combine into one number.

A first version of the study script reported the GIL as on for every Python from 3.13, because it read the function `sys._is_gil_enabled` and not its result. The free threaded job showed the GIL off in the per measure record, so the script was fixed before these files were retained. A test now covers it. The retained Windows file is unaffected, because it ran on 3.11 and records null.

## A second hosted run on the next day

I dispatched nothing new for this. The merge of pull request 40 touched the workflow, so the same jobs ran again on November 15, a day after the first retained run. The files are in [`docs/wall-clock-hosted-day2`](wall-clock-hosted-day2), from run 43702666430 at revision d95bf1b. The runner images were the same as in the first run, and the Linux jobs again had four logical CPUs and the macOS job three. The format of each cell is the same, the median and then the 95th percentile in microseconds.

| Runner | Measure | 60 events | 240 events | 960 events |
| --- | --- | ---: | ---: | ---: |
| Linux, 3.13 | Planning | 553 / 568 | 2213 / 2253 | 8938 / 9025 |
| Linux, 3.13 | First poll | 57 / 67 | 232 / 245 | 252 / 266 |
| Linux, 3.13 | Adoption, tight loop | 5795 / 6101 | 7513 / 7796 | 19236 / 19935 |
| Linux, 3.13 | Adoption, `sleep(0)` | 708 / 990 | 2409 / 2598 | 9015 / 9327 |
| Linux, 3.13 | Adoption, `sleep(0.001)` | 2161 / 2223 | 3455 / 3871 | 10275 / 10517 |
| macOS, 3.13 | Planning | 657 / 735 | 2718 / 3070 | 13613 / 17875 |
| macOS, 3.13 | First poll | 50 / 84 | 198 / 217 | 221 / 427 |
| macOS, 3.13 | Adoption, tight loop | 23279 / 29352 | 25414 / 31610 | 36608 / 62705 |
| macOS, 3.13 | Adoption, `sleep(0)` | 878 / 1285 | 2697 / 3548 | 12247 / 15946 |
| macOS, 3.13 | Adoption, `sleep(0.001)` | 12704 / 15140 | 10202 / 13788 | 16882 / 22680 |
| Linux, 3.14t | Planning | 730 / 744 | 2863 / 2909 | 11461 / 11666 |
| Linux, 3.14t | First poll | 92 / 102 | 366 / 380 | 398 / 411 |
| Linux, 3.14t | Adoption, tight loop | 836 / 965 | 3125 / 3209 | 12386 / 12736 |
| Linux, 3.14t | Adoption, `sleep(0)` | 940 / 1219 | 3248 / 3355 | 12864 / 13371 |
| Linux, 3.14t | Adoption, `sleep(0.001)` | 2184 / 2194 | 4378 / 5440 | 14344 / 14446 |

How it compares with the first retained run:

1. The Linux tight loop gap held. It was 5.2, 5.3 and 10.3 ms at 60, 240 and 960 events, against 5.2, 5.2 and 10.5 ms before. The cost is a gap over planning that stays close to the 5 ms switch interval at small sizes, and it is not a single number.
2. The macOS tight loop medians were 23.3, 25.4 and 36.6 ms, against 24.2, 25.4 and 36.9 ms. The 95th percentile at 960 events was 62.7 ms, against 61.8 ms.
3. The free threaded tight loop stayed within 0.1, 0.3 and 0.9 ms of planning, and it was at or below the `sleep(0)` median at every size, as before.
4. Planning itself moved more than the gaps did. On the free threaded Linux job the planning median at 60 events was 730 microseconds, against 562 in the first run, and it was now about 30 percent above the Linux 3.13 job. In the first run the two were level. That is the size of host and image variation I would expect between days on shared machines, so I do not read a difference between 3.13 and 3.14t into planning.
5. The earlier run I did not retain gave a Linux tight loop median of 27.1 ms at 960 events. The three runs I saw gave 27.1, 19.9 and 19.2 ms. The two retained runs agree within about 4 percent here, and the one I dropped did not.

Two runs on two days still give no bound. They do show that the gap pattern repeated on Linux, macOS and the free threaded build, which one run could not.

## What I did not establish

The 15.5 ms with a tight loop is consistent with the owner thread holding the interpreter while it spins, so that the planning thread waits for a thread switch. A short side probe on the same machine supports that reading. At 60 events the tight loop took a median of 15.6 ms with the default switch interval of 5 ms and 3.0 ms with `sys.setswitchinterval(0.0001)`. I did not separate this from the Windows timer tick. The probe is not in the retained results. The hosted runs above add Linux, macOS and a free threaded build, and they change the picture without settling it. I have not run the switch interval probe on them, and each hosted figure is one run on a shared machine.

The study does not show an operating system wakeup bound, a hard deadline or a figure that carries over to another machine. The synthetic workloads stay synthetic. Treat each table as how this implementation behaved on that machine in that run.
