"""End-to-end fleet tests over the full five-day scenario.

These are the claims the submission actually rests on. Each one is a sentence
someone could reasonably dispute, turned into an assertion.
"""

import pytest

from praetor.agents.coldchain import ColdChainAgent
from praetor.agents.diagnostician import Diagnostician
from praetor.agents.lots import LotAgent
from praetor.agents.qc import QCAgent
from praetor.agents.scribe import Scribe
from praetor.gate.audit import AuditChain, verify_chain
from praetor.gate.policy import PolicyGate
from praetor.orchestrator import Fleet
from praetor.policy_config import default_breaker, default_budget, default_capabilities
from praetor.reasoning import build_offline_reasoner
from praetor.sim.lab import HOUR, T0, LabSim


@pytest.fixture(scope="module")
def run():
    reasoner = build_offline_reasoner()
    fleet = Fleet(
        agents=[ColdChainAgent(), QCAgent(), LotAgent(), Diagnostician(reasoner)],
        gate=PolicyGate(
            capabilities=default_capabilities(),
            budget=default_budget(),
            breaker=default_breaker(),
            chain=AuditChain(),
        ),
    )
    fleet.run(LabSim().run())
    return fleet, reasoner


def hours(fleet, action, verdict="allow"):
    return [int((e.at - T0) / HOUR) for e in fleet.timeline
            if e.action == action and e.verdict == verdict]


# -- the fleet does the useful thing ---------------------------------------

def test_the_compromised_lots_are_quarantined(run):
    fleet, _ = run
    assert "lot:REAG-4471" in fleet.board.quarantined_lots
    assert "lot:REAG-4472" in fleet.board.quarantined_lots


def test_the_lot_in_the_healthy_fridge_is_left_alone(run):
    """Quarantining everything would also 'catch' the problem, and be useless."""
    fleet, _ = run
    assert "lot:REAG-8830" not in fleet.board.quarantined_lots


def test_quarantine_lands_a_full_day_before_qc_ever_rejects(run):
    """The point of the cold-chain agent: act on the leading indicator, not on
    the confirmation. Waiting for QC means a day of reportable-looking results
    that were already wrong."""
    fleet, _ = run
    first_quarantine = min(hours(fleet, "lot.quarantine"))
    first_hold = min(hours(fleet, "results.hold_batch"))
    assert first_quarantine < first_hold - 20, f"q={first_quarantine} hold={first_hold}"


def test_patient_batches_are_held_once_controls_reject(run):
    fleet, _ = run
    assert hours(fleet, "results.hold_batch"), "no batch was ever held"


def test_the_scientist_is_notified(run):
    fleet, _ = run
    assert hours(fleet, "notify.scientist")


# -- and does not do the harmful thing -------------------------------------

def test_the_analyser_is_never_taken_out_of_service(run):
    """The misdiagnosis this scenario exists to catch. Two lots reject on the
    same analyser, which naively reads as an instrument fault -- but both sit in
    the same failed fridge, and pulling a rural clinic's only analyser for a
    cold-chain problem turns a reagent incident into an outage."""
    fleet, _ = run
    assert hours(fleet, "instrument.take_offline") == []
    assert fleet.board.offline_instruments == set()


def test_no_batch_is_ever_released_without_a_human(run):
    fleet, _ = run
    assert hours(fleet, "results.release_batch", "allow") == []
    assert hours(fleet, "results.release_batch", "escalate")


def test_every_fail_open_request_reaches_the_escalation_queue(run):
    fleet, _ = run
    assert fleet.escalations
    assert all(p.action_type == "results.release_batch" for p in fleet.escalations)


# -- the diagnosis ---------------------------------------------------------

def test_the_diagnosis_blames_the_cold_chain_not_the_instrument(run):
    fleet, _ = run
    diagnosis = fleet.board.of_kind("diagnosis")
    assert diagnosis, "the fleet never reached a diagnosis"
    assert diagnosis[0].facts["implicates_instrument"] is False
    assert "fridge-clinic-2" in fleet.board.root_cause


def test_the_diagnosis_is_confident_enough_to_act_on(run):
    fleet, _ = run
    assert fleet.board.root_cause_confidence >= 0.7


# -- the record ------------------------------------------------------------

def test_the_audit_chain_verifies_after_a_full_run(run):
    fleet, _ = run
    result = verify_chain([e.to_dict() for e in fleet.chain.entries], fleet.chain.public_key_hex)
    assert result.ok, result.failures
    assert result.checked == len(fleet.timeline)


def test_the_chain_records_refusals_as_well_as_actions(run):
    fleet, _ = run
    verdicts = {e.payload["decision"]["verdict"] for e in fleet.chain.entries}
    assert {"allow", "escalate"} <= verdicts


def test_the_executor_is_idempotent(run):
    fleet, _ = run
    performed = fleet.executor.performed
    keys = [p["proposal_id"] for p in performed]
    assert len(keys) == len(set(keys))


def test_the_scribe_writes_a_report_naming_the_real_cause(run):
    fleet, reasoner = run
    report = Scribe(reasoner).report(fleet.board, fleet.timeline)
    assert "fridge-clinic-2" in report["title"]
    assert report["recommendations"]


# -- cost ------------------------------------------------------------------

def test_the_frontier_model_is_called_once_not_per_event(run):
    """735 events, one diagnosis. If this ever climbs, the clinic's bill does."""
    _, reasoner = run
    assert sum(1 for task, _ in reasoner.calls if task == "diagnose") == 1
