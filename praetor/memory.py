"""Incident memory: has the fleet seen this before?

A lab that has run for years has usually met the problem already. The knowledge
lives in the head of whoever was on shift, and it leaves when they do. This is
the cheap version of keeping it: every resolved incident is embedded and stored,
and when a new one opens the diagnostician is handed the closest past matches
before it reasons.

Embeddings rather than keyword search because the same failure is described
differently every time ("fridge warm", "cold chain excursion", "compressor
noise then QC drift") and a scientist searching their own notes for the right
phrase is exactly the friction this is supposed to remove.

Deliberately small: cosine similarity over a Firestore collection, no vector
index, no dedicated cluster. A four-site clinic network produces a few hundred
incidents a year, and a linear scan over a few hundred vectors costs less than
the index would.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

EMBED_MODEL = os.environ.get("PRAETOR_EMBED_MODEL", "gemini-embedding-001")
COLLECTION = "incident_memory"

# Calibrated against real embeddings rather than guessed, and the calibration
# is narrower than it looks. Every pair of laboratory sentences is somewhat
# alike: an unrelated query about a noisy centrifuge scores about 0.60
# against a cold-chain incident, because both are clinical English about
# equipment. Genuine matches run 0.77 to 0.86. So the usable window is roughly
# 0.60 to 0.77 and the floor belongs just above the noise, not just below the
# signal:
#
#   0.55  returns three confident-looking matches for a question with no
#         answer in the archive, which is worse than returning nothing
#   0.78  discards a real match: an instrument fault genuinely resembling a
#         past photometer failure at 0.772
#   0.72  clears the noise with margin and keeps the true positives
#
# Worth re-measuring if the embedding model changes; these numbers are a
# property of gemini-embedding-001, not of the domain.
SIMILARITY_FLOOR = 0.72


@dataclass
class Recollection:
    incident_id: str
    summary: str
    root_cause: str
    resolution: str
    similarity: float
    facts: dict[str, Any] = field(default_factory=dict)


def embed(text: str) -> list[float]:
    from google import genai

    client = genai.Client()
    response = client.models.embed_content(model=EMBED_MODEL, contents=text)
    return list(response.embeddings[0].values)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def describe(signals: list) -> str:
    """One text blob per incident. What gets embedded, and what a human would
    have written in the shift book."""
    return " | ".join(f"{s.kind}: {s.summary}" for s in signals)


class IncidentMemory:
    """Firestore-backed. Falls back to a plain list when handed no client, so
    the tests and the offline demo exercise the same retrieval path."""

    def __init__(self, project: str | None = None, client: Any = None, embedder=embed) -> None:
        self._embed = embedder
        self._local: list[dict] = []
        self._db = None
        if client is not None or project is not None:
            from google.cloud import firestore

            self._db = client or firestore.Client(project=project)

    def _all(self) -> list[dict]:
        if self._db is None:
            return list(self._local)
        return [d.to_dict() for d in self._db.collection(COLLECTION).stream()]

    def remember(self, incident_id: str, summary: str, root_cause: str,
                 resolution: str, facts: dict | None = None) -> dict:
        record = {
            "incident_id": incident_id,
            "summary": summary,
            "root_cause": root_cause,
            "resolution": resolution,
            "facts": facts or {},
            "vector": self._embed(f"{summary} Root cause: {root_cause}"),
            "model": EMBED_MODEL,
        }
        if self._db is None:
            self._local.append(record)
        else:
            self._db.collection(COLLECTION).document(incident_id).set(record)
        return record

    def recall(self, summary: str, k: int = 3, threshold: float = SIMILARITY_FLOOR) -> list[Recollection]:
        """Closest past incidents, best first.

        The threshold matters more than k. Handing the diagnostician three
        weakly-related incidents is worse than handing it none: it invites the
        model to force a resemblance that is not there, which is precisely the
        failure mode a confident language model is prone to.
        """
        stored = self._all()
        if not stored:
            return []
        query = self._embed(summary)
        scored = [
            Recollection(
                incident_id=r["incident_id"], summary=r["summary"],
                root_cause=r["root_cause"], resolution=r["resolution"],
                similarity=cosine(query, r["vector"]), facts=r.get("facts", {}),
            )
            for r in stored
        ]
        scored.sort(key=lambda r: r.similarity, reverse=True)
        return [r for r in scored if r.similarity >= threshold][:k]
