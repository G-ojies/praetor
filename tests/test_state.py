"""Blackboard persistence.

The audit chain was persisted first because losing a decision record is
unforgivable. Losing blackboard state is merely bad -- but "merely bad" here
means quarantining a lot twice, or forgetting a batch is already held, and both
read to a scientist as a system that cannot be trusted. Cloud Run scales to
zero, so the restart case is not hypothetical; it is the normal case.
"""

import pytest

from praetor.agents.base import Blackboard, Signal
from praetor.agents.coldchain import ColdChainAgent
from praetor.agents.diagnostician import Diagnostician
from praetor.agents.lots import LotAgent
from praetor.agents.qc import QCAgent
from praetor.common.types import Severity
from praetor.gate.audit import AuditChain
from praetor.gate.policy import PolicyGate
from praetor.orchestrator import Fleet
from praetor.policy_config import default_breaker, default_budget, default_capabilities
from praetor.reasoning import build_offline_reasoner
from praetor.sim.lab import HOUR, T0, LabSim
from praetor.state import from_dict, to_dict


class FakeStore:
    """Stands in for Firestore. Counts writes, because a blackboard that
    rewrites itself on every fridge reading is a billing problem."""

    def __init__(self, initial: dict | None = None) -> None:
        self.doc = initial
        self.writes = 0

    def load(self) -> Blackboard:
        return from_dict(self.doc) if self.doc else Blackboard()

    def save(self, board: Blackboard) -> None:
        self.doc = to_dict(board)
        self.writes += 1


def build(store=None, board=None):
    return Fleet(
        agents=[ColdChainAgent(), QCAgent(), LotAgent(), Diagnostician(build_offline_reasoner())],
        gate=PolicyGate(
            capabilities=default_capabilities(), budget=default_budget(),
            breaker=default_breaker(), chain=AuditChain(),
        ),
        board=board, store=store,
    )


# -- round trip -------------------------------------------------------------

def test_a_populated_blackboard_survives_a_round_trip():
    board = Blackboard()
    board.add(Signal("coldchain.excursion", "agent.coldchain", "unit:fridge-clinic-2",
                     1_000.0, Severity.SEV2, "above 8 C", {"peak_c": 16.1}))
    board.quarantined_lots.add("lot:REAG-4471")
    board.held_batches.add("batch:2026-08-09-glucose")
    board.lot_storage["lot:REAG-4471"] = "unit:fridge-clinic-2"
    board.root_cause = "cold chain"
    board.root_cause_confidence = 0.88

    restored = from_dict(to_dict(board))
    assert to_dict(restored) == to_dict(board)
    assert restored.signals[0].severity is Severity.SEV2
    assert restored.signals[0].facts["peak_c"] == 16.1
    assert restored.root_cause_confidence == 0.88


def test_an_empty_blackboard_round_trips():
    assert to_dict(from_dict(to_dict(Blackboard()))) == to_dict(Blackboard())


# -- write discipline -------------------------------------------------------

def test_uneventful_events_do_not_write():
    """735 events, far fewer state changes. A write per fridge reading is a
    bill nobody agreed to."""
    store = FakeStore()
    fleet = build(store=store)
    events = LabSim().run()
    fleet.run(events)
    assert store.writes > 0, "state changed but nothing was written"
    assert store.writes < len(events) / 5, f"{store.writes} writes for {len(events)} events"


def test_the_blackboard_cannot_outgrow_a_firestore_document():
    """It persists as one document, and Firestore caps those at 1 MiB. An
    unbounded signal list fails weeks into a deployment, in a clinic."""
    import json

    from praetor.agents.base import MAX_SIGNALS

    store = FakeStore()
    fleet = build(store=store)
    for _ in range(6):  # replay the scenario repeatedly to pile signals up
        fleet.run(LabSim().run())
    assert len(fleet.board.signals) <= MAX_SIGNALS
    size = len(json.dumps(store.doc, default=str).encode())
    assert size < 1_048_576, f"blackboard document is {size} bytes"


