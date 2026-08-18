"""The fleet's capability grants, in one readable place.

This is the file a lab manager or an assessor should be able to read without
knowing Python. Every line answers: which agent, may do what, to which class of
thing, at what incident severity, on its own.

Note what is absent. No agent is granted `results.release_batch`,
`lot.clear_quarantine` or `instrument.return_to_service` as an autonomous
action -- and even if a grant were added, the gate's fail-open check would still
escalate it. The grants are the first lock; the safety direction is the second.
"""

from __future__ import annotations

from praetor.common.types import Severity
from praetor.gate.budget import BlastRadiusBudget, CircuitBreaker
from praetor.gate.capabilities import Capability, CapabilitySet

GRANTS = [
    # Observation is not a privilege. Every agent sees everything; the gate is
    # what constrains them, not a filtered view of the world.
    Capability("agent.coldchain", "observe.*", "*", Severity.SEV1),
    Capability("agent.qc", "observe.*", "*", Severity.SEV1),
    Capability("agent.lots", "observe.*", "*", Severity.SEV1),
    Capability("agent.diagnostician", "observe.*", "*", Severity.SEV1),

    # Cold chain owns storage units, and may always raise the alarm.
    Capability("agent.coldchain", "notify.scientist", "*", Severity.SEV1),
    Capability("agent.coldchain", "coldchain.setpoint", "unit:*", Severity.SEV2),

    # QC owns control runs, patient batches, and -- on evidence -- analysers.
    Capability("agent.qc", "qc.flag_run", "run:*", Severity.SEV2),
    Capability("agent.qc", "results.hold_batch", "batch:*", Severity.SEV2),
    Capability("agent.qc", "instrument.take_offline", "instr:*", Severity.SEV2),

    # Lots owns reagents and reordering.
    Capability("agent.lots", "lot.quarantine", "lot:*", Severity.SEV2),
    Capability("agent.lots", "inventory.reorder", "order:*", Severity.SEV2),

    # The diagnostician may ask for a release. It will always be escalated;
    # the grant exists so the request is well-formed, not so it is honoured.
    Capability("agent.diagnostician", "results.release_batch", "batch:*", Severity.SEV2),
]


def default_capabilities() -> CapabilitySet:
    return CapabilitySet(GRANTS)


def default_budget(clock=None) -> BlastRadiusBudget:
    """Four tokens per resource per ten minutes.

    Sized so the fleet can hold a batch (2) and flag a run (1) in one pass, but
    cannot hold, quarantine and pull an analyser against the same resource in a
    burst without a human noticing.
    """
    return BlastRadiusBudget(capacity=4, window_s=600, clock=clock)


def default_breaker() -> CircuitBreaker:
    return CircuitBreaker(max_ineffective=3)
