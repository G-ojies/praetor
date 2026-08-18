"""Firestore-backed audit chain.

The in-memory chain in `audit.py` is correct but single-process. Moving it to
Firestore raises a problem that does not exist in memory: a hash chain has a
*head*, and two agents appending at once must not both build on the same
predecessor. If they do, the chain forks -- and a forked chain is a chain that
cannot be verified, which defeats the entire point of signing it.

So the head is a document, and every append is a Firestore transaction that
reads the head, writes the entry, and advances the head atomically. Firestore
aborts and retries the transaction if the head moved underneath it, which gives
strictly serialised sequence numbers across every instance of every agent, on
any number of Cloud Run containers.

The signing key comes from Secret Manager. It is never written to Firestore,
never logged, and never leaves the control plane -- the console verifies the
chain with the public key alone.
"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Any, Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from praetor.gate.audit import GENESIS_HASH, AuditEntry, compute_hash

ENTRIES = "audit_entries"
META = "audit_meta"
HEAD = "head"

# A hash chain is serial by construction: every entry commits to its
# predecessor, so appends cannot be parallelised, and the head document is a
# contention point by design rather than by accident. Two consequences, both
# handled here rather than discovered in production:
#
#   1. Threads inside one container must not fight each other over the head.
#      They serialise on a process-local lock first, which costs nothing and
#      removes the great majority of contention before it reaches Firestore.
#   2. Containers still contend with each other, so the transaction retries
#      with exponential backoff and jitter. Firestore's default of 5 immediate
#      attempts is not enough: at eight concurrent writers it exhausts them and
#      the append fails, which for an audit log means losing a decision record.
#
# The residual limit is Firestore's own: roughly one sustained write per second
# to a single document. That is far above the decision rate of a clinical agent
# fleet, but it is a real ceiling and it is written down rather than assumed
# away. A fleet that outgrew it would shard the chain per site and verify each
# shard independently.
TXN_ATTEMPTS = 5
MAX_RETRIES = 6
BASE_BACKOFF_S = 0.12

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _namespace_lock(ns: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(ns, threading.Lock())


def load_signing_key(project: str, secret: str = "praetor-audit-key") -> Ed25519PrivateKey:
    """Fetch the Ed25519 private key from Secret Manager.

    Falls back to a generated ephemeral key only when explicitly asked, so a
    misconfigured deployment fails loudly rather than silently signing with a
    key nobody can verify against.
    """
    if os.environ.get("PRAETOR_EPHEMERAL_KEY") == "1":
        return Ed25519PrivateKey.generate()

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret}/versions/latest"
    payload = client.access_secret_version(request={"name": name}).payload.data
    return serialization.load_pem_private_key(payload, password=None)


class FirestoreAuditChain:
    """Same surface as `AuditChain`, durable and safe under concurrency."""

    def __init__(
        self,
        project: str,
        signing_key: Ed25519PrivateKey | None = None,
        client: Any = None,
        namespace: str = "default",
        clock=None,
    ) -> None:
        from google.cloud import firestore

        self._db = client or firestore.Client(project=project)
        self._key = signing_key or load_signing_key(project)
        self._ns = namespace
        self._clock = clock or time.time

    # -- collections --------------------------------------------------------
    def _entries(self):
        return self._db.collection(ENTRIES).document(self._ns).collection("items")

    def _head_ref(self):
        return self._db.collection(META).document(self._ns)

    # -- keys ---------------------------------------------------------------
    @property
    def public_key_hex(self) -> str:
        raw = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        return raw.hex()

    # -- writing ------------------------------------------------------------
    def append(self, kind: str, payload: dict[str, Any]) -> AuditEntry:
        """Append one entry. Serialised, durable, and never silently dropped."""
        from google.api_core import exceptions as gexc

        last: Exception | None = None
        with _namespace_lock(self._ns):
            for attempt in range(MAX_RETRIES):
                try:
                    return self._append_once(kind, payload)
                except (gexc.Aborted, ValueError) as exc:
                    # `ValueError` is what the Firestore client raises once it
                    # has burned its own internal attempts on contention.
                    last = exc
                    if attempt == MAX_RETRIES - 1:
                        break
                    time.sleep(BASE_BACKOFF_S * (2 ** attempt) * (0.5 + random.random()))

        raise RuntimeError(
            f"audit append failed after {MAX_RETRIES} attempts under contention; "
            f"refusing to proceed with an unrecorded decision"
        ) from last

    def _append_once(self, kind: str, payload: dict[str, Any]) -> AuditEntry:
        from google.cloud import firestore

        key, clock, entries, head_ref = self._key, self._clock, self._entries(), self._head_ref()

        @firestore.transactional
        def _append(txn) -> AuditEntry:
            snap = head_ref.get(transaction=txn)
            if snap.exists:
                seq = snap.get("seq") + 1
                prev = snap.get("head_hash")
            else:
                seq, prev = 0, GENESIS_HASH

            recorded_at = clock()
            h = compute_hash(seq, prev, kind, payload, recorded_at)
            sig = key.sign(bytes.fromhex(h)).hex()
            entry = AuditEntry(seq, prev, kind, payload, recorded_at, h, sig)

            # Sequence-padded document id, so a plain lexicographic listing is
            # already in chain order and needs no index to read back correctly.
            txn.set(entries.document(f"{seq:012d}"), entry.to_dict())
            txn.set(head_ref, {"seq": seq, "head_hash": h, "updated_at": recorded_at})
            return entry

        return _append(self._db.transaction(max_attempts=TXN_ATTEMPTS))

    # -- reading ------------------------------------------------------------
    @property
    def head_hash(self) -> str:
        snap = self._head_ref().get()
        return snap.get("head_hash") if snap.exists else GENESIS_HASH

    @property
    def entries(self) -> list[AuditEntry]:
        return [AuditEntry(**doc.to_dict()) for doc in self._stream()]

    def _stream(self) -> Iterable[Any]:
        return self._entries().order_by("__name__").stream()

    def read_dicts(self) -> list[dict]:
        """What the console fetches. Verifiable with the public key alone."""
        return [doc.to_dict() for doc in self._stream()]

    def __len__(self) -> int:
        snap = self._head_ref().get()
        return (snap.get("seq") + 1) if snap.exists else 0
