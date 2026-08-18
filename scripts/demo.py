#!/usr/bin/env python3
"""Run the full scenario and print the fleet's timeline.

    python3 scripts/demo.py            # offline reasoner, no credentials needed
    PRAETOR_OFFLINE=0 python3 scripts/demo.py   # live Gemini, if configured
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praetor.agents.coldchain import ColdChainAgent
from praetor.agents.diagnostician import Diagnostician
from praetor.agents.lots import LotAgent
from praetor.agents.qc import QCAgent
from praetor.agents.scribe import Scribe
from praetor.gate.audit import AuditChain, verify_chain
from praetor.gate.policy import PolicyGate
from praetor.orchestrator import Fleet
from praetor.policy_config import default_breaker, default_budget, default_capabilities
from praetor.reasoning import select_reasoner
from praetor.sim.lab import HOUR, T0, LabSim

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
COLOUR = {"allow": "\033[32m", "escalate": "\033[33m", "deny": "\033[31m", "halt": "\033[35m"}


def main() -> int:
    os.environ.setdefault("PRAETOR_OFFLINE", "1")
    reasoner = select_reasoner()

    fleet = Fleet(
        agents=[ColdChainAgent(), QCAgent(), LotAgent(), Diagnostician(reasoner)],
        gate=PolicyGate(
            capabilities=default_capabilities(),
            budget=default_budget(),
            breaker=default_breaker(),
            chain=AuditChain(),
        ),
    )

    events = LabSim().run()
    print(f"{BOLD}Praetor{RESET}  {len(events)} events, 4 clinics, 5 days\n")
    fleet.run(events)

    print(f"{BOLD}Fleet timeline{RESET}")
    print(f"{DIM}{'hour':>6}  {'agent':<22} {'action':<28} {'verdict':<9} why{RESET}")
    for e in fleet.timeline:
        c = COLOUR.get(e.verdict, "")
        hour = f"h{int((e.at - T0) / HOUR)}"
        why = e.escalation or (e.reasons[0] if e.reasons else "")
        print(f"{hour:>6}  {e.agent:<22} {e.action:<28} {c}{e.verdict:<9}{RESET} {DIM}{why[:64]}{RESET}")

    counts = fleet.counts()
    print(f"\n{BOLD}Decisions{RESET}  " + "  ".join(
        f"{COLOUR.get(k,'')}{k}={v}{RESET}" for k, v in sorted(counts.items())))

    print(f"\n{BOLD}Diagnosis{RESET}")
    print(f"  {fleet.board.root_cause}")
    print(f"  {DIM}confidence {fleet.board.root_cause_confidence:.2f}{RESET}")

    if fleet.escalations:
        print(f"\n{BOLD}Awaiting a human{RESET}")
        for p in fleet.escalations:
            print(f"  {p.action_type} on {p.resource}")
            print(f"    {DIM}{p.rationale}{RESET}")

    v = verify_chain([e.to_dict() for e in fleet.chain.entries], fleet.chain.public_key_hex)
    print(f"\n{BOLD}Audit chain{RESET}  {len(fleet.chain)} entries, "
          f"verified={v.ok}, key={fleet.chain.public_key_hex[:16]}...")

    report = Scribe(reasoner).report(fleet.board, fleet.timeline)
    print(f"\n{BOLD}Incident report{RESET}  {DIM}({report['model']}){RESET}")
    print(f"  {BOLD}{report['title']}{RESET}")
    print(f"  {report['summary']}")
    for r in report["recommendations"]:
        print(f"    - {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
