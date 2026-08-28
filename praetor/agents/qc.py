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

import bisect
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
        self._rejected: set[str] = set()

    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        if event.kind is not EventKind.QC_RUN:
            return ()
        p = event.payload
        key = (p["lot_id"], p["analyte"], p["level"])
        point = QCPoint(p["run_id"], p["analyte"], p["level"], p["value"],
                        p["target_mean"], p["target_sd"], event.at)

        # Inserted in time order, not arrival order. Pub/Sub does not guarantee
        # ordering, and Westgard is entirely a statement about *sequence*: 2-2s
        # means two consecutive results, 10x means ten on one side in a row. Fed
        # a shuffled series the rules fire on runs that never happened: a 10x
        # rejection four hours into a series that is only four hours old.
        #
        # Fixing it here rather than by turning on ordered delivery, because an
        # agent that is only correct while its transport preserves order is an
        # agent with a hidden precondition. A replay, a retry or a dead-letter
        # redrive would all break it again.
        series = self._series[key]
        bisect.insort(series, point, key=lambda q: q.at)

        # Recompute the whole series, in time order, on every arrival.
        #
        # Evaluating only the newest point is wrong under out-of-order
        # delivery, and not subtly: ten points that are consecutive in what has
        # arrived so far may have gaps in reality, so 10x fires on a run that
        # never happened. Inserting in time order is necessary but not
        # sufficient, because the rules were already applied to the partial
        # series before the missing points landed.
        #
        # So the disposition of every point in the series is derived fresh from
        # everything currently held. Late arrivals correct earlier verdicts
        # instead of compounding them, and the answer converges on the same
        # result the ordered stream would have produced. O(n^2) in a series
        # bounded at a few dozen control results is not worth optimising.
        # Fast path: the point arrived in order and is the newest in its
        # series, so nothing earlier can have changed and only the tail needs
        # evaluating. This is the overwhelmingly common case, and paying the
        # full recomputation for it would make every ingest quadratic for the
        # sake of the rare late delivery.
        if series[-1] is point:
            peers = [q for k, o in self._series.items()
                     if k[0] == key[0] and k[1] == key[1] and k[2] != key[2]
                     for q in o if q.run_id == point.run_id]
            violations = evaluate(series, across_levels=peers)
            disposition = verdict(violations)
            fresh = None
        else:
            fresh = self._reevaluate(key, series)
            disposition, violations = fresh[point.run_id]

        # Every control result is retained, not only the rejecting ones. The
        # point of the chart is the long stretch where nothing fired.
        board.record_qc({
            "at": event.at, "run_id": p["run_id"], "lot_id": p["lot_id"],
            "analyte": p["analyte"], "level": p["level"],
            "value": round(p["value"], 4), "z": round(point.z, 3),
            "target_mean": p["target_mean"], "target_sd": p["target_sd"],
            "disposition": disposition.value,
            "rules": [v.rule for v in violations],
        })
        # Only a late arrival can invalidate verdicts already on the board.
        if fresh is not None:
            self._sync_dispositions(board, key, series, fresh)

        if disposition is not RuleAction.REJECT:
            return ()
        if point.run_id in self._rejected:
            return ()          # already reported; a later arrival re-confirmed it
        self._rejected.add(point.run_id)

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
        # count reads that as an instrument fault, taking a clinic's only
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

    # -- evaluation ---------------------------------------------------------
    def _reevaluate(self, key, series) -> dict[str, tuple]:
        """Disposition for every run in this series, from the full ordered set."""
        lot, analyte, level = key
        out: dict[str, tuple] = {}
        for i in range(len(series)):
            window = series[: i + 1]
            run_id = series[i].run_id
            # R-4s is the only rule that spans control levels, and only within
            # one run, so the peers are the other levels of this same run.
            peers = [q for k, o in self._series.items()
                     if k[0] == lot and k[1] == analyte and k[2] != level
                     for q in o if q.run_id == run_id]
            violations = evaluate(window, across_levels=peers)
            out[run_id] = (verdict(violations), violations)
        return out

    def _sync_dispositions(self, board: Blackboard, key, series, fresh) -> None:
        """Write the corrected verdicts back onto the recorded control points,
        so the chart shows what the rules currently say rather than what they
        said before the late points arrived."""
        lot, analyte, level = key
        by_run = {q.run_id for q in series}
        for point in board.qc_points:
            if (point["lot_id"] == lot and point["analyte"] == analyte
                    and point["level"] == level and point["run_id"] in by_run):
                disposition, violations = fresh[point["run_id"]]
                point["disposition"] = disposition.value
                point["rules"] = [v.rule for v in violations]

    def propose(self, board: Blackboard) -> Iterable[ActionProposal]:
        out, self._pending = self._pending, []
        return out
