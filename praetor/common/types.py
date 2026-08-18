"""Typed domain model for Praetor.

Everything an agent can propose is drawn from a closed catalogue defined here.
Agents never emit free-form commands; they emit `ActionProposal` instances whose
`action_type` must resolve against `ACTION_CATALOGUE`. That closure is what makes
the policy gate decidable: an unknown action is a denial, not an interpretation.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


class Severity(enum.IntEnum):
    """Incident severity. Ordered so comparisons read naturally."""

    SEV4 = 4  # cosmetic / informational
    SEV3 = 3  # degraded, no user impact
    SEV2 = 2  # user-visible degradation
    SEV1 = 1  # outage


class Reversibility(enum.IntEnum):
    """How cleanly an action can be undone. Ordered best-to-worst."""

    REVERSIBLE = 0  # exact prior state restorable from data we already hold
    PARTIAL = 1  # prior state restorable, but with side effects (dropped conns)
    IRREVERSIBLE = 2  # cannot be undone by us


class SafetyDirection(enum.Enum):
    """Which way an action moves the lab's safety posture.

    This is the sharpest lever in the whole policy. A fleet that may only ever
    move *toward* safety on its own is autonomous where autonomy is cheap and
    supervised where it is not. Holding a batch of results, quarantining a
    reagent lot, taking a drifting analyser offline: all of these fail closed,
    so the fleet does them unattended. Releasing results, clearing a quarantine,
    returning an instrument to service: these fail open, and a human owns them
    regardless of how confident the model is.
    """

    TIGHTENS = "tightens"  # fails closed; safe for the fleet to do alone
    NEUTRAL = "neutral"  # observation, notification, scheduling
    LOOSENS = "loosens"  # fails open; always a human decision


class Verdict(enum.Enum):
    ALLOW = "allow"
    ESCALATE = "escalate"  # needs a human; the fleet keeps the incident open
    DENY = "deny"  # policy forbids it outright; agent must propose otherwise
    HALT = "halt"  # circuit breaker tripped; fleet stands down entirely


@dataclass(frozen=True)
class ActionSpec:
    """Static properties of an action type. Not agent-controlled."""

    action_type: str
    mutating: bool
    reversibility: Reversibility
    # What the action costs against a service's blast-radius budget.
    blast_cost: int
    # Required parameter names. Presence is validated; values are not trusted.
    params: tuple[str, ...]
    safety_direction: SafetyDirection = SafetyDirection.NEUTRAL
    # Does this action attempt to *fix* something? Flagging a run, notifying a
    # scientist and reordering stock are bookkeeping: they change no clinical
    # state and cannot thrash. Only remediating actions count against the
    # circuit breaker, and only they are withheld once it trips -- a fleet that
    # has stood down should still be telling you what it sees.
    remediating: bool = True


# The closed catalogue. Adding a row here is a deliberate, reviewable act.
#
# Resource identifiers are namespaced: instr:<id>, lot:<id>, batch:<id>,
# unit:<id> (cold-chain units), site:<id>.
_T, _N, _L = SafetyDirection.TIGHTENS, SafetyDirection.NEUTRAL, SafetyDirection.LOOSENS

ACTION_CATALOGUE: dict[str, ActionSpec] = {
    spec.action_type: spec
    for spec in [
        # -- observation: free, unlimited, still capability-scoped ----------
        ActionSpec("observe.annotate", False, Reversibility.REVERSIBLE, 0, ("note",), _N, False),
        ActionSpec("observe.telemetry", False, Reversibility.REVERSIBLE, 0, ("window_s",), _N, False),
        ActionSpec("observe.qc_history", False, Reversibility.REVERSIBLE, 0, ("analyte", "days"), _N, False),
        # -- notification and scheduling: neutral ---------------------------
        ActionSpec("notify.scientist", True, Reversibility.REVERSIBLE, 0, ("channel", "message"), _N, False),
        ActionSpec("schedule.recalibration", True, Reversibility.REVERSIBLE, 1, ("instrument", "at"), _N),
        ActionSpec("inventory.reorder", True, Reversibility.PARTIAL, 2, ("lot_family", "quantity"), _N, False),
        # -- fail-closed: the fleet may do these alone ----------------------
        ActionSpec("qc.flag_run", True, Reversibility.REVERSIBLE, 1, ("run_id", "reason"), _T, False),
        ActionSpec("results.hold_batch", True, Reversibility.REVERSIBLE, 2, ("batch_id", "reason"), _T),
        ActionSpec("lot.quarantine", True, Reversibility.REVERSIBLE, 2, ("lot_id", "reason"), _T),
        ActionSpec("instrument.take_offline", True, Reversibility.PARTIAL, 3, ("instrument", "reason"), _T),
        ActionSpec("coldchain.setpoint", True, Reversibility.REVERSIBLE, 2, ("unit", "celsius"), _T),
        # -- fail-open: never the fleet's call, however confident -----------
        ActionSpec("results.release_batch", True, Reversibility.IRREVERSIBLE, 8, ("batch_id",), _L),
        ActionSpec("lot.clear_quarantine", True, Reversibility.REVERSIBLE, 4, ("lot_id",), _L),
        ActionSpec("instrument.return_to_service", True, Reversibility.PARTIAL, 4, ("instrument",), _L),
    ]
}


def _now() -> float:
    return time.time()


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class ActionProposal:
    """What an agent asks to do. Never executed without a gate decision."""

    agent_id: str
    incident_id: str
    action_type: str
    resource: str  # e.g. "svc:checkout-api"
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    confidence: float = 0.0
    # Idempotency: the executor refuses a second execution of the same key.
    idempotency_key: str = field(default_factory=lambda: _uid("idem"))
    proposal_id: str = field(default_factory=lambda: _uid("prop"))
    created_at: float = field(default_factory=_now)

    def spec(self) -> ActionSpec | None:
        return ACTION_CATALOGUE.get(self.action_type)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Incident:
    incident_id: str = field(default_factory=lambda: _uid("inc"))
    subject: str = ""  # the resource the incident is about
    severity: Severity = Severity.SEV3
    title: str = ""
    opened_at: float = field(default_factory=_now)
    resolved_at: float | None = None
    root_cause: str | None = None
    # Every proposal ever made against this incident, allowed or not.
    proposal_ids: list[str] = field(default_factory=list)

    @property
    def open(self) -> bool:
        return self.resolved_at is None


@dataclass(frozen=True)
class Decision:
    """The gate's ruling. Immutable, signed, and chained into the audit log."""

    verdict: Verdict
    proposal_id: str
    reasons: tuple[str, ...]
    # Populated on ESCALATE so the console can render an approve/reject card.
    escalation_reason: str | None = None
    decided_at: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "proposal_id": self.proposal_id,
            "reasons": list(self.reasons),
            "escalation_reason": self.escalation_reason,
            "decided_at": self.decided_at,
        }
