"""Scenario tests for the lab simulator.

The demo, the video and every agent evaluation depend on this scenario behaving
exactly as described. If a tuning change silently turns the subtle drift into an
obvious one, the whole premise stops holding, so the properties are pinned here.
"""

import pytest

from praetor.sim.lab import HOUR, T0, EventKind, LabSim
from praetor.sim.westgard import QCPoint, RuleAction, evaluate, verdict


@pytest.fixture(scope="module")
def events():
    return LabSim().run()


def at_hour(e):
    return int((e.at - T0) / HOUR)


def temps(events, unit_suffix):
    return [
        (at_hour(e), e.payload["celsius"])
        for e in events
        if e.kind is EventKind.COLDCHAIN and e.payload["unit"].endswith(unit_suffix)
    ]


def rejections(events, lot_id):
    """Hour of each Westgard rejection for one lot, in order."""
    qc = [e for e in events if e.kind is EventKind.QC_RUN and e.payload["lot_id"] == lot_id]
    series, out = {1: [], 2: [], 3: []}, []
    for e in qc:
        p = e.payload
        series[p["level"]].append(
            QCPoint(p["run_id"], p["analyte"], p["level"], p["value"], p["target_mean"], p["target_sd"], e.at)
        )
        others = [s[-1] for lv, s in series.items() if lv != p["level"] and s]
        v = evaluate(series[p["level"]], across_levels=others)
        if verdict(v) is RuleAction.REJECT:
            out.append((at_hour(e), tuple(sorted({x.rule for x in v}))))
    return out


# -- determinism ------------------------------------------------------------

def test_the_scenario_is_reproducible():
    a, b = LabSim().run(), LabSim().run()
    assert [(e.kind, e.at, e.payload) for e in a] == [(e.kind, e.at, e.payload) for e in b]


# -- the cold chain ---------------------------------------------------------

def test_the_failing_fridge_is_healthy_before_the_compressor_goes():
    early = [c for h, c in temps(LabSim().run(), "clinic-2") if h < 18]
    assert max(early) < 5.5, "the fault must not be visible before it starts"


def test_the_failing_fridge_crosses_the_8c_threshold_on_day_two():
    t = dict(temps(LabSim().run(), "clinic-2"))
    assert t[24] < 8.0, "still in range at h24"
    assert t[36] > 8.0, "unambiguously out of range by h36"


def test_the_failing_fridge_plateaus_rather_than_spiking(events):
    """A degrading compressor, not an open door. If it spiked, the cold-chain
    agent's job would be trivial and the scenario would prove nothing."""
    late = [c for h, c in temps(events, "clinic-2") if h > 60]
    assert 14.0 < max(late) < 18.0


def test_the_healthy_fridges_stay_at_setpoint(events):
    for site in ("clinic-1", "clinic-3", "clinic-4"):
        assert max(c for _, c in temps(events, site)) < 5.5, site


# -- the reagent ------------------------------------------------------------

def test_the_affected_lot_loses_a_clinically_plausible_amount_of_potency():
    sim = LabSim()
    units, lots = sim.scenario()
    lot = next(l for l in lots if l.lot_id == "lot:REAG-4471")
    unit = next(u for u in units if u.unit_id == lot.stored_in)
    loss = 1.0 - sim.potency(lot, unit, T0 + 119 * HOUR)
    assert 0.08 < loss < 0.14, f"{loss:.1%} is not a believable degradation"


# -- the detection window ---------------------------------------------------

def test_the_drift_is_not_detectable_on_day_one(events):
    """The premise: a human reading the chart has nothing to see for two days."""
    early = [h for h, _ in rejections(events, "lot:REAG-4471") if h < 48]
    assert early == [], f"rejected too early at {early}"


def test_the_fleet_gets_a_true_rejection_on_day_three(events):
    hits = rejections(events, "lot:REAG-4471")
    assert hits, "the scenario must eventually reject"
    first_hour, first_rules = hits[0]
    assert 55 < first_hour < 80, f"first rejection at h{first_hour}"
    assert "2-2s" in first_rules


def test_the_control_lot_in_the_healthy_fridge_never_rejects(events):
    """The control that makes the whole scenario mean something: glucose on a
    lot stored at 4 C runs clean for the full five days. So the drift is the
    reagent, not the analyte, the instrument, or the simulator's noise."""
    assert rejections(events, "lot:REAG-8830") == []


def test_the_cold_chain_leads_the_qc_signal(events):
    """The multi-agent argument in one assertion: the fridge is provably out of
    range roughly a day and a half before QC produces a rejection. A fleet that
    correlates the two catches this materially earlier than one that watches
    control charts alone."""
    excursion = min(h for h, c in temps(events, "clinic-2") if c > 8.0)
    first_reject = rejections(events, "lot:REAG-4471")[0][0]
    assert excursion < first_reject - 24, f"excursion h{excursion}, rejection h{first_reject}"
