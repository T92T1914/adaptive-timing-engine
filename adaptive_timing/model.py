"""Offline planning with explicit windows and one serial server per resource.

Time values are seconds on the caller's clock. Events are processed by target
time, then ID; this preserves a stable sequence but is not an optimal admission
algorithm. Workload uses lookahead because this planner receives a known batch.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import hashlib
import math
import random


def finite(value: float, name: str, minimum: float = 0.0) -> None:
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")


@dataclass(frozen=True)
class Event:
    id: str
    target: float
    earliest: float
    deadline: float
    duration: float
    resource: str = "worker-0"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("event ID must be a nonempty string")
        if not isinstance(self.resource, str) or not self.resource:
            raise ValueError("resource must be a nonempty string")
        for name in ("target", "earliest", "deadline", "duration"):
            finite(getattr(self, name), name)
        if not self.earliest <= self.target <= self.deadline:
            raise ValueError("require earliest <= target <= deadline")


@dataclass(frozen=True)
class Policy:
    sigma: float = 0.012
    density_window: float = 0.8
    comfortable_rate: float = 8.0
    load_gain: float = 0.55
    fatigue_gain: float = 0.65
    effort_per_event: float = 0.018
    recovery_seconds: float = 2.0
    resource_gap: float = 0.002
    use_load: bool = True
    use_fatigue: bool = True
    use_noise: bool = True

    def __post_init__(self) -> None:
        for name in ("use_load", "use_fatigue", "use_noise"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        for name in ("sigma", "load_gain", "fatigue_gain", "effort_per_event", "resource_gap"):
            finite(getattr(self, name), name)
        for name in ("density_window", "comfortable_rate", "recovery_seconds"):
            finite(getattr(self, name), name)
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class Scheduled:
    event: Event
    requested: float
    start: float
    finish: float
    density: float
    fatigue: float
    sigma: float
    constrained: bool


@dataclass(frozen=True)
class Rejected:
    event: Event
    reason: str


@dataclass(frozen=True)
class Plan:
    scheduled: tuple[Scheduled, ...]
    rejected: tuple[Rejected, ...]
    resource_gap: float

    def __post_init__(self) -> None:
        finite(self.resource_gap, "resource_gap")
        if not isinstance(self.scheduled, tuple) or not isinstance(self.rejected, tuple):
            raise ValueError("plan entries must be immutable tuples")
        seen: set[str] = set()
        ready: dict[str, float] = {}
        previous = (-math.inf, "")
        for item in self.scheduled:
            if not isinstance(item, Scheduled) or not isinstance(item.event, Event):
                raise TypeError("scheduled entries must be Scheduled records containing Event snapshots")
            event = item.event
            finite(item.requested, "requested", minimum=-math.inf)
            for name in ("density", "fatigue", "sigma"):
                finite(getattr(item, name), name)
            finite(item.start, "start")
            finite(item.finish, "finish")
            if (event.id in seen or (item.start, event.id) < previous
                    or item.start < event.earliest
                    or item.finish > event.deadline + 1e-12
                    or abs(item.finish - item.start - event.duration) > 1e-9
                    or item.start + 1e-12 < ready.get(event.resource, 0.0)):
                raise ValueError("invalid, duplicate, unsorted, or overlapping plan entry")
            seen.add(event.id)
            previous = (item.start, event.id)
            ready[event.resource] = item.finish + self.resource_gap
        for item in self.rejected:
            if not isinstance(item, Rejected) or not isinstance(item.event, Event):
                raise TypeError("rejected entries must be Rejected records containing Event snapshots")
            if item.event.id in seen:
                raise ValueError("an event appears more than once in the plan")
            seen.add(item.event.id)


def noise(seed: int, event_id: str) -> float:
    # An ID gets the same exogenous draw in every ablation. Changing policy or
    # dropping another event must not silently change the random comparison.
    digest = hashlib.sha256(f"{seed}:{event_id}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big")).gauss(0.0, 1.0)


def build_plan(events: tuple[Event, ...], policy: Policy = Policy(), *,
               seed: int = 42, now: float = 0.0) -> Plan:
    finite(now, "now")
    ordered = sorted(events, key=lambda e: (e.target, e.id))
    if len({e.id for e in ordered}) != len(ordered):
        raise ValueError("event IDs must be unique within a plan")
    times: dict[str, list[float]] = {}
    for event in ordered:
        times.setdefault(event.resource, []).append(event.target)
    ready: dict[str, float] = {}
    state: dict[str, tuple[float, float]] = {}
    scheduled: list[Scheduled] = []
    rejected: list[Rejected] = []
    for event in ordered:
        history = times[event.resource]
        half = policy.density_window / 2
        count = (bisect_right(history, event.target + half)
                 - bisect_left(history, event.target - half))
        density = count / policy.density_window
        previous, fatigue = state.get(event.resource, (event.target, 0.0))
        # The full idle interval matters: capping dt would retain fatigue after
        # a long rest. This is a synthetic policy, not a physiological fit.
        fatigue *= math.exp(-(event.target - previous) / policy.recovery_seconds)
        excess = max(0.0, density / policy.comfortable_rate - 1.0)
        sigma = policy.sigma * (1 + policy.load_gain * excess if policy.use_load else 1)
        sigma *= 1 + policy.fatigue_gain * fatigue if policy.use_fatigue else 1
        requested = event.target + (sigma * noise(seed, event.id) if policy.use_noise else 0)
        lower = max(now, event.earliest, ready.get(event.resource, 0.0))
        upper = event.deadline - event.duration
        if lower > upper:
            rejected.append(Rejected(event, "no feasible interval on resource"))
            # A rejected request consumes no resource time or new effort.
            state[event.resource] = (event.target, fatigue)
            continue
        start = min(upper, max(lower, requested))
        finish = start + event.duration
        scheduled.append(Scheduled(event, requested, start, finish, density, fatigue,
                                   sigma, abs(start - requested) > 1e-12))
        ready[event.resource] = finish + policy.resource_gap
        state[event.resource] = (event.target,
                                 min(1.0, fatigue + policy.effort_per_event * (1 + excess)))
    return Plan(tuple(sorted(scheduled, key=lambda item: (item.start, item.event.id))),
                tuple(rejected), policy.resource_gap)
