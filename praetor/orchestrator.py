"""The fleet loop.

Every event is offered to every agent. Agents emit signals deterministically,
then are asked for proposals. Every proposal -- without exception, including
observation -- goes through the policy gate, and only ALLOW reaches the executor.

The ordering matters and is not incidental: proposals are collected from all
agents before any are evaluated, so the gate sees a round at a time and the
blast-radius budget is charged against a coherent snapshot rather than depending
on which agent happened to run first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from praetor.agents.base import Agent, Blackboard
from praetor.common.types import ActionProposal, Severity, Verdict
from praetor.gate.audit import AuditChain
from praetor.gate.policy import PolicyGate


class Executor(Protocol):
    def execute(self, proposal: ActionProposal) -> dict: ...


@dataclass
class SimulatedExecutor:
    """Records what would have happened. Idempotent by construction.

    The real executor calls the LIS and the sensor gateway; it shares this
    idempotency-key contract, because an agent fleet that retries is an agent
    fleet that will eventually double-execute.
    """

    performed: list[dict] = field(default_factory=list)
    _keys: set[str] = field(default_factory=set)

    def execute(self, proposal: ActionProposal) -> dict:
        if proposal.idempotency_key in self._keys:
            return {"status": "duplicate", "proposal_id": proposal.proposal_id}
        self._keys.add(proposal.idempotency_key)
        record = {
            "status": "ok",
            "proposal_id": proposal.proposal_id,
            "action": proposal.action_type,
            "resource": proposal.resource,
            "params": proposal.params,
        }
        self.performed.append(record)
        return record


@dataclass
class TimelineEntry:
    at: float
    agent: str
    action: str
    resource: str
    verdict: str
    reasons: tuple[str, ...]
    rationale: str
    escalation: str | None = None


class Fleet:
    def __init__(
        self,
        agents: list[Agent],
        gate: PolicyGate,
        executor: Executor | None = None,
        board: Blackboard | None = None,
    ) -> None:
        self.agents = agents
        self.gate = gate
        self.executor = executor or SimulatedExecutor()
        self.board = board or Blackboard()
        self.timeline: list[TimelineEntry] = []
        self.escalations: list[ActionProposal] = []

    @property
    def chain(self) -> AuditChain:
        return self.gate.chain

    def _severity(self, proposal: ActionProposal) -> Severity:
        """Severity of the worst signal touching this proposal's subject.

        Falls back to the fleet-wide worst, because an action taken during a
        SEV1 is a SEV1 action even when its own subject looks quiet.
        """
        related = [s for s in self.board.signals if s.subject == proposal.resource]
        pool = related or self.board.signals
        return min((s.severity for s in pool), default=Severity.SEV3)

    def step(self, event: Any) -> None:
        for agent in self.agents:
            agent.observe(event, self.board)

        for agent in self.agents:
            for proposal in agent.propose(self.board):
                self.board.proposals.append(proposal)
                decision = self.gate.evaluate(proposal, self._severity(proposal))
                self.timeline.append(TimelineEntry(
                    at=event.at,
                    agent=proposal.agent_id,
                    action=proposal.action_type,
                    resource=proposal.resource,
                    verdict=decision.verdict.value,
                    reasons=decision.reasons,
                    rationale=proposal.rationale,
                    escalation=decision.escalation_reason,
                ))
                if decision.verdict is Verdict.ALLOW:
                    self.executor.execute(proposal)
                elif decision.verdict is Verdict.ESCALATE:
                    self.escalations.append(proposal)

    def run(self, events: list[Any]) -> "Fleet":
        for event in events:
            self.step(event)
        return self

    # -- reporting ----------------------------------------------------------
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.timeline:
            out[entry.verdict] = out.get(entry.verdict, 0) + 1
        return out
