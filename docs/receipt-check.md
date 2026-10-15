# Inspect a saved analysis receipt

The installed checker reads a saved revised-analysis report without running its
work again:

```sh
python -m adaptive_timing.receipt_check revised-analysis.json
python -m adaptive_timing.receipt_check revised-analysis.json --json
```

It reports three separate facts: whether the input is admitted, whether its
supported recorded claims agree, and whether it represents complete or incomplete
work. A consistent receipt does not authenticate its producer or prove that the
represented execution happened.

The checker uses only the standard library. It does not start a planner or
executor, import a producer named in a record, follow source paths, fetch URLs,
replay a schedule or load executable objects. Source hashes remain saved claims.

## Inputs and results

The initial supported profile is the current schema-1 report from
`adaptive_timing.revised_analysis`. A bare report is the default input.
Retained client and operating-run records sometimes wrap the report in a named
field. Select that field explicitly:

```sh
python -m adaptive_timing.receipt_check client.json --record session --json
python -m adaptive_timing.receipt_check operating.json --record consumer --json
```

The whole file must pass bounded JSON admission before wrapper selection. The
checker never searches arbitrary nested records. An unsupported version, profile
or value is distinct from a contradiction in an admitted record.

The Python API provides `inspect_bytes(data, record="root")` and
`inspect_file(path, record="root")`. Both return a dictionary containing
`admission`, `consistency`, `completion`,
`issues`, `not_checked`, the record selector and
`receipt_sha256`. Each issue has a code, JSON location and actionable
message. The receipt hash identifies the exact inspected bytes. It is separate
from the captured digest of the original analysis input, which is absent.

| Exit | Meaning |
| --- | --- |
| 0 | Supported recorded claims are internally consistent, including a valid incomplete report |
| 1 | An admitted record contains a demonstrated contradiction |
| 2 | Input cannot be read/admitted, or requested report delivery failed |

Read `completion` separately from exit status. A cleaned-up timeout or
missing current result can be consistently incomplete. A clean zero-revision
session can be complete with no current answer. Empty input on a real revision
instead has an actual zero-count Summary.

Add `--output NEW_FILE` to save the verdict as JSON as well as printing it.
The file is created exclusively. Existing files, input aliases, hard links and
symlinks are refused without truncation. A delivery failure has a separate
`delivery_issue` and exit 2. No crash-durability promise is made. A disk
write failure after creation can leave a partial new output file.
Choose distinct paths before invoking a shell: shell redirection can truncate
a file before the checker starts.

## What the recorded checks mean

Historical usability is evaluated from a result's own reconciliation fields:
completed status, no stale flag and no cancellation request. A later offer can
clear the current answer while retaining earlier usable results and adoption
timestamps. Final currency is checked separately against the exact saved eligible
result, including its histogram and digest.

A revision's data identity joins to physical result generations. Mailbox and
causal generations remain separate. The same job ID in a revision, dispatch,
result and observation is an intentional reference. Coincident generation
numbers cannot reveal which domain a writer meant.

Numeric Summary claims require 256 nonnegative integer bins, their sum equal to
count and their weighted sum equal to total. The result count and digest must
match the referenced revision's input identity. A different valid hexadecimal
digest is a contradiction. Changing both untrusted digests consistently may
evade that comparison. Histogram counts cannot recover original byte order.

These are the declared numeric operation's rules. The producer does not validate
every injected callback's Summary. A reported adoption error can also accompany a
physically completed non-Summary value, including False or a Summary-looking
plain dictionary. The checker preserves that lifecycle and does not infer
original Python types or numeric adoption from JSON shape alone.
An object outcome without a recorded adoption error is checked as a declared
numeric value only when all four Summary fields are present. Other object
outcomes are an explicitly unsupported value profile. This compatibility rule
does not establish the original Python class or identify a custom analyzer.

Metrics use the producer's recorded populations:

| Metric | Recomputed from |
| --- | --- |
| offered | Revision rows |
| admitted | Presence of admitted_at, including zero |
| refused | Sum of revision refusal attempts |
| replaced | Superseded-before-admission dispositions |
| physical_started | Retained results with a nonnull start |
| physical_completed | Retained completed results |
| physical_values | Recorded progress across revisions |
| usable / stale | Retained result flags and usability expression |

A live start need not yet appear in result-based metrics. Progress, retained
results and drained observations are sampled separately. The file is not an
atomic replay trace. One snapshot cannot establish unrecorded transitions,
progress history or the analyzer implementation.

Physical endpoints and delays are checked within their own clock. Equal
readings are supported. Queued cancellation and submission failure can have null
starts, while completed work requires a recorded start. Session elapsed times stay separate.
The submission cutoff does not impose a start or completion deadline on work
already accepted into the physical queue. A later row cancellation also need
not equal the earlier immutable result's cancellation flag.
A true result cancellation flag does require the owning revision's cancellation
flag. The reverse implication is not valid after later owner cancellation.
The closed flag stops new admission. An owner-thread report captured reentrantly
during close can still precede cleanup, so a closed incomplete snapshot alone
does not certify that its work has retired.

## Supported bounds and missing evidence

Input must be an explicit local regular UTF-8 file. The checker refuses UNC
paths, malformed UTF-8, unpaired surrogate escapes, duplicate object keys and
nonfinite numbers. It applies these supported limits to the entire document:

| Limit | Value |
| --- | ---: |
| Raw bytes | 4,194,304 |
| Nested containers | 32 |
| Numeric token characters | 128 |
| Decoded string characters | 16,384 |
| Object members | 128 |
| Array items | 512 |
| Parsed values and keys | 100,000 |
| Diagnostic issues | 32 |

The selected report additionally admits at most 64 revisions, 64 results and
512 observations, using the producer's input ceiling of 65,536 byte-valued
integers, one physical worker and capacity one or two. Over-limit records are
refused. These are consumer compatibility limits, not universal maximum valid
producer output or a general security certification.

Bounded annotation fields may remain unvalidated metadata. Unknown core states
and incompatible semantic structures receive explicit unsupported or malformed
dispositions. An earlier schema-1 profile missing current clock/source/timeout
metadata is unsupported with field locations. Missing fields do not authenticate
a historical origin. Ordinary reports need neither demonstration annotations
nor successful completion. Explicit unavailable source-hash entries are supported.

Original private receipts remain unchanged. Public test fixtures are separately
identified sanitized derivatives with synthetic paths and a documented omission
policy. Their identities are distinct from the original evidence.

## Validation boundary

Tests pair valid complete, incomplete, empty and live receipts with targeted
contradictions and parser-limit controls. Installed verification invokes the
shipped command outside the checkout with thread/executor, subprocess, network,
receipt-directed import and unapproved-file guards. These checks establish the
exercised passive paths, not an untrusted-code sandbox.

The existing revised-analysis command, wheel resources and CI environment matrix
remain in use. No new performance study, native backend, GPU operation or
long-session framework is part of this consumer.
