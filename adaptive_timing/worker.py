"""One computation in progress, one replaceable request, one completed outcome.

Adapted and generalized from a larger private application's request mailbox.
Request payloads must be immutable snapshots (or otherwise exclusively owned).
take() is atomic and returns a result current at that instant, not a perpetual
lease. Use Controller when consuming into an owner-thread execution loop.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Generic, TypeVar

RequestT = TypeVar("RequestT")
ValueT = TypeVar("ValueT")


@dataclass(frozen=True)
class Outcome(Generic[ValueT]):
    generation: int
    value: ValueT | None
    error: str | None
    compute_seconds: float


@dataclass(frozen=True)
class WorkerStats:
    generation: int
    submitted: int
    computed: int
    replaced: int
    stale: int
    failures: int
    queued: int
    running: bool


class LatestWorker(Generic[RequestT, ValueT]):
    def __init__(self, compute: Callable[[RequestT], ValueT]):
        self._compute = compute
        self._condition = threading.Condition()
        self._generation = 0
        self._queued: tuple[int, RequestT] | None = None
        self._pending: Outcome[ValueT] | None = None
        self._closed = False
        self._running = False
        self._computed = self._replaced = self._stale = self._failures = 0
        self._thread = threading.Thread(target=self._run, name="latest-planner", daemon=True)
        self._thread.start()

    def request(self, payload: RequestT) -> int:
        with self._condition:
            if self._closed:
                raise RuntimeError("worker is closed")
            self._generation += 1
            self._replaced += self._queued is not None
            self._queued = (self._generation, payload)
            self._pending = None
            self._condition.notify()
            return self._generation

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._queued is not None)
                if self._closed:
                    return
                assert self._queued is not None
                generation, payload = self._queued
                self._queued = None
                self._running = True
            started = time.perf_counter()
            value = None
            error = None
            try:
                value = self._compute(payload)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            outcome = Outcome(generation, value, error, time.perf_counter() - started)
            with self._condition:
                self._computed += 1
                self._failures += error is not None
                if not self._closed and generation == self._generation:
                    self._pending = outcome
                else:
                    self._stale += 1
            # A sleeping thread keeps its frame alive. Release completed work
            # outside the lock, before wait_idle can report cleanup complete.
            del payload, value, outcome, error
            with self._condition:
                self._running = False
                self._condition.notify_all()

    def take(self) -> Outcome[ValueT] | None:
        with self._condition:
            outcome, self._pending = self._pending, None
            return outcome

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """For tests/shutdown, never the execution loop."""
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._running and self._queued is None, timeout=timeout)

    def stats(self) -> WorkerStats:
        with self._condition:
            return WorkerStats(self._generation, self._generation, self._computed,
                               self._replaced, self._stale, self._failures,
                               int(self._queued is not None), self._running)

    def close(self, timeout: float = 0.0) -> bool:
        """Stop accepting work; running compute can finish. Return whether stopped."""
        with self._condition:
            self._closed = True
            self._queued = None
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close(timeout=2.0)
