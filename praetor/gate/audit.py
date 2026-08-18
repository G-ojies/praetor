"""Append-only, hash-chained, Ed25519-signed decision log.

Why not just write rows to Firestore and call it an audit log: a plain table is
only as trustworthy as whoever holds write access to it. Here every entry commits
to its predecessor's hash, and the whole chain is signed by a key the control
plane holds but the console does not need. That buys two properties worth having
when an autonomous fleet is touching production:

* **Tamper evidence.** Editing or deleting entry *n* invalidates every hash from
  *n* onward. You cannot quietly rewrite what the fleet decided last Tuesday.
* **Independent verification.** `verify_chain` needs only the public key, so the
  console (and a judge, and an auditor) can check the history without being
  trusted to write it.

The signing key lives in Secret Manager in production and is generated
ephemerally in tests and local runs.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

GENESIS_HASH = "0" * 64


def canonical(payload: Any) -> bytes:
    """Deterministic JSON. Any two processes must hash a payload identically."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    prev_hash: str
    kind: str
    payload: dict[str, Any]
    recorded_at: float
    entry_hash: str
    signature: str  # hex

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "kind": self.kind,
            "payload": self.payload,
            "recorded_at": self.recorded_at,
            "entry_hash": self.entry_hash,
            "signature": self.signature,
        }


def compute_hash(seq: int, prev_hash: str, kind: str, payload: dict, recorded_at: float) -> str:
    return hashlib.sha256(
        canonical(
            {
                "seq": seq,
                "prev_hash": prev_hash,
                "kind": kind,
                "payload": payload,
                "recorded_at": recorded_at,
            }
        )
    ).hexdigest()


class AuditChain:
    """In-memory chain. `FirestoreAuditChain` persists it; the logic is identical."""

    def __init__(self, signing_key: Ed25519PrivateKey | None = None, clock=None) -> None:
        self._key = signing_key or Ed25519PrivateKey.generate()
        self._clock = clock or time.time
        self._entries: list[AuditEntry] = []

    # -- keys ---------------------------------------------------------------
    @property
    def public_key_hex(self) -> str:
        raw = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()

    @staticmethod
    def load_public_key(hex_key: str) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key))

    # -- writing ------------------------------------------------------------
    @property
    def head_hash(self) -> str:
        return self._entries[-1].entry_hash if self._entries else GENESIS_HASH

    def append(self, kind: str, payload: dict[str, Any]) -> AuditEntry:
        seq = len(self._entries)
        prev = self.head_hash
        recorded_at = self._clock()
        h = compute_hash(seq, prev, kind, payload, recorded_at)
        sig = self._key.sign(bytes.fromhex(h)).hex()
        entry = AuditEntry(seq, prev, kind, payload, recorded_at, h, sig)
        self._entries.append(entry)
        return entry

    # -- reading ------------------------------------------------------------
    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class ChainVerification:
    ok: bool
    checked: int
    failures: list[str] = field(default_factory=list)


def verify_chain(entries: Iterable[dict | AuditEntry], public_key_hex: str) -> ChainVerification:
    """Recompute every hash, check every link, verify every signature.

    Accepts dicts so the console can verify straight off the wire.
    """
    pub = AuditChain.load_public_key(public_key_hex)
    failures: list[str] = []
    expected_prev = GENESIS_HASH
    count = 0

    for raw in entries:
        e = raw.to_dict() if isinstance(raw, AuditEntry) else raw
        count += 1
        seq = e["seq"]

        if e["prev_hash"] != expected_prev:
            failures.append(f"seq {seq}: broken link (expected prev {expected_prev[:12]}…)")

        recomputed = compute_hash(seq, e["prev_hash"], e["kind"], e["payload"], e["recorded_at"])
        if recomputed != e["entry_hash"]:
            failures.append(f"seq {seq}: payload altered (hash mismatch)")

        try:
            pub.verify(bytes.fromhex(e["signature"]), bytes.fromhex(e["entry_hash"]))
        except (InvalidSignature, ValueError):
            failures.append(f"seq {seq}: bad signature")

        expected_prev = e["entry_hash"]

    return ChainVerification(ok=not failures, checked=count, failures=failures)
