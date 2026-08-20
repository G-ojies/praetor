#!/usr/bin/env python3
"""Seed the incident archive.

A deployment that has been running for a year has an archive; a fresh one does
not, and an empty archive makes the recall feature untestable and invisible.
These are plausible past incidents for a four-site clinic network, written the
way a scientist writes a shift book. They are clearly seed data -- each id
begins with `seed_` -- so nothing here can be mistaken for a real incident this
fleet handled.

    python3 scripts/seed_memory.py [--project praetor-505914]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praetor.memory import IncidentMemory

ARCHIVE = [
    ("seed_2025_03_fridge",
     "Fridge at clinic 2 ran warm over a weekend. Glucose controls drifted low across all three "
     "levels until 2-2s and then 4-1s rejected on the Monday morning run.",
     "Compressor degradation. The glucose reagent stored in that unit lost potency and read low.",
     "Lot quarantined, unit replaced, four days of glucose results recalled and re-run on a fresh lot."),
    ("seed_2025_07_lamp",
     "Analyser at clinic 1 reported repeated flag codes. Two unrelated reagent lots failed QC on "
     "the same instrument within a day of each other, with no storage problem at either site.",
     "Photometer lamp at end of life. The instrument itself was reading incorrectly.",
     "Instrument taken offline, lamp replaced, calibration verified before returning to service."),
    ("seed_2026_01_equilibration",
     "Sodium controls shifted immediately after a reagent delivery. The shift was present from the "
     "first run on the new lot and did not worsen over time.",
     "New lot was loaded straight from cold storage and never equilibrated to room temperature.",
     "Handling protocol updated; lots now rest for thirty minutes before loading."),
    ("seed_2026_04_carryover",
     "Potassium results at clinic 3 were intermittently high, but only on samples run immediately "
     "after a haemolysed specimen. QC between runs was clean.",
     "Probe carryover. The wash cycle was insufficient after high-potassium specimens.",
     "Wash volume increased and a rerun rule added for the position following a flagged specimen."),
    ("seed_2026_05_power",
     "Clinic 4 lost mains power overnight. The generator started but the laboratory refrigerator "
     "was on an unswitched circuit and stayed off for six hours.",
     "Refrigerator was not on the backup circuit. Cold chain broken for six hours.",
     "All reagents in the unit discarded, circuit rewired to the generator, temperature logger added."),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "praetor-505914"))
    ap.add_argument("--local", action="store_true", help="do not write to Firestore")
    args = ap.parse_args()

    memory = IncidentMemory() if args.local else IncidentMemory(project=args.project)
    for incident_id, summary, root_cause, resolution in ARCHIVE:
        memory.remember(incident_id, summary, root_cause, resolution, facts={"seed": True})
        print(f"  remembered {incident_id}")
    print(f"archive seeded with {len(ARCHIVE)} incidents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
