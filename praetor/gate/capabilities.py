"""Capability grants: which agent may propose which action against what.

Deliberately *not* role-based. Roles drift and accumulate; a capability is a
narrow, individually revocable grant of one action type over one resource
pattern, with an optional ceiling on the incident severity at which the holder
may act autonomously.

Matching is exact-or-prefix-wildcard only. No regex: a policy language you
cannot read at a glance is a policy language you cannot audit.
"""

from __future__ import annotations

from dataclasses import dataclass

from praetor.common.types import ACTION_CATALOGUE, Severity


class PolicyConfigError(ValueError):
    """Raised at load time for a grant that could never be satisfied."""


@dataclass(frozen=True)
class Capability:
    agent_id: str
    action_type: str  # exact, or "prefix.*" (e.g. "observe.*")
    resource: str  # exact, or "prefix:*" (e.g. "svc:*")
    # The most severe incident this grant may be exercised on autonomously.
    # SEV1 is the most severe, so "max_severity" is a numeric *floor*.
    max_severity: Severity = Severity.SEV2

    def __post_init__(self) -> None:
        if not self.action_type.endswith(".*") and self.action_type not in ACTION_CATALOGUE:
            raise PolicyConfigError(
                f"grant for {self.agent_id!r} names unknown action {self.action_type!r}"
            )

    def matches(self, action_type: str, resource: str) -> bool:
        return _glob(self.action_type, action_type, ".") and _glob(self.resource, resource, ":")


def _glob(pattern: str, value: str, sep: str) -> bool:
    """Exact match, a bare `*`, or a single trailing `*` after the separator.

    Three forms, all readable aloud: "this exact thing", "anything", or
    "anything under this prefix". Nothing else parses, so a grant cannot mean
    something subtler than it looks.
    """
    if pattern == "*" or pattern == value:
        return True
    suffix = f"{sep}*"
    if pattern.endswith(suffix):
        return value.startswith(pattern[: -len("*")])
    return False


class CapabilitySet:
    """The fleet's grant table. Loaded from config, never mutated by agents."""

    def __init__(self, grants: list[Capability]) -> None:
        self._grants = list(grants)

    @classmethod
    def from_config(cls, raw: list[dict]) -> CapabilitySet:
        return cls(
            [
                Capability(
                    agent_id=g["agent_id"],
                    action_type=g["action_type"],
                    resource=g["resource"],
                    max_severity=Severity(int(g.get("max_severity", Severity.SEV2))),
                )
                for g in raw
            ]
        )

    def find(self, agent_id: str, action_type: str, resource: str) -> Capability | None:
        """Return the matching grant, preferring the one with the widest
        autonomous ceiling so a narrow grant never shadows a broader one."""
        candidates = [
            g
            for g in self._grants
            if g.agent_id == agent_id and g.matches(action_type, resource)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda g: g.max_severity)

    def for_agent(self, agent_id: str) -> list[Capability]:
        return [g for g in self._grants if g.agent_id == agent_id]
