# Planning after information arrives

The causal policy dispatched fewer tasks in 9 of the 24 pairs, the same number in 7, and more in 8. This comparison retains every declared workload and seed.

The largest dispatch deficit was steady with revisions delivery at seed 7. The causal session dispatched 100 tasks and the offline comparator dispatched 111. Admission alone would not describe that result.

At seed 42 with rolling saturation, the causal policy dispatched 45 tasks and expired 7. The offline comparator dispatched 8 and expired 42. The causal session evaluated 736 event snapshots across replans, compared with 120 in the single offline calculation. Only 7 IDs were dispatched by both policies in that pair, which limits its shared offset comparison.

The offline comparator knows final versions and cancellations before service begins. The causal policy knows only delivered facts and also changes how effort and replanning are handled. These results compare those complete policies. They do not isolate one mechanism or establish a universal winner.

Offsets are measured among dispatched tasks, so the sample changes when admission or expiration changes. The paired table also compares mean absolute offsets only among IDs dispatched by both policies. A positive difference means the causal policy started farther from its applicable target on that subset.

Evaluated implementation and protocol: [`dba8f6fe92e6c7f7d9f5b160fe1f6031285b6a18`](https://github.com/T92T1914/adaptive-timing-engine/tree/dba8f6fe92e6c7f7d9f5b160fe1f6031285b6a18). Four synthetic workloads, two delivery patterns, three seeds and 120 IDs per stream produce 24 pairs.

Read the [information contract](../causal-policy.md), [committed protocol](../causal-protocol.md), [HTML report](https://t92t1914.github.io/adaptive-timing-engine/causal.html), [summary](summary.json) and [raw traces](raw.json.gz).

Planned entries count every admitted schedule across calculations. Admission counts distinct IDs that were admitted at least once. Rejected, expired, dispatched, canceled and unknown are separate final states and partition the event IDs. They must not be added to admission. Completed remains null because there is no external completion observation. Modeled service finish uses the assumed duration only.

All 24 pairs. Differences are causal minus offline.

| Workload | Delivery | Seed | Admission difference | Dispatch difference | Expired difference | Shared IDs | Mean offset difference on shared IDs (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| steady | rolling | 7 | +0 | +0 | +0 | 120 | +0.000 |
| steady | rolling | 42 | +0 | +0 | +0 | 120 | +0.167 |
| steady | rolling | 73 | +0 | +0 | +0 | 120 | +0.000 |
| steady | revisions | 7 | -2 | -11 | +0 | 100 | +0.000 |
| steady | revisions | 42 | -2 | -11 | +0 | 100 | -0.200 |
| steady | revisions | 73 | -2 | -9 | +0 | 100 | +0.000 |
| burst | rolling | 7 | +0 | +0 | +0 | 120 | -3.000 |
| burst | rolling | 42 | +0 | +0 | +0 | 120 | -2.800 |
| burst | rolling | 73 | +0 | +1 | -1 | 119 | -2.992 |
| burst | revisions | 7 | -2 | -11 | +0 | 100 | -2.360 |
| burst | revisions | 42 | -2 | -10 | +0 | 100 | -2.780 |
| burst | revisions | 73 | -2 | -8 | -1 | 100 | -2.400 |
| recovery | rolling | 7 | +0 | +0 | +0 | 120 | -3.100 |
| recovery | rolling | 42 | +0 | +0 | +0 | 120 | -2.300 |
| recovery | rolling | 73 | +0 | +1 | -1 | 119 | -2.992 |
| recovery | revisions | 7 | -2 | -11 | +0 | 100 | -2.280 |
| recovery | revisions | 42 | -2 | -10 | +0 | 100 | -2.580 |
| recovery | revisions | 73 | -2 | -8 | -1 | 100 | -2.200 |
| saturation | rolling | 7 | +19 | +38 | -36 | 8 | -12.000 |
| saturation | rolling | 42 | +20 | +37 | -35 | 7 | -20.000 |
| saturation | rolling | 73 | +18 | +37 | -35 | 9 | -13.333 |
| saturation | revisions | 7 | +18 | +35 | -36 | 8 | -12.000 |
| saturation | revisions | 42 | +19 | +33 | -33 | 8 | -20.000 |
| saturation | revisions | 73 | +16 | +37 | -38 | 9 | -11.111 |

All 48 runs. Terminal columns exclude admission, which can overlap them.

| Workload | Delivery | Seed | Policy | Admitted | Rejected | Expired | Dispatched | Canceled | Unknown | Offset p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| steady | rolling | 7 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 20.000 |
| steady | rolling | 7 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 20.000 |
| steady | rolling | 42 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 21.000 |
| steady | rolling | 42 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 21.000 |
| steady | rolling | 73 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 20.000 |
| steady | rolling | 73 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 20.000 |
| steady | revisions | 7 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 30.000 |
| steady | revisions | 7 | causal | 109 | 11 | 0 | 100 | 9 | 0 | 30.000 |
| steady | revisions | 42 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 30.000 |
| steady | revisions | 42 | causal | 109 | 11 | 0 | 100 | 9 | 0 | 30.500 |
| steady | revisions | 73 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 20.000 |
| steady | revisions | 73 | causal | 109 | 11 | 0 | 102 | 7 | 0 | 20.000 |
| burst | rolling | 7 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 44.000 |
| burst | rolling | 7 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 36.000 |
| burst | rolling | 42 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 36.400 |
| burst | rolling | 42 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 36.000 |
| burst | rolling | 73 | offline | 120 | 0 | 1 | 119 | 0 | 0 | 36.000 |
| burst | rolling | 73 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 28.000 |
| burst | revisions | 7 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 40.000 |
| burst | revisions | 7 | causal | 109 | 11 | 0 | 100 | 9 | 0 | 28.200 |
| burst | revisions | 42 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 36.000 |
| burst | revisions | 42 | causal | 109 | 11 | 0 | 101 | 8 | 0 | 36.000 |
| burst | revisions | 73 | offline | 111 | 0 | 1 | 110 | 9 | 0 | 32.000 |
| burst | revisions | 73 | causal | 109 | 11 | 0 | 102 | 7 | 0 | 27.900 |
| recovery | rolling | 7 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 44.000 |
| recovery | rolling | 7 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 36.000 |
| recovery | rolling | 42 | offline | 120 | 0 | 0 | 120 | 0 | 0 | 36.400 |
| recovery | rolling | 42 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 32.000 |
| recovery | rolling | 73 | offline | 120 | 0 | 1 | 119 | 0 | 0 | 36.000 |
| recovery | rolling | 73 | causal | 120 | 0 | 0 | 120 | 0 | 0 | 28.000 |
| recovery | revisions | 7 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 40.000 |
| recovery | revisions | 7 | causal | 109 | 11 | 0 | 100 | 9 | 0 | 28.200 |
| recovery | revisions | 42 | offline | 111 | 0 | 0 | 111 | 9 | 0 | 36.000 |
| recovery | revisions | 42 | causal | 109 | 11 | 0 | 101 | 8 | 0 | 32.000 |
| recovery | revisions | 73 | offline | 111 | 0 | 1 | 110 | 9 | 0 | 32.000 |
| recovery | revisions | 73 | causal | 109 | 11 | 0 | 102 | 7 | 0 | 27.900 |
| saturation | rolling | 7 | offline | 52 | 68 | 39 | 13 | 0 | 0 | 56.000 |
| saturation | rolling | 7 | causal | 71 | 66 | 3 | 51 | 0 | 0 | 52.000 |
| saturation | rolling | 42 | offline | 50 | 70 | 42 | 8 | 0 | 0 | 56.000 |
| saturation | rolling | 42 | causal | 70 | 68 | 7 | 45 | 0 | 0 | 55.200 |
| saturation | rolling | 73 | offline | 53 | 67 | 39 | 14 | 0 | 0 | 56.000 |
| saturation | rolling | 73 | causal | 71 | 65 | 4 | 51 | 0 | 0 | 56.000 |
| saturation | revisions | 7 | offline | 52 | 59 | 37 | 15 | 9 | 0 | 56.000 |
| saturation | revisions | 7 | causal | 70 | 65 | 1 | 50 | 4 | 0 | 52.000 |
| saturation | revisions | 42 | offline | 50 | 61 | 34 | 16 | 9 | 0 | 56.000 |
| saturation | revisions | 42 | causal | 69 | 66 | 1 | 49 | 4 | 0 | 52.000 |
| saturation | revisions | 73 | offline | 53 | 58 | 39 | 14 | 9 | 0 | 56.000 |
| saturation | revisions | 73 | causal | 69 | 64 | 1 | 51 | 4 | 0 | 50.000 |

Work and policy estimates across every installed plan.

| Workload | Delivery | Seed | Policy | Calculations | Event evaluations | Planned entries | Busy seconds | Mean density | Mean fatigue | Mean sigma (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| steady | rolling | 7 | offline | 1 | 120 | 120 | 2.040 | 3.708 | 0.082 | 12.640 |
| steady | rolling | 7 | causal | 120 | 120 | 120 | 2.040 | 1.250 | 0.082 | 12.643 |
| steady | rolling | 42 | offline | 1 | 120 | 120 | 2.040 | 3.708 | 0.082 | 12.640 |
| steady | rolling | 42 | causal | 120 | 120 | 120 | 2.040 | 1.250 | 0.082 | 12.643 |
| steady | rolling | 73 | offline | 1 | 120 | 120 | 2.040 | 3.708 | 0.082 | 12.640 |
| steady | rolling | 73 | causal | 120 | 120 | 120 | 2.040 | 1.250 | 0.082 | 12.642 |
| steady | revisions | 7 | offline | 1 | 111 | 111 | 1.887 | 3.502 | 0.075 | 12.588 |
| steady | revisions | 7 | causal | 139 | 130 | 119 | 1.700 | 1.250 | 0.069 | 12.536 |
| steady | revisions | 42 | offline | 1 | 111 | 111 | 1.887 | 3.502 | 0.075 | 12.588 |
| steady | revisions | 42 | causal | 139 | 130 | 119 | 1.700 | 1.250 | 0.069 | 12.536 |
| steady | revisions | 73 | offline | 1 | 111 | 111 | 1.887 | 3.502 | 0.075 | 12.588 |
| steady | revisions | 73 | causal | 139 | 130 | 119 | 1.734 | 1.250 | 0.070 | 12.548 |
| burst | rolling | 7 | offline | 1 | 120 | 120 | 2.040 | 10.562 | 0.363 | 19.398 |
| burst | rolling | 7 | causal | 120 | 287 | 287 | 2.040 | 2.078 | 0.249 | 13.941 |
| burst | rolling | 42 | offline | 1 | 120 | 120 | 2.040 | 10.562 | 0.363 | 19.398 |
| burst | rolling | 42 | causal | 120 | 286 | 286 | 2.040 | 2.072 | 0.249 | 13.944 |
| burst | rolling | 73 | offline | 1 | 120 | 120 | 2.023 | 10.562 | 0.363 | 19.398 |
| burst | rolling | 73 | causal | 120 | 276 | 276 | 2.040 | 2.011 | 0.245 | 13.908 |
| burst | revisions | 7 | offline | 1 | 111 | 111 | 1.887 | 9.628 | 0.307 | 17.932 |
| burst | revisions | 7 | causal | 130 | 294 | 283 | 1.700 | 2.133 | 0.207 | 13.612 |
| burst | revisions | 42 | offline | 1 | 111 | 111 | 1.887 | 9.628 | 0.307 | 17.932 |
| burst | revisions | 42 | causal | 130 | 282 | 271 | 1.717 | 2.016 | 0.207 | 13.612 |
| burst | revisions | 73 | offline | 1 | 111 | 111 | 1.870 | 9.628 | 0.307 | 17.932 |
| burst | revisions | 73 | causal | 130 | 284 | 273 | 1.734 | 2.047 | 0.209 | 13.632 |
| recovery | rolling | 7 | offline | 1 | 120 | 120 | 2.040 | 10.479 | 0.307 | 18.910 |
| recovery | rolling | 7 | causal | 120 | 287 | 287 | 2.040 | 2.078 | 0.236 | 13.840 |
| recovery | rolling | 42 | offline | 1 | 120 | 120 | 2.040 | 10.479 | 0.307 | 18.910 |
| recovery | rolling | 42 | causal | 120 | 286 | 286 | 2.040 | 2.072 | 0.236 | 13.842 |
| recovery | rolling | 73 | offline | 1 | 120 | 120 | 2.023 | 10.479 | 0.307 | 18.910 |
| recovery | rolling | 73 | causal | 120 | 276 | 276 | 2.040 | 2.011 | 0.231 | 13.803 |
| recovery | revisions | 7 | offline | 1 | 111 | 111 | 1.887 | 9.538 | 0.260 | 17.512 |
| recovery | revisions | 7 | causal | 130 | 294 | 283 | 1.700 | 2.133 | 0.196 | 13.529 |
| recovery | revisions | 42 | offline | 1 | 111 | 111 | 1.887 | 9.538 | 0.260 | 17.512 |
| recovery | revisions | 42 | causal | 130 | 282 | 271 | 1.717 | 2.016 | 0.195 | 13.524 |
| recovery | revisions | 73 | offline | 1 | 111 | 111 | 1.870 | 9.538 | 0.260 | 17.512 |
| recovery | revisions | 73 | causal | 130 | 284 | 273 | 1.734 | 2.047 | 0.198 | 13.544 |
| saturation | rolling | 7 | offline | 1 | 120 | 52 | 0.221 | 72.019 | 0.839 | 100.717 |
| saturation | rolling | 7 | causal | 48 | 739 | 673 | 0.867 | 10.006 | 0.265 | 16.148 |
| saturation | rolling | 42 | offline | 1 | 120 | 50 | 0.136 | 72.075 | 0.834 | 100.520 |
| saturation | rolling | 42 | causal | 48 | 736 | 668 | 0.765 | 9.963 | 0.254 | 16.010 |
| saturation | rolling | 73 | offline | 1 | 120 | 53 | 0.238 | 71.934 | 0.842 | 100.713 |
| saturation | rolling | 73 | causal | 48 | 735 | 670 | 0.867 | 9.951 | 0.266 | 16.104 |
| saturation | revisions | 7 | offline | 1 | 111 | 52 | 0.255 | 66.611 | 0.829 | 93.383 |
| saturation | revisions | 7 | causal | 53 | 761 | 696 | 0.850 | 9.626 | 0.264 | 15.879 |
| saturation | revisions | 42 | offline | 1 | 111 | 50 | 0.272 | 66.500 | 0.823 | 92.960 |
| saturation | revisions | 42 | causal | 53 | 760 | 694 | 0.833 | 9.609 | 0.258 | 15.803 |
| saturation | revisions | 73 | offline | 1 | 111 | 53 | 0.238 | 66.580 | 0.832 | 93.455 |
| saturation | revisions | 73 | causal | 53 | 757 | 693 | 0.867 | 9.578 | 0.265 | 15.840 |

The raw archive retains all delivery groups, event versions, installed plans and execution records. The summary also records ignored terminal changes, modeled service finishes, utilization and diagnostic calculation times. Means over plan entries include repeated IDs. Calculation time is local context, not a scaling study or a deadline guarantee. Other figure unit checks were active during this run, so calculation durations are not compared. Expiration records retain the executor's attempted service start, without a separate poll emission timestamp.

The study ran synchronously in one process, with virtual polls every 20 ms and a candidate budget of 32. No external device was controlled. Stale and delayed planner results are tested with deterministic gates outside this comparison. Lower offset alone does not establish a better scheduling policy, and synthetic workloads do not establish behavior on an unseen production stream.

Raw archive SHA-256: `efb2b3355a927af656495844e82e0e7b6d57c358e969fe3e94a4f53e1fcbcc6c`. The original 48 case batch study and its figures remain unchanged. Its evaluated revision was not recorded and remains unknown.
