#!/usr/bin/env python3
"""Generate the demo narration track.

Uses the same Chirp 3 HD voice the product uses for its own handovers, which
seems only fair.

Each segment is synthesised separately and laid onto a silent bed at a fixed
start time, so the narration lines up with the shot list rather than drifting.
If a segment overruns its slot the script says so instead of silently pushing
everything later: an overrun that eats the four-minute limit is worth knowing
about before you record, not after.

    python3 scripts/make_voiceover.py --out evidence/voiceover.mp3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praetor.media import speak

# (start_seconds, slot_id, text). Starts match docs/video-script.md.
SEGMENTS = [
    (0, "problem",
     "A medical laboratory scientist covering four rural clinics is also the quality control "
     "officer, the inventory manager, and the cold chain custodian. "
     "A refrigerator compressor degrades overnight. The reagent inside slowly loses potency. "
     "Control results drift downward, and stay inside two standard deviations for forty hours. "
     "Invisible on a control chart. By the time Westgard rules reject, a day and a half of patient "
     "results have gone out on a reagent that was already wrong."),

    (30, "rule",
     "Praetor is a fleet of agents watching quality control, cold chain, and reagent lots. "
     "Underneath it is one rule. The fleet may move the laboratory toward safety on its own, "
     "and never away from it. "
     "Hold a batch, quarantine a lot, pull a drifting analyser. All autonomous. "
     "Release a patient result? Never. Not at any confidence."),

    (52, "run",
     "These events are going through Cloud Pub Sub, not straight into the process. "
     "In the console: the chain is verified in this browser, from the public key alone. "
     "Both reagent lots were quarantined at hour thirty two, a full day before quality control "
     "rejected anything, because the cold chain is the leading indicator. "
     "Offline instruments: none. Two lots were failing on the same analyser. A naive system pulls "
     "the instrument. But both lots sat in the same failed fridge, so the common factor is the "
     "refrigerator. This clinic has one analyser. Taking it offline turns a reagent problem into "
     "an outage."),

    (102, "human",
     "The fleet wants to release a held batch. Gemini diagnosed this at zero point eight five "
     "confidence, and the gate refused. "
     "Approvals are attributable. An anonymous one is rejected. And the approval is itself an "
     "audit entry, because for a fail open action, the accountable event is the approval, not the "
     "request."),

    (128, "cloud",
     "The backend runs on Cloud Run. State is in Firestore: the audit chain, and the incident "
     "archive. Telemetry arrives over Pub Sub. Reasoning is Gemini three point seven Flash on "
     "Vertex A I."),

    (145, "models",
     "The person standing at that fridge at six in the morning is whoever opened the clinic, not "
     "the scientist. Written steps assume laboratory training. This does not. And the temperature "
     "on it is the measured value, not one the model invented. "
     "The handover is also speech, because the scientist covering four sites is usually driving "
     "between them. "
     "And it checks whether this has happened before. It found one match and ignored four. Saying "
     "nothing beats forcing a resemblance."),

    (178, "close",
     "One hundred tests, named after what must never happen. "
     "No fail open action is reachable by any grant. The analyser is never taken out of service. "
     "Unknown lot provenance never implicates the analyser. That last one is there because it did, "
     "once, when a record was missing and the code read the gap as evidence. "
     "Autonomy is not the hard part. Deciding what the autonomy is forbidden to do, and proving it "
     "cannot, is the hard part."),
]

LIMIT = 240.0  # the contest's hard four-minute ceiling
# Aim well under it. A track that lands at 3:59 leaves no room for the pause
# before you stop the recording, and a video rejected on length scores nothing.
TARGET = 225.0


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evidence/voiceover.mp3")
    ap.add_argument("--workdir", default="evidence/vo")
    args = ap.parse_args()

    work = Path(args.workdir); work.mkdir(parents=True, exist_ok=True)
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)

    timings, overruns = [], []
    for i, (start, slot, text) in enumerate(SEGMENTS):
        path = work / f"{i:02d}-{slot}.mp3"
        media = speak(text)
        path.write_bytes(media.data)
        d = duration(path)
        last = i + 1 == len(SEGMENTS)
        # The final segment has no following slot to collide with; it is bounded
        # by the contest limit instead. Comparing it against its own end would
        # report a spurious overrun of exactly zero every time.
        nxt = LIMIT if last else SEGMENTS[i + 1][0]
        slack = (nxt - start) - d
        timings.append({"slot": slot, "start": start, "duration": round(d, 1),
                        "ends_at": round(start + d, 1),
                        "slot_length": round(nxt - start, 1), "slack": round(slack, 1)})
        flag = "" if slack >= 0 else "  <-- OVERRUNS"
        if slack < 0:
            overruns.append(slot)
        bound = "to limit" if last else f"{nxt - start:>3.0f}s slot"
        print(f"  {start:>4}s  {slot:<9} {d:5.1f}s, ends {start + d:6.1f}s  "
              f"({bound}, slack {slack:+.1f}s){flag}")

    # Lay each segment onto one silent bed at its start time.
    inputs, filters = [], []
    for i, (start, slot, _) in enumerate(SEGMENTS):
        inputs += ["-i", str(work / f"{i:02d}-{slot}.mp3")]
        filters.append(f"[{i}:a]adelay={int(start*1000)}|{int(start*1000)}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(SEGMENTS)))
    graph = ";".join(filters) + f";{mix}amix=inputs={len(SEGMENTS)}:normalize=0[out]"

    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", graph,
                    "-map", "[out]", "-b:a", "192k", str(out)],
                   capture_output=True, check=True)

    total = duration(out)
    (out.parent / "voiceover-timings.json").write_text(json.dumps(timings, indent=2))
    print(f"\n  track: {out}  {total:.1f}s  ({total/60:.2f} min)")
    print(f"  limit: {LIMIT:.0f}s hard, {TARGET:.0f}s target  ->  "
          f"{'OK' if total <= TARGET else ('TIGHT' if total <= LIMIT else 'OVER THE LIMIT')}")
    if overruns:
        print(f"  segments overrunning their slot: {', '.join(overruns)}")
        print("  shorten the text or move the following slot later in docs/video-script.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
