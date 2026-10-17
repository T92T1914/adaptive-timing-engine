# Run caller-provided revisions

The installed module command uses the existing session and default CPU analyzer:

```sh
python -m adaptive_timing.revision_file revisions.json --output receipt.json
```

An input file has this shape:

```json
{"schema_version": 1, "revisions": [
  {"not_before": 0, "values": [1, 2, 255]},
  {"not_before": 0.01, "values": []}
]}
```

The whole local regular UTF-8 file is admitted before workers start. It is at
most 4 MiB, contains 1 through 64 revisions, and permits only the fields above.
Each input contains at most 65,536 integers from 0 through 255. Empty input is
valid. Offsets strictly increase within zero through ten seconds. Duplicate
keys, nonfinite numbers, booleans as numbers and excessive nesting are refused.
There is no callback, path-loading, stdin, watch or network input mechanism.

The callable APIs are `run_manifest(data: bytes, timeout=15.0)` and
`run_file(path, timeout=15.0)`. Invalid admission raises `AdmissionError` without
creating a session. The timeout must be positive and at most thirty seconds.
It is a soft observation budget followed by waiting cleanup, not a hard shutdown
or execution deadline. The runner mutates the session only on its owner thread.

Requested offsets use runner-relative `perf_counter` seconds. Actual deliveries
are copied from the session's own offered-at values. Their separate origins
are retained explicitly. Offsets promise neither exact delivery nor physical
start or completion time. Every accepted revision remains recorded in order.

The output wraps the unchanged schema-1 session receipt under `session`, plus
the input-byte digest, requested/actual deliveries and runner outcome. It omits
the input arrays. Inspect supported saved session claims with:

```sh
python -m adaptive_timing.receipt_check receipt.json --record session --json
```

CLI exits are zero for completed work, one for incomplete work and two for
admission or output-delivery refusal/failure. Read the wrapper outcome and the
session receipt separately. An interrupted or timed-out runner may preserve a
cleanly retired session without having delivered every requested revision.

Receipts go to the requested file or stdout. Bounded admission/delivery
diagnostics use stderr. If that channel also fails, exit two remains the only
available indication. Stdout is explicitly flushed before success is returned.
The standalone module retires failed standard streams before interpreter
shutdown so another failed flush cannot replace the delivery status. Calling
`main` from Python returns a status, including for help and argument errors,
without closing the embedding caller's standard streams. Help and argument
diagnostics use the same guarded write/flush boundary. Normal help returns zero;
an unavailable or failed help-output channel returns two. These are bounded
Python stream checks, not a guarantee about every shell or operating system.

Output is reserved exclusively before work. Existing files, hard links and
symlinks are refused. A write/close failure may leave a partial new file; no
crash-durability claim is made. The old demonstration and commands are unchanged.
