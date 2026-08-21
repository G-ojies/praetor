"""What protects a publicly reachable control plane.

Going public trades one risk for another. Judges get a link that works without
an access grant, which is worth a lot when a broken invite means an unjudgeable
entry -- but every endpoint that costs money becomes reachable by anyone.

Three tiers, sized by what abuse of each would actually cost:

  /ingest        Runs the whole fleet and can invoke Gemini. Never public. It
                 requires a Google-signed OIDC token from the Pub/Sub push
                 identity, verified here rather than delegated to Cloud Run's
                 IAM, because the service itself is now unauthenticated.

  media, memory  Each call invokes a paid model. Tightly bucketed per client.

  everything     Reads. Generous, but bounded, so a loop cannot bury the
  else           service or the Firestore read quota.

The buckets are in-process, which is exactly right for a single-instance
deployment and would need Redis or Memorystore if that ever changed. Saying so
here is cheaper than someone discovering it later.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

# Only this identity may push telemetry.
PUSH_SA = os.environ.get(
    "PRAETOR_PUSH_SA", "praetor-run@praetor-505914.iam.gserviceaccount.com")
# Set to "1" to bypass ingest auth locally. Never set in the deployment.
ALLOW_UNVERIFIED_INGEST = os.environ.get("PRAETOR_ALLOW_UNVERIFIED_INGEST") == "1"


@dataclass
class Bucket:
    capacity: float
    per_second: float
    tokens: dict[str, float] = field(default_factory=dict)
    seen: dict[str, float] = field(default_factory=dict)

    def take(self, key: str, now: float) -> bool:
        last = self.seen.get(key, now)
        tokens = min(self.capacity, self.tokens.get(key, self.capacity)
                     + (now - last) * self.per_second)
        self.seen[key] = now
        if tokens < 1:
            self.tokens[key] = tokens
            return False
        self.tokens[key] = tokens - 1
        return True

    def retry_after(self, key: str) -> int:
        deficit = 1 - self.tokens.get(key, 0)
        return max(1, int(deficit / self.per_second)) if deficit > 0 else 1


# 120 reads/min, 6 paid-model calls/hour.
READS = Bucket(capacity=120, per_second=2.0)
PAID = Bucket(capacity=6, per_second=6 / 3600)

PAID_PREFIXES = ("/api/media", "/api/memory")
EXEMPT = ("/health",)


def client_key(request: Request) -> str:
    """Cloud Run puts the real client first in X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def enforce_rate_limit(request: Request) -> None:
    path = request.url.path
    if path in EXEMPT:
        return
    key = client_key(request)
    bucket = PAID if path.startswith(PAID_PREFIXES) else READS
    if not bucket.take(key, time.time()):
        raise HTTPException(
            429,
            detail=("Rate limit. Paid-model endpoints allow 6 calls an hour per client; "
                    "reads allow 120 a minute."),
            headers={"Retry-After": str(bucket.retry_after(key))},
        )


def verify_push_token(request: Request) -> str:
    """Verify the OIDC token Pub/Sub attaches to a push delivery.

    Cloud Run is no longer checking this for us, so the service checks it
    itself. An unsigned, expired, wrong-audience or wrong-account token is
    rejected before a single event reaches an agent.
    """
    if ALLOW_UNVERIFIED_INGEST:
        return "unverified(local)"

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "ingest requires a Google-signed OIDC token")

    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(header[7:], grequests.Request())
    except Exception as exc:
        raise HTTPException(401, f"invalid OIDC token: {type(exc).__name__}") from exc

    email = claims.get("email", "")
    if email != PUSH_SA or not claims.get("email_verified", False):
        raise HTTPException(403, f"{email or 'unknown principal'} may not publish telemetry")
    return email
