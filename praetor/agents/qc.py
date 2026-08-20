"""QC agent: Westgard multirules over control results.

The judgement encoded here that a naive implementation gets wrong: a rejection
does not mean the analyser is broken. A reagent lot can drift while the
instrument reading it is in perfect health, and pulling the only analyser in a
rural clinic because one lot went bad turns a reagent problem into an outage.

So `instrument.take_offline` is gated on evidence that implicates the instrument
rather than the reagent: two or more *distinct lots* rejecting on the same
instrument. With a single lot failing, this agent holds results and flags runs
and deliberately leaves the analyser running.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from praetor.agents.base import Agent, Blackboard, Signal
from praetor.common.types import ActionProposal, Severity
from praetor.sim.lab import EventKind
from praetor.sim.westgard import QCPoint, RuleAction, evaluate, verdict

# Distinct lots that must reject on one instrument before we blame the box.
LOTS_IMPLICATING_INSTRUMENT = 2


def batch_id(at: float, analyte: str) -> str:
    day = datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{day}-{analyte}"


class QCAgent(Agent):
    def __init__(self, agent_id: str = "agent.qc") -> None:
        super().__init__(agent_id)
        # (lot, analyte, level) -> trailing control series
        self._series: dict[tuple[str, str, int], list[QCPoint]] = defaultdict(list)
        # instrument -> lots currently rejecting on it
        self._rejecting: dict[str, set[str]] = defaultdict(set)
        self._pending: list[ActionProposal] = []
        self._flagged: set[str] = set()

    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        if event.kind is not EventKind.QC_RUN:
            return ()
        p = event.payload
        key = (p["lot_id"], p["analyte"], p["level"])
        point = QCPoint(p["run_id"], p["analyte"], p["level"], p["value"],
                        p["target_mean"], p["target_sd"], event.at)
        self._series[key].append(point)

        peers = [s[-1] for k, s in self._series.items()
                 if k[0] == p["lot_id"] and k[1] == p["analyte"] and k[2] != p["level"] and s]
        violations = evaluate(self._series[key], across_levels=peers)
        disposition = verdict(violations)
        if disposition is not RuleAction.REJECT:
            return ()

        lot, instrument = p["lot_id"], p["instrument"]
        batch = f"batch:{batch_id(event.at, p['analyte'])}"
        self._rejecting[instrument].add(lot)

        signal = board.add(Signal(
            kind="qc.rejection",
            source=self.agent_id,
            subject=lot,
            at=event.at,
            severity=Severity.SEV2,
            summary=(f"{p['analyte']} L{p['level']} rejected on {lot}: "
                     + ", ".join(v.rule for v in violations)),
            facts={"lot_id": lot, "analyte": p["analyte"], "level": p["level"],
                   "instrument": instrument, "batch": batch, "run_id": p["run_id"],
                   "z": round(point.z, 2), "site": event.site,
                   "rules": [v.rule for v in violations],
                   "error_types": sorted({v.error_type.value for v in violations})},
        ))

        incident = f"inc_{lot.split(':')[-1]}"
        # Scoped to the run, not the lot: three control levels rejecting is one
        # unreportable run, and budgeting it against the lot would starve the
        # quarantine the lot agent needs to make.
        if p["run_id"] not in self._flagged:
            self._flagged.add(p["run_id"])
            self._pending.append(self.action(
                incident, "qc.flag_run", f"run:{p['run_id']}",
                confidence=1.0,
                rationale="Westgard rejection is deterministic; the run is not reportable.",
                run_id=p["run_id"], reason=signal.summary,
            ))
        if batch not in board.held_batches:
            board.held_batches.add(batch)
            self._pending.append(self.action(
                incident, "results.hold_batch", batch,
                confidence=0.95,
                rationale=("Controls failed on this analyte; patient results in the "
                           "batch cannot be released until the cause is known."),
                batch_id=batch_id(event.at, p["analyte"]), reason=signal.summary,
            ))

        # Only now, and only on evidence that points at the box rather than the
        # reagent, does the analyser come out of service.
        #
        # Counting distinct rejecting lots is not enough. Two lots stored in the
        # same failing fridge will both reject on the same analyser, and a naive
        # count reads that as an instrument fault -- taking a clinic's only
        # analyser out of service for a cold-chain problem. So lots whose
        # storage unit is under an active excursion are set aside first; the
        # instrument is implicated only by what is left unexplained.
        # A lot counts against the instrument only when we positively know
        # where it was stored *and* that store was healthy. Unknown provenance
        # must not implicate the analyser: `lot_storage.get(l)` returning None
        # for a lot whose registration event we never saw would otherwise read
        # as "no cold-chain explanation", and the fleet would pull a clinic's
        # only analyser because of a gap in its own records. Missing data has
        # to fail closed, toward leaving the instrument in service.
        excursed = {s.subject for s in board.of_kind("coldchain.excursion")}
        unexplained = set()
        unknown_provenance = set()
        for lot in self._rejecting[instrument]:
            stored_in = board.lot_storage.get(lot)
            if stored_in is None:
                unknown_provenance.add(lot)
            elif stored_in not in excursed:
                unexplained.add(lot)
        if unknown_provenance and instrument not in board.offline_instruments:
            # Say so rather than failing silently: a scientist can supply the
            # storage location, and the fleet should not look like it simply
            # ignored a repeatedly failing analyser.
            board.add(Signal(
                kind="provenance.unknown",
                source=self.agent_id,
                subject=instrument,
                at=event.at,
                severity=Severity.SEV3,
                summary=(f"{instrument}: storage location unknown for "
                         f"{', '.join(sorted(unknown_provenance))}; cannot rule out a "
                         f"cold-chain cause, so the analyser stays in service"),
                facts={"instrument": instrument, "lots": sorted(unknown_provenance)},
            ))

        if (len(unexplained) >= LOTS_IMPLICATING_INSTRUMENT
                and instrument not in board.offline_instruments):
            board.offline_instruments.add(instrument)
            self._pending.append(self.action(
                incident, "instrument.take_offline", instrument,
                confidence=0.85,
                rationale=(f"{len(unexplained)} distinct lots rejecting on {instrument} with no "
                           f"cold-chain explanation; the common factor is the analyser."),
                instrument=instrument.split(":")[-1], reason="multi-lot QC failure",
            ))
        return (signal,)

    def propose(self, board: Blackboard) -> Iterable[ActionProposal]:
        out, self._pending = self._pending, []
        return out
