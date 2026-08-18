#!/usr/bin/env python3
"""Replay the scenario into a running Praetor deployment.

Wraps each event in the same Pub/Sub push envelope the real subscription
delivers, so the service cannot tell this from live telemetry.

    python3 scripts/feed.py <base-url> [--hours 40] [--token <id-token>]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praetor.sim.lab import HOUR, T0, LabSim


def envelope(event) -> dict:
    body = json.dumps({
        "kind": event.kind.value,
        "at": event.at,
        "site": event.site,
        "payload": event.payload,
    }).encode()
    return {"message": {"data": base64.b64encode(body).decode(), "messageId": f"m{int(event.at)}"}}


def post(url: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--hours", type=int, default=40)
    ap.add_argument("--token", default="")
    args = ap.parse_args()

    cutoff = T0 + args.hours * HOUR
    events = [e for e in LabSim().run() if e.at <= cutoff]
    print(f"replaying {len(events)} events (first {args.hours}h) -> {args.base_url}")

    decisions = 0
    for i, e in enumerate(events):
        result = post(f"{args.base_url}/ingest", args.token, envelope(e))
        for d in result.get("decisions", []):
            decisions += 1
            hour = int((e.at - T0) / HOUR)
            print(f"  h{hour:<4} {d['agent']:<20} {d['action']:<24} {d['verdict']}")
        if i % 50 == 0 and i:
            print(f"  ... {i}/{len(events)}")
    print(f"done: {len(events)} events, {decisions} gate decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
