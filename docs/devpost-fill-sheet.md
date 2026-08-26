# Devpost submission — field-by-field fill sheet

Work top to bottom. Every value below is verified against the repo and live URLs.
Two fields are **blocked** until the video is recorded — they are marked 🔴.

- **Event:** All Things Agentic Hackathon (Google · Devpost)
- **Draft URL:** https://devpost.com/submit-to/30845-all-things-agentic-hackathon/manage/submissions/1143840
- **Deadline:** 31 Aug 2026, 17:00 PDT — no edits after it. Submit with time to spare.

---

## 0. Decide the lane FIRST (worth $10k)

- [ ] Submitting from `greatojies@gmail.com` → **Individual / Hobbyist** ($10k, 2 slots).
- [ ] If a **GreYat Labs corporate-domain email** exists, submit from it instead →
      **Startup Excellence** ($20k, 1 slot). This choice is made at account/entry level,
      so settle it before filling anything else.

---

## 1. Project name

```
Praetor
```

## 2. Elevator pitch  (198 / 200 chars — paste exactly)

```
A fleet of agents watches QC, cold chain and reagent lots for one lab scientist across four rural clinics. It can hold results, quarantine a lot or pull an analyser on its own. It can never release.
```

## 3. Thumbnail  (upload)

```
docs/thumbnail.png          (1200×800, 3:2 — the required aspect ratio)
```

## 4. Project story  (paste the WHOLE file into the rich-text "Story" box)

```
docs/devpost-story.md       (113 lines: Inspiration → What it does → How I built it → Challenges → Accomplishments → What I learned → What's next)
```
Markdown pastes cleanly into Devpost's editor. After pasting, eyeball the two
tables (agent roster, safety-direction) — Devpost occasionally needs a blank line
before a table to render it.

## 5. "Built with"  (tags — add all 25, one at a time)

```
gemini  gemini-3.7-flash  google-genai-sdk  google-adk  vertex-ai
google-cloud  cloud-run  firestore  pub-sub  secret-manager
gemini-3-pro-image  lyria  chirp-3-hd  gemini-embedding  python
fastapi  docker  pytest  ed25519  webcrypto
javascript  multi-agent  healthcare  laboratory  westgard
```

## 6. "Try it out" links  (add both)

```
https://praetor-519854598879.us-central1.run.app      (live console)
https://github.com/G-ojies/praetor                    (source)
```
Optional third (the published blog earns the bonus, worth linking here too):
```
https://g-ojies.github.io/praetor/
```

## 7. Image gallery  (upload, in this order)

```
docs/architecture.png        (system diagram — lead with it)
docs/console.png             (the console incl. the control chart)
docs/remediation-card.png    (the multimodal remediation card)
```

## 8. 🔴 Video demo link  (REQUIRED — blocked until recorded)

- [ ] Record via `./scripts/prep_recording.sh take1` → follow `docs/video-script.md`.
- [ ] Upload to **YouTube or Vimeo, set PUBLIC** (not unlisted-only if the form asks for public).
- [ ] Paste the watch URL here, then into the form.
```
VIDEO URL: ______________________________________________
```

## 9. Category / track  (dropdown)

```
The Fortified Enterprise Fleet
```
**Do not reassign.** Any other track loses the "Unlikely Hero" scoring criterion.

---

## Before you click Submit

- [ ] **Namespace is back to `demo`**, or the console link shows judges an empty/`takeN` chain:
      ```
      gcloud run services update praetor --region us-central1 \
        --update-env-vars PRAETOR_NAMESPACE=demo --quiet
      ```
      Then load the console once to confirm it shows a populated, `chain verified` state.
- [ ] Video field (§8) filled and the link plays in an incognito window.
- [ ] Category is **The Fortified Enterprise Fleet**.
- [ ] All three gallery images present; thumbnail is the 3:2 one.
- [ ] Lane/email (§0) is the one you intend.

## Bonus points already banked (nothing to do)

- +0.6 — four extra models beyond text Gemini (image, Lyria, Chirp, embedding)
- +0.2 — blog post published: https://g-ojies.github.io/praetor/
- +0.2 — social post with `#AllThingsAgentic` — **pending**, see `docs/social-post.md`
