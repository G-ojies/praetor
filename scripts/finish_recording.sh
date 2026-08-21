#!/usr/bin/env bash
# Lay the voiceover onto a screen capture and check it against the 4:00 limit.
#
#   ./scripts/finish_recording.sh ~/Videos/capture.mp4 [audio-offset-seconds]
#
# The offset shifts the narration if your actions drifted from the shot list;
# nudging it beats re-recording. Per-segment timings are in
# evidence/voiceover-timings.json.
set -euo pipefail
cd "$(dirname "$0")/.."

VIDEO="${1:?usage: finish_recording.sh <capture.mp4> [offset-seconds]}"
OFFSET="${2:-0}"
VO=evidence/voiceover.mp3
OUT=evidence/praetor-demo.mp4
LIMIT=240

[ -f "$VIDEO" ] || { echo "  no such file: $VIDEO"; exit 1; }
[ -f "$VO" ] || { echo "  missing $VO -- run scripts/make_voiceover.py"; exit 1; }

dur() { ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$1"; }

printf '\n  capture   %6.1fs\n' "$(dur "$VIDEO")"
printf '  voiceover %6.1fs  (offset %ss)\n' "$(dur "$VO")" "$OFFSET"

ffmpeg -y -loglevel error -i "$VIDEO" -itsoffset "$OFFSET" -i "$VO" \
  -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest "$OUT"

TOTAL=$(dur "$OUT")
printf '\n  %s  %.1fs (%.2f min)\n' "$OUT" "$TOTAL" "$(echo "$TOTAL/60" | bc -l)"

if (( $(echo "$TOTAL > $LIMIT" | bc -l) )); then
  printf '  OVER THE %ss LIMIT. Trim the tail, not the head -- the close is\n' "$LIMIT"
  printf '  the least load-bearing part:\n'
  printf '    ffmpeg -i %s -t 235 -c copy evidence/praetor-demo-final.mp4\n\n' "$OUT"
else
  printf '  within the %ss limit.\n\n' "$LIMIT"
fi

cat <<'EOF'
  BEFORE UPLOADING
    [ ] Google Cloud console visible on screen
    [ ] "chain verified" visible in the console header
    [ ] the escalation refusal is shown, not just narrated
    [ ] no cut inside a command's execution
    [ ] no tokens, keys or private tabs visible
    [ ] public on YouTube or Vimeo

  AFTER UPLOADING
    Put the deployment back on a populated namespace so the Devpost
    "try it out" link shows a judge a working console, not an empty one:

      gcloud run services update praetor --region us-central1 \
        --update-env-vars PRAETOR_NAMESPACE=demo --quiet
EOF
