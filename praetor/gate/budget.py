"""Blast-radius budgeting and the fleet circuit breaker.

Two independent brakes, because they fail differently:

* `BlastRadiusBudget` is a per-resource token bucket. It bounds how much change
  the fleet may inflict on one service in a window, regardless of how confident
  any agent is. A model that is confidently wrong runs out of tokens.

* `CircuitBreaker` is per-incident. It counts mutating actions that did not
  resolve the incident. A fleet thrashing against a cause it cannot fix is the
  failure mode that turns an outage into a longer outage, so after N ineffective
  mutations the fleet stands down and hands to a human.

Both are pure and clock-injectable so the tests do not sleep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class BlastRadiusBudget:
    """Token bucket keyed by resource.

    `capacity` tokens, refilled at `capacity / window_s` tokens per second, so a
    fully drained bucket takes exactly one window to recover.
    """

    def __init__(
        self,
        capacity: int = 4,
        window_s: float = 600.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if capacity <= 0 or window_s <= 0:
            raise ValueError("capacity and window_s must be positive")
        self.capacity = float(capacity)
        self.window_s = float(window_s)
        self._clock = clock or __import__("time").time
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, resource: str) -> _Bucket:
        now = self._clock()
        b = self._buckets.get(resource)
        if b is None:
            b = _Bucket(tokens=self.capacity, last_refill=now)
            self._buckets[resource] = b
            return b
        elapsed = max(0.0, now - b.last_refill)
        b.tokens = min(self.capacity, b.tokens + elapsed * (self.capacity / self.window_s))
        b.last_refill = now
        return b

    def available(self, resource: str) -> float:
        return self._bucket(resource).tokens

    def can_afford(self, resource: str, cost: int) -> bool:
        return self._bucket(resource).tokens >= cost

    def charge(self, resource: str, cost: int) -> None:
        """Deduct. Call only after a decision to ALLOW, never speculatively."""
        b = self._bucket(resource)
        if b.tokens < cost:
            raise RuntimeError(f"charged {cost} to {resource} with {b.tokens:.2f} available")
        b.tokens -= cost

    def seconds_until(self, resource: str, cost: int) -> float:
        """How long until `cost` tokens are available. Drives the escalation text."""
        deficit = cost - self._bucket(resource).tokens
        if deficit <= 0:
            return 0.0
        return deficit / (self.capacity / self.window_s)


@dataclass
class CircuitBreaker:
    """Trips when the fleet mutates repeatedly without resolving the incident."""

    max_ineffective: int = 3
    _ineffective: dict[str, int] = field(default_factory=dict)

    def record_mutation(self, incident_id: str) -> None:
        self._ineffective[incident_id] = self._ineffective.get(incident_id, 0) + 1

    def record_resolution(self, incident_id: str) -> None:
        """A resolved incident clears the count; progress earns back trust."""
        self._ineffective.pop(incident_id, None)

    def tripped(self, incident_id: str) -> bool:
        return self._ineffective.get(incident_id, 0) >= self.max_ineffective

    def count(self, incident_id: str) -> int:
        return self._ineffective.get(incident_id, 0)
