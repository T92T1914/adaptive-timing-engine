# Revised numeric analysis through real completion

The installed consumer connects the existing causal planner, latest-request
mailbox and physical executor. It computes a count, sum, 256-bin histogram and
input digest for an immutable tuple of integers from 0 through 255. Revising
the input clears the current answer while preserving work that was already
physically accepted.

Install the package in a fresh environment, change to a directory outside the
source checkout, and run:

```sh
python -m adaptive_timing.revised_analysis --output revised-analysis.json
```

The demonstration uses three input revisions. After the first operation has
processed an actual chunk, a controlled gate holds it while the owner offers
two newer revisions. The second dispatch meets full physical capacity and is
retained. The third supersedes that unsubmitted dispatch with an explicit
outcome. The first calculation then finishes as inspectable stale work. After
reconciliation releases capacity, the third calculation supplies the current
answer. These gates establish a correctness schedule. They do not measure
service time, throughput or responsiveness.

## Programmatic consumer

`AnalysisSession` supports one logical analysis in a finite session. Offer an
immutable tuple with `offer`, call `step` to advance planning and reconcile real
completion, and read `current` for the answer that still belongs to the current
data revision. `report` retains the lifecycle evidence and remains available
after `close`. Explicitly close the session, including on exceptions.

The maximum input contains 65,536 integers. A session accepts at most 64 data
revisions. It uses one physical callable worker, physical capacity of one or two
jobs, one planner worker and at most one retained unsubmitted dispatch. Input
validation precedes revision changes. Custom analyzers are trusted synchronous
callbacks and must honor the same ownership and cleanup rules as `RealExecutor`.
The default operation has bounded input work. An arbitrary replacement callback
does not inherit a hard shutdown bound.

Every revision gets a new job ID. A capacity-refused dispatch can retry its exact
ID because physical refusal did not consume it. It is never reintroduced as new
causal work. Pending work has a finite physical-submission cutoff and an explicit
supersession or cancellation outcome. Retry follows reconciliation rather than
a spin loop. Accepted work retains its input and resource until physical return
and reconciliation, even when its data revision becomes obsolete.

Data revisions, mailbox generations and exact causal request leases are
recorded separately. Planning generations can advance without a data revision.
The owner checks the data revision again when adopting a usable completion.
A previously adopted answer is historical evidence after the next revision,
and is no longer the current answer.

## Clocks, work and limits

Planner seconds are elapsed `time.perf_counter()` time from a recorded session
origin. This clock is monotonic and supplies finer resolution for rapid
information deliveries on Windows Python 3.11 than that version's
`time.monotonic()` implementation. Custom monotonic session clocks are allowed,
but information deliveries must advance their observed timestamp. The consumer
does not fabricate a later clock value when a clock repeats.
Physical submission, start, completion and reconciliation retain their own raw
monotonic observations. The virtual service estimate is zero because the
consumer does not predict callable duration. It does not stand in for measured
physical completion. Clock origins and implementation/resolution metadata remain
in the receipt. Physical delays use physical timestamps from the same clock,
without subtracting a planner timestamp from a physical one. The deadline governs
physical submission, including retry
of an owner-held dispatch. Work already accepted into the physical queue can
start and finish after that cutoff. It retains its input and ownership until
return and reconciliation, and remains eligible for adoption while its data
revision is current. The cutoff does not promise a callable start or completion
time.

The receipt distinguishes offered requests, physical admission refusals,
superseded waiting work, physical outcomes, stale outcomes and adopted answers.
Processed input values count analysis-loop visits, including partial canceled
work. They exclude input validation, input digests and receipt overhead.
Queue delay, callable duration and
reconciliation delay have different boundaries and must not be combined into
a speed claim. Coarse physical clock resolution can produce zero measured
duration for short operations. Zero does not establish instantaneous execution.
A gated demonstration is not a sustained workload measurement. `finish` has an
observation timeout followed by waiting cleanup, so that timeout is not a hard
bound on the complete call.

The receipt records hashes of source files on disk with their capture scope.
Those hashes support an external source/install comparison. They do not attest
loaded bytecode, arbitrary injected analyzer code or source authenticity.

Finite revision and input limits bound this consumer's retained history. They
do not change the executor's issued-ID lifetime or authorize session rollover
while old physical work still owns resources. No native backend, GPU, network
data, external service or new runtime dependency is needed. The consumer is
neither a hostile-code sandbox nor a hard real-time controller.
