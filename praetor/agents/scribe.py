"""Scribe: turns a resolved incident into something a human will actually read.

Runs once, at the end, over the audit chain and the blackboard. It is the only
agent whose output is prose, and it holds no capabilities at all -- it cannot
propose an action, so nothing it writes can move the lab.
"""

from __future__ import annotations

from typing import Any, Iterable

from praetor.agents.base import Agent, Blackboard, Signal
from praetor.reasoning import Reasoner, Tier

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "contributing_factors": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "contributing_factors", "recommendations"],
}

SYSTEM = """Write the incident report for a laboratory quality incident, addressed to one
medical laboratory scientist who covers four rural clinics and has no time to spare.

Be specific and unhedged. Name the lot, the unit and the analyte. Say plainly what the
fleet did on its own and what it asked a human to approve. Do not pad, do not moralise,
and do not claim certainty the evidence does not support."""


class Scribe(Agent):
    def __init__(self, reasoner: Reasoner, agent_id: str = "agent.scribe") -> None:
        super().__init__(agent_id)
        self._reasoner = reasoner

    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        return ()

    def report(self, board: Blackboard, timeline: list[Any]) -> dict:
        """`timeline` is the fleet's decision timeline, not the audit chain.
        The chain is the tamper-evident record; this is the readable one."""
        acted = [e for e in timeline if e.verdict == "allow"]
        escalated = [e for e in timeline if e.verdict == "escalate"]
        lines = [f"- [{s.kind}] {s.subject}: {s.summary}" for s in board.signals]
        prompt = (
            f"Signals:\n" + "\n".join(lines)
            + f"\n\nRoot cause: {board.root_cause} (confidence {board.root_cause_confidence:.2f})"
            + f"\n\nActions taken autonomously: {len(acted)}"
            + f"\nActions escalated to a human: {len(escalated)}"
        )
        completion = self._reasoner.complete(
            "narrate", tier=Tier.REASON, system=SYSTEM, prompt=prompt, schema=REPORT_SCHEMA
        )
        return completion.data | {"model": completion.model}
