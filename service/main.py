"""Praetor control plane, as it runs on Cloud Run.

Three surfaces, deliberately separated by who is allowed to use them:

  /ingest        Pub/Sub push endpoint. Telemetry arrives here, is offered to
                 the agents, and whatever they propose goes through the gate.
                 Authenticated as a service account; no human touches it.

  /api/*         Read models for the console, plus the one write a human is
                 ever asked for: ratifying an escalation.

  /              The console itself, which verifies the audit chain in the
                 browser from the public key. It is a reader, not a trustee.

State lives in Firestore, not in this process, because Cloud Run will start and
stop containers underneath us and an incident must survive that.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from praetor.agents.coldchain import ColdChainAgent
from praetor.agents.diagnostician import Diagnostician
from praetor.agents.lots import LotAgent
from praetor.agents.qc import QCAgent
from praetor.gate.audit import verify_chain
from praetor.gate.firestore_audit import FirestoreAuditChain
from praetor.gate.policy import PolicyGate
from praetor.orchestrator import Fleet
from praetor.policy_config import default_breaker, default_budget, default_capabilities
from praetor.reasoning import select_reasoner
from praetor.sim.lab import Event, EventKind

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "praetor-505914")
NAMESPACE = os.environ.get("PRAETOR_NAMESPACE", "live")

app = FastAPI(title="Praetor", docs_url="/api/docs")

_fleet: Fleet | None = None


def fleet() -> Fleet:
    """Built lazily so a cold start that only serves /health costs nothing."""
    global _fleet
    if _fleet is None:
        reasoner = select_reasoner()
        _fleet = Fleet(
            agents=[ColdChainAgent(), QCAgent(), LotAgent(), Diagnostician(reasoner)],
            gate=PolicyGate(
                capabilities=default_capabilities(),
                budget=default_budget(),
                breaker=default_breaker(),
                chain=FirestoreAuditChain(project=PROJECT, namespace=NAMESPACE),
            ),
        )
    return _fleet


@app.get("/health")
def health() -> dict:
    """Liveness only. Deliberately does not touch Firestore or Gemini: a health
    check that depends on every downstream turns one outage into three."""
    return {"status": "ok", "project": PROJECT, "namespace": NAMESPACE}


@app.post("/ingest")
async def ingest(request: Request) -> JSONResponse:
    """Pub/Sub push. One message is one telemetry reading or control result."""
    envelope = await request.json()
    message = envelope.get("message")
    if not message:
        raise HTTPException(400, "not a Pub/Sub push envelope")

    try:
        payload = json.loads(base64.b64decode(message["data"]).decode())
    except Exception as exc:
        # Return 400, not 500: a malformed message must be dead-lettered, not
        # redelivered forever. Pub/Sub retries 5xx indefinitely.
        raise HTTPException(400, f"undecodable message: {exc}") from exc

    try:
        event = Event(
            kind=EventKind(payload["kind"]),
            at=float(payload["at"]),
            site=payload["site"],
            payload=payload["payload"],
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, f"malformed event: {exc}") from exc

    before = len(fleet().timeline)
    fleet().step(event)
    decided = fleet().timeline[before:]
    return JSONResponse({
        "accepted": True,
        "decisions": [
            {"agent": e.agent, "action": e.action, "resource": e.resource, "verdict": e.verdict}
            for e in decided
        ],
    })


@app.get("/api/state")
def state() -> dict:
    f = fleet()
    return {
        "signals": [
            {"kind": s.kind, "subject": s.subject, "severity": int(s.severity), "summary": s.summary}
            for s in f.board.signals
        ],
        "root_cause": f.board.root_cause,
        "confidence": f.board.root_cause_confidence,
        "quarantined_lots": sorted(f.board.quarantined_lots),
        "held_batches": sorted(f.board.held_batches),
        "offline_instruments": sorted(f.board.offline_instruments),
        "counts": f.counts(),
    }


@app.get("/api/escalations")
def escalations() -> dict:
    """What the fleet is waiting on a human for."""
    return {
        "pending": [
            {
                "proposal_id": p.proposal_id,
                "agent": p.agent_id,
                "action": p.action_type,
                "resource": p.resource,
                "rationale": p.rationale,
                "confidence": p.confidence,
            }
            for p in fleet().escalations
        ]
    }


@app.get("/api/audit")
def audit() -> dict:
    """The chain, plus the key needed to check it. Everything a verifier needs
    and nothing that would let them forge it."""
    chain = fleet().chain
    entries = chain.read_dicts()
    result = verify_chain(entries, chain.public_key_hex)
    return {
        "public_key": chain.public_key_hex,
        "length": len(entries),
        "verified": result.ok,
        "failures": result.failures,
        "entries": entries,
    }


@app.get("/", response_class=HTMLResponse)
def console() -> str:
    page = Path(__file__).parent / "console.html"
    return page.read_text() if page.exists() else "<h1>Praetor</h1><p>Console not built.</p>"
