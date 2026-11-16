# Queue model

`LatestWorker` has one computation running and one place for a waiting request. A newer request replaces the waiting one, and a finished result counts only if no newer request arrived while it ran. This is a small queue, and queueing theory can predict what it does. I derived the prediction, then measured the real worker and set the two side by side. Where they disagree, I say so and say why.

```sh
python tools/queue_model_study.py --output reports/queue-model.json
```

The retained run is [`docs/evidence/queue-model.json`](evidence/queue-model.json). The script uses only the standard library.

## What is predicted

Three quantities, all per request that reaches the worker.

1. The computed share is the fraction of requests that ever start computing. The rest were replaced while waiting.
2. The adopted share is the fraction of requests whose result is kept. A computed result that was overtaken by a newer request is discarded as stale, so this is never larger than the computed share.
3. The mean adopted age is the time from a kept result's request to the moment the result finished. It is measured in mean service times. A request that found the worker idle and finished with no interruption has an age of one service time. A request that waited first has more.

## The model

I use Poisson arrivals at rate `load` per mean service time, and either a fixed service time or an exponential one with the same mean. Let `p` be the chance that a computation sees no arrival while it runs. For a fixed service time of one unit, `p = exp(-load)`. For exponential service, `p = 1 / (1 + load)`.

A busy period starts when a request finds the worker idle. Each computation in it ends in one of two ways. With chance `p` nothing arrived, the result is kept and the busy period ends. Otherwise the result is stale and the newest waiting request starts at once. So a busy period holds `1 / p` computations on average and exactly one kept result. Counting arrivals with the compensator of a Poisson process and Wald's identity gives `1 + load / p` arrivals per busy period. Dividing:

1. computed share = `1 / (p + load)`
2. adopted share = `p / (p + load)`
3. mean adopted age = `m / p + (1 - p) / load - m`, where `m` is the mean of `S * exp(-load * S)` over the service time `S`. That is `p` for a fixed service time and `1 / (1 + load)^2` for an exponential one.

The age term comes from how long the winning request waited, and a longer stale computation leaves a longer wait, which is why the service time is weighted by the chance it was interrupted. A request is waiting only because arrivals landed inside an earlier computation, and the last of those arrivals sits a truncated exponential distance before that computation ended.

The age of information literature analyzes a related queue. The rule here is stricter, since one arrival anywhere during a computation makes its result stale. That is why I derived the formulas for this rule instead of quoting a published result.

## What is measured

The real `LatestWorker` runs the requests. A driver feeds it arrivals on a virtual clock and releases each computation when its virtual service time is over, so the counts do not depend on thread timing and the run is repeatable. The service durations are assumed inputs. Nothing here is a wall clock measurement. Each cell is the mean of five seeds with 3000 requests each. The script also asserts, on every run, that submitted requests equal computed plus replaced, and that computed results minus stale results equal the results the driver took.

A comparison is labeled within noise when the predicted value is no further than three standard errors from the mean of the five seeds, and a miss otherwise. Within noise outside the Poisson case happens by chance and does not show the model holds there.

## Where the model holds

With Poisson arrivals, all 30 comparisons for the two service laws and five loads are within noise.

Fixed service time:

| Load | Computed share predicted | measured | Adopted share predicted | measured | Mean adopted age predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.3 | 0.961 | 0.962 | 0.712 | 0.712 | 1.123 | 1.124 |
| 0.6 | 0.870 | 0.869 | 0.478 | 0.475 | 1.203 | 1.202 |
| 0.9 | 0.765 | 0.768 | 0.311 | 0.317 | 1.253 | 1.248 |
| 1.5 | 0.580 | 0.582 | 0.129 | 0.130 | 1.295 | 1.308 |
| 3.0 | 0.328 | 0.330 | 0.016 | 0.016 | 1.267 | 1.269 |

Exponential service time:

| Load | Computed share predicted | measured | Adopted share predicted | measured | Mean adopted age predicted | measured |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.3 | 0.935 | 0.936 | 0.719 | 0.717 | 0.947 | 0.956 |
| 0.6 | 0.816 | 0.818 | 0.510 | 0.513 | 0.859 | 0.846 |
| 0.9 | 0.701 | 0.706 | 0.369 | 0.370 | 0.776 | 0.778 |
| 1.5 | 0.526 | 0.524 | 0.211 | 0.205 | 0.640 | 0.630 |
| 3.0 | 0.308 | 0.303 | 0.077 | 0.075 | 0.438 | 0.417 |

The worker does what the closed form says under these assumptions. The more useful result is the shape of the curve. The computed share falls slowly as load rises, but the adopted share falls fast. At a load of 3 about a third of requests are computed and under 2 percent of requests produce a kept result when the service time is fixed. Most of the CPU goes to results that are thrown away. That follows from the design, which never cancels a running computation and never adopts a result that is out of date.

## Where the model misses

The two other arrival patterns use the same long run rate and the same Poisson prediction. Every row below is a miss on at least one quantity, and each miss has a reason.

Periodic arrivals, one request every `1 / load` service times:

