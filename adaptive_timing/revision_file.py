"""Run a finite, admitted JSON revision file with the default CPU analyzer."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time

from .revised_analysis import AnalysisSession, MAX_REVISIONS, MAX_VALUES

MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 8


class AdmissionError(ValueError):
    """The entire input must be admitted before creating a session."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AdmissionError("duplicate JSON object key")
        result[key] = value
    return result


def _number(token, kind):
    if len(token) > 64:
        raise AdmissionError("numeric token exceeds 64 characters")
    value = kind(token)
    if not math.isfinite(value):
        raise AdmissionError("nonfinite number")
    return value


def _constant(token):
    raise AdmissionError("nonfinite JSON constant")


def admit_bytes(data: bytes) -> tuple[tuple[float, tuple[int, ...]], ...]:
    """Admit schema 1 with only schema_version and revision data fields."""
    if type(data) is not bytes or len(data) > MAX_BYTES:
        raise AdmissionError("input must be bytes of at most 4 MiB")
    try:
        text = data.decode("utf-8", errors="strict")
        depth, quoted, escaped = 0, False, False
        for character in text:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in "[{":
                depth += 1
                if depth > MAX_DEPTH:
                    raise AdmissionError("JSON nesting exceeds eight containers")
            elif character in "]}":
                depth -= 1
        document = json.loads(text, object_pairs_hook=_pairs,
                              parse_int=lambda token: _number(token, int),
                              parse_float=lambda token: _number(token, float),
                              parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, AdmissionError):
            raise
        raise AdmissionError("invalid UTF-8 JSON") from error
    if type(document) is not dict or set(document) != {"schema_version", "revisions"}:
        raise AdmissionError("root requires only schema_version and revisions")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise AdmissionError("unsupported manifest schema_version")
    rows = document["revisions"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_REVISIONS:
        raise AdmissionError("revisions must contain 1 through 64 records")
    admitted = []
    previous = -1.0
    for index, row in enumerate(rows, 1):
        if type(row) is not dict or set(row) != {"not_before", "values"}:
            raise AdmissionError(f"revision {index} requires only not_before and values")
        offset, values = row["not_before"], row["values"]
        if (type(offset) not in (int, float) or not math.isfinite(offset)
                or not previous < offset <= 10 or offset < 0):
            raise AdmissionError(f"revision {index} offset must increase within 0 through 10 seconds")
        if (type(values) is not list or len(values) > MAX_VALUES
                or any(type(value) is not int or not 0 <= value <= 255 for value in values)):
            raise AdmissionError(f"revision {index} needs at most 65536 byte-valued integers")
        admitted.append((float(offset), tuple(values)))
        previous = offset
    return tuple(admitted)


def read_manifest(path: str | Path) -> bytes:
    """Bound a local regular file before decoding or creating any workers."""
    path = os.fspath(path)
    if path.startswith(("\\\\", "//")):
        raise AdmissionError("input must be a local regular file")
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise AdmissionError("input must be a local regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise AdmissionError("input must be a local regular file")
            data = source.read(MAX_BYTES + 1)
    except OSError as error:
        raise AdmissionError("cannot read local manifest") from error
    if len(data) > MAX_BYTES:
        raise AdmissionError("input exceeds 4 MiB")
    return data


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 30:
        raise AdmissionError("timeout must be finite, positive and at most 30 seconds")


def run_manifest(data: bytes, *, timeout: float = 15.0) -> dict:
    """Run admitted revisions serially on the owner thread, then retire work.

    The timeout is a soft observation budget followed by synchronous cleanup.
    Requested offsets do not promise delivery, start or completion deadlines.
    """
    revisions = admit_bytes(data)
    _timeout(timeout)
    origin = time.perf_counter()
    stop_at = time.monotonic() + timeout
    deliveries, error, session = [], None, None
    try:
        session = AnalysisSession()
        previous = 0.0
        for requested, values in revisions:
            while True:
                remaining = stop_at - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("runner observation budget expired")
                elapsed = time.perf_counter() - origin
                if not math.isfinite(elapsed) or elapsed < previous:
                    raise ValueError("runner clock is invalid or moved backwards")
                previous = elapsed
                if elapsed >= requested:
                    break
                session.step(min(0.01, requested - elapsed, remaining))
            revision = session.offer(values)
            offered = session.report()["revisions"][-1]["offered_at"]
            deliveries.append({"revision": revision, "requested_not_before": requested,
                               "actual_delivery_session_elapsed": offered})
        remaining = stop_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("runner observation budget expired")
        session.finish(remaining)
    except (Exception, KeyboardInterrupt) as caught:
        error = {"type": type(caught).__name__, "message": str(caught)[:256]}
    finally:
        if session is not None:
            session.close()
    receipt = None if session is None else session.report()
    return {"schema_version": 1, "mode": "finite revision-file runner",
            "input_sha256": hashlib.sha256(data).hexdigest(),
            "requested_clock": {"name": "time.perf_counter", "origin": origin,
                                "scope": "runner-relative seconds, separate from session elapsed"},
            "delivery_clock": "session elapsed, origin retained in session.clock",
            "deliveries": deliveries, "runner_error": error,
            "complete": bool(error is None and len(deliveries) == len(revisions)
                             and receipt is not None and receipt["complete"]),
            "session": receipt}


def run_file(path: str | Path, *, timeout: float = 15.0) -> dict:
    return run_manifest(read_manifest(path), timeout=timeout)


def _write_message(text: str, stream, name: str, failed_streams: set[str]) -> None:
    """Track immediate and buffered failures without recursive diagnostics."""
    if stream is None:
        failed_streams.add(name)
        return
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError):
        failed_streams.add(name)


def _diagnostic(payload: dict, failed_streams: set[str]) -> None:
    _write_message(json.dumps(payload) + "\n", sys.stderr, "stderr", failed_streams)


class _ArgumentParser(argparse.ArgumentParser):
    """Keep argparse exits inside the same standard-stream ownership boundary."""

    def __init__(self, failed_streams: set[str]):
        super().__init__(description=__doc__)
        self.failed_streams = failed_streams

    def _print_message(self, message, file=None):
        if message:
            stream = sys.stderr if file is None else file
            name = "stdout" if stream is sys.stdout and stream is not None else "stderr"
            _write_message(message, stream, name, self.failed_streams)

    def print_help(self, file=None):
        stream = sys.stdout if file is None else file
        _write_message(self.format_help(), stream, "stdout", self.failed_streams)

    def error(self, message):
        _write_message(self.format_usage(), sys.stderr, "stderr", self.failed_streams)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _main(argv=None) -> tuple[int, set[str]]:
    failed_streams: set[str] = set()
    parser = _ArgumentParser(failed_streams)
    parser.add_argument("file", help="Local schema-1 JSON revision manifest")
    parser.add_argument("--output", help="New JSON receipt path, created exclusively before work")
    parser.add_argument("--timeout", type=float, default=15.0)
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return (2 if failed_streams else int(error.code or 0)), failed_streams
    output = None
    try:
        data = read_manifest(args.file)
        admit_bytes(data)
        _timeout(args.timeout)
        if args.output is not None:
            output = open(args.output, "x", encoding="utf-8")
    except (AdmissionError, OSError) as error:
        _diagnostic({"admission": "refused", "message": str(error)[:256]}, failed_streams)
        return 2, failed_streams
    status = 1
    try:
        receipt = run_manifest(data, timeout=args.timeout)
        text = json.dumps(receipt, allow_nan=False, indent=2) + "\n"
        if output is not None:
            output.write(text)
        else:
            if sys.stdout is None:
                raise OSError("stdout is unavailable")
            sys.stdout.write(text)
            sys.stdout.flush()
        status = 0 if receipt["complete"] else 1
    except (OSError, ValueError) as error:
        if output is None:
            failed_streams.add("stdout")
        _diagnostic({"delivery": "failed", "message": str(error)[:256]}, failed_streams)
        status = 2
    finally:
        if output is not None:
            try:
                output.close()
            except (OSError, ValueError) as error:
                _diagnostic({"delivery": "failed", "message": str(error)[:256]}, failed_streams)
                status = 2
    return status, failed_streams


def main(argv=None) -> int:
    """Return a CLI status without closing an embedding caller's streams."""
    return _main(argv)[0]


def _entrypoint() -> None:
    status, failed_streams = _main()
    # Only the standalone process owns shutdown of its failed standard streams.
    # Otherwise Python's later flush could replace delivery status 2 with 120.
    for name in failed_streams:
        stream = getattr(sys, name)
        setattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    raise SystemExit(status)


if __name__ == "__main__":
    _entrypoint()
