"""Passive, bounded inspection of saved revised-analysis claims.

This module never imports the producer, follows recorded paths or starts work.
A consistent receipt is not authenticated execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys

from ._receipt_rules import check_record


MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_NUMBER_CHARS = 128
MAX_STRING_CHARS = 16 * 1024
MAX_VALUES = 100_000
MAX_ARRAY_ITEMS = 512
MAX_OBJECT_KEYS = 128
MAX_ISSUES = 32
SELECTORS = {"root": "$", "session": "$.session", "consumer": "$.consumer"}


class _AdmissionError(ValueError):
    def __init__(self, admission: str, code: str, message: str):
        self.admission = admission
        self.code = code
        super().__init__(message)


def _refuse(message: str) -> None:
    raise _AdmissionError("unsupported", "resource_limit", message)


def _preflight(text: str) -> None:
    """Bound nesting and numeric tokens before the JSON decoder allocates them."""
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index += 1
            units = 0
            while index < len(text):
                char = text[index]
                if char == '"':
                    index += 1
                    break
                if char == "\\":
                    # Grammar and escape validity belong to the JSON decoder.
                    index += 6 if text[index:index + 2] == "\\u" else 2
                else:
                    index += 1
                units += 1
                # A supplementary code point can have two escaped UTF-16 units.
                # The exact decoded length is checked after bounded parsing.
                if units > 2 * MAX_STRING_CHARS:
                    _refuse("A string exceeds the supported length budget.")
            continue
        if char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                _refuse("JSON nesting exceeds 32 containers.")
        elif char in "]}":
            depth -= 1
        elif char in "-0123456789":
            end = index + 1
            while end < len(text) and text[end] in "0123456789.eE+-":
                end += 1
                if end - index > MAX_NUMBER_CHARS:
                    _refuse("A numeric token exceeds 128 characters.")
            index = end
            continue
        index += 1


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    if len(pairs) > MAX_OBJECT_KEYS:
        _refuse("An object exceeds 128 members.")
    result = {}
    for key, value in pairs:
        if key in result:
            raise _AdmissionError("malformed", "duplicate_key",
                                  "A JSON object repeats a member name.")
        result[key] = value
    return result


def _constant(value: str):
    raise _AdmissionError("malformed", "nonfinite_number",
                          "NaN and Infinity are not supported JSON numbers.")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise _AdmissionError("malformed", "nonfinite_number",
                              "A JSON number is outside finite binary64 range.")
    return number


def _walk(value: object) -> None:
    pending = [value]
    visited = 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > MAX_VALUES:
            _refuse("The document exceeds 100000 parsed values and keys.")
        if type(item) is str:
            if len(item) > MAX_STRING_CHARS:
                _refuse("A decoded string exceeds 16384 characters.")
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise _AdmissionError("malformed", "invalid_unicode",
                                      "Unpaired Unicode surrogates are unsupported.")
        elif type(item) is list:
            if len(item) > MAX_ARRAY_ITEMS:
                _refuse("An array exceeds 512 items.")
            pending.extend(item)
        elif type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())


def _failure(admission: str, code: str, message: str, *,
             record: str, digest: str | None = None, path: str = "$") -> dict:
    return {
        "admission": admission, "consistency": "not_checked",
        "completion": "unavailable",
        "issues": [{"code": code, "path": path, "message": message}],
        "not_checked": ["Semantic checks require an admitted supported record."],
        "record": record, "receipt_sha256": digest,
        "execution_authenticated": False,
    }


def inspect_bytes(data: bytes, *, record: str = "root") -> dict:
    """Inspect original bytes without mutating them or executing their claims."""
    if type(data) is not bytes:
        return _failure("malformed", "input_type", "Input must be bytes.", record=record)
    if len(data) > MAX_BYTES:
        return _failure("unsupported", "resource_limit",
                        "The input exceeds the 4194304-byte limit.", record=record)
    digest = hashlib.sha256(data).hexdigest()
    if type(record) is not str or record not in SELECTORS:
        return _failure("unsupported", "record_selector",
                        "Select root, session or consumer explicitly.",
                        record="unsupported", digest=digest)
    try:
        text = data.decode("utf-8", errors="strict")
        _preflight(text)
        document = json.loads(text, object_pairs_hook=_pairs,
                              parse_float=_float, parse_constant=_constant)
        _walk(document)
    except UnicodeDecodeError:
        return _failure("malformed", "invalid_utf8",
                        "Input is not valid UTF-8.", record=record, digest=digest)
    except json.JSONDecodeError as error:
        return _failure("malformed", "json_syntax",
                        f"Invalid JSON at line {error.lineno}, column {error.colno}.",
                        record=record, digest=digest)
    except _AdmissionError as error:
        return _failure(error.admission, error.code, str(error),
                        record=record, digest=digest)
    except (ValueError, RecursionError, OverflowError):
        return _failure("malformed", "json_value",
                        "JSON values cannot be decoded under the supported contract.",
                        record=record, digest=digest)
    if record != "root":
        if type(document) is not dict or record not in document:
            return _failure("unsupported", "selected_record",
                            "The explicitly selected wrapper record is missing.",
                            record=record, digest=digest, path=SELECTORS[record])
        document = document[record]
    verdict = check_record(document, root=SELECTORS[record])
    verdict.update(record=record, receipt_sha256=digest, execution_authenticated=False)
    return verdict


def inspect_file(path: str | os.PathLike, *, record: str = "root") -> dict:
    """Read one explicit regular input file, never paths stored inside it."""
    descriptor = None
    try:
        input_path = Path(path)
        if str(input_path).startswith(("\\\\", "//")):
            return _failure("unsupported", "input_file",
                            "Use an explicit local regular file.", record=record)
        if not stat.S_ISREG(input_path.stat().st_mode):
            return _failure("unsupported", "input_file",
                            "The input must be a regular file.", record=record)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(input_path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return _failure("unsupported", "input_file",
                            "The opened input must be a regular file.", record=record)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_BYTES + 1)
        return inspect_bytes(data, record=record)
    except (OSError, ValueError, TypeError) as error:
        return _failure("unreadable", "input_read",
                        f"Cannot read the input file ({type(error).__name__}).",
                        record=record)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _safe_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)[1:-1]


def _readable(verdict: dict) -> str:
    lines = [
        "Admission: " + verdict["admission"],
        "Recorded consistency: " + verdict["consistency"],
        "Represented completion: " + verdict["completion"],
        "Execution authenticated: no",
    ]
    if verdict.get("receipt_sha256"):
        lines.append("Receipt SHA256: " + verdict["receipt_sha256"])
    for issue in verdict["issues"]:
        lines.append(f'{_safe_text(issue["path"])} [{_safe_text(issue["code"])}]: '
                     + _safe_text(issue["message"]))
    if not verdict["issues"]:
        lines.append("No contradiction found in the supported recorded claims.")
    for limit in verdict["not_checked"]:
        lines.append("Limit: " + _safe_text(limit))
    if verdict.get("delivery") == "refused":
        lines.append("Report delivery refused: " + _safe_text(verdict["delivery_issue"]["message"]))
    return "\n".join(lines) + "\n"


def exit_status(verdict: dict) -> int:
    """Zero means a consistent inspection, including valid incomplete work."""
    if verdict.get("delivery") == "refused" or verdict["admission"] != "accepted":
        return 2
    return 0 if verdict["consistency"] == "consistent" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect saved revised-analysis claims without executing work.")
    parser.add_argument("file", help="Explicit local UTF-8 JSON receipt file")
    parser.add_argument("--record", choices=tuple(SELECTORS), default="root",
                        help="Select an existing known wrapper explicitly")
    parser.add_argument("--json", action="store_true", help="Write a machine-readable verdict")
    parser.add_argument("--output", help="Also save the verdict as JSON to a new file")
    args = parser.parse_args(argv)
    verdict = inspect_file(args.file, record=args.record)
    if args.output is not None:
        verdict["delivery"] = "written"
        try:
            # Exclusive creation refuses existing files, hard links and symlinks
            # without truncating the input or another report.
            with open(args.output, "x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(verdict, ensure_ascii=True, indent=2) + "\n")
        except (OSError, ValueError) as error:
            verdict["delivery"] = "refused"
            verdict["delivery_issue"] = {
                "code": "output_write", "path": "$.output",
                "message": f"Choose a new writable report path ({type(error).__name__}).",
            }
    try:
        sys.stdout.write((json.dumps(verdict, ensure_ascii=True, indent=2) + "\n")
                         if args.json else _readable(verdict))
    except (OSError, UnicodeError):
        return 2
    return exit_status(verdict)


if __name__ == "__main__":
    raise SystemExit(main())
