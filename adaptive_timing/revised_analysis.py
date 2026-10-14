"""A finite integer analysis consumer with separate planning and completion."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import threading
import time
from typing import Callable

from .causal import CausalScheduler, Information, build_causal_plan
from .execution import ExecutionResult, OperationCancelled, RealExecutor
from .model import Event, Policy
from .runtime import Dispatch
from .worker import LatestWorker


MAX_VALUES = 65_536
MAX_REVISIONS = 64
CHUNK_VALUES = 1_024


@dataclass(frozen=True)
class Summary:
    count: int
    total: int
    histogram: tuple[int, ...]
    digest: str


def _values(values: tuple[int, ...]) -> None:
    if not isinstance(values, tuple):
        raise TypeError("values must be an immutable tuple")
    if len(values) > MAX_VALUES:
        raise ValueError(f"values must contain at most {MAX_VALUES} integers")
    if any(type(value) is not int or not 0 <= value <= 255 for value in values):
        raise ValueError("values must be integers from 0 through 255, excluding booleans")


def _seconds(value: float, name: str, *, positive: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")


def analyze(values: tuple[int, ...], cancellation: threading.Event,
            progress: Callable[[int], None]) -> Summary:
    """Count bounded byte-valued integers, hashing exactly bytes(values)."""
    _values(values)
    histogram = [0] * 256
    total = 0
    digest = hashlib.sha256()
    for start in range(0, len(values), CHUNK_VALUES):
        if cancellation.is_set():
            raise OperationCancelled()
        chunk = values[start:start + CHUNK_VALUES]
        for value in chunk:
            histogram[value] += 1
            total += value
        digest.update(bytes(chunk))
        progress(start + len(chunk))
    if cancellation.is_set():
        raise OperationCancelled()
    return Summary(len(values), total, tuple(histogram), digest.hexdigest())


def _summary(value: Summary) -> dict:
    return {"count": value.count, "total": value.total,
            "histogram": list(value.histogram), "digest": value.digest}


def _source_identity() -> dict:
    directory = Path(__file__).resolve().parent
    try:
        installed_version = version("adaptive-timing-engine")
    except PackageNotFoundError:
        installed_version = None
    sources = {}
    for name in ("__init__.py", "model.py", "causal.py", "runtime.py",
                 "worker.py", "execution.py", "revised_analysis.py"):
        path = directory / name
        try:
            sources[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        except OSError as error:
            sources[name] = {"unavailable": type(error).__name__}
    return {"distribution_version": installed_version,
            "scope": "On-disk module files read when this report is captured, not loaded-bytecode attestation. External run records bind execution to source.",
            "captured_monotonic": time.monotonic(),
            "package_directory": str(directory), "module_files": sources}


def _clock_identity(clock) -> dict:
    if clock is time.perf_counter:
        name = "perf_counter"
    elif clock is time.monotonic:
        name = "monotonic"
    else:
        return {"name": "injected clock", "implementation": None,
                "resolution": None, "monotonic": None, "adjustable": None}
    info = time.get_clock_info(name)
    return {"name": f"time.{name}", "implementation": info.implementation,
            "resolution": info.resolution, "monotonic": info.monotonic,
            "adjustable": info.adjustable}


class AnalysisSession:
    """One finite logical analysis, a planner mailbox and one callable worker.

    Inputs are immutable byte-valued integer tuples. At most one refused
    dispatch remains outside the physical executor. New revisions supersede
    unsubmitted work, while accepted callables retain physical ownership.
    The TTL limits physical submission. An already accepted queued operation
    may start after that deadline and still complete normally.
    Injected analyzers are trusted synchronous operations responsible for
    returning after cleanup. No forced termination or shutdown bound is offered.
    """

    def __init__(self, *, analyzer=analyze, capacity: int = 1,
                 max_revisions: int = MAX_REVISIONS, pending_seconds: float = 5.0,
                 clock=time.perf_counter):
        if type(capacity) is not int or capacity not in (1, 2):
            raise ValueError("capacity must be 1 or 2")
        if type(max_revisions) is not int or not 1 <= max_revisions <= MAX_REVISIONS:
            raise ValueError(f"max_revisions must be from 1 through {MAX_REVISIONS}")
        _seconds(pending_seconds, "pending_seconds", positive=True)
        if not callable(analyzer) or not callable(clock):
            raise TypeError("analyzer and clock must be callable")
        origin = clock()
        _seconds(origin, "clock origin")
        self._owner = threading.get_ident()
        self._clock = clock
        self._origin = origin
        self._physical_origin = time.monotonic()
        self._last_now = 0.0
        self._delivery_at = -1.0
        self._capacity = capacity
        self._max_revisions = max_revisions
        self._pending_seconds = pending_seconds
        self._analyzer = analyzer
        self._scheduler = CausalScheduler(Policy(use_noise=False, resource_gap=0.0))
        self._revision = 0
        self._mailbox_generation = 0
        self._planning_revision: int | None = None
        self._retire_ids: list[str] = []
        self._pending: tuple[Dispatch, int] | None = None
        self._inputs: dict[int, tuple[int, ...]] = {}
        self._rows: dict[int, dict] = {}
        self._progress: dict[int, int] = {}
        self._progress_lock = threading.Lock()
        self._results: list[ExecutionResult] = []
        self._observations: list[dict] = []
        self._current: Summary | None = None
        self._closed = False
        self._planner_stopped = False
        self._stop_reason: str | None = None
        self._execution = RealExecutor(max_workers=1, capacity=capacity)
        try:
            self._planner = LatestWorker(build_causal_plan)
        except BaseException:
            self._execution.close()
            raise

    def _assert_owner(self, *, open_required: bool = True) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("analysis session must be used on its owner thread")
        if open_required and self._closed:
            raise RuntimeError("analysis session is closed")

    def _now(self) -> float:
        observed = self._clock()
        _seconds(observed, "clock")
        elapsed = observed - self._origin
        if elapsed < self._last_now:
            raise ValueError("session clock moved backwards")
        return elapsed

    @property
    def revision(self) -> int:
        self._assert_owner(open_required=False)
        return self._revision

    @property
    def pending_revision(self) -> int | None:
        self._assert_owner(open_required=False)
        return None if self._pending is None else self._pending[1]

    @property
    def current(self) -> Summary | None:
        self._assert_owner(open_required=False)
        return self._current

    def offer(self, values: tuple[int, ...]) -> int:
        self._assert_owner()
        _values(values)
        if self._revision >= self._max_revisions:
            raise ValueError("finite session revision limit reached")
        now = self._now()
        if now <= max(self._delivery_at, self._last_now if self._rows else -1.0):
            raise ValueError("clock must advance before another information delivery")
        deadline = now + self._pending_seconds
        if not math.isfinite(deadline) or deadline <= now:
            raise ValueError("pending_seconds is not resolvable on the session clock")
        revision = self._revision + 1
        ident = f"analysis-{revision:04d}"
        event = Event(ident, now, now, deadline, 0.0, "analysis-cpu")
        canceled_ids = list(self._retire_ids)
        old_planning = self._planning_revision
        if old_planning is not None:
            old = self._rows[old_planning]
            canceled_ids.append(old["job_id"])
        facts = [Information(now, sequence, now, "cancel", old_id, 2)
                 for sequence, old_id in enumerate(canceled_ids)]
        facts.append(Information(now, len(facts), now, "announce", ident, 1, event))
        self._scheduler.receive(tuple(facts), now=now)
        self._retire_ids.clear()
        for old_revision in (old_planning, self.pending_revision):
            if old_revision is not None:
                self._dispose(old_revision, "superseded_before_admission", now)
        for row in self._rows.values():
            if row["disposition"] == "admitted":
                row["superseded"] = True
        self._pending = None
        self._revision = revision
        self._current = None
        self._inputs[revision] = values
        with self._progress_lock:
            self._progress[revision] = 0
        self._rows[revision] = {
            "revision": revision, "job_id": ident, "input_count": len(values),
            "input_digest": hashlib.sha256(bytes(values)).hexdigest(),
            "offered_at": now, "deadline": deadline, "disposition": "planning",
            "refusals": 0, "superseded": False, "cancel_requested": False,
            "adopted_at": None,
        }
        self._last_now = self._delivery_at = now
        request = self._scheduler.snapshot(now)
        self._planning_revision = revision
        self._mailbox_generation = self._planner.request(request)
        self._rows[revision]["causal_generation"] = request.generation
        self._rows[revision]["mailbox_generation"] = self._mailbox_generation
        return revision

    def _dispose(self, revision: int, disposition: str, now: float) -> None:
        self._rows[revision]["disposition"] = disposition
        self._rows[revision]["disposed_at"] = now
        self._inputs.pop(revision, None)
        if self._planning_revision == revision:
            self._planning_revision = None
        if self._pending is not None and self._pending[1] == revision:
            self._pending = None

    def _take_plan(self, now: float) -> None:
        outcome = self._planner.take()
        if outcome is None or outcome.generation != self._mailbox_generation:
            return
        revision = self._planning_revision
        if revision is None:
            return
        row = self._rows[revision]
        if outcome.error is not None:
            row["error"] = outcome.error
            self._retire_ids.append(row["job_id"])
            self._dispose(revision, "planning_failed", now)
            return
        if not self._scheduler.install(outcome.value):
            row["error"] = "planning lease was no longer current"
            self._retire_ids.append(row["job_id"])
            self._dispose(revision, "planning_failed", now)
            return
        row["planned_at"] = now
        if outcome.value.plan.rejected:
            self._dispose(revision, "expired_before_admission", now)

    def _dispatch(self, now: float) -> None:
        for dispatch in self._scheduler.poll(now, budget=1):
            revision = self._planning_revision
            if revision is None or self._rows[revision]["job_id"] != dispatch.id:
                raise RuntimeError("unexpected causal dispatch identity")
            self._planning_revision = None
            self._rows[revision]["dispatch"] = asdict(dispatch)
            self._rows[revision]["dispatched_at"] = now
            if dispatch.status != "dispatched" or now >= self._rows[revision]["deadline"]:
                self._dispose(revision, "expired_before_admission", now)
            else:
                self._pending = (dispatch, revision)
                self._rows[revision]["disposition"] = "pending_admission"

    def _attempt_pending(self, now: float, *, retired: bool = False) -> None:
        if self._pending is None:
            return
        dispatch, revision = self._pending
        row = self._rows[revision]
        if now >= row["deadline"]:
            self._dispose(revision, "expired_before_admission", now)
            return
        if row["refusals"] and not retired:
            return
        values = self._inputs[revision]

        def progress(processed: int) -> None:
            with self._progress_lock:
                previous = self._progress[revision]
                if type(processed) is not int or not previous <= processed <= len(values):
                    raise ValueError("progress must be a cumulative count within the input")
                self._progress[revision] = processed

        def operation(cancellation):
            return self._analyzer(values, cancellation, progress)

        try:
            self._execution.submit(dispatch, revision, operation)
        except RuntimeError as error:
            if str(error) != "execution capacity is full, reconcile before submitting":
                raise
            row["refusals"] += 1
            row["last_refused_at"] = now
            return
        row["disposition"] = "admitted"
        row["admitted_at"] = now
        self._pending = None
        del self._inputs[revision]

    def _consume(self, results: tuple[ExecutionResult, ...], now: float) -> None:
        for result in results:
            row = self._rows[result.generation]
            row["disposition"] = result.status
            row["physical_stale"] = result.stale
            self._results.append(result)
            if result.usable and result.generation == self._revision:
                if isinstance(result.value, Summary):
                    self._current = result.value
                    row["adopted_at"] = now
                else:
                    row["adoption_error"] = "numeric analysis requires a Summary result"

    def step(self, timeout: float = 0.0) -> tuple[ExecutionResult, ...]:
        self._assert_owner()
        _seconds(timeout, "timeout")
        now = self._now()
        self._take_plan(now)
        self._dispatch(now)
        self._attempt_pending(now)
        self._last_now = now
        if self._execution.outstanding:
            results = self._execution.wait(max(1, self._revision), timeout=timeout)
        else:
            if timeout:
                # This is an owner polling interval, never completion evidence.
                time.sleep(min(timeout, 0.01))
            results = ()
        now = self._now()
        self._consume(results, now)
        self._take_plan(now)
        self._dispatch(now)
        self._attempt_pending(now, retired=bool(results))
        self._last_now = now
        self._observations.extend(asdict(row) for row in self._execution.observations())
        return results

    def cancel(self, revision: int) -> bool:
        self._assert_owner()
        if type(revision) is not int or revision not in self._rows:
            raise KeyError(revision)
        row = self._rows[revision]
        if row["disposition"] == "planning":
            now = self._now()
            if now <= max(self._delivery_at, self._last_now):
                raise ValueError("clock must advance before a cancellation delivery")
            fact = Information(now, 0, now, "cancel", row["job_id"], 2)
            self._scheduler.receive((fact,), now=now)
            self._last_now = self._delivery_at = now
            self._dispose(revision, "canceled_before_admission", now)
            confirmed = True
        elif row["disposition"] == "pending_admission":
            self._dispose(revision, "canceled_before_admission", self._now())
            confirmed = True
        elif row["disposition"] == "admitted":
            confirmed = self._execution.cancel(row["job_id"])
        else:
            confirmed = True
        row["cancel_requested"] = True
        if revision == self._revision:
            self._current = None
        return confirmed

    def _active(self) -> bool:
        return (self._planning_revision is not None or self._pending is not None
                or bool(self._execution.outstanding))

    def finish(self, timeout: float = 5.0) -> dict:
        """Use a soft observation budget, then wait for trusted-operation cleanup."""
        self._assert_owner()
        _seconds(timeout, "timeout")
        stop_at = time.monotonic() + timeout
        while self._active():
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                self._stop_reason = "timeout"
                break
            self.step(min(remaining, 0.05))
        self.close()
        return self.report()

    def close(self) -> None:
        self._assert_owner(open_required=False)
        if self._closed:
            return
        self._closed = True
        now = self._last_now
        for revision in tuple(self._inputs):
            self._rows[revision]["cancel_requested"] = True
            self._dispose(revision, "canceled_before_admission", now)
        self._pending = None
        self._planning_revision = None
        self._retire_ids.clear()
        try:
            self._planner_stopped = self._planner.close(timeout=None)
        finally:
            for row in self._rows.values():
                if row["disposition"] == "admitted":
                    row["cancel_requested"] = True
            self._execution.close()
            results = self._execution.reconcile(max(1, self._revision))
            self._consume(results, now)
            self._observations.extend(asdict(row) for row in self._execution.observations())
        if self._stop_reason is None and self._revision and self._current is None:
            self._stop_reason = "no_current_result"

    def report(self) -> dict:
        self._assert_owner(open_required=False)
        with self._progress_lock:
            progress = dict(self._progress)
        rows = [dict(row, processed=progress[revision],
                     current=revision == self._revision and self._current is not None)
                for revision, row in self._rows.items()]
        for row in rows:
            if "dispatch" in row:
                row["dispatch"] = dict(row["dispatch"])
        results = []
        for result in self._results:
            item = asdict(result)
            if isinstance(result.value, Summary):
                item["value"] = _summary(result.value)
            item["usable"] = result.usable
            item["queue_seconds"] = (None if result.started_at is None
                                     else result.started_at - result.submitted_at)
            item["callable_seconds"] = (None if result.started_at is None
                                        else result.completed_at - result.started_at)
            item["reconciliation_seconds"] = result.reconciled_at - result.completed_at
            results.append(item)
        return {
            "schema_version": 1, "mode": "bounded real integer analysis",
            "source_identity": _source_identity(),
            "qualification": "Trusted synchronous CPU work, not a real-time deadline guarantee",
            "clock": {"session_origin": self._origin, "physical_origin": self._physical_origin,
                      "session_clock": _clock_identity(self._clock),
                      "physical_clock": _clock_identity(time.monotonic),
                      "session_units": "seconds elapsed on injected monotonic clock",
                      "physical_units": "process time.monotonic seconds, separate clock domain",
                      "deadline": "Physical submission only, expires at or after deadline. Accepted queued work may start later and complete normally.",
                      "duration_estimate": 0.0, "resource_gap": 0.0},
            "bounds": {"max_values": MAX_VALUES, "max_revisions": self._max_revisions,
                       "physical_workers": 1, "physical_capacity": self._capacity,
                       "unsubmitted_dispatches": 1, "pending_seconds": self._pending_seconds},
            "revision": self._revision, "current_revision": self._revision,
            "current": None if self._current is None else _summary(self._current),
            "closed": self._closed, "planner_stopped": self._planner_stopped,
            "outstanding": self._execution.outstanding,
            "reserved_resources": list(self._execution.reserved_resources),
            "pending_revision": self.pending_revision,
            "complete": self._closed and self._planner_stopped and not self._active()
                        and self._stop_reason is None,
            "stop_reason": self._stop_reason,
            "timeout_policy": "finish(timeout) is a soft owner observation budget. Retirement then waits for trusted synchronous work and cleanup, without a total shutdown deadline.",
            "metrics": {
                "offered": len(rows), "admitted": sum("admitted_at" in row for row in rows),
                "refused": sum(row["refusals"] for row in rows),
                "replaced": sum(row["disposition"] == "superseded_before_admission" for row in rows),
                "physical_started": sum(result.started_at is not None for result in self._results),
                "physical_completed": sum(result.status == "completed" for result in self._results),
                "physical_values": sum(progress.values()),
                "usable": sum(result.usable for result in self._results),
                "stale": sum(result.stale for result in self._results),
            },
            "metric_definitions": {
                "physical_values": "Analysis loop visits, including partial canceled work. Excludes validation, input digests and receipt overhead.",
                "usable": "Executor-usable completions at reconciliation. Numeric adoption additionally requires a Summary and a current revision.",
                "stale": "Physical outcomes reconciled under a newer data revision, including cancellations and failures.",
            },
            "revisions": rows, "results": results,
            "observations": [dict(row) for row in self._observations],
            "planner": asdict(self._planner.stats()),
        }

    def __enter__(self):
        self._assert_owner()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def demonstration() -> dict:
    """Force one capacity refusal with gates around actual completed chunks."""
    first_chunk = threading.Event()
    release = threading.Event()
    first_values = tuple(range(256)) * 32

    def gated(values, cancellation, progress):
        def observed(processed):
            progress(processed)
            if values is first_values and processed == CHUNK_VALUES:
                first_chunk.set()
                if not release.wait(5.0):
                    raise TimeoutError("demonstration gate was not released")
        return analyze(values, cancellation, observed)

    session = AnalysisSession(analyzer=gated)
    try:
        session.offer(first_values)
        until = time.monotonic() + 5.0
        while not first_chunk.is_set():
            if time.monotonic() >= until:
                raise TimeoutError("first actual chunk did not finish")
            session.step(0.01)
        session.offer((2, 2, 3) * 256)
        while session.pending_revision != 2:
            if time.monotonic() >= until:
                raise TimeoutError("second revision did not reach physical admission")
            session.step(0.01)
        current_values = (7, 8, 8, 255) * 512
        session.offer(current_values)
        release.set()
        receipt = session.finish(5.0)
        expected = Counter(current_values)
        current = receipt["current"]
        if (not receipt["complete"] or current is None
                or current["count"] != len(current_values)
                or current["total"] != sum(current_values)
                or current["histogram"] != [expected[value] for value in range(256)]
                or current["digest"] != hashlib.sha256(bytes(current_values)).hexdigest()
                or receipt["metrics"]["refused"] < 1
                or receipt["revisions"][1]["disposition"] != "superseded_before_admission"
                or not any(row["generation"] == 1 and row["status"] == "completed"
                           and row["stale"] for row in receipt["results"])):
            raise AssertionError("revised analysis demonstration did not meet its contract")
        receipt["controlled_schedule"] = "First real chunk held while revisions 2 and 3 arrive, not performance evidence"
        receipt["oracle"] = "Independent Counter, built-in sum and raw-byte SHA256"
        return receipt
    except Exception as error:
        release.set()
        session.close()
        receipt = session.report()
        receipt["complete"] = False
        receipt["stop_reason"] = receipt["stop_reason"] or "demonstration_error"
        receipt["demonstration_error"] = f"{type(error).__name__}: {error}"
        return receipt
    finally:
        release.set()
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("output already exists, choose a new receipt path")
    receipt = demonstration()
    text = json.dumps(receipt, indent=2) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(text)
        print(f"Saved real revised-work observations to {args.output}")
    if not receipt["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
