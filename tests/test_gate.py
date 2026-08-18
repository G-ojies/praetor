"""Behavioural tests for the policy gate.

Each test names the property it defends, because together they are the argument
that a fleet of language models is safe to point at a working diagnostic lab.
"""

import pytest

from praetor.common.types import ActionProposal, SafetyDirection, Severity, Verdict
from praetor.gate.audit import AuditChain, verify_chain
from praetor.gate.budget import BlastRadiusBudget, CircuitBreaker
from praetor.gate.capabilities import Capability, CapabilitySet, PolicyConfigError
from praetor.gate.policy import GateConfig, PolicyGate

QC = "agent.qc"
COLDCHAIN = "agent.coldchain"
LOTS = "agent.lots"
BATCH = "batch:2026-08-18-hematology"
LOT = "lot:REAG-4471"
INSTR = "instr:cobas-c311-a"
FRIDGE = "unit:fridge-clinic-2"


@pytest.fixture
def clock():
    return [1_000_000.0]


@pytest.fixture
def gate(clock):
    caps = CapabilitySet(
        [
            # Every agent may look at anything. Observation is not a privilege.
            Capability(QC, "observe.*", "*", Severity.SEV1),
            Capability(COLDCHAIN, "observe.*", "*", Severity.SEV1),
            Capability(LOTS, "observe.*", "*", Severity.SEV1),
            # The QC analyst may flag runs and hold batches, but owns no lots.
            Capability(QC, "qc.flag_run", "run:*", Severity.SEV2),
            Capability(QC, "results.hold_batch", "batch:*", Severity.SEV2),
            Capability(QC, "instrument.take_offline", "instr:*", Severity.SEV2),
            Capability(QC, "results.release_batch", "batch:*", Severity.SEV2),
            # The lot agent quarantines reagents and nothing else.
            Capability(LOTS, "lot.quarantine", "lot:*", Severity.SEV2),
            Capability(LOTS, "lot.clear_quarantine", "lot:*", Severity.SEV2),
            # Cold chain owns the fridges.
            Capability(COLDCHAIN, "coldchain.setpoint", "unit:*", Severity.SEV2),
            Capability(COLDCHAIN, "notify.scientist", "*", Severity.SEV1),
        ]
    )
    return PolicyGate(
        capabilities=caps,
        budget=BlastRadiusBudget(capacity=4, window_s=600, clock=lambda: clock[0]),
        breaker=CircuitBreaker(max_ineffective=3),
        chain=AuditChain(clock=lambda: clock[0]),
        config=GateConfig(),
    )


def propose(action_type, *, agent=QC, resource=BATCH, confidence=0.9, **params):
    return ActionProposal(
        agent_id=agent,
        incident_id="inc_test",
        action_type=action_type,
        resource=resource,
        params=params,
        confidence=confidence,
        rationale="test",
    )


HOLD = dict(batch_id="2026-08-18-hematology", reason="2-2s Westgard violation")


# -- the fleet may move toward safety on its own ----------------------------

def test_holding_a_batch_is_allowed_unattended(gate):
    d = gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
    assert d.verdict is Verdict.ALLOW


def test_quarantining_a_reagent_lot_is_allowed_unattended(gate):
    d = gate.evaluate(
        propose("lot.quarantine", agent=LOTS, resource=LOT, lot_id="REAG-4471", reason="lot-to-lot bias"),
        Severity.SEV2,
    )
    assert d.verdict is Verdict.ALLOW


def test_pulling_a_drifting_analyser_is_allowed_unattended(gate):
    d = gate.evaluate(
        propose("instrument.take_offline", resource=INSTR, instrument="cobas-c311-a", reason="calibration drift"),
        Severity.SEV2,
    )
    assert d.verdict is Verdict.ALLOW


# -- and never away from it, however confident ------------------------------

def test_releasing_results_always_escalates(gate):
    d = gate.evaluate(propose("results.release_batch", confidence=1.0, batch_id="2026-08-18-hematology"), Severity.SEV4)
    assert d.verdict is Verdict.ESCALATE
    assert "loosens" in d.reasons[0]


