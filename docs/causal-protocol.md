# Causal information comparison protocol

This protocol and the evaluated implementation must be committed before the
comparison runs. Execute the declared matrix once. Keep all results, including
unfavorable cases, failures and unresolved outcomes. A defect that invalidates
the run requires a retained record and a separately identified amendment.

## Question and fixed scope

How do an available information policy and the preserved complete batch
comparator differ under the same synthetic stream and event noise?

The matrix has four workloads from the existing generator, `steady`, `burst`,
`recovery` and `saturation`, two delivery patterns, `rolling` and `revisions`,
and seeds 7, 42 and 73. Each stream contains 120 stable event IDs on two
resources. This produces 24 pairs and 48 policy runs. Count 120 retains the
generator's recovery break at event 96. No old experiment is rerun.

`rolling` announces each original event 80 ms before its target, rounded up to
the next 10 ms delivery boundary. `revisions` uses the same announcement rule
except that IDs with index divisible by 11 arrive 60 ms after their target.
For other IDs, those divisible by 10 receive an update 20 ms before the original
target. The update moves target and deadline 30 ms later and preserves the
release and service duration. IDs divisible by 13 receive a cancellation 10 ms
before the original target, unless their announcement is deliberately late.
Where both apply, update precedes cancellation and increments the version.
Every source observation is 5 ms before delivery, clamped to zero. A stable
insertion sequence orders simultaneous facts. The saved stream is authoritative
for all exact floating point values and rounding.

Both policies use the unchanged default `Policy`. All random draws use the
same seed and event ID. No sequential RNG is consumed by policy decisions.
No seed is selected after viewing results.

## Comparator and execution

The causal session receives facts only at delivery. It replans synchronously
after each delivery group and installs that result immediately. It does not
replan solely because a poll occurred. At equal times, delivery and planning
precede polling. The stream driver holds future input outside the scheduler.

The offline comparator resolves the full stream into final event versions
before calling the unchanged `build_plan` at time zero. It omits every canceled
ID before service begins. It thus knows future arrivals, updates and
cancellations. This is an information advantaged comparator, not a deployable
online baseline. Differences combine information, estimator history and
replanning choices. They do not isolate the effect of the density formula and
do not establish that one policy is generally better.

Both use the existing virtual executor. Poll every 20 ms, with a candidate
budget of 32, until 200 ms beyond the later of the final fact and all event
deadlines. Information delivery remains at 10 ms boundaries. Time is simulated,
not measured operating system wakeup latency. Service duration is an assumed
input. The study uses one process sequentially and has no external I/O or device
actuation. Delayed computation and stale output are covered by deterministic
regressions, not a new latency benchmark.

## Outcomes and measurements

Retain every delivered fact, plan at its decision time, adoption, dispatch and
expiration, final state, seed, policy and environment identity. Record the exact
clean evaluated Git commit, protocol hash, Python version and platform.

`planned` counts scheduled entries across installed calculations, so repeated
plans can count an ID more than once. `admitted` counts distinct IDs admitted
at least once. Neither is a terminal outcome or can be added to terminal counts.
`rejected` means the policy found no feasible interval and made that ID terminal.
`expired` means an admitted task missed completion feasibility at actual polling.
`dispatched` means the executor emitted a dispatch record. `canceled` means
cancellation became terminal before dispatch. `unknown` means no terminal
outcome was available at the horizon. These five terminal categories partition
the 120 announced IDs. Also count `ignored_terminal` information changes.

`completed` is null because no external completion was observed. Report
`modeled_service_finished` separately when an assumed service finish is at or
before the horizon. Do not label modeled elapsed duration as verified completion.

For dispatched IDs record the absolute start offset p50 and p95 in ms, assumed
resource busy seconds excluding gaps, and per resource utilization over the
whole horizon. Report admission plan density, sigma and fatigue means and the
number of event evaluations across calculations. Record diagnostic total
calculation time, but do not make process scaling or latency guarantees from it.
Offset samples depend on admission. Also compare offsets on IDs dispatched by
both members of a pair, with that intersection size shown. Empty samples are
null, not zero performance. Pair differences use causal minus offline.

Retain all 24 pair rows. Describe directions and concrete cases, without a
significance claim, confidence interval or universal winner. Seeds within a
workload are repeated synthetic conditions, not independent real environments.

## Reproduction

```sh
python tools/run_causal_study.py --output /path/to/new/causal-run
```

The runner refuses a dirty checkout and an existing output directory. Preserve
the original output before any authorized reproduction. Render from the saved
JSON rather than running the experiment during a site build. The new report
uses the existing Clair/Obscur HTML adapter, local Inter and explicit fallback
policy. It links the protocol, raw JSON and current repository implementation.
