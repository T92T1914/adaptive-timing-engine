# Caller clock and interval precision

Time fields use finite nonnegative seconds on one caller clock. A scheduled
finish is the forward arithmetic result `start + event.duration`. `Plan` checks
that result directly. Subtracting two large rounded timestamps cannot reliably
recover the original duration, so an inverse subtraction residual is no longer
the admission rule.

For binary64 inputs, each endpoint has the rounding of one floating point
addition. This is not exact real arithmetic or a promise of translation
invariance. Representable spacing grows with the clock magnitude. A positive
event duration must be at least `math.ulp(event.deadline + 1e-12)`, the spacing
at the end of its window including the existing late-finish allowance.
Zero durations remain valid. This conservative whole-window
rule keeps positive service resolvable even when a later virtual poll starts
it in a coarser part of the clock. A positive resource gap has the same minimum
for each scheduled event. An unsupported interval raises a specific clock
resolution error with guidance to use relative session seconds.

Both planners check their subtraction-derived start bound using forward addition. If
`deadline - duration` rounds upward far enough to finish late, they move that
bound down one representable step. An earlier feasible start can then be used.
If the event's release time is later than that bound, it receives the existing
infeasible-window rejection. The bound remains conservative. It does not search
every representable start or promise admission of every interval whose rounded
finish could fit. The deadline and resource overlap checks remain
separate and retain their existing allowances. This correction does not enlarge
either allowance.

For example, an origin of 100 million seconds with a duration of 0.017 seconds
can produce `finish - start - duration` around 4.649 nanoseconds. That residual
does not make the forward endpoint inconsistent. The direct and causal builders
now validate the same endpoint that they construct. At `2**53` seconds the
spacing is two seconds, so a one-second positive service is explicitly refused.
A two-second service and zero-duration events remain supported with a resolvable
gap, including a zero gap.

## Compatibility and clock mapping

Manually assembled `Scheduled` records must use `finish = start + duration`.
An independently approximated endpoint that differed by less than the former
one-nanosecond tolerance can now be refused. Planner-produced records continue
to use the same forward calculation. Events or gaps below their window's
declared clock resolution are newly refused instead of silently losing their
represented occupancy. This includes events that could have run early in a broad
window but whose much later deadline has coarser spacing. It also covers the
binade immediately beyond a deadline when the existing allowance reaches it.

Keep a caller's original origin as metadata when converting to relative time.
For a local monotonic session, capture `origin = time.monotonic()` once and use
`time.monotonic() - origin` for planner seconds. Keep that mapping separate from
the physical executor's raw monotonic observations and from source timestamps.
The mapping establishes units and an origin, not a real deadline guarantee.

The existing synthetic policy, historical experiment files, event IDs and
physical resource lifecycle are unchanged. This contract qualifies represented
interval admission. It does not certify operating-system wakeups, hard real-time
execution, or accuracy below the clock's representable resolution.
