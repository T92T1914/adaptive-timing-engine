"""A virtual executor: emits records, never controls an external application."""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import threading
from typing import Callable

from .model import Event, Plan, Policy, Scheduled, build_plan, finite
from .worker import LatestWorker


@dataclass(frozen=True)
class Dispatch:
    id: str
    resource: str
    actual_start: float
    finish: float
    status: str


class Executor:
    """Single-owner execution state; replace future work, preserve issued IDs.

    poll has a work budget, including delayed and expired events. It makes no
    callbacks. Issued-ID storage grows with session length; create a fresh
    executor for an independent session rather than silently forgetting IDs.
    """
    def __init__(self):
        self._owner = threading.get_ident()
        self._plan = Plan((), (), 0.0)
        self._index = 0
        self._queue: list[tuple[float, str, Scheduled]] = []
        self._issued: set[str] = set()
        self._busy: dict[str, float] = {}
        self._gap = 0.0
        self._last_now = 0.0

    def _assert_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("executor must be used on its owner thread")

    @property
    def queued(self) -> int:
        self._assert_owner()
        return len(self._plan.scheduled) - self._index + len(self._queue)

    def install(self, plan: Plan) -> None:
        self._assert_owner()
        if not isinstance(plan, Plan):
            raise TypeError("install requires a validated immutable Plan")
        # Validation happens when the worker constructs Plan. Adoption replaces
        # references; issued IDs are skipped incrementally within the poll budget.
        self._plan = plan
        self._index = 0
        self._queue = []
        self._gap = plan.resource_gap

    def poll(self, now: float, *, budget: int = 256) -> tuple[Dispatch, ...]:
        self._assert_owner()
        finite(now, "now")
        if now < self._last_now:
            raise ValueError("execution clock moved backwards")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise ValueError("budget must be a positive integer")
        self._last_now = now
        output = []
        for _ in range(budget):
            original = (self._plan.scheduled[self._index]
                        if self._index < len(self._plan.scheduled) else None)
            original_key = ((original.start, original.event.id) if original is not None
                            else (float("inf"), ""))
            deferred_key = self._queue[0][:2] if self._queue else (float("inf"), "")
            if min(original_key[0], deferred_key[0]) > now:
                break
            if deferred_key < original_key:
                _, _, item = heapq.heappop(self._queue)
            else:
                assert original is not None
                item = original
                self._index += 1
            event = item.event
            if event.id in self._issued:
                continue
            start = max(now, self._busy.get(event.resource, 0.0))
            finish = start + event.duration
            if finish > event.deadline + 1e-12:
                self._issued.add(event.id)
                output.append(Dispatch(event.id, event.resource, start, finish, "expired"))
            elif start > now:
                heapq.heappush(self._queue, (start, event.id, item))
            else:
                self._issued.add(event.id)
                self._busy[event.resource] = finish + self._gap
                output.append(Dispatch(event.id, event.resource, start, finish, "dispatched"))
        return tuple(output)


@dataclass(frozen=True)
class PlanningRequest:
    events: tuple[Event, ...]
    policy: Policy = Policy()
    seed: int = 42
    now: float = 0.0

    def __post_init__(self):
        if not isinstance(self.events, tuple):
            raise ValueError("events must be an immutable tuple")
        if any(not isinstance(event, Event) for event in self.events):
            raise TypeError("events must contain Event snapshots")
        if not isinstance(self.policy, Policy):
            raise TypeError("policy must be a Policy snapshot")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        finite(self.now, "now")


def compute(request: PlanningRequest) -> Plan:
    return build_plan(request.events, request.policy, seed=request.seed, now=request.now)


class Controller:
    """Submit and consume on one owner thread; compute runs on one worker.

    The single-owner API closes the gap between taking a current outcome and
    installing it. A second submitting thread cannot invalidate it mid-install.
    A thread avoids running planning inside poll, but does not bypass the GIL.
    """
    def __init__(self, planner: Callable[[PlanningRequest], Plan] = compute):
        self.executor = Executor()
        self.worker = LatestWorker(planner)
        self.revision = 0
        self.installed_revision = 0
        self.last_error: str | None = None

    def request(self, request: PlanningRequest) -> int:
        self.executor._assert_owner()
        if not isinstance(request, PlanningRequest):
            raise TypeError("request requires a PlanningRequest snapshot")
        self.revision = self.worker.request(request)
        return self.revision

    def poll(self, now: float, *, budget: int = 256) -> tuple[Dispatch, ...]:
        self.executor._assert_owner()
        finite(now, "now")
        if now < self.executor._last_now:
            raise ValueError("execution clock moved backwards")
        # Reject an invalid call before consuming a ready outcome or changing
        # the installed plan. The caller can correct it and retry safely.
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise ValueError("budget must be a positive integer")
        outcome = self.worker.take()
        if outcome is not None and outcome.generation == self.revision:
            self.last_error = outcome.error
            if outcome.error is None:
                try:
                    self.executor.install(outcome.value)
                except (ValueError, TypeError, AttributeError) as exc:
                    self.last_error = str(exc)
                else:
                    self.installed_revision = outcome.generation
        return self.executor.poll(now, budget=budget)

    def close(self, timeout: float = 0.0) -> bool:
        self.executor._assert_owner()
        return self.worker.close(timeout)
