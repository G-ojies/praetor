"""Agent scaffolding and the boundary between deterministic and model work.

Praetor draws one line hard, and it is the line the whole system is arguing for:

  * **Detection is deterministic.** Westgard rules, temperature thresholds,
    excursion durations, lot expiry arithmetic. These have correct answers, so a
    model is not asked. A model that hallucinates a 2-2s violation is worse than
    no automation at all, because it burns the scientist's trust on noise.

  * **Judgement is the model's.** Which of four simultaneous signals share a
    root cause; whether a drift is the reagent or the analyser; what to tell a
    scientist at 06:00 who has ninety samples waiting. There is no rule for
    these, which is exactly why they are worth a Gemini call.

Agents emit `Signal`s (something I observed, deterministically) and
`ActionProposal`s (something I want done, which the gate rules on). They never
execute. They never write to the audit chain. Both of those belong to components
that are not language models.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Iterable

from praetor.common.types import ActionProposal, Severity


@dataclass(frozen=True)
class Signal:
    """A deterministic observation. The unit of inter-agent communication.

    Signals are facts, not opinions: an agent asserting one is asserting that a
    rule fired, not that it believes something. The diagnostician's job is to
    turn a set of signals into an opinion, and it is the only agent that may.
    """

    kind: str  # e.g. "qc.rejection", "coldchain.excursion"
    source: str  # emitting agent id
    subject: str  # namespaced resource: lot:..., unit:..., instr:...
    at: float
    severity: Severity
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)


# The blackboard is a working set, not a history. The audit chain is the
# durable record and it is append-only; the blackboard exists so agents can see
# what is currently going on. Left unbounded it grows a signal per QC rejection
# forever, and since it persists as a single Firestore document, it would
# eventually hit the 1 MiB document limit and start failing writes -- weeks
# into a deployment, in a clinic, which is the worst possible time to discover
# a storage ceiling.
MAX_SIGNALS = 250


@dataclass
class Blackboard:
    """Shared fleet state for one simulated (or real) run.

    Deliberately a plain, inspectable record rather than a conversation history.
    Agents coordinate by writing facts another agent can read, not by passing
    each other prose -- prose accumulates errors, and a fleet that talks to
    itself in natural language drifts. In production this is a Firestore
    document; the interface is the same so the agents cannot tell.
    """

    signals: list[Signal] = field(default_factory=list)
    proposals: list[ActionProposal] = field(default_factory=list)
    # What the fleet is waiting on a human for. On the blackboard rather than
    # on the Fleet object because it must survive a restart: Cloud Run scales
    # to zero, and a control plane whose whole claim is "it stops and asks"
    # cannot forget what it asked. A dropped queue is a batch that stays held
    # with nobody left to release it.
    escalations: list[ActionProposal] = field(default_factory=list)
    # Every (kind, subject) the fleet has *ever* signalled. Kept separately and
    # permanently, because `seen` drives once-only behaviour -- if it consulted
    # the trimmed signal window instead, an old excursion would age out and the
    # fleet would re-announce it as new.
    seen_keys: set[str] = field(default_factory=set)
    # Resources the fleet has already acted on, so agents do not re-propose.
    held_batches: set[str] = field(default_factory=set)
    quarantined_lots: set[str] = field(default_factory=set)
    offline_instruments: set[str] = field(default_factory=set)
    # lot id -> storage unit id. Needed to tell "these lots share an analyser"
    # from "these lots share a fridge", which is the difference between pulling
    # an instrument and pulling a reagent.
    lot_storage: dict[str, str] = field(default_factory=dict)
    # Set by the diagnostician once it has a hypothesis worth acting on.
    root_cause: str | None = None
    root_cause_confidence: float = 0.0

    def add(self, signal: Signal) -> Signal:
        self.signals.append(signal)
        self.seen_keys.add(f"{signal.kind}\u0000{signal.subject}")
        if len(self.signals) > MAX_SIGNALS:
            del self.signals[: len(self.signals) - MAX_SIGNALS]
        return signal

    def since(self, at: float, kind: str | None = None) -> list[Signal]:
        return [s for s in self.signals if s.at >= at and (kind is None or s.kind == kind)]

    def of_kind(self, kind: str) -> list[Signal]:
        return [s for s in self.signals if s.kind == kind]

    def seen(self, kind: str, subject: str) -> bool:
        """Has this ever been signalled? Independent of the retention window."""
        return f"{kind}\u0000{subject}" in self.seen_keys


class Agent(abc.ABC):
    """One specialism. Sees the whole event stream, speaks about its own domain.

    Sub-agents are scoped by *capability*, not by input filtering: every agent
    may observe everything, and the gate is what stops the cold-chain agent from
    holding patient results. Scoping by what an agent can see would be a weaker
    guarantee, because it depends on the agent's own code being correct.
    """

    agent_id: str

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    @abc.abstractmethod
    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        """Deterministic. Return signals for anything this agent's rules catch."""

    def propose(self, board: Blackboard) -> Iterable[ActionProposal]:
        """Return actions this agent wants taken. Default: none."""
        return ()

    # -- helper so subclasses stay short -----------------------------------
    def action(
        self,
        incident_id: str,
        action_type: str,
        resource: str,
        *,
        confidence: float,
        rationale: str,
        **params: Any,
    ) -> ActionProposal:
        return ActionProposal(
            agent_id=self.agent_id,
            incident_id=incident_id,
            action_type=action_type,
            resource=resource,
            params=params,
            rationale=rationale,
            confidence=confidence,
        )
