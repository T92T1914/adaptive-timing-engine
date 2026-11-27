"""Qualify the caller manifest runner in a fresh environment outside the checkout."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


CHECKOUT = Path(__file__).resolve().parents[1]
# The literal input in docs/revision-file.md.
MANIFEST = {"schema_version": 1, "revisions": [
    {"not_before": 0, "values": [9]},
    {"not_before": 0.01, "values": [1, 2, 255, 2]},
]}
LOCATE = ("import adaptive_timing.revision_file as runner; "
          "print(runner.__file__)")


def run(command: list[str], cwd: Path, expected: int = 0) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=300)
    if done.returncode != expected:
        raise SystemExit(f"{command[1:]} returned {done.returncode}, expected {expected}\n"
                         f"{done.stdout}{done.stderr}")
    return done


def git(*arguments: str) -> str:
    return run(["git", *arguments], CHECKOUT).stdout.strip()


def require(condition: bool, claim: str) -> None:
    if not condition:
        raise SystemExit(f"Qualification failed: {claim}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new JSON record")
    output = parser.parse_args().output.resolve()
    require(not output.exists(), "the record path is unused")
    revision = git("rev-parse", "HEAD")
    require(git("status", "--porcelain", "--untracked-files=no") == "",
            "tracked files match the recorded revision")

    with tempfile.TemporaryDirectory(prefix="adaptive-revision-file-") as directory:
        outside = Path(directory).resolve()
        require(not outside.is_relative_to(CHECKOUT), "the consumer is outside the checkout")
        wheels = outside / "wheel"
        run([sys.executable, "-m", "pip", "wheel", str(CHECKOUT), "--no-deps",
             "--wheel-dir", str(wheels)], outside)
        (wheel,) = wheels.glob("*.whl")
        environment = outside / "venv"
        run([sys.executable, "-I", "-m", "venv", str(environment)], outside)
        python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        run([python, "-I", "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], outside)
        module = Path(run([python, "-I", "-c", LOCATE], outside).stdout.strip()).resolve()
        require(module.is_relative_to(environment), "the runner is imported from the new environment")

        (outside / "revisions.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        runner = [python, "-I", "-m", "adaptive_timing.revision_file", "revisions.json",
                  "--output", "receipt.json"]
        run(runner, outside)
        raw = (outside / "receipt.json").read_bytes()
        receipt = json.loads(raw)
        current = receipt["session"]["current"]
        require(receipt["complete"] is True, "the run is complete")
        require((current["count"], current["total"]) == (4, 260), "the final summary is current")
        require(receipt["session"]["closed"] is True
                and receipt["session"]["outstanding"] == 0, "the session retired")

        verdict = json.loads(run(
            [python, "-I", "-m", "adaptive_timing.receipt_check", "receipt.json",
             "--record", "session", "--json"], outside).stdout)
        require(verdict["consistency"] == "consistent" and verdict["completion"] == "complete",
                "the passive checker accepts the saved session")

        run(runner, outside, expected=2)
        require((outside / "receipt.json").read_bytes() == raw, "an existing receipt is kept")
        (outside / "refused.json").write_text('{"schema_version": 2, "revisions": []}',
                                              encoding="utf-8")
        run([python, "-I", "-m", "adaptive_timing.revision_file", "refused.json",
             "--output", "unused.json"], outside, expected=2)
        require(not (outside / "unused.json").exists(), "a refused input writes no receipt")

        record = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "revision": revision,
            "command": "python tools/check_revision_file_install.py --output <record>",
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "wheel": wheel.name,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "imported_from_new_environment": True,
            "manifest": MANIFEST,
            "exit_codes": {"complete_run": 0, "passive_check": 0, "existing_output": 2,
                           "refused_input": 2},
            "receipt": receipt,
            "passive_verdict": verdict,
        }

    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    require(str(Path.home()) not in text and str(Path.home()).replace("\\", "\\\\") not in text,
            "the record holds no home directory path")
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    print(f"Qualification passed at {revision} on {record['platform']} "
          f"with {record['implementation']} {record['python']}")


if __name__ == "__main__":
    main()
