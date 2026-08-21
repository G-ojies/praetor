#!/usr/bin/env bash
# Get the deployment into a clean, warm, reproducible state for a take.
#
#   ./scripts/prep_recording.sh take1
#
# Switching namespace gives a genuinely empty chain, so the run you record
# reproduces exactly and the console opens on "chain verified - 0 entries"
# rather than a pile of state from an earlier take.
set -euo pipefail
cd "$(dirname "$0")/.."

NS="${1:-take1}"
PROJECT=praetor-505914
REGION=us-central1
URL="https://praetor-519854598879.us-central1.run.app"

printf '\n  switching to namespace %s ...\n' "$NS"
gcloud run services update praetor --region "$REGION" --project "$PROJECT" \
  --update-env-vars "PRAETOR_NAMESPACE=$NS" --quiet >/dev/null

printf '  warming the service (so the first click is not a cold start) ...\n'
for _ in 1 2 3; do curl -s -m 60 -o /dev/null "$URL/health" || true; done

CHAIN=$(curl -s -m 60 "$URL/api/audit" | python3 -c 'import json,sys; print(json.load(sys.stdin)["length"])')
PEND=$(curl -s -m 60 "$URL/api/escalations" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["pending"]))')

cat <<EOF

  namespace   $NS
  chain       $CHAIN entries
  escalations $PEND pending

  Ready when chain is 0. If it is not, pick a new namespace: ./scripts/prep_recording.sh take2

  ---------------------------------------------------------------------
  BEFORE YOU HIT RECORD

   1. Open these, in this order, so you never alt-tab into something raw:
        terminal, here, in ~/Development/praetor
        browser  $URL
        browser  Cloud Run  -> the praetor service page
        browser  Firestore  -> Data -> audit_entries
        browser  Pub/Sub    -> praetor-telemetry

   2. Turn on Do Not Disturb. Close anything with a private tab title.

   3. Put the voiceover on HEADPHONES, not speakers:
        mpv evidence/voiceover.mp3     (or any player)
      On speakers it bleeds into the capture and you re-record.

   4. Start recording:
        spectacle --record screen

   5. Follow docs/video-script.md. The one command you type on camera:
        ./.venv/bin/python scripts/publish.py --hours 76

   6. Stop recording. Then:
        ./scripts/finish_recording.sh ~/Videos/<file>.mp4
  ---------------------------------------------------------------------

EOF
