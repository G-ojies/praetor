"""Durable blackboard.

The audit chain was persisted first because losing a decision record is
unforgivable. The blackboard is the other half: signals, quarantined lots, held
batches, the pending escalation queue. Losing that is merely bad -- the fleet
re-derives most of it from the next events -- but "merely bad" here means
quarantining a lot twice, or forgetting that a batch is already held, and both
of those are visible to a scientist as the system being unreliable.

The design constraint that shapes this: a blackboard is read on every event and
written only when something changes. So it is cached in memory, written through
on mutation, and reloaded on cold start. Firestore sees a write per change, not
a write per reading, which matters when the cold-chain stream is a reading per
unit per hour and almost all of them are boring.

Concurrency is handled by not needing it. The service runs one instance for the
reasons written up in service/main.py; this class makes the *restart* case
correct, which is the one Cloud Run actually forces on us by scaling to zero.
"""

from __future__ import annotations

from typing import Any

from praetor.agents.base import Blackboard, Signal
from praetor.common.types import ActionProposal, Severity

DOC = "blackboard"


def _signal_to_dict(s: Signal) -> dict[str, Any]:
    return {
        "kind": s.kind, "source": s.source, "subject": s.subject,
        "at": s.at, "severity": int(s.severity), "summary": s.summary, "facts": s.facts,
    }


def _signal_from_dict(d: dict[str, Any]) -> Signal:
    return Signal(
        kind=d["kind"], source=d["source"], subject=d["subject"], at=d["at"],
        severity=Severity(int(d["severity"])), summary=d["summary"], facts=d.get("facts", {}),
    )


def to_dict(board: Blackboard) -> dict[str, Any]:
    return {
        "signals": [_signal_to_dict(s) for s in board.signals],
        "seen_keys": sorted(board.seen_keys),
        "escalations": [p.to_dict() for p in board.escalations],
        "held_batches": sorted(board.held_batches),
        "quarantined_lots": sorted(board.quarantined_lots),
        "offline_instruments": sorted(board.offline_instruments),
        "lot_storage": dict(board.lot_storage),
        "root_cause": board.root_cause,
        "root_cause_confidence": board.root_cause_confidence,
    }


def from_dict(d: dict[str, Any]) -> Blackboard:
    board = Blackboard()
    board.signals = [_signal_from_dict(s) for s in d.get("signals", [])]
    board.seen_keys = set(d.get("seen_keys", []))
    board.escalations = [ActionProposal.from_dict(p) for p in d.get("escalations", [])]
    board.held_batches = set(d.get("held_batches", []))
    board.quarantined_lots = set(d.get("quarantined_lots", []))
    board.offline_instruments = set(d.get("offline_instruments", []))
    board.lot_storage = dict(d.get("lot_storage", {}))
    board.root_cause = d.get("root_cause")
    board.root_cause_confidence = float(d.get("root_cause_confidence", 0.0))
    return board


class FirestoreBlackboard:
    """Write-through persistence for one namespace's blackboard."""

    def __init__(self, project: str, namespace: str = "default", client: Any = None) -> None:
        from google.cloud import firestore

        self._db = client or firestore.Client(project=project)
        self._ref = self._db.collection(DOC).document(namespace)

    def load(self) -> Blackboard:
        snap = self._ref.get()
        return from_dict(snap.to_dict()) if snap.exists else Blackboard()

    def save(self, board: Blackboard) -> None:
        self._ref.set(to_dict(board))


class PersistentBlackboard(Blackboard):
    """A Blackboard that writes itself through to a store on every mutation.

    Subclassing rather than wrapping, so the agents keep the plain `Blackboard`
    interface and cannot tell the difference. `add` is the only mutation that
    goes through a method; the set and scalar mutations are done directly by
    agents on the attributes, so the orchestrator calls `flush()` once per
    event rather than trying to intercept every field.
    """

    def __init__(self, store: FirestoreBlackboard | None = None) -> None:
        super().__init__()
        self._store = store
        self._dirty = False

    @classmethod
    def restored(cls, store: FirestoreBlackboard) -> "PersistentBlackboard":
        board = cls(store)
        loaded = store.load()
        board.signals = loaded.signals
        board.seen_keys = loaded.seen_keys
        board.escalations = loaded.escalations
        board.held_batches = loaded.held_batches
        board.quarantined_lots = loaded.quarantined_lots
        board.offline_instruments = loaded.offline_instruments
        board.lot_storage = loaded.lot_storage
        board.root_cause = loaded.root_cause
        board.root_cause_confidence = loaded.root_cause_confidence
        return board

    def add(self, signal: Signal) -> Signal:
        self._dirty = True
        return super().add(signal)

    def touch(self) -> None:
        self._dirty = True

    def flush(self) -> bool:
        """Persist if anything changed. Returns whether a write happened."""
        if self._store is None or not self._dirty:
            return False
        self._store.save(self)
        self._dirty = False
        return True

    def snapshot_differs(self, previous: dict[str, Any]) -> bool:
        return to_dict(self) != previous
