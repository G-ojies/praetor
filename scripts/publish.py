#!/usr/bin/env python3
"""Publish scenario events to the Pub/Sub topic.

The difference from scripts/feed.py: that one posts straight to /ingest, which
is useful for a fast loop but bypasses the delivery path entirely. This one
publishes to the topic and lets Pub/Sub push, so retries, acknowledgement and
dead-lettering are all exercised as they would be in production.

    python3 scripts/publish.py --hours 70 [--topic praetor-telemetry]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praetor.sim.lab import HOUR, T0, LabSim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "praetor-505914"))
    ap.add_argument("--topic", default="praetor-telemetry")
    ap.add_argument("--hours", type=int, default=70)
    ap.add_argument("--from-hour", type=int, default=0)
    args = ap.parse_args()

    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    path = publisher.topic_path(args.project, args.topic)

    lo, hi = T0 + args.from_hour * HOUR, T0 + args.hours * HOUR
    events = [e for e in LabSim().run() if lo <= e.at <= hi]
    print(f"publishing {len(events)} events to {path}")

    futures = [
        publisher.publish(path, json.dumps({
            "kind": e.kind.value, "at": e.at, "site": e.site, "payload": e.payload,
        }).encode())
        for e in events
    ]
    for f in futures:
        f.result(timeout=60)
    print(f"published {len(futures)} messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
