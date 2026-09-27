# Planning with information that has arrived

`build_plan` remains the complete batch offline comparator. It knows every event
in its argument and uses a centered workload window. `CausalScheduler` adds an
incremental session that only constructs plans from delivered facts. Both use
the existing serial resource rule and the same random draw for a given seed
and event ID. Neither is an optimal admission algorithm.

## What the session knows

An immutable `Information` record contains `observed_at`, `available_at`,
`sequence`, `kind`, `id`, `version` and an optional immutable `Event`.
`observed_at` records when the source observed the fact. `available_at` records
when this session receives it, and must be at least `observed_at`. An event's
`earliest` is its release time. `target` is the desired start, and `deadline`
is the latest completion. Delivery can legitimately announce a task long
before its release. A source timestamp does not let the planner use a fact
before delivery.

The caller delivers one complete group at each `available_at` timestamp with
`receive(facts, now=t)`. Facts in that group are applied in ascending `sequence`
order. Sequence numbers must be unique within the group. Announcement starts
at version one, and updates and cancellations increment that ID's version by
one. The whole group is checked before any state changes. IDs are never reused.
Input tuple order does not change equal timestamp ordering.

All facts at time `t` are delivered before planning or polling at `t`. Planning
or polling closes that timestamp. A second delivery group at the same time,
backdated delivery, or information with a future availability time is rejected.
Applications should buffer simultaneous source messages into one group. This
contract establishes an explicit ordering boundary. It cannot verify whether
an external producer reported its real observation or delivery time honestly.

## Policy and resource state

The causal policy sorts the available outstanding catalog by target and ID.
Its centered density window counts only that catalog. A future task that was
already announced contributes. A task that has not arrived does not. This is
an estimate of known scheduled demand, not a claim to predict hidden arrivals.

Effort begins with the session's virtual dispatch history, decayed to the
decision time or target, whichever is later. The current calculation then
projects effort through the tasks it admits. A new calculation starts again
from observed dispatch effort, so repeating a plan does not manufacture new
fatigue. Rejection and expiration add no service effort. This synthetic state
is a scheduling mechanism, not a fitted physiological model.

Each resource permits one service at a time. Actual virtual dispatch reserves
its duration plus `resource_gap`. Reservations survive updates, cancellations
and plan replacement. Different resources can work concurrently. Noise is
keyed by seed and stable ID, including after an update, so dropping another
event cannot shift the random draw sequence.

Rejection is terminal in this policy. Dispatched, expired, rejected and
canceled IDs cannot be resurrected. A later well formed update or cancellation
of a terminal ID is recorded as `ignored_terminal`. It cannot revoke service
that has already started. This is deliberately a small session contract,
without retry, preemption or a second queue for rejected work.

## Using the API

```python
from adaptive_timing import (
    CausalScheduler, Event, Information, build_causal_plan,
)

session = CausalScheduler(seed=42)
event = Event("task-a", target=1.0, earliest=0.95, deadline=1.2, duration=0.05)
fact = Information(available_at=0.0, sequence=0, observed_at=0.0,
                   kind="announce", id=event.id, version=1, event=event)
session.receive((fact,), now=0.0)
request = session.snapshot(0.0)
result = build_causal_plan(request)
assert session.install(result)
records = session.poll(1.0)
```

`CausalRequest` and `CausalResult` are immutable. The existing `LatestWorker`
can compute `build_causal_plan(request)` in its thread. Pass a successful
outcome's value to `session.install` on the session's owning thread. The result
must carry the exact request object issued by that session and its current
generation. This supports the existing thread design, not serialization to a
process. There is no process rewrite or hard real time guarantee.

Receiving new information immediately clears the old unissued plan. An older
result cannot be adopted even while its replacement is still computing. A new
snapshot or a dispatch or expiration also invalidates an outstanding result.
A current result that is merely late can be adopted, but the existing executor
checks the actual poll time and can expire its tasks. Plan admission is not
dispatch. The executor emits records and does not observe external completion.

During computation the session may temporarily have no installable plan.
That conservative invalidation can lose service opportunities. Session ID,
version and outcome storage grows with the event count. Start a fresh session
for independent work. The implementation is intended for inspectable bounded
streams, with no new claims about constant memory or CPU isolation.

The [comparison protocol](causal-protocol.md) declares the new experiment.
The [causality tests](../tests/test_causal.py) change future announcements,
updates and cancellations while requiring earlier plans and dispatch records
to remain identical. They also include an offline positive control that does
respond to the hidden suffix. The retained 48 case study remains separate and
its evaluated source revision remains unknown.