def test_clearing_a_quarantine_always_escalates(gate):
    d = gate.evaluate(
        propose("lot.clear_quarantine", agent=LOTS, resource=LOT, confidence=1.0, lot_id="REAG-4471"),
        Severity.SEV4,
    )
    assert d.verdict is Verdict.ESCALATE


def test_no_fail_open_action_is_reachable_by_any_grant(gate):
    """The strong form: no capability, severity, budget or confidence
    combination exists that lets the fleet loosen the lab unattended."""
    from praetor.common.types import ACTION_CATALOGUE

    loosening = [a for a, s in ACTION_CATALOGUE.items() if s.safety_direction is SafetyDirection.LOOSENS]
    assert loosening, "catalogue must contain fail-open actions for this to mean anything"
    for action in loosening:
        spec = ACTION_CATALOGUE[action]
        agent, resource = (LOTS, LOT) if action.startswith("lot.") else (QC, BATCH)
        p = propose(action, agent=agent, resource=resource, confidence=1.0, **{k: "x" for k in spec.params})
        for sev in (Severity.SEV1, Severity.SEV2, Severity.SEV3, Severity.SEV4):
            assert gate.evaluate(p, sev).verdict is not Verdict.ALLOW, action


# -- the closed catalogue ---------------------------------------------------

def test_action_outside_the_catalogue_is_denied(gate):
    d = gate.evaluate(propose("lims.sql", query="UPDATE results SET status='released'"), Severity.SEV2)
    assert d.verdict is Verdict.DENY
    assert "not in the catalogue" in d.reasons[0]


def test_missing_required_parameters_are_denied(gate):
    d = gate.evaluate(propose("results.hold_batch", batch_id="2026-08-18-hematology"), Severity.SEV2)
    assert d.verdict is Verdict.DENY
    assert "reason" in d.reasons[0]


def test_a_grant_naming_an_unknown_action_is_rejected_at_load_time():
    with pytest.raises(PolicyConfigError):
        Capability(QC, "lims.sql", "*")


# -- capability scoping -----------------------------------------------------

def test_qc_agent_cannot_touch_reagent_lots(gate):
    d = gate.evaluate(
        propose("lot.quarantine", agent=QC, resource=LOT, lot_id="REAG-4471", reason="hunch"),
        Severity.SEV2,
    )
    assert d.verdict is Verdict.DENY
    assert "no capability" in d.reasons[0]


def test_coldchain_agent_cannot_hold_patient_results(gate):
    d = gate.evaluate(propose("results.hold_batch", agent=COLDCHAIN, **HOLD), Severity.SEV2)
    assert d.verdict is Verdict.DENY


def test_every_agent_may_observe_anything(gate):
    for agent in (QC, COLDCHAIN, LOTS):
        d = gate.evaluate(propose("observe.telemetry", agent=agent, resource=FRIDGE, window_s=300), Severity.SEV1)
        assert d.verdict is Verdict.ALLOW, agent


def test_observation_never_charges_the_budget(gate):
    for _ in range(20):
        gate.evaluate(propose("observe.telemetry", resource=FRIDGE, window_s=60), Severity.SEV2)
    assert gate.budget.available(FRIDGE) == 4.0


# -- escalation, not denial -------------------------------------------------

def test_incident_more_severe_than_the_grant_ceiling_escalates(gate):
    d = gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV1)
    assert d.verdict is Verdict.ESCALATE
    assert "SEV1" in d.reasons[0]


def test_low_confidence_diagnosis_escalates(gate):
    d = gate.evaluate(propose("results.hold_batch", confidence=0.31, **HOLD), Severity.SEV2)
    assert d.verdict is Verdict.ESCALATE
    assert "confidence" in d.reasons[0]


def test_exhausted_blast_radius_escalates_rather_than_denying(gate):
    gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)  # 4 -> 2
    gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)  # 2 -> 0
    d = gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
    assert d.verdict is Verdict.ESCALATE
    assert "budget exhausted" in d.reasons[0]


