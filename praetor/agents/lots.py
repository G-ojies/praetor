"""Reagent lot agent: owns quarantine, and acts on the leading indicator.

The point of this agent is that it does not wait for QC. A lot stored in a unit
that has been out of range for hours is compromised whether or not the control
chart has noticed yet, and quarantine fails closed -- the cost of quarantining a
good lot is a courier run, and the cost of not quarantining a bad one is wrong
results on real patients. So it quarantines on the excursion alone, and raises
its confidence when QC confirms.
"""

from __future__ import annotations

from typing import Any, Iterable

from praetor.agents.base import Agent, Blackboard, Signal
from praetor.common.types import ActionProposal, Severity
from praetor.sim.lab import EventKind


class LotAgent(Agent):
    def __init__(self, agent_id: str = "agent.lots") -> None:
        super().__init__(agent_id)
        self._lots: dict[str, dict] = {}
        self._pending: list[ActionProposal] = []

    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        if event.kind is EventKind.LOT_IN_SERVICE:
            self._lots[event.payload["lot_id"]] = dict(event.payload)
            board.lot_storage[event.payload["lot_id"]] = event.payload["stored_in"]
            return ()
        return ()

    def propose(self, board: Blackboard) -> Iterable[ActionProposal]:
        for excursion in board.of_kind("coldchain.excursion"):
            unit = excursion.subject
            for lot_id, lot in self._lots.items():
                if lot["stored_in"] != unit or lot_id in board.quarantined_lots:
                    continue
                confirmed = any(s.subject == lot_id for s in board.of_kind("qc.rejection"))
                board.quarantined_lots.add(lot_id)
                incident = f"inc_{lot_id.split(':')[-1]}"
                reason = (
                    f"{lot_id} stored in {unit}, which {excursion.summary.split(' for ')[0]}"
                    + (". QC has since rejected on this lot." if confirmed
                       else ". Quarantined ahead of QC confirmation: quarantine fails closed.")
                )
                self._pending.append(self.action(
                    incident, "lot.quarantine", lot_id,
                    confidence=0.92 if confirmed else 0.70,
                    rationale=reason,
                    lot_id=lot_id.split(":")[-1], reason=reason,
                ))
                self._pending.append(self.action(
                    incident, "inventory.reorder", f"order:{lot['analyte']}",
                    confidence=0.9,
                    rationale=f"Replacement stock for quarantined {lot['analyte']} reagent.",
                    lot_family=lot["analyte"], quantity=2,
                ))
        out, self._pending = self._pending, []
        return out
