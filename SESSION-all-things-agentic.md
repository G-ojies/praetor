# Session handover — All Things Agentic Hackathon

**Event:** All Things Agentic Hackathon (Google · Devpost)
**Track:** The Fortified Enterprise Fleet
**Project:** Praetor
**Sessions:** 18–22 Aug 2026
**Submission deadline:** **31 Aug 2026, 17:00 PDT** — no edits are permitted after it, none

---

## Live things

| | |
|---|---|
| Repository | https://github.com/G-ojies/praetor (public) |
| Console | https://praetor-519854598879.us-central1.run.app (public, rate-limited) |
| Blog post | https://g-ojies.github.io/praetor/ (published — bonus earned) |
| Devpost draft | https://devpost.com/submit-to/30845-all-things-agentic-hackathon/manage/submissions/1143840 |
| GCP project | `praetor-505914`, org `greatojies-org`, $300 trial to 17 Nov |
| Cloud Run | revision `praetor-00009-ff5`, `us-central1`, max 1 instance |

**17 commits · 115 tests · 5,449 lines.**

---

## NOT FINISHED

### 1. The demo video — the only required field still empty

Everything around it is ready. Only the recording itself is outstanding, and it
cannot be done without a GUI session: the rules require the Google Cloud console
visible on screen, which needs a logged-in browser.

```
./scripts/prep_recording.sh take1        # clean namespace, warm, checklist
spectacle --record screen                # KDE Wayland; ffmpeg x11grab CANNOT see the compositor
./scripts/finish_recording.sh ~/Videos/<file>.mp4
```

- Shot list with timings: `docs/video-script.md`
- Full guide: `docs/recording.md`
- Narration already generated: `evidence/voiceover.mp3`, 206s (3:26), 34s of headroom
- **Voiceover on headphones, not speakers**, or it bleeds into the capture
- **Do not cut inside a running command** — "unedited live execution" is a rubric requirement

### 2. Social post — bonus, not yet claimed

Drafts with real links in `docs/social-post.md`. Cannot be done for you: no
credentials for X or LinkedIn.

- `#AllThingsAgentic` must be **in the post itself**, not a reply
- The four-part bug thread will travel further than the announcement — post it
  separately, a day later

### 3. Devpost form — partially filled

| Field | State |
|---|---|
| Project name, elevator pitch | ready to paste, see below |
| Thumbnail | `docs/thumbnail.png` (1200×800, 3:2) |
| Project story | `docs/devpost-story.md` — paste whole file |
| Built with | 25 tags listed below |
| Try it out links | both URLs above |
| Image gallery | `docs/architecture.png`, `docs/console.png`, `docs/remediation-card.png` |
| **Video demo link** | **blocked on (1)** |
| **Category** | **must be The Fortified Enterprise Fleet** — reassignment loses the "Unlikely Hero" criterion |

### 4. Post-recording cleanup

`prep_recording.sh` leaves the deployment on a `takeN` namespace with an empty
chain. Put it back afterwards or the Devpost link shows judges nothing:

```
gcloud run services update praetor --region us-central1 \
  --update-env-vars PRAETOR_NAMESPACE=demo --quiet
```

### 5. Known limitations, documented not hidden

- **Single instance.** The blackboard is durable but not concurrent; two
  containers would restore two copies and diverge. Explained in
  `service/main.py`. Fixing it means putting the blackboard behind the chain's
  transactional discipline.
- **Rate-limit buckets are in-process.** Correct for one instance; needs Redis
  if that changes. Noted in `service/guard.py`.
- **Audit chain ceiling** is Firestore's ~1 write/sec per document. Far above a
  clinical decision rate. A larger fleet would shard per site.
- **Veo and Gemma are unavailable** on this project at every published version.
  Not integrated, and deliberately absent from `/api/models`, which exists to
  report truthfully what runs.

---

## Paste-ready

**Project name** (7/60): `Praetor`

**Elevator pitch** (198/200):

> A fleet of agents watches QC, cold chain and reagent lots for one lab scientist across four rural clinics. It can hold results, quarantine a lot or pull an analyser on its own. It can never release.