| Service | Load | Computed share predicted | measured | Adopted share predicted | measured | Mean adopted age predicted | measured | Adopted results per run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 0.3 | 0.961 | 1.000 | 0.712 | 1.000 | 1.123 | 1.000 | 3000 |
| fixed | 0.6 | 0.870 | 1.000 | 0.478 | 1.000 | 1.203 | 1.000 | 3000 |
| fixed | 0.9 | 0.765 | 1.000 | 0.311 | 1.000 | 1.253 | 1.000 | 3000 |
| fixed | 1.5 | 0.580 | 0.667 | 0.129 | 0.000 | 1.295 | 1.667 | 1 |
| fixed | 3.0 | 0.328 | 0.334 | 0.016 | 0.000 | 1.267 | 1.333 | 1 |
| exponential | 0.3 | 0.935 | 0.999 | 0.719 | 0.959 | 0.947 | 0.905 | 2877 |
| exponential | 0.6 | 0.816 | 0.947 | 0.510 | 0.727 | 0.859 | 0.667 | 2180 |
| exponential | 0.9 | 0.701 | 0.833 | 0.369 | 0.485 | 0.776 | 0.525 | 1455 |
| exponential | 1.5 | 0.526 | 0.601 | 0.211 | 0.219 | 0.640 | 0.365 | 656 |
| exponential | 3.0 | 0.308 | 0.325 | 0.077 | 0.060 | 0.438 | 0.211 | 179 |

1. With a fixed service time and a load below 1, evenly spaced requests never overlap a computation. Every request is computed and every result is kept. Poisson arrivals clump, so the model predicts losses that this stream never has.
2. With a fixed service time and a load above 1, a new request lands inside every computation, so every result is stale. The run kept exactly one result, the last one, after the arrivals stopped. The model predicts a small positive adopted share and the worker delivers none while the stream lasts. The two mean ages in those rows come from that single result and say nothing about the stream. This is the sharpest finding in the study. Under a steady stream faster than the service time, this worker starves its consumer, and the Poisson model hides it.
3. With exponential service, even spacing removes the clumping, but random service times still make some computations short. The model misses the computed share by up to 13 points and the adopted share by up to 24, and it overstates the age. I expect the cause is that evenly spaced waiting requests are younger when they start than the model assumes, but this study does not test that.

Bursty arrivals, five requests 0.05 service times apart, with Poisson gaps between bursts:

| Service | Load | Computed share predicted | measured | Adopted share predicted | measured | Mean adopted age predicted | measured | Adopted results per run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 0.3 | 0.961 | 0.370 | 0.712 | 0.178 | 1.123 | 1.767 | 534 |
| fixed | 0.6 | 0.870 | 0.345 | 0.478 | 0.158 | 1.203 | 1.732 | 475 |
| fixed | 0.9 | 0.765 | 0.325 | 0.311 | 0.141 | 1.253 | 1.704 | 424 |
| fixed | 1.5 | 0.580 | 0.286 | 0.129 | 0.115 | 1.295 | 1.661 | 346 |
| fixed | 3.0 | 0.328 | 0.220 | 0.016 | 0.069 | 1.267 | 1.560 | 208 |
| exponential | 0.3 | 0.935 | 0.398 | 0.719 | 0.184 | 0.947 | 1.818 | 552 |
| exponential | 0.6 | 0.816 | 0.363 | 0.510 | 0.160 | 0.859 | 1.682 | 480 |
| exponential | 0.9 | 0.701 | 0.346 | 0.369 | 0.148 | 0.776 | 1.618 | 443 |
| exponential | 1.5 | 0.526 | 0.298 | 0.211 | 0.119 | 0.640 | 1.478 | 356 |
| exponential | 3.0 | 0.308 | 0.214 | 0.077 | 0.072 | 0.438 | 1.147 | 217 |

1. A burst is five requests inside one computation. The first starts work and the other four replace each other in the waiting place. So at low load the computed share is about two in five, not near one, and each burst costs one stale result before the survivor is computed. The adopted share falls to about a sixth.
2. The age is higher than predicted because every burst's survivor waits for the stale computation to end before it starts.
3. At a load of 3 with fixed service, the measured adopted share of 0.069 is above the predicted 0.016. Bursts leave quiet gaps between them, and a computation that starts in a gap can finish untouched. With fixed service, clumping lowered the adopted share at every load up to 1.5 and raised it at 3. With exponential service the bursty value at 3 is 0.072 against 0.077 predicted, which is within noise.

## Assumptions the model makes

1. Arrivals are Poisson. This is the assumption that fails in both tables above.
2. Service times are fixed, or exponential with a known mean, and independent of the request.
3. Every request counts. That matches `LatestWorker` and not a caller that filters out unchanged inputs first.
4. The consumer takes each result the moment it finishes. A result left waiting while a newer request arrives is dropped without being counted as stale, so a slow consumer would lower the adopted share below the prediction.
5. When a finish and an arrival fall on the same instant, the finish is handled first, so that arrival counts as coming after the result. The periodic fixed service rows at loads 1.5 and 3 hit this often. The kept counts there stay at one per run either way.
6. The age is measured to the moment the result finishes, not to the later poll that adopts it. The wall clock study shows how large the gap can be.

## What this does not show

1. The service times are assumed inputs and the clock is virtual. Nothing here measures how long a real planning call takes. The [wall clock study](wall-clock-study.md) does that for one set of machines.
2. The arrival patterns are generated. They are not captured traffic, and no claim is made about any real workload.
3. Five seeds per cell is enough to separate the Poisson cases from the misses, and no more than that. The standard errors in the JSON show how tight each cell is.
4. The model covers the worker alone. It does not include the plan being adopted by the owner thread, the dispatch of tasks or the planner's own cost.
5. It sets no bound on latency or on how many results get kept.

## Where this comes from

The age of information literature models a related queue, a single server with one waiting place that a newer update replaces, for example [Age of Information: An Introduction and Survey](https://consensus.app/papers/details/w:06ef2908-0032-4213-8911-78dad7d94cc4/). I used it to choose the quantities. The closed forms above are my own derivation for this worker's rule, and I did not reproduce any published result here.