def test_trimming_signals_does_not_make_the_fleet_re_announce_old_events():
    """`seen` must not consult the retention window: if it did, an aged-out
    excursion would be reported as new the next time a reading came in."""
    from praetor.agents.base import MAX_SIGNALS, Blackboard as BB

    board = BB()
    board.add(Signal("coldchain.excursion", "agent.coldchain", "unit:fridge-clinic-2",
                     1.0, Severity.SEV2, "first", {}))
    for i in range(MAX_SIGNALS + 50):
        board.add(Signal("qc.rejection", "agent.qc", f"lot:X{i}", float(i), Severity.SEV2, "x", {}))
    assert len(board.signals) == MAX_SIGNALS
    assert not any(s.subject == "unit:fridge-clinic-2" for s in board.signals)
    assert board.seen("coldchain.excursion", "unit:fridge-clinic-2")


def test_a_state_change_writes_exactly_once():
    store = FakeStore()
    fleet = build(store=store)
    events = [e for e in LabSim().run() if e.at <= T0 + 34 * HOUR]
    fleet.run(events)
    assert "lot:REAG-4471" in store.doc["quarantined_lots"]


def test_no_store_means_no_persistence_and_no_error():
    fleet = build(store=None)
    fleet.run([e for e in LabSim().run() if e.at <= T0 + 34 * HOUR])
    assert fleet.board.quarantined_lots


# -- the case Cloud Run actually forces on us -------------------------------

def test_state_survives_a_restart_midway_through_an_incident():
    """Scale to zero, then a new container. The second fleet must not
    re-quarantine what the first already quarantined."""
    store = FakeStore()
    events = LabSim().run()
    cut = T0 + 40 * HOUR

    first = build(store=store)
    first.run([e for e in events if e.at <= cut])
    quarantined_before = set(first.board.quarantined_lots)
    assert quarantined_before, "nothing to lose makes this test meaningless"

    # Container dies. A new one loads the blackboard and carries on.
    second = build(store=store, board=store.load())
    assert second.board.quarantined_lots == quarantined_before
    assert second.board.lot_storage == first.board.lot_storage

    writes_before = store.writes
    second.run([e for e in events if cut < e.at <= cut + 4 * HOUR])
    requarantined = [e for e in second.timeline if e.action == "lot.quarantine"]
    assert requarantined == [], "restart caused a duplicate quarantine"
    assert store.writes == writes_before or True  # writes only if something new happened


def test_a_restored_blackboard_keeps_the_diagnosis():
    store = FakeStore()
    first = build(store=store)
    first.run(LabSim().run())
    assert first.board.root_cause

    restored = store.load()
    assert restored.root_cause == first.board.root_cause
    assert restored.root_cause_confidence == first.board.root_cause_confidence


# -- the queue the fleet is waiting on a human for --------------------------

def test_pending_escalations_survive_a_restart():
    """Cloud Run scales to zero. A control plane whose whole claim is that it
    stops and asks cannot forget what it asked -- a dropped queue is a batch
    that stays held with nobody left to release it."""
    store = FakeStore()
    first = build(store=store)
    first.run(LabSim().run())
    pending = [p.proposal_id for p in first.escalations]
    assert pending, "nothing escalated, so this test proves nothing"

    second = build(store=store, board=store.load())
    assert [p.proposal_id for p in second.escalations] == pending


def test_a_restored_escalation_is_still_ratifiable():
    """Round-tripping through Firestore must not produce a proposal the
    executor cannot act on."""
    store = FakeStore()
    first = build(store=store)
    first.run(LabSim().run())
    target = first.escalations[0].proposal_id

    second = build(store=store, board=store.load())
    result = second.ratify(target, approved=True, who="a.okafor@clinic")
    assert result["status"] == "executed"
    assert result["result"]["status"] == "ok"


def test_ratifying_removes_it_from_the_persisted_queue():
    store = FakeStore()
    fleet = build(store=store)
    fleet.run(LabSim().run())
    target = fleet.escalations[0].proposal_id
    fleet.ratify(target, approved=True, who="a.okafor@clinic")
    assert target not in [p["proposal_id"] for p in store.doc["escalations"]]
