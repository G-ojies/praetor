"""The policy gate: the one component in Praetor that is not a language model.

Every mutating action the fleet takes passes through `PolicyGate.evaluate`. It is
deterministic, ordered, and total — each proposal yields exactly one of ALLOW,
ESCALATE, DENY, or HALT, with the reasons recorded. That determinism is the whole
argument: you can reason about what an autonomous fleet is permitted to do
without reasoning about what a model might say.

Check order matters and is deliberate, cheapest and most absolute first:

  0. HALT      circuit breaker tripped for this incident -> fleet stands down
  1. DENY      action outside the closed catalogue
  2. DENY      malformed parameters
  3. DENY      no capability grant for (agent, action, resource)
  4. ESCALATE  action loosens the safety posture (fails open)
  5. ESCALATE  incident more severe than the grant's autonomous ceiling
  6. ESCALATE  action is irreversible
  7. ESCALATE  diagnostic confidence below the floor
  8. ESCALATE  blast-radius budget exhausted for this resource
  9. ALLOW

Check 4 is the one that matters most in a laboratory. The fleet may move the
lab toward safety on its own (hold a batch, quarantine a lot, pull a drifting
analyser) because every one of those fails closed. It may never move the lab
away from safety unattended, however confident the diagnosis, because releasing
a patient result you should not have released is not an incident you can undo.

DENY means "never, re-plan". ESCALATE means "plausibly right, but not yours to
decide alone" and parks the proposal for a human. The distinction is what lets
the fleet stay autonomous on the boring 90% without ever silently crossing a
line that matters.
"""

from __future__ import annotations

from dataclasses import dataclass

from praetor.common.types import (
    ACTION_CATALOGUE,
    ActionProposal,
    Decision,
    Reversibility,
    SafetyDirection,
    Severity,
    Verdict,
)
from praetor.gate.audit import AuditChain
from praetor.gate.budget import BlastRadiusBudget, CircuitBreaker
from praetor.gate.capabilities import CapabilitySet


@dataclass
class GateConfig:
    """Fleet-wide policy knobs. Loaded from `deploy/policy.yaml`."""

    # Actions at or worse than this reversibility always need a human.
    escalate_at_reversibility: Reversibility = Reversibility.IRREVERSIBLE
    # Confidence below which even a permitted action is escalated.
    min_confidence: float = 0.55


class PolicyGate:
    def __init__(
        self,
        capabilities: CapabilitySet,
        budget: BlastRadiusBudget,
        breaker: CircuitBreaker,
        chain: AuditChain,
        config: GateConfig | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.budget = budget
        self.breaker = breaker
        self.chain = chain
        self.config = config or GateConfig()

    def evaluate(self, proposal: ActionProposal, severity: Severity) -> Decision:
        decision = self._decide(proposal, severity)

        # Charge the budget only for permitted mutations, and only once.
        spec = ACTION_CATALOGUE.get(proposal.action_type)
        if decision.verdict is Verdict.ALLOW and spec and spec.mutating:
            self.budget.charge(proposal.resource, spec.blast_cost)
            if spec.remediating:
                self.breaker.record_mutation(proposal.incident_id)

        # Every decision is logged, including the denials. A gate that only
        # records its approvals tells you nothing about what it stopped.
        self.chain.append(
            "decision",
            {
                "proposal": proposal.to_dict(),
                "severity": int(severity),
                "decision": decision.to_dict(),
            },
        )
        return decision

    # -- the ordered checks -------------------------------------------------
    def _decide(self, p: ActionProposal, severity: Severity) -> Decision:
        def out(v: Verdict, *reasons: str, escalation: str | None = None) -> Decision:
            return Decision(v, p.proposal_id, tuple(reasons), escalation)

        # 0. Circuit breaker. Absolute for remediating actions, and checked
        #    before anything else. Bookkeeping passes through: standing down
        #    means the fleet stops trying to fix the incident, not that it goes
        #    silent on a scientist who now has to handle it themselves.
        spec_for_breaker = ACTION_CATALOGUE.get(p.action_type)
        if (self.breaker.tripped(p.incident_id)
                and spec_for_breaker is not None
                and spec_for_breaker.remediating):
            return out(
                Verdict.HALT,
                f"circuit breaker tripped: {self.breaker.count(p.incident_id)} "
                f"mutations on {p.incident_id} without resolution",
                escalation="Fleet stood down. A human must take the incident.",
            )

        # 1. Closed catalogue.
        spec = p.spec()
        if spec is None:
            return out(Verdict.DENY, f"action {p.action_type!r} is not in the catalogue")

        # 2. Parameters. Presence only; the executor validates values against
        #    the live resource, because only it knows what exists.
        missing = [k for k in spec.params if k not in p.params]
        if missing:
            return out(Verdict.DENY, f"missing required params: {', '.join(missing)}")

        # Non-mutating observation needs a grant but nothing further.
        grant = self.capabilities.find(p.agent_id, p.action_type, p.resource)
        if grant is None:
            return out(
                Verdict.DENY,
                f"{p.agent_id} holds no capability for {p.action_type} on {p.resource}",
            )
        if not spec.mutating:
            return out(Verdict.ALLOW, "non-mutating action within granted capability")

        # 4. Safety direction. Checked before everything else escalatable:
        #    a fail-open action is a human's call even at SEV4, on a fully
        #    stocked budget, with a perfectly confident diagnosis.
        if spec.safety_direction is SafetyDirection.LOOSENS:
            return out(
                Verdict.ESCALATE,
                f"{p.action_type} loosens the lab's safety posture",
                escalation=(
                    "Fail-open action. The fleet may hold, quarantine and pull "
                    "on its own; only a scientist may release."
                ),
            )

        # 5. Severity ceiling. Lower Severity value == more severe.
        if severity < grant.max_severity:
            return out(
                Verdict.ESCALATE,
                f"incident is SEV{int(severity)}; {p.agent_id} acts autonomously "
                f"only at SEV{int(grant.max_severity)} or milder",
                escalation=f"SEV{int(severity)} mutation requires human approval.",
            )

        # 6. Reversibility.
        if spec.reversibility >= self.config.escalate_at_reversibility:
            return out(
                Verdict.ESCALATE,
                f"{p.action_type} is {spec.reversibility.name.lower()}",
                escalation="Action cannot be undone by the fleet.",
            )

        # 7. Confidence floor.
        if p.confidence < self.config.min_confidence:
            return out(
                Verdict.ESCALATE,
                f"confidence {p.confidence:.2f} below floor {self.config.min_confidence:.2f}",
                escalation="Diagnosis too uncertain to act on unattended.",
            )

        # 8. Blast radius.
        if not self.budget.can_afford(p.resource, spec.blast_cost):
            wait_s = self.budget.seconds_until(p.resource, spec.blast_cost)
            return out(
                Verdict.ESCALATE,
                f"blast-radius budget exhausted for {p.resource} "
                f"(needs {spec.blast_cost}, has {self.budget.available(p.resource):.1f})",
                escalation=f"Budget recovers in {wait_s / 60:.0f} min, or a human may override.",
            )

        # 9.
        return out(
            Verdict.ALLOW,
            f"within capability {grant.action_type} on {grant.resource}",
            f"reversible, cost {spec.blast_cost}, "
            f"{self.budget.available(p.resource):.1f} budget remaining",
        )
