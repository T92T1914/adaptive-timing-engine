"""Exercise the shipped passive checker in an external installed environment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


GUARDED = r"""
import builtins
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

inputs = [Path(name).resolve() for name in sys.argv[1:3]]
environment = Path(sys.prefix).resolve()
original = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
allowed = {os.path.normcase(str(path)) for path in inputs}
real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
real_import = builtins.__import__
attempts = []

def denied(*args, **kwargs):
    attempts.append("execution")
    raise AssertionError("Passive inspection attempted execution or network access")

def input_only(function):
    def checked(path, *args, **kwargs):
        if isinstance(path, int):
            return function(path, *args, **kwargs)
        candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
        if candidate not in allowed:
            attempts.append("file")
            raise AssertionError("Passive inspection followed an unapproved file")
        return function(path, *args, **kwargs)
    return checked

def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name in {"adaptive_timing.revised_analysis", "receipt_claimed_backend"}:
        attempts.append("import")
        raise AssertionError("Passive inspection imported a claimed producer")
    if name in {"revised_analysis", "execution", "cuda"} and level:
        attempts.append("import")
        raise AssertionError("Passive inspection imported an execution module")
    return real_import(name, globals, locals, fromlist, level)

threading.Thread.start = denied
ThreadPoolExecutor.submit = denied
subprocess.Popen = denied
os.system = denied
socket.socket = denied
socket.create_connection = denied
urllib.request.urlopen = denied
builtins.open = input_only(real_open)
io.open = input_only(real_io_open)
os.open = input_only(real_os_open)
builtins.__import__ = checked_import

spec = importlib.util.find_spec("adaptive_timing.receipt_check")
assert spec is not None and Path(spec.origin).resolve().is_relative_to(environment)
verdicts = []
for path, expected in zip(inputs, (0, 1)):
    sys.argv = ["adaptive_timing.receipt_check", str(path), "--json"]
    output = io.StringIO()
    with redirect_stdout(output):
        try:
            runpy.run_module("adaptive_timing.receipt_check", run_name="__main__")
        except SystemExit as error:
            status = error.code
        else:
            status = 0
    assert status == expected, (status, output.getvalue())
    verdict = json.loads(output.getvalue())
    assert verdict["admission"] == "accepted", verdict
    assert verdict["execution_authenticated"] is False
    assert verdict["receipt_sha256"] == original[str(path)]
    verdicts.append(verdict)
assert verdicts[0]["consistency"] == "consistent"
assert verdicts[0]["completion"] == "complete"
assert verdicts[1]["consistency"] == "contradictory"
assert any(issue["path"].endswith(".value.digest") for issue in verdicts[1]["issues"])
assert attempts == [], attempts
for path in inputs:
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original[str(path)]
print(json.dumps({"installed_passive_check": "passed", "input_unchanged": True,
                  "guard_attempts": attempts, "positive_exit": 0, "negative_exit": 1,
                  "checks": ["thread", "executor", "subprocess", "network",
                             "receipt-directed imports", "unapproved file reads"]},
                 sort_keys=True))
"""


def main() -> None:
    interpreter = Path(sys.argv[1]).resolve()
    source = Path(sys.argv[2]).resolve()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    positive = json.loads(source.read_bytes())
    with tempfile.TemporaryDirectory(prefix="adaptive-passive-") as directory:
        outside = Path(directory).resolve()
        assert not outside.is_relative_to(Path(__file__).resolve().parents[1])
        canary = outside / "do-not-read.txt"
        canary.write_text("This path is data, not an inspection input.\n", encoding="utf-8")
        positive["source_identity"]["package_directory"] = str(canary)
        positive["receipt_annotation"] = {
            "module": "receipt_claimed_backend",
            "url": "https://invalid.example/do-not-fetch",
        }
        first = outside / "positive.json"
        first.write_text(json.dumps(positive), encoding="utf-8")
        negative = json.loads(first.read_bytes())
        value = next(row["value"] for row in negative["results"]
                     if isinstance(row["value"], dict) and "digest" in row["value"])
        value["digest"] = "0" * 64 if value["digest"] != "0" * 64 else "1" * 64
        second = outside / "negative.json"
        second.write_text(json.dumps(negative), encoding="utf-8")
        subprocess.run([str(interpreter), "-I", "-c", GUARDED, str(first), str(second)],
                       cwd=outside, check=True, timeout=30)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


if __name__ == "__main__":
    main()