def test_blast_radius_budget_refills_over_time(gate, clock):
    for _ in range(2):
        gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
    assert gate.budget.available(BATCH) == 0.0
    clock[0] += 600
    assert gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2).verdict is Verdict.ALLOW


def test_denied_actions_do_not_charge_the_budget(gate):
    gate.evaluate(propose("lot.quarantine", agent=QC, resource=LOT, lot_id="x", reason="y"), Severity.SEV2)
    assert gate.budget.available(LOT) == 4.0


def test_escalated_actions_do_not_charge_the_budget(gate):
    gate.evaluate(propose("results.release_batch", batch_id="2026-08-18-hematology"), Severity.SEV2)
    assert gate.budget.available(BATCH) == 4.0


# -- the circuit breaker ----------------------------------------------------

def test_fleet_halts_after_repeated_ineffective_mutations(gate, clock):
    for _ in range(3):
        gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
        clock[0] += 300  # refill the budget so the breaker is what trips
    d = gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
    assert d.verdict is Verdict.HALT


def test_bookkeeping_still_passes_once_the_fleet_has_stood_down(gate, clock):
    """Standing down means the fleet stops trying to fix the incident, not that
    it goes silent on the scientist who now has to handle it."""
    for _ in range(3):
        gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
        clock[0] += 300
    assert gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2).verdict is Verdict.HALT
    flag = gate.evaluate(propose("qc.flag_run", resource="run:r1", run_id="r1", reason="drift"), Severity.SEV2)
    assert flag.verdict is Verdict.ALLOW
    note = gate.evaluate(propose("observe.annotate", note="handed to a human"), Severity.SEV2)
    assert note.verdict is Verdict.ALLOW


def test_resolving_an_incident_clears_the_breaker(gate, clock):
    for _ in range(3):
        gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
        clock[0] += 300
    gate.breaker.record_resolution("inc_test")
    assert gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2).verdict is Verdict.ALLOW


def test_bookkeeping_never_counts_toward_the_breaker(gate, clock):
    """Flagging fifty runs is not the fleet thrashing; it is the fleet working."""
    for i in range(50):
        gate.evaluate(propose("qc.flag_run", resource=f"run:r{i}", run_id=f"r{i}", reason="drift"), Severity.SEV2)
    assert gate.breaker.count("inc_test") == 0


# -- the audit chain --------------------------------------------------------

def test_every_decision_is_recorded_including_denials(gate):
    gate.evaluate(propose("results.hold_batch", **HOLD), Severity.SEV2)
    gate.evaluate(propose("lims.sql", query="x"), Severity.SEV2)
    gate.evaluate(propose("results.release_batch", batch_id="b"), Severity.SEV2)
    assert [e.payload["decision"]["verdict"] for e in gate.chain.entries] == ["allow", "deny", "escalate"]


def test_audit_chain_verifies_against_the_public_key(gate):
    for _ in range(3):
        gate.evaluate(propose("observe.annotate", note="n"), Severity.SEV2)
    result = verify_chain([e.to_dict() for e in gate.chain.entries], gate.chain.public_key_hex)
    assert result.ok and result.checked == 3


def test_altering_a_logged_decision_breaks_verification(gate):
    gate.evaluate(propose("results.release_batch", batch_id="b"), Severity.SEV2)
    gate.evaluate(propose("observe.annotate", note="n"), Severity.SEV2)
    raw = [e.to_dict() for e in gate.chain.entries]
    raw[0]["payload"]["decision"]["verdict"] = "allow"  # rewrite history
    result = verify_chain(raw, gate.chain.public_key_hex)
    assert not result.ok and "seq 0" in result.failures[0]


def test_deleting_an_entry_breaks_the_chain(gate):
    for _ in range(3):
        gate.evaluate(propose("observe.annotate", note="n"), Severity.SEV2)
    raw = [e.to_dict() for e in gate.chain.entries]
    del raw[1]
    result = verify_chain(raw, gate.chain.public_key_hex)
    assert not result.ok and "broken link" in result.failures[0]
