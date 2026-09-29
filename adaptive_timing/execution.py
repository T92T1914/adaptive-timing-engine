"""Optional callable execution with physical completion and owner reconciliation."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable

from .runtime import Dispatch


def _positive_integer(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _error_text(exc: BaseException) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except BaseException:
        return f"{type(exc).__name__}: diagnostic unavailable"


class OperationCancelled(Exception):
    """A callable confirms it has stopped after receiving cancellation."""


@dataclass(frozen=True)
class ExecutionObservation:
    id: str
    generation: int
    resource: str
    kind: str
    at: float
    stale: bool | None = None


@dataclass(frozen=True)
class ExecutionResult:
    dispatch: Dispatch
    generation: int
    status: str
    submitted_at: float
    started_at: float | None
    completed_at: float
    reconciled_at: float
    cancellation_requested: bool
    stale: bool
    value: Any = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        """Whether this completion can be adopted in the reconciliation generation."""
        return self.status == "completed" and not self.stale and not self.cancellation_requested


@dataclass(frozen=True)
class _Completion:
    status: str
    started_at: float | None
    completed_at: float
    value: Any = None
    error: str | None = None


@dataclass
class _Job:
    dispatch: Dispatch
    generation: int
    operation: Callable[[threading.Event], Any]
    cancellation: threading.Event
    submitted_at: float
    future: Future | None = None
    completion: _Completion | None = None
    admission: threading.Event = field(default_factory=threading.Event)
    admitted: bool = False


class RealExecutor:
    """Bounded, optional execution of synchronous callables.

    Submit, cancel and reconcile on the creating thread. Each resource runs at
    most one callable at a time. A callable must retain its inputs and finish
    every external operation before returning or raising, including on errors.
    A GPU launch alone is not completion. Reconciliation releases reservations
    only after the Future confirms that the callable has returned.

    Generations gate result adoption, not physical execution. Cancellation is
    cooperative once a callable starts. No thread is killed or detached.
    """

    def __init__(self, *, max_workers: int = 1, capacity: int = 64):
        _positive_integer(max_workers, "max_workers")
        _positive_integer(capacity, "capacity")
        self._owner = threading.get_ident()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="timing-execution")
        self._max_workers = max_workers
        self._capacity = capacity
        self._jobs: dict[str, _Job] = {}
        self._issued: set[str] = set()
        self._pending: list[str] = []
        self._active: dict[str, str] = {}
        self._observations: list[ExecutionObservation] = []
        self._observation_lock = threading.Lock()
        self._closed = False

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("real executor must be used on its owner thread")

    def _record(self, job: _Job, kind: str, *, stale: bool | None = None) -> float:
        with self._observation_lock:
            at = time.monotonic()
            self._observations.append(ExecutionObservation(
                job.dispatch.id, job.generation, job.dispatch.resource, kind, at, stale))
            return at

    @property
    def outstanding(self) -> int:
        self._assert_owner()
        return len(self._jobs)

    @property
    def reserved_resources(self) -> tuple[str, ...]:
        self._assert_owner()
        return tuple(sorted(self._active))

    def observations(self) -> tuple[ExecutionObservation, ...]:
        """Drain observed transitions. This does not reconcile results or run work."""
        self._assert_owner()
        with self._observation_lock:
            rows = tuple(self._observations)
            self._observations.clear()
            return rows

    def submit(self, dispatch: Dispatch, generation: int,
               operation: Callable[[threading.Event], Any]) -> None:
        """Accept a virtual dispatch and an owned callable, without sleeping to its time.

        The caller decides when to submit. Dispatch times remain virtual planner
        metadata. Observation times use a separate monotonic wall clock.
        Capacity includes completed jobs that the owner has not reconciled.
        """
        self._assert_owner()
        if self._closed:
            raise RuntimeError("real executor is closed")
        if not isinstance(dispatch, Dispatch) or dispatch.status != "dispatched":
            raise ValueError("submit requires a dispatched record")
        if not all(isinstance(value, str) and value for value in (dispatch.id, dispatch.resource)):
            raise ValueError("dispatch ID and resource must be named")
        _positive_integer(generation, "generation")
        if not callable(operation):
            raise TypeError("operation must be callable")
        if dispatch.id in self._issued:
            raise ValueError("an event ID cannot be submitted twice in this session")
        if len(self._jobs) >= self._capacity:
            raise RuntimeError("execution capacity is full, reconcile before submitting")
        job = _Job(dispatch, generation, operation, threading.Event(), 0.0)
        job.submitted_at = self._record(job, "submitted")
        self._jobs[dispatch.id] = job
        self._issued.add(dispatch.id)
        self._pending.append(dispatch.id)
        self._start_ready()

    def _execute(self, job: _Job) -> _Completion:
        # ThreadPoolExecutor can enqueue a work item before starting a thread.
        # If thread startup raises, that item may still reach an existing worker.
        # It must not invoke work the owner has already recorded as failed.
        job.admission.wait()
        if not job.admitted:
            assert job.completion is not None
            return job.completion
        started = self._record(job, "started")
        try:
            value = job.operation(job.cancellation)
        except OperationCancelled:
            if job.cancellation.is_set():
                completed = self._record(job, "cancel_confirmed")
                return _Completion("canceled", started, completed)
            completed = self._record(job, "failed")
            return _Completion("failed", started, completed,
                               error="OperationCancelled without a cancellation request")
        except BaseException as exc:
            completed = self._record(job, "failed")
            return _Completion("failed", started, completed, error=_error_text(exc))
        completed = self._record(job, "completed")
        return _Completion("completed", started, completed, value=value)

    def _start_ready(self) -> None:
        if self._closed:
            return
        for ident in tuple(self._pending):
            if len(self._active) >= self._max_workers:
                break
            job = self._jobs[ident]
            resource = job.dispatch.resource
            if resource in self._active:
                continue
            self._pending.remove(ident)
            self._active[resource] = ident
            try:
                job.future = self._pool.submit(self._execute, job)
                job.admitted = True
            except BaseException as exc:
                # Submission failure is an outcome, never an invisible lost ID.
                completed = self._record(job, "failed")
                job.completion = _Completion("failed", None, completed,
                                             error=_error_text(exc))
                if not isinstance(exc, Exception):
                    raise
            finally:
                job.admission.set()

    def cancel(self, ident: str) -> bool:
        """Request cancellation. Return true only when no callable can still run.

        False does not mean the request was ignored. A running operation sees
        its Event and can acknowledge by raising OperationCancelled after safe
        cleanup. A completed result is not retroactively called canceled.
        """
        self._assert_owner()
        job = self._jobs[ident]
        if not job.cancellation.is_set():
            self._record(job, "cancel_requested")
            job.cancellation.set()
        if job.completion is not None:
            return job.completion.status == "canceled"
        if job.future is None or job.future.cancel():
            if ident in self._pending:
                self._pending.remove(ident)
            completed = self._record(job, "cancel_confirmed")
            job.completion = _Completion("canceled", None, completed)
            return True
        return False

    def reconcile(self, current_generation: int) -> tuple[ExecutionResult, ...]:
        """Return completed outcomes once, retaining stale values for inspection.

        A result's usability applies to this generation only. The owning caller
        must adopt it before advancing the generation. No scheduler reservation
        or assumed duration is changed by this optional executor.
        """
        self._assert_owner()
        _positive_integer(current_generation, "current_generation")
        results = []
        for ident, job in tuple(self._jobs.items()):
            completion = job.completion
            if completion is None:
                if job.future is None or not job.future.done():
                    continue
                completion = job.future.result()
            stale = job.generation != current_generation
            reconciled = self._record(job, "reconciled", stale=stale)
            results.append(ExecutionResult(job.dispatch, job.generation, completion.status,
                job.submitted_at, completion.started_at, completion.completed_at,
                reconciled, job.cancellation.is_set(), stale, completion.value, completion.error))
            if self._active.get(job.dispatch.resource) == ident:
                del self._active[job.dispatch.resource]
            del self._jobs[ident]
        self._start_ready()
        return tuple(results)

    def wait(self, current_generation: int, *, timeout: float | None = None) -> tuple[ExecutionResult, ...]:
        """Wait for a completion, then reconcile. Intended for command line callers."""
        self._assert_owner()
        if timeout is not None and (not isinstance(timeout, (int, float))
                                    or isinstance(timeout, bool) or timeout < 0
                                    or not float(timeout) < float("inf")):
            raise ValueError("timeout must be finite and nonnegative")
        ready = self.reconcile(current_generation)
        if ready:
            return ready
        futures = [job.future for job in self._jobs.values() if job.future is not None]
        if futures:
            wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
        return self.reconcile(current_generation)

    def close(self, *, wait_for_completion: bool = True) -> None:
        """Stop submission and request cancellation, optionally waiting for cleanup.

        A nonwaiting close is not a claim that work stopped. Keep this executor
        and reconcile until outstanding is zero. Python will still wait for its
        worker threads at process exit. No unsafe forced termination is offered.
        """
        self._assert_owner()
        self._closed = True
        for ident in tuple(self._jobs):
            self.cancel(ident)
        self._pool.shutdown(wait=wait_for_completion)

    def __enter__(self) -> RealExecutor:
        self._assert_owner()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
