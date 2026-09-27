"""Incremental planning over delivered information, with explicit snapshot leases."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
import threading

from .model import Event, Plan, Policy, Rejected, Scheduled, finite, noise
from .runtime import Dispatch, Executor


def integer(value: int, name: str, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Information:
    """One delivered fact. Sequence orders facts sharing an available_at time.

    observed_at is source metadata. available_at is when this session can use
    the fact. earliest in Event is the release time, even for announced future
    work. Revisions start at one and increase by one for each ID.
    """
    available_at: float
    sequence: int
    observed_at: float
    kind: str
    id: str
    version: int
    event: Event | None = None

    def __post_init__(self):
        finite(self.available_at, "available_at")
        finite(self.observed_at, "observed_at")
        integer(self.sequence, "sequence")
        integer(self.version, "version", 1)
        if self.observed_at > self.available_at:
            raise ValueError("observation cannot follow delivery")
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("information requires a stable ID")
        if self.kind not in ("announce", "update", "cancel"):
            raise ValueError("unknown information kind")
        if self.kind == "cancel":
            if self.event is not None:
                raise ValueError("cancel carries an ID, not an event")
        elif not isinstance(self.event, Event) or self.event.id != self.id:
            raise ValueError("announce and update require a matching immutable Event")


@dataclass(frozen=True)
class ResourceState:
    resource: str
    ready: float
    effort_at: float
    effort: float

    def __post_init__(self):
        if not isinstance(self.resource, str) or not self.resource:
            raise ValueError("resource must be named")
        for name in ("ready", "effort_at", "effort"):
            finite(getattr(self, name), name)
        if self.effort > 1:
            raise ValueError("effort must be at most one")


@dataclass(frozen=True)
class CausalRequest:
    generation: int
    now: float
    events: tuple[Event, ...]
    resources: tuple[ResourceState, ...]
    policy: Policy
    seed: int

    def __post_init__(self):
        integer(self.generation, "generation", 1)
        finite(self.now, "now")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        if not isinstance(self.policy, Policy):
            raise TypeError("policy must be immutable")
        for name, cls, key in (("events", Event, "id"),
                               ("resources", ResourceState, "resource")):
            rows = getattr(self, name)
            if not isinstance(rows, tuple) or any(not isinstance(row, cls) for row in rows):
                raise TypeError(f"{name} must be an immutable tuple of {cls.__name__}")
            if len({getattr(row, key) for row in rows}) != len(rows):
                raise ValueError(f"duplicate {name}")
        if any(row.effort_at > self.now for row in self.resources):
            raise ValueError("observed effort cannot come from the future")


@dataclass(frozen=True)
class CausalResult:
    request: CausalRequest
    plan: Plan

    def __post_init__(self):
        if not isinstance(self.request, CausalRequest) or not isinstance(self.plan, Plan):
            raise TypeError("result requires immutable request and plan")
        events = tuple(row.event for row in self.plan.scheduled + self.plan.rejected)
        if set(events) != set(self.request.events):
            raise ValueError("result must account for exactly the requested snapshots")
        ready = {row.resource: row.ready for row in self.request.resources}
        if self.plan.resource_gap != self.request.policy.resource_gap:
            raise ValueError("result changed the resource gap")
        if any(row.start < max(self.request.now, ready.get(row.event.resource, 0.0))
               for row in self.plan.scheduled):
            raise ValueError("result violates an existing reservation or request time")


def build_causal_plan(request: CausalRequest) -> CausalResult:
    """Project only the available outstanding work and observed dispatch effort.

    Density is a centered window over this delivered catalog, not hidden work.
    Effort starts with actual virtual dispatch history and projects admitted
    tasks in this calculation. Replanning does not count those projections as
    new observations. The complete batch comparator remains build_plan.
    """
    if not isinstance(request, CausalRequest):
        raise TypeError("build_causal_plan requires a CausalRequest")
    policy = request.policy
    ordered = sorted(request.events, key=lambda event: (event.target, event.id))
    times: dict[str, list[float]] = {}
    for event in ordered:
        times.setdefault(event.resource, []).append(event.target)
    ready = {row.resource: row.ready for row in request.resources}
    effort = {row.resource: (row.effort_at, row.effort) for row in request.resources}
    scheduled, rejected = [], []
    for event in ordered:
        history = times[event.resource]
        half = policy.density_window / 2
        density = (bisect_right(history, event.target + half)
                   - bisect_left(history, event.target - half)) / policy.density_window
        at = max(request.now, event.target)
        previous, fatigue = effort.get(event.resource, (at, 0.0))
        fatigue *= math.exp(-(at - previous) / policy.recovery_seconds)
        excess = max(0.0, density / policy.comfortable_rate - 1.0)
        sigma = policy.sigma * (1 + policy.load_gain * excess if policy.use_load else 1)
        sigma *= 1 + policy.fatigue_gain * fatigue if policy.use_fatigue else 1
        requested = event.target + (sigma * noise(request.seed, event.id) if policy.use_noise else 0)
        lower = max(request.now, event.earliest, ready.get(event.resource, 0.0))
        upper = event.deadline - event.duration
        if lower > upper:
            rejected.append(Rejected(event, "no feasible interval on known resource"))
            effort[event.resource] = (at, fatigue)
            continue
        start = min(upper, max(lower, requested))
        finish = start + event.duration
        scheduled.append(Scheduled(event, requested, start, finish, density, fatigue,
                                   sigma, abs(start - requested) > 1e-12))
        ready[event.resource] = finish + policy.resource_gap
        effort[event.resource] = (at, min(1.0, fatigue + policy.effort_per_event * (1 + excess)))
    plan = Plan(tuple(sorted(scheduled, key=lambda row: (row.start, row.event.id))),
                tuple(rejected), policy.resource_gap)
    return CausalResult(request, plan)


class CausalScheduler:
    """Owner thread session. Inputs are delivered groups, never a future stream.

    Call receive for every fact at a timestamp before snapshot or poll there.
    A timestamp is closed by snapshot or poll. A complete delivery group is
    validated before mutation. Results are leases for the exact request object
    issued by this session, suitable for the existing in-process LatestWorker.
    """
    def __init__(self, policy: Policy = Policy(), *, seed: int = 42):
        CausalRequest(1, 0.0, (), (), policy, seed)
        self.policy, self.seed = policy, seed
        self._owner = threading.get_ident()
        self._executor = Executor()
        self._events: dict[str, Event] = {}
        self._versions: dict[str, int] = {}
        self._terminal: dict[str, str] = {}
        self._resources: dict[str, ResourceState] = {}
        self._scheduled: dict[str, Scheduled] = {}
        self._generation = 0
        self._request: CausalRequest | None = None
        self._now = 0.0
        self._closed_at = -1.0
        self._last_delivery = -1.0

    def _clock(self, now: float):
        if threading.get_ident() != self._owner:
            raise RuntimeError("causal scheduler must be used on its owner thread")
        finite(now, "now")
        if now < self._now:
            raise ValueError("session clock moved backwards")

    def receive(self, facts: tuple[Information, ...], *, now: float) -> tuple[str, ...]:
        """Deliver one whole timestamp. Return applied or ignored_terminal per fact.

        Rejected, dispatched, expired and canceled IDs cannot be resurrected.
        Later valid versions are recorded but cannot undo a terminal outcome.
        """
        self._clock(now)
        if not isinstance(facts, tuple) or any(not isinstance(row, Information) for row in facts):
            raise TypeError("facts must be an immutable tuple of Information")
        if not facts:
            raise ValueError("receive needs a nonempty delivery group")
        if now <= self._closed_at or now <= self._last_delivery:
            raise ValueError("delivery timestamp was already closed")
        if any(row.available_at != now for row in facts):
            raise ValueError("deliver exactly the information available at now")
        if len({row.sequence for row in facts}) != len(facts):
            raise ValueError("sequence must be unique at a timestamp")
        ordered = sorted(facts, key=lambda row: row.sequence)
        versions = dict(self._versions)
        for row in ordered:
            previous = versions.get(row.id, 0)
            if row.version != previous + 1 or (row.kind == "announce") != (previous == 0):
                raise ValueError("announce once, then use consecutive update or cancel versions")
            versions[row.id] = row.version
        statuses = []
        for row in ordered:
            if row.id in self._terminal:
                statuses.append("ignored_terminal")
            else:
                statuses.append("applied")
                if row.kind == "cancel":
                    self._events.pop(row.id, None)
                    self._terminal[row.id] = "canceled"
                else:
                    self._events[row.id] = row.event
        self._versions = versions
        self._now = self._last_delivery = now
        self._generation += 1
        self._request = None
        self._scheduled = {}
        # Invalidate immediately, including before an asynchronous result arrives.
        # Executor retains issued IDs and service reservations across install.
        self._executor.install(Plan((), (), self.policy.resource_gap))
        return tuple(statuses)

    def snapshot(self, now: float) -> CausalRequest:
        self._clock(now)
        self._now = self._closed_at = now
        self._generation += 1
        self._request = CausalRequest(self._generation, now,
            tuple(sorted(self._events.values(), key=lambda event: event.id)),
            tuple(sorted(self._resources.values(), key=lambda row: row.resource)),
            self.policy, self.seed)
        return self._request

    def install(self, result: CausalResult) -> bool:
        self._clock(self._now)
        if not isinstance(result, CausalResult):
            raise TypeError("install requires a CausalResult")
        if result.request is not self._request or result.request.generation != self._generation:
            return False
        self._executor.install(result.plan)
        self._scheduled = {row.event.id: row for row in result.plan.scheduled}
        for row in result.plan.rejected:
            self._events.pop(row.event.id)
            self._terminal[row.event.id] = "rejected"
        # A result is consumed once. Delay alone is allowed. poll checks deadlines.
        self._request = None
        return True

    def poll(self, now: float, *, budget: int = 256) -> tuple[Dispatch, ...]:
        self._clock(now)
        integer(budget, "budget", 1)
        output = self._executor.poll(now, budget=budget)
        self._now = self._closed_at = now
        if output:
            self._generation += 1
            self._request = None
        for row in output:
            item = self._scheduled.pop(row.id)
            self._events.pop(row.id)
            self._terminal[row.id] = row.status
            if row.status == "dispatched":
                previous = self._resources.get(row.resource, ResourceState(row.resource, 0, now, 0))
                effort = previous.effort * math.exp(-(now - previous.effort_at) / self.policy.recovery_seconds)
                excess = max(0.0, item.density / self.policy.comfortable_rate - 1.0)
                effort = min(1.0, effort + self.policy.effort_per_event * (1 + excess))
                self._resources[row.resource] = ResourceState(row.resource,
                    row.finish + self.policy.resource_gap, now, effort)
        return output

    def outcomes(self) -> tuple[tuple[str, str], ...]:
        self._clock(self._now)
        return tuple(sorted(self._terminal.items()))
