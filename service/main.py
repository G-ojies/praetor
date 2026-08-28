"""Praetor control plane, as it runs on Cloud Run.

Three surfaces, deliberately separated by who is allowed to use them:

  /ingest        Pub/Sub push endpoint. Telemetry arrives here, is offered to
                 the agents, and whatever they propose goes through the gate.
                 Authenticated as a service account; no human touches it.

  /api/*         Read models for the console, plus the one write a human is
                 ever asked for: ratifying an escalation.

  /              The console itself, which verifies the audit chain in the
                 browser from the public key. It is a reader, not a trustee.

Both halves of the state are durable. The audit chain is append-only and
tamper-evident; the blackboard (signals, quarantined lots, held batches) is
a bounded working set written through on change. A cold start restores the
blackboard rather than beginning fresh, because Cloud Run scales to zero and a
restart midway through an incident is the normal case, not an edge one.

The service still runs one instance. Durability is not the same as
concurrency: two containers would hold two copies of the same restored
blackboard and diverge as each handled different events. Making that correct
means moving the blackboard behind the same transactional discipline as the
chain, which is worth doing when the decision rate justifies it and is not
close to justifying it today. The gate's guarantees do not depend on either
choice; they are enforced per proposal.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

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
from praetor.state import FirestoreBlackboard
from service.guard import enforce_rate_limit, verify_push_token

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "praetor-505914")
NAMESPACE = os.environ.get("PRAETOR_NAMESPACE", "live")

app = FastAPI(title="Praetor", docs_url="/api/docs")


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Applied to everything. The service is publicly reachable so that judges
    need no access grant, which means every endpoint is now a cost surface.

    Starlette does not route an HTTPException raised inside middleware through
    the app's exception handlers (it surfaces as a 500), so the response is
    built here rather than raised.
    """
    try:
        enforce_rate_limit(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=exc.headers or {})
    return await call_next(request)

_fleet: Fleet | None = None


