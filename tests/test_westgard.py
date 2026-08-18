"""Westgard multirule tests.

A QC engine that gets these wrong either misses drift or cries wolf, and a lab
that is cried wolf at learns to ignore the fleet. Both failure modes are worse
than no automation, so each rule is pinned individually.
"""

import pytest

from praetor.sim.westgard import ErrorType, QCPoint, RuleAction, evaluate, verdict

MEAN, SD = 100.0, 2.0


def pts(*zs, analyte="glucose", level=2, run_prefix="run"):
    """Build a control series from z-scores, oldest first."""
    return [
        QCPoint(f"{run_prefix}_{i}", analyte, level, MEAN + z * SD, MEAN, SD, 1_000_000.0 + i * 3600)
        for i, z in enumerate(zs)
    ]


def rules(violations):
    return {v.rule for v in violations}


# -- in control -------------------------------------------------------------

def test_a_stable_series_raises_nothing():
    v = evaluate(pts(0.3, -0.5, 0.8, -0.2, 0.4))
    assert v == []
    assert verdict(v) is RuleAction.ACCEPT


def test_an_empty_series_raises_nothing():
    assert evaluate([]) == []


# -- 1-2s is a warning, never a rejection -----------------------------------

def test_single_result_past_2s_warns_but_does_not_reject():
    v = evaluate(pts(0.1, -0.4, 2.4))
    assert rules(v) == {"1-2s"}
    assert verdict(v) is RuleAction.WARN


def test_a_lone_1_2s_does_not_trip_2_2s():
    """Only the latest point is out; 2-2s needs two consecutive."""
    v = evaluate(pts(0.5, 2.3))
    assert "2-2s" not in rules(v)


# -- rejections -------------------------------------------------------------

def test_1_3s_rejects_as_random_error():
    v = evaluate(pts(0.2, -0.3, 3.4))
    assert "1-3s" in rules(v)
    assert verdict(v) is RuleAction.REJECT
    assert next(x for x in v if x.rule == "1-3s").error_type is ErrorType.RANDOM


def test_1_3s_supersedes_the_1_2s_warning():
    """A 3s outlier is also past 2s; it must not be reported as both."""
    v = evaluate(pts(0.0, 3.5))
    assert "1-2s" not in rules(v)


def test_2_2s_rejects_as_systematic_error():
    v = evaluate(pts(0.4, 2.2, 2.5))
    assert "2-2s" in rules(v)
    assert next(x for x in v if x.rule == "2-2s").error_type is ErrorType.SYSTEMATIC
    assert verdict(v) is RuleAction.REJECT


def test_2_2s_requires_the_same_side():
    """+2.3s then -2.4s is scatter, not a shift."""
    v = evaluate(pts(0.1, 2.3, -2.4))
    assert "2-2s" not in rules(v)


def test_4_1s_rejects_as_systematic_error():
    v = evaluate(pts(0.2, 1.3, 1.4, 1.2, 1.6))
    assert "4-1s" in rules(v)
    assert verdict(v) is RuleAction.REJECT


def test_4_1s_requires_the_same_side():
    v = evaluate(pts(1.3, 1.4, -1.2, 1.6))
    assert "4-1s" not in rules(v)


def test_10x_catches_drift_that_never_leaves_1s():
    """The rule that matters most: ten points on one side, none of them
    individually remarkable. This is calibration drift, and it is exactly what
    a human eyeballing a Levey-Jennings chart misses."""
    v = evaluate(pts(*([0.4] * 10)))
    assert "10x" in rules(v)
    assert verdict(v) is RuleAction.REJECT
    assert "1-2s" not in rules(v)  # no single point is even past 2s


def test_10x_needs_ten_not_nine():
    assert "10x" not in rules(evaluate(pts(*([0.4] * 9))))


def test_10x_resets_when_a_point_crosses_the_mean():
    v = evaluate(pts(*([0.4] * 5), -0.1, *([0.4] * 4)))
    assert "10x" not in rules(v)


# -- R-4s spans control levels ---------------------------------------------

def test_r_4s_rejects_when_levels_diverge_within_one_run():
    low = QCPoint("run_x", "glucose", 1, MEAN - 2.2 * SD, MEAN, SD, 1_000_000.0)
    high = QCPoint("run_x", "glucose", 3, MEAN + 2.3 * SD, MEAN, SD, 1_000_000.0)
    v = evaluate([high], across_levels=[low])
    assert "R-4s" in rules(v)
    assert next(x for x in v if x.rule == "R-4s").error_type is ErrorType.RANDOM


def test_r_4s_ignores_points_from_other_runs():
    """Levels only compare within the same run; across runs it is meaningless."""
    other = QCPoint("run_earlier", "glucose", 1, MEAN - 2.2 * SD, MEAN, SD, 999_000.0)
    latest = QCPoint("run_x", "glucose", 3, MEAN + 2.3 * SD, MEAN, SD, 1_000_000.0)
    assert "R-4s" not in rules(evaluate([latest], across_levels=[other]))


# -- guards -----------------------------------------------------------------

def test_zero_target_sd_is_an_error_not_a_division_by_zero():
    bad = QCPoint("run_0", "glucose", 2, 100.0, 100.0, 0.0, 1_000_000.0)
    with pytest.raises(ValueError, match="zero target SD"):
        _ = bad.z


def test_multiple_rules_can_fire_together():
    v = evaluate(pts(1.2, 1.3, 2.4, 2.6))
    assert {"2-2s", "4-1s"} <= rules(v)
    assert verdict(v) is RuleAction.REJECT
