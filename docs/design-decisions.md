# Decisions, failure modes, and tradeoffs

## Keep computation and adoption separate

The original request mailbox design solves a concrete failure: an older calculation can finish after the state that requested it has become obsolete. Generation numbers identify requests. Submitting a new request replaces the waiting slot and invalidates pending output immediately. The worker only publishes an outcome if its generation remains current.

The public version generalizes the payload and result types, adds an explicit shutdown path, records errors and counters, and keeps a persistent waiting worker. A result whose value is `None` is distinct from a failed computation.

The low level mailbox's `take()` is atomic, but validity at consumption is not a perpetual guarantee. A new request could arrive just afterward. `Controller` therefore owns both submission and adoption on one thread. Other threads cannot submit through that controller; the worker only computes immutable snapshots. The caller must also avoid sharing mutable payloads through the low level generic worker.

## Bound waiting work instead of promising cancellation

Running arbitrary Python code cannot be safely killed by this worker. The active calculation finishes, but its stale output is discarded; intermediate waiting requests are replaced. This bounds waiting slots rather than waiting time. A slow or stuck callback still delays the newest calculation. Shutdown with a finite timeout reports whether the worker actually stopped.

The gated worker experiment forces this situation and records two computed payloads out of 101 requests. That is evidence about queue policy, not a claim that 101 arbitrary calculations became 50 times faster.

## Validate once, process incrementally

`Plan` checks ordering, unique IDs, finite values, resource separation, and event windows when it is constructed. The executor can then adopt the immutable plan by replacing references instead of scanning it again on each tick. The first draft rebuilt a heap during adoption; that explicit O(n) work was removed because large replacements would enter the execution path.

The executor merges an index into the sorted plan with a heap containing only tasks deferred by resource occupancy. Each examined item consumes the poll budget, including already issued IDs. Otherwise, installing a plan with many previously issued tasks could turn one poll into an unbounded scan. Heap work is O(log n) per deferred task, while the issued ID set retains session history.

This is an algorithmic work bound, not a hard wall clock bound: releasing old Python objects can require work proportional to their size, and the runtime and OS can pause the thread. CPU isolated planning and deferred reclamation would need their own implementation and latency evaluation before making stronger claims.

## Preserve execution facts across replanning

A new plan replaces future intent. It cannot undo an event already emitted or a resource already reserved by a late dispatch. The executor retains issued IDs and resource availability. At dispatch time it checks whether the task can still complete before its deadline; if not, it emits an expiration record. Invalid plan adoption leaves the previous plan installed.

The resource gap belongs to the reservation made when a task is dispatched. A later policy change does not retroactively alter that reservation. Applications needing longer safety intervals must model those intervals in their event durations or define a different reservation contract.

## Compare policies without changing the random experiment

A single sequential random generator is easy to disturb: reject one task, add a draw, and all subsequent tasks get different randomness. Here the seed and event ID determine a standardized Gaussian draw. Individual policy ablations change its scale without changing that underlying draw. Cross resource events can change their execution order, so comparisons join by ID rather than by output position.

The model measures density separately for each resource. Extra work on an unrelated resource does not manufacture pressure on this one. Recovery uses the full elapsed interval rather than a capped time step; a long rest therefore actually dissipates the synthetic effort state.

The defaults are illustrative parameters. Workload response, fatigue response, and noise can be disabled separately. Accumulated effort is still recorded in an ablation that disables its effect on timing; this keeps the state trajectory inspectable.

## Make rejected and expired work visible

A task rejected while planning had no feasible interval under the selected greedy order. An expired task was admitted but could not complete after the virtual executor woke up. These are different failure modes. A rejected task consumes no service time or additional effort, and a deadline expiration never appears as successful dispatch.

The saturation result is intentionally retained: full variability admits fewer tasks than target only requests. Neither result proves global scheduling optimality. An optimization objective, alternative admission order, and comparable experiments would be needed to study that question.

## Keep this package independent

Only the general request mailbox architecture was adapted from the private application. The public event model, behavioral policy equations, executor, fixtures, and viewer are new standalone code. They do not contain private integrations, original calibration values, profiles, captured datasets, or inherited history. The private implementation has not been replaced by this package.
