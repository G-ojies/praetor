"""Diagnostician: the only agent permitted to hold an opinion.

Every other agent asserts facts: a rule fired, a threshold was crossed. This
one takes the set of facts on the blackboard and asks Gemini the question that
has no deterministic answer: do these signals share a cause, and if so what is
it? Its output is a hypothesis with a confidence, and that confidence flows
straight into the policy gate, which refuses to act autonomously below a floor.

It also proposes the release of held results once it believes the incident is
understood and remediated. That proposal always escalates. It is meant to: the
diagnostician is the component most likely to be confidently wrong, so it is the
one component whose favourable conclusions a human must ratify.
"""

from __future__ import annotations

from typing import Any, Iterable

from praetor.agents.base import Agent, Blackboard, Signal
from praetor.common.types import ActionProposal
from praetor.reasoning import Reasoner, Tier

DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "implicates_instrument": {"type": "boolean"},
        "primary_subject": {"type": "string"},
    },
    "required": ["root_cause", "confidence", "reasoning", "implicates_instrument"],
}

SYSTEM = """You are the diagnostician for a laboratory agent fleet serving four rural clinics.
You receive deterministic signals: Westgard QC rejections and cold-chain excursions.
Decide whether they share a single root cause.

Weigh especially: a reagent stored in a unit that went out of range before the QC drift
began is a far better explanation than an instrument fault, and pulling an analyser from
a clinic that has only one is expensive. Only set implicates_instrument when the evidence
points at the analyser itself, such as multiple distinct reagent lots failing on it.

Be honest about confidence. Below 0.55 the fleet will not act without a human, which is
the correct outcome when the signals do not actually cohere."""


class Diagnostician(Agent):
    MIN_SIGNALS = 2

    def __init__(self, reasoner: Reasoner, agent_id: str = "agent.diagnostician") -> None:
        super().__init__(agent_id)
        self._reasoner = reasoner
        self._pending: list[ActionProposal] = []
        self._diagnosed = False
        self._released: set[str] = set()

    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        return ()  # reasons over the blackboard, not the raw stream

    def propose(self, board: Blackboard) -> Iterable[ActionProposal]:
        actionable = board.of_kind("qc.rejection") + board.of_kind("coldchain.excursion")
        if not self._diagnosed and len(actionable) >= self.MIN_SIGNALS:
            self._diagnose(board, actionable)

        # Once the cause is understood and the offending lot quarantined, ask to
        # release what is being held. The gate will send this to a human.
        if self._diagnosed and board.root_cause_confidence >= 0.7:
            for batch in sorted(board.held_batches - self._released):
                self._released.add(batch)
                self._pending.append(self.action(
                    f"inc_release_{batch.split(':')[-1]}",
                    "results.release_batch", batch,
                    confidence=board.root_cause_confidence,
                    rationale=(f"Cause identified and remediated: {board.root_cause.rstrip('.')}. "
                               "Results on unaffected lots are reportable."),
                    batch_id=batch.split(":", 1)[-1],
                ))

        out, self._pending = self._pending, []
        return out

    def _diagnose(self, board: Blackboard, signals: list[Signal]) -> None:
        lines = [
            f"- [{s.kind}] {s.subject} at t={s.at:.0f}: {s.summary} | facts={s.facts}"
            for s in signals
        ]
        completion = self._reasoner.complete(
            "diagnose",
            tier=Tier.REASON,
            system=SYSTEM,
            prompt="Signals on the blackboard:\n" + "\n".join(lines),
            schema=DIAGNOSIS_SCHEMA,
        )
        data = completion.data
        board.root_cause = data["root_cause"]
        board.root_cause_confidence = float(data["confidence"])
        self._diagnosed = True
        board.add(Signal(
            kind="diagnosis",
            source=self.agent_id,
            subject=data.get("primary_subject", "fleet"),
            at=max(s.at for s in signals),
            severity=min(s.severity for s in signals),
            summary=data["root_cause"],
            facts={"confidence": board.root_cause_confidence,
                   "reasoning": data["reasoning"],
                   "implicates_instrument": data["implicates_instrument"],
                   "model": completion.model},
        ))