**Built with** (25):

```
gemini  gemini-3.7-flash  google-genai-sdk  google-adk  vertex-ai
google-cloud  cloud-run  firestore  pub-sub  secret-manager
gemini-3-pro-image  lyria  chirp-3-hd  gemini-embedding  python
fastapi  docker  pytest  ed25519  webcrypto
javascript  multi-agent  healthcare  laboratory  westgard
```

---

## Scoring position

| Criterion | Weight | State |
|---|---|---|
| Innovation & Operational Utility | 40% | strong — unlikely hero, real autonomy, non-obvious diagnosis |
| Architectural Discipline | 30% | strong — deterministic gate, signed chain, 115 tests |
| Demo & Production Readiness | 30% | README, diagram, thumbnail, story done; **video outstanding** |
| Bonus (max +1.0) | — | **+0.8 banked** (4 extra models +0.6, blog +0.2) · social +0.2 pending |

**Identity decision:** submitting as `greatojies@gmail.com`, so the lane is
Individual/Hobbyist ($10k, 2 slots), not Startup Excellence ($20k, 1 slot).
Startup Excellence needs an incorporated entity submitting from a corporate
email. If a GreYat Labs domain address exists, switching is worth $10k.

---

## What was built

Praetor is a policy-gated control plane for a fleet of autonomous agents
watching a four-site rural clinic laboratory. The reasoning is probabilistic;
the permissions are not.

**The idea it rests on:** every action carries a safety direction. The fleet may
move the lab *toward* safety unattended — hold a batch, quarantine a lot, pull a
drifting analyser — and may *never* move it away, because releasing a patient
result you should not have released is not an incident you can undo. A test
asserts the strong form: no combination of capability, severity, budget or
confidence lets any fail-open action reach `ALLOW`.

**The separation:** detection is deterministic (Westgard multirules, thresholds),
judgement is the model's (correlation, root cause), and permission is neither —
the gate is not a language model. The frontier model is called once across 735
events, with a test asserting it.

### Defects found by running it, not reading it

1. **It pulled the analyser.** Two lots rejecting on one instrument read as an
   instrument fault. Both were in the same failed fridge.
2. **Then it pulled it again.** `lot_storage.get(lot)` returns `None` when a
   record is missing, and `None` is not in the excursed set — so unknown
   provenance counted as *no cold-chain explanation*. Missing data now fails closed.
3. **The circuit breaker ate the fleet.** Bookkeeping counted as failed
   remediation. Standing down should stop it fixing things, not silence it.
4. **The console reported tampering on an intact chain.** JavaScript has one
   number type; Python's `1.0` re-serialises as `"1"`. Live payloads carry
   `confidence: 1.0`. The console now parses numbers as raw wire tokens.
5. **The audit chain dropped writes** under eight concurrent writers.
6. **The blackboard would have outgrown a Firestore document** — unbounded
   signal list against a 1 MiB cap.
7. **The escalation queue vanished on cold start.** A control plane whose claim
   is that it stops and asks cannot forget what it asked.
8. **The terminal recorder rendered blank frames.** A pty ends lines with CRLF
   and the carriage-return handler wiped every line before its own newline.

---

## Regenerating anything

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m pytest tests/ -q          # 115 tests, no credentials needed
./.venv/bin/python scripts/demo.py              # full scenario offline

./.venv/bin/python scripts/make_voiceover.py    # narration
./.venv/bin/python scripts/build_site.py        # blog page
./scripts/make_thumbnail.sh                     # Devpost card
python3 scripts/record_terminal.py --out out.mp4 -- <command>
./.venv/bin/python scripts/seed_memory.py       # incident archive
```

Credentials: `gcloud auth application-default login`, then
`GOOGLE_CLOUD_PROJECT=praetor-505914 GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_LOCATION=global`.

**Vertex endpoint split, undocumented and load-bearing:** Gemini text models
serve from `global` and 404 in every region; Veo and Lyria are regional and
absent from `global`.
