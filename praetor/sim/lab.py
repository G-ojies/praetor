"""A deterministic simulation of a four-site rural clinic laboratory.

Real instrument telemetry is not something a hackathon can obtain, so Praetor
develops and demos against this. It is a simulator, labelled as one, and the
agents cannot tell the difference: they consume the same event shapes they would
consume from a LIS feed and a cold-chain sensor gateway.

What it is *not* is a random walk with a bug bolted on. The scenario models a
causal chain that actually happens in under-resourced labs, and models it with
the timing that makes it hard to spot:

    a fridge compressor degrades overnight at one clinic
      -> the glucose reagent stored in it loses potency slowly
        -> control values drift downward, staying inside 2s for a day and a half
          -> only 10x, then 4-1s, then 2-2s fire, in that order

The important property is that the *instrument is fine*. A fleet that reaches
for `instrument.take_offline` here has misdiagnosed, taken a clinic's only
analyser out of service, and made the outage worse. Getting to the reagent lot
instead is the whole test.

Everything is seeded, so the demo runs identically every time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

HOUR = 3600.0
T0 = 1_786_000_000.0  # fixed epoch so runs are reproducible


class EventKind(Enum):
    QC_RUN = "qc_run"
    COLDCHAIN = "coldchain"
    LOT_IN_SERVICE = "lot_in_service"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    at: float
    site: str
    payload: dict

    def __repr__(self) -> str:  # keeps the demo timeline readable
        return f"<{self.kind.value} {self.site} {self.payload}>"


@dataclass
class Analyte:
    name: str
    target_mean: float
    target_sd: float
    unit: str


@dataclass
class ReagentLot:
    lot_id: str
    analyte: str
    stored_in: str  # cold-chain unit id
    in_service_at: float
    # Fraction of nominal potency. Degradation biases results low.
    potency: float = 1.0


@dataclass
class ColdChainUnit:
    unit_id: str
    site: str
    setpoint_c: float = 4.0
    # Hour at which the compressor starts failing; None means healthy.
    fails_at: float | None = None
    drift_c_per_hour: float = 0.0


GLUCOSE = Analyte("glucose", 5.4, 0.18, "mmol/L")
SODIUM = Analyte("sodium", 140.0, 1.4, "mmol/L")
ANALYTES = {a.name: a for a in (GLUCOSE, SODIUM)}


@dataclass
class LabSim:
    """Generates the event stream for one scenario run."""

    seed: int = 20260831
    hours: int = 120
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    # -- the scenario -------------------------------------------------------
    def scenario(self) -> tuple[list[ColdChainUnit], list[ReagentLot]]:
        units = [
            ColdChainUnit("unit:fridge-clinic-1", "site:clinic-1"),
            # The failing one. Slow: 0.35 C/hour, so it looks like nothing for
            # most of a day before it is unambiguously out of range.
            ColdChainUnit("unit:fridge-clinic-2", "site:clinic-2", fails_at=T0 + 18 * HOUR, drift_c_per_hour=0.35),
            ColdChainUnit("unit:fridge-clinic-3", "site:clinic-3"),
            ColdChainUnit("unit:fridge-clinic-4", "site:clinic-4"),
        ]
        lots = [
            ReagentLot("lot:REAG-4471", "glucose", "unit:fridge-clinic-2", T0 + 12 * HOUR),
            ReagentLot("lot:REAG-4472", "sodium", "unit:fridge-clinic-2", T0 + 12 * HOUR),
            ReagentLot("lot:REAG-8830", "glucose", "unit:fridge-clinic-1", T0),
        ]
        return units, lots

    def temperature(self, unit: ColdChainUnit, at: float) -> float:
        noise = self._rng.gauss(0, 0.12)
        if unit.fails_at is None or at < unit.fails_at:
            return unit.setpoint_c + noise
        elapsed_h = (at - unit.fails_at) / HOUR
        # Rises toward ambient and plateaus; it is a failing compressor, not an
        # open door, so it does not shoot to 25 C in an hour.
        rise = min(12.0, elapsed_h * unit.drift_c_per_hour)
        return unit.setpoint_c + rise + noise

    def potency(self, lot: ReagentLot, unit: ColdChainUnit, at: float) -> float:
        """Cumulative degradation above 8 C. Below that, none."""
        if at < lot.in_service_at:
            return 1.0
        loss = 0.0
        t = lot.in_service_at
        while t < at:
            temp = unit.setpoint_c if unit.fails_at is None or t < unit.fails_at else self.temperature(unit, t)
            if temp > 8.0:
                # Calibrated so a fully-excursed lot loses ~10% potency over
                # three days: roughly 3 SD on glucose. Enough to be clinically
                # wrong, small enough that no single control result screams.
                loss += (temp - 8.0) * 0.00018
            t += HOUR
        return max(0.88, 1.0 - loss)

    # -- event generation ---------------------------------------------------
    def run(self) -> list[Event]:
        units, lots = self.scenario()
        by_id = {u.unit_id: u for u in units}
        events: list[Event] = []

        for lot in lots:
            events.append(Event(EventKind.LOT_IN_SERVICE, lot.in_service_at,
                                by_id[lot.stored_in].site,
                                {"lot_id": lot.lot_id, "analyte": lot.analyte,
                                 "stored_in": lot.stored_in}))

        for h in range(self.hours):
            at = T0 + h * HOUR

            # Cold chain: one reading per unit per hour. High volume, low value
            # individually: this is the stream Gemma triages before Gemini
            # ever sees it.
            for u in units:
                events.append(Event(EventKind.COLDCHAIN, at, u.site,
                                    {"unit": u.unit_id, "celsius": round(self.temperature(u, at), 2),
                                     "setpoint_c": u.setpoint_c}))

            # QC: three levels per analyte, twice a shift (every 4h).
            if h % 4 == 0:
                for lot in lots:
                    analyte = ANALYTES[lot.analyte]
                    unit = by_id[lot.stored_in]
                    if at < lot.in_service_at:
                        continue
                    pot = self.potency(lot, unit, at)
                    # Lost potency reads low, proportional to the target mean.
                    bias = (pot - 1.0) * analyte.target_mean
                    run_id = f"qc_{int(at)}_{lot.analyte}_{lot.lot_id[-4:]}"
                    for level in (1, 2, 3):
                        value = analyte.target_mean + bias + self._rng.gauss(0, analyte.target_sd * 0.55)
                        events.append(Event(EventKind.QC_RUN, at, unit.site, {
                            "run_id": run_id, "analyte": lot.analyte, "level": level,
                            "value": round(value, 3), "target_mean": analyte.target_mean,
                            "target_sd": analyte.target_sd, "lot_id": lot.lot_id,
                            "instrument": f"instr:cobas-c311-{unit.site[-1]}",
                        }))

        events.sort(key=lambda e: (e.at, e.kind.value))
        return events
