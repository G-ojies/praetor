#!/usr/bin/env bash
# Generate the audit chain's Ed25519 signing key and place it in Secret Manager.
# The private key touches a 600 temp file and nothing else: never the repo,
# never the logs, never Firestore. Verification needs only the public key.
set -euo pipefail

PROJECT="${1:?usage: create_signing_key.sh <project-id> [secret-name]}"
SECRET="${2:-praetor-audit-key}"
PYTHON="${PYTHON:-./.venv/bin/python}"

TMPKEY="$(mktemp)"; chmod 600 "$TMPKEY"
trap 'shred -u "$TMPKEY" 2>/dev/null || rm -f "$TMPKEY"' EXIT

"$PYTHON" - "$TMPKEY" <<'PY'
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key = Ed25519PrivateKey.generate()
open(sys.argv[1], "wb").write(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print(f"public key (safe to publish): {pub.hex()}")
PY

gcloud secrets create "$SECRET" --replication-policy=automatic --project "$PROJECT" --quiet 2>/dev/null || true
gcloud secrets versions add "$SECRET" --data-file="$TMPKEY" --project "$PROJECT" --quiet
echo "signing key stored as secret '$SECRET' in $PROJECT"
