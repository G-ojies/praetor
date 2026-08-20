# Recording the demo

The voiceover is already generated: **`evidence/voiceover.mp3`**, 206 seconds (3:26), produced by
the same Chirp 3 HD voice the product uses for its own handovers. You record the screen to match
it, then mux the two together.

Total budget is 4:00. The narration ends at 3:26, so you have 34 seconds of slack.

---

## Why not ffmpeg

You are on KDE Wayland. `ffmpeg -f x11grab` only sees XWayland surfaces — I tested it and a
12-frame capture came back at 2.3 KB, which is a black screen. Your browser and the Cloud Console
would not appear.

**Spectacle 6.3.5 is installed and records natively through KWin.** Use that.

---

## 1. Set the stage

```bash
cd ~/Development/praetor
export PATH="$PATH:$HOME/google-cloud-sdk/bin"
export GOOGLE_CLOUD_PROJECT=praetor-505914

# Warm the service so the first click is not a cold start.
TOK=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOK" \
  https://praetor-519854598879.us-central1.run.app/health

# Proxy the private service so the browser can reach it.
gcloud run services proxy praetor --region us-central1 --project praetor-505914
# leave this running -> http://localhost:8080
```

Open and arrange, so you never alt-tab to something unprepared:

1. **Terminal**, in `~/Development/praetor`
2. **Browser** at `http://localhost:8080`
3. **Browser tab 2** at the Cloud Run service page in the Google Cloud console
4. **Browser tab 3** at Firestore → Data → `audit_entries`
5. **Browser tab 4** at Pub/Sub → `praetor-telemetry`

Then check your screen for anything you would rather not publish: other terminals, notifications,
bookmarks bar, tab titles. Turn on Do Not Disturb.

## 2. Reset to a clean state

Record against a fresh namespace so the run reproduces exactly.

```bash
gcloud run services update praetor --region us-central1 \
  --update-env-vars PRAETOR_NAMESPACE=take1 --quiet
```

Reload `localhost:8080`. It should show an empty fleet and `chain verified · 0 entries`.

## 3. Record

```bash
spectacle --record screen
```

Stop with the tray icon or the same shortcut. Spectacle writes to `~/Videos` by default.

Follow **`docs/video-script.md`** for what to do and when. Play `evidence/voiceover.mp3` on
headphones while you record so your actions land under the right narration — do **not** play it on
speakers, or it will bleed into the capture.

### The beats, with their narration times

| Time | On screen | Narration |
|---|---|---|
| 0:00 | Architecture diagram, or the drift | the problem |
| 0:30 | README safety-direction table | the one rule |
| 0:52 | `./.venv/bin/python scripts/publish.py --hours 76` then the console | the live run |
| 1:42 | Type your name, click **Approve** | the human decision |
| 2:08 | Cloud Run → Firestore → Pub/Sub tabs | Google Cloud proof |
| 2:25 | **Illustrate the fix**, **Speak the handover**, Seen before? | the other models |
| 2:58 | `./.venv/bin/python -m pytest tests/ -q` | the close |

**Do not cut inside a running command.** The rubric asks for unedited live execution, and the
`publish.py` run plus the console refresh is the single most important thirty seconds in the video.

## 4. Mux the voiceover onto the capture

```bash
VIDEO=~/Videos/<the-file-spectacle-wrote>.mp4

ffmpeg -i "$VIDEO" -i evidence/voiceover.mp3 \
  -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest \
  evidence/praetor-demo.mp4

ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \
  evidence/praetor-demo.mp4
```

If the capture is longer than 3:26, trim the tail rather than the head — the close is the least
load-bearing part:

```bash
ffmpeg -i evidence/praetor-demo.mp4 -t 235 -c copy evidence/praetor-demo-trimmed.mp4
```

### If your timing drifted

Nudge the whole narration instead of re-recording:

```bash
# start the voiceover 2.5s later
ffmpeg -i "$VIDEO" -itsoffset 2.5 -i evidence/voiceover.mp3 \
  -map 0:v -map 1:a -c:v copy -c:a aac -shortest evidence/praetor-demo.mp4
```

Per-segment timings are in `evidence/voiceover-timings.json` if you would rather re-cut the
narration around what you actually captured. Regenerate the whole track with different wording or
slots via `scripts/make_voiceover.py`.

## 5. Before uploading

- [ ] Under 4:00
- [ ] Google Cloud console visible on screen
- [ ] `chain verified` visible in the console header
- [ ] The escalation refusal is on screen, not just described
- [ ] No cut inside a command's execution
- [ ] No tokens, keys or private tabs visible
- [ ] Public on YouTube or Vimeo
- [ ] English audio — the Chirp track is en-GB; add subtitles only if you overdub

## 6. Put the namespace back

```bash
gcloud run services update praetor --region us-central1 \
  --update-env-vars PRAETOR_NAMESPACE=demo --quiet
```
