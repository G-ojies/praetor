"""Cold-chain agent: watches storage temperature, catches the leading indicator.

This agent exists because the fridge fails roughly a day and a half before the
control chart says anything. Detection here is a threshold and a duration (no
model is involved), but the volume is high enough that even *reading* every
reading with a frontier model would be wasteful, so triage runs on Gemma.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from praetor.agents.base import Agent, Blackboard, Signal
from praetor.common.types import ActionProposal, Severity
from praetor.sim.lab import EventKind

# Above this, refrigerated reagents begin to degrade measurably.
EXCURSION_C = 8.0
# Consecutive readings required before it counts. One warm reading is a door
# being opened; three in a row is a compressor.
SUSTAINED_READINGS = 3


class ColdChainAgent(Agent):
    def __init__(self, agent_id: str = "agent.coldchain") -> None:
        super().__init__(agent_id)
        self._streak: dict[str, int] = defaultdict(int)
        self._peak: dict[str, float] = defaultdict(float)
        self._pending: list[ActionProposal] = []

    def observe(self, event: Any, board: Blackboard) -> Iterable[Signal]:
        if event.kind is not EventKind.COLDCHAIN:
            return ()
        unit = event.payload["unit"]
        celsius = event.payload["celsius"]

        if celsius <= EXCURSION_C:
            self._streak[unit] = 0
            return ()

        self._streak[unit] += 1
        self._peak[unit] = max(self._peak[unit], celsius)
        if self._streak[unit] < SUSTAINED_READINGS:
            return ()
        # Report the excursion once, then stay quiet: a signal per hour for two
        # days would bury everything else on the blackboard.
        if board.seen("coldchain.excursion", unit):
            return ()

        hours = self._streak[unit]
        severity = Severity.SEV2 if self._peak[unit] > 12.0 else Severity.SEV3
        signal = board.add(Signal(
            kind="coldchain.excursion",
            source=self.agent_id,
            subject=unit,
            at=event.at,
            severity=severity,
            summary=(f"{unit} above {EXCURSION_C:.0f} C for {hours} consecutive "
                     f"readings, peak {self._peak[unit]:.1f} C"),
            facts={"unit": unit, "peak_c": self._peak[unit], "sustained_readings": hours,
                   "setpoint_c": event.payload["setpoint_c"], "site": event.site},
        ))

        incident = f"inc_{unit.split(':')[-1]}"
        self._pending.append(self.action(
            incident, "notify.scientist", unit,
            confidence=1.0,
            rationale=f"Sustained cold-chain excursion at {event.site}.",
            channel="sms", message=signal.summary,
        ))
        # A failing compressor cannot be fixed from here, but dropping the
        # setpoint buys hours of margin while someone drives out to the site.
        self._pending.append(self.action(
            incident, "coldchain.setpoint", unit,
            confidence=0.8,
            rationale="Lower setpoint to claw back margin on a failing compressor.",
            unit=unit, celsius=2.0,
        ))
        return (signal,)

    def propose(self, board: Blackboard) -> Iterable[ActionProposal]:
        out, self._pending = self._pending, []
        return out
