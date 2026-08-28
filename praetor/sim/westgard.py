"""Westgard multirule QC evaluation.

This is the statistical core of laboratory quality control, and it is not
optional detail: a lab that runs controls without multirules catches gross
failure and misses drift. Praetor's QC agent reasons *about* these violations,
but does not compute them: the rules are deterministic, so a language model
has no business deciding whether 2-2s fired.

Each control result is expressed as a z-score: (value - target_mean) / target_sd.
Rules are evaluated over the trailing series for one control level, and across
levels where the rule spans them.

Implemented (the standard Westgard 6-rule set):

  1-2s   one result beyond 2s                  -- warning only, never rejects
  1-3s   one result beyond 3s                  -- random error, reject
  2-2s   two consecutive beyond same-side 2s   -- systematic error, reject
  R-4s   range between levels exceeds 4s       -- random error, reject
  4-1s   four consecutive beyond same-side 1s  -- systematic error, reject
  10x    ten consecutive on one side of mean   -- systematic error, reject
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuleAction(Enum):
    ACCEPT = "accept"
    WARN = "warn"  # 1-2s alone: inspect, do not reject the run
    REJECT = "reject"


class ErrorType(Enum):
    NONE = "none"
    RANDOM = "random"  # imprecision; often a one-off
    SYSTEMATIC = "systematic"  # drift or shift; the calibration is moving


@dataclass(frozen=True)
class QCPoint:
    run_id: str
    analyte: str
    level: int  # control level, 1 = low, 2 = normal, 3 = high
    value: float
    target_mean: float
    target_sd: float
    at: float

    @property
    def z(self) -> float:
        if self.target_sd == 0:
            raise ValueError(f"{self.analyte} level {self.level} has zero target SD")
        return (self.value - self.target_mean) / self.target_sd


@dataclass(frozen=True)
class Violation:
    rule: str
    action: RuleAction
    error_type: ErrorType
    detail: str
    run_ids: tuple[str, ...]


def _same_side(zs: list[float], threshold: float) -> bool:
    """All beyond `threshold` on the same side of the mean."""
    return all(z > threshold for z in zs) or all(z < -threshold for z in zs)


def evaluate(series: list[QCPoint], across_levels: list[QCPoint] | None = None) -> list[Violation]:
    """Evaluate the trailing series for one control level, newest last.

    `across_levels` carries the *same run's* points at other levels, which R-4s
    needs because it is the only rule comparing levels against each other.
    """
    if not series:
        return []

    out: list[Violation] = []
    zs = [p.z for p in series]
    latest = series[-1]

    # 1-3s: random error, reject.
    if abs(zs[-1]) > 3:
        out.append(Violation(
            "1-3s", RuleAction.REJECT, ErrorType.RANDOM,
            f"{latest.analyte} L{latest.level} at {zs[-1]:+.2f}s", (latest.run_id,)))
    # 1-2s: warning only. Never rejects alone: treating it as a rejection is
    # the classic false-rejection trap in QC.
    elif abs(zs[-1]) > 2:
        out.append(Violation(
            "1-2s", RuleAction.WARN, ErrorType.NONE,
            f"{latest.analyte} L{latest.level} at {zs[-1]:+.2f}s", (latest.run_id,)))

    # 2-2s: two consecutive beyond 2s on the same side.
    if len(zs) >= 2 and _same_side(zs[-2:], 2):
        out.append(Violation(
            "2-2s", RuleAction.REJECT, ErrorType.SYSTEMATIC,
            f"{latest.analyte} L{latest.level} at {zs[-2]:+.2f}s, {zs[-1]:+.2f}s",
            tuple(p.run_id for p in series[-2:])))

    # 4-1s: four consecutive beyond 1s on the same side.
    if len(zs) >= 4 and _same_side(zs[-4:], 1):
        out.append(Violation(
            "4-1s", RuleAction.REJECT, ErrorType.SYSTEMATIC,
            f"{latest.analyte} L{latest.level}: 4 consecutive same-side >1s",
            tuple(p.run_id for p in series[-4:])))

    # 10x: ten consecutive on one side of the mean, however small.
    if len(zs) >= 10 and _same_side(zs[-10:], 0):
        out.append(Violation(
            "10x", RuleAction.REJECT, ErrorType.SYSTEMATIC,
            f"{latest.analyte} L{latest.level}: 10 consecutive on one side of mean",
            tuple(p.run_id for p in series[-10:])))

    # R-4s: within one run, the spread between levels exceeds 4s.
    if across_levels:
        peers = [p for p in across_levels if p.run_id == latest.run_id] + [latest]
        if len(peers) >= 2:
            spread = max(p.z for p in peers) - min(p.z for p in peers)
            if spread > 4:
                out.append(Violation(
                    "R-4s", RuleAction.REJECT, ErrorType.RANDOM,
                    f"{latest.analyte}: level spread {spread:.2f}s within run",
                    (latest.run_id,)))

    return out


def verdict(violations: list[Violation]) -> RuleAction:
    """The run's overall disposition: worst action wins."""
    if any(v.action is RuleAction.REJECT for v in violations):
        return RuleAction.REJECT
    if any(v.action is RuleAction.WARN for v in violations):
        return RuleAction.WARN
    return RuleAction.ACCEPT