def fleet() -> Fleet:
    """Built lazily so a cold start that only serves /health costs nothing."""
    global _fleet
    if _fleet is None:
        reasoner = select_reasoner()
        store = FirestoreBlackboard(project=PROJECT, namespace=NAMESPACE)
        _fleet = Fleet(
            agents=[ColdChainAgent(), QCAgent(), LotAgent(), Diagnostician(reasoner)],
            gate=PolicyGate(
                capabilities=default_capabilities(),
                budget=default_budget(),
                breaker=default_breaker(),
                chain=FirestoreAuditChain(project=PROJECT, namespace=NAMESPACE),
            ),
            # Restored, not fresh. Cloud Run scales to zero, so a cold start
            # midway through an incident is the normal case, not an edge one.
            board=store.load(),
            store=store,
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
    # Cloud Run no longer authenticates callers, so the endpoint that runs the
    # fleet and can invoke Gemini authenticates them itself.
    verify_push_token(request)

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


@app.get("/api/qc")
def qc_series() -> dict:
    """Control results, grouped into series a Levey-Jennings plot can draw.

    Returned whole rather than filtered server-side: the window is bounded, so
    one request beats a round trip every time the scientist switches level.
    """
    grouped: dict[tuple, dict] = {}
    for point in fleet().board.qc_points:
        key = (point["lot_id"], point["analyte"], point["level"])
        series = grouped.setdefault(key, {
            "lot_id": point["lot_id"], "analyte": point["analyte"],
            "level": point["level"], "target_mean": point["target_mean"],
            "target_sd": point["target_sd"], "points": [],
        })
        series["points"].append({
            "at": point["at"], "z": point["z"], "value": point["value"],
            "run_id": point["run_id"], "disposition": point["disposition"],
            "rules": point["rules"],
        })

    series = sorted(grouped.values(), key=lambda s: (s["analyte"], s["level"], s["lot_id"]))
    quarantined = fleet().board.quarantined_lots
    for s in series:
        s["quarantined"] = s["lot_id"] in quarantined
    return {
        "series": series,
        "analytes": sorted({s["analyte"] for s in series}),
        "levels": sorted({s["level"] for s in series}),
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


@app.post("/api/escalations/{proposal_id}/decide")
async def decide(proposal_id: str, request: Request) -> dict:
    """Ratify or refuse one escalated action. The only write a human makes."""
    body: dict[str, Any] = await request.json()
    who = (body.get("who") or "").strip()
    if not who:
        # No anonymous approvals. An audit trail naming "someone" is not one.
        raise HTTPException(400, "'who' is required: approvals are attributable")
    try:
        return fleet().ratify(proposal_id, bool(body.get("approve")), who)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


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


# Generated media is cached in process. It is deliberately not cached in
# Firestore: a 1 MB PNG does not fit a document, and paying Cloud Storage for
# an artefact regenerated a handful of times is worse than regenerating it.
# Nothing here runs on an event: media is requested, never triggered.
_media_cache: dict[str, Any] = {}


def _media(key: str, produce) -> Response:
    if key not in _media_cache:
        try:
            _media_cache[key] = produce()
        except Exception as exc:
            raise HTTPException(502, f"{key}: {type(exc).__name__}: {exc}") from exc
    item = _media_cache[key]
    return Response(content=item.data, media_type=item.mime,
                    headers={"X-Praetor-Model": item.model, "Cache-Control": "public, max-age=3600"})


@app.get("/api/media/remediation.png")
def remediation_card() -> Response:
    """An illustrated instruction for whoever opened the clinic.

    The person standing at the failed fridge at 06:00 is usually not the
    laboratory scientist. Written steps assume training this person may not
    have; a picture does not.
    """
    from praetor.media import generate_image, remediation_prompt

    board = fleet().board
    excursions = board.of_kind("coldchain.excursion")
    if not excursions:
        raise HTTPException(404, "no cold-chain excursion to illustrate")
    unit = excursions[0].subject
    peak = float(excursions[0].facts.get("peak_c", 12.0))
    lots = sorted(board.quarantined_lots)
    healthy = next((u for u in sorted(set(board.lot_storage.values())) if u != unit), "a working fridge")
    return _media(f"remediation:{unit}:{peak:.0f}:{len(lots)}",
                  lambda: generate_image(remediation_prompt(unit, lots, healthy, peak)))


@app.get("/api/media/handover.mp3")
def handover_audio() -> Response:
    """The same briefing the console shows, as speech. The scientist covering
    four sites is usually driving between them."""
    from praetor.media import speak

    f = fleet()
    counts = f.counts()
    text = (
        f"Praetor handover. {f.board.root_cause or 'No single root cause identified yet.'} "
        f"The fleet took {counts.get('allow', 0)} actions on its own and is holding "
        f"{len(f.escalations)} for your approval. "
        f"{len(f.board.quarantined_lots)} reagent lots are quarantined and "
        f"{len(f.board.held_batches)} batches of results are held."
    )
    return _media(f"handover:{hash(text)}", lambda: speak(text))


@app.get("/api/media/alarm/{severity}.wav")
def alarm(severity: int) -> Response:
    """A distinct motif per severity. At the bench, hands are gloved and eyes
    are down a microscope; audio is the channel that still works."""
    from praetor.media import alarm_prompt, generate_music

    if severity not in (1, 2, 3, 4):
        raise HTTPException(400, "severity must be 1-4")
    return _media(f"alarm:{severity}", lambda: generate_music(alarm_prompt(severity)))


@app.get("/api/memory")
def memory() -> dict:
    """Past incidents resembling the current one."""
    from praetor.memory import IncidentMemory, describe

    board = fleet().board
    if not board.signals:
        return {"query": None, "recollections": []}
    query = describe(board.signals[-8:])
    try:
        hits = IncidentMemory(project=PROJECT).recall(query)
    except Exception as exc:
        raise HTTPException(502, f"recall failed: {exc}") from exc
    return {
        "query": query[:280],
        "recollections": [
            {"incident_id": h.incident_id, "summary": h.summary, "root_cause": h.root_cause,
             "resolution": h.resolution, "similarity": round(h.similarity, 3)}
            for h in hits
        ],
    }


@app.get("/api/models")
def models() -> dict:
    """Exactly which models this deployment uses, read from the code that uses
    them rather than from a claim in a document."""
    from praetor.media import models_in_use
    from praetor.memory import EMBED_MODEL
    from praetor.reasoning import DEFAULT_MODELS, MEDIA_LOCATIONS, Tier

    return {
        "reasoning": DEFAULT_MODELS[Tier.REASON],
        "triage": DEFAULT_MODELS[Tier.TRIAGE],
        "embedding": EMBED_MODEL,
        "media": models_in_use(),
        "locations": MEDIA_LOCATIONS,
        "speech": "chirp3-hd",
    }


@app.get("/", response_class=HTMLResponse)
def console() -> str:
    page = Path(__file__).parent / "console.html"
    return page.read_text() if page.exists() else "<h1>Praetor</h1><p>Console not built.</p>"
