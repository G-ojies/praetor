# Demo video — shot list

**Hard limits:** 4 minutes maximum · YouTube or Vimeo, public · English or English subtitles ·
must show *unedited, live execution* · must show the backend running on Google Cloud.

**Do not:** speed up the terminal, cut mid-command, or show a pre-recorded result. The rubric asks
for unedited live execution and judges have seen every trick.

---

## Before you record

```bash
cd ~/Development/praetor
export PATH="$PATH:$HOME/google-cloud-sdk/bin"

# 1. Reset the demo namespace so the run is clean and reproducible on camera.
gcloud firestore databases delete-documents ... # or simply use a fresh namespace:
#   redeploy with PRAETOR_NAMESPACE=take1

# 2. Warm the service so the first request is not a cold start.
TOK=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOK" \
  https://praetor-519854598879.us-central1.run.app/health

# 3. Open a local proxy so the browser can reach the private service.
gcloud run services proxy praetor --region us-central1 --project praetor-505914
#    -> http://localhost:8080
```

Have open, in this order: **terminal**, **browser at localhost:8080**, **Google Cloud console on
the Cloud Run service page**.

---

## 0:00 – 0:30 · The problem

*Screen: the Levey-Jennings drift, or just the terminal.*

> A medical laboratory scientist covering four rural clinics is also the QC officer, the inventory
> manager and the cold-chain custodian.
>
> A fridge compressor degrades overnight. The reagent inside slowly loses potency. Control results
> drift down — and stay inside two standard deviations for forty hours. Invisible on a control
> chart. By the time Westgard rules reject, a day and a half of patient results have gone out on a
> reagent that was already wrong.
>
> Nobody was negligent. The signal was spread across three systems that don't talk to each other.

## 0:30 – 1:00 · What it is, and the one rule

*Screen: `docs/architecture.png`, then the safety-direction table in the README.*

> Praetor is a fleet of agents watching QC, cold chain and reagent lots, with one rule underneath
> it: **the fleet may move the lab toward safety on its own, and never away from it.**
>
> Hold a batch, quarantine a lot, pull a drifting analyser — autonomous. Release a patient result —
> never. Not at any confidence.

## 1:00 – 1:50 · Live run *(the unedited execution the rubric wants)*

*Screen: terminal. Type it; do not paste a pre-baked scroll.*

```bash
./.venv/bin/python scripts/publish.py --hours 76
```

> These are going through Cloud Pub/Sub, not straight into the process.

*Switch to the browser, refresh the console.*

Point at, in order:

1. **`chain verified · N entries`** in the header — "that's verified in this browser, from the
   public key. The page can prove the history is intact and has no ability to write it."
2. **Both lots quarantined at hour 32** — "a full day before QC rejected anything. The cold chain
   is the leading indicator."
3. **Offline instruments: none** — "two lots were failing on the same analyser. A naive system
   pulls the instrument. Both lots were in the same failed fridge — the common factor is the
   refrigerator. This clinic has one analyser; taking it offline turns a reagent problem into an
   outage."
4. **The escalation card** — "the fleet wants to release a held batch. Gemini diagnosed this at
   0.85 confidence. The gate refused."

## 1:50 – 2:20 · The human decision

*Type your name into the escalation card. Click Approve.*

> Approvals are attributable — anonymous ones are refused. And the approval is itself an audit
> entry, because for a fail-open action the accountable event is the approval, not the request.

*Refresh. Point at the new `ratification` entry and the still-green seal.*

## 2:20 – 2:50 · Google Cloud proof *(required)*

*Switch to the Google Cloud console. Show, on screen:*

- **Cloud Run** → the `praetor` service, revision, and the `.run.app` URL
- **Firestore** → `audit_entries` with the chain, and `incident_memory`
- **Pub/Sub** → the `praetor-telemetry` topic and `praetor-ingest` subscription

> Backend is on Cloud Run. State is in Firestore. Telemetry is Pub/Sub. Reasoning is Gemini 3.7
> Flash on Vertex.

## 2:50 – 3:30 · The other models, briefly

*Back in the console.* Click **Illustrate the fix**.

> The person standing at that fridge at six in the morning is whoever opened the clinic, not the
> scientist. Written steps assume lab training. This doesn't — and the temperature on it is the
> measured value, not one the model invented.

Click **Speak the handover**. Let two seconds play.

> Because the scientist covering four sites is usually driving between them.

*Point at "Seen before?"*

> And it checks whether this has happened before. It found one match and ignored four. Saying
> nothing beats forcing a resemblance.

## 3:30 – 4:00 · Close

*Screen: the test run.*

```bash
./.venv/bin/python -m pytest tests/ -q
```

> A hundred tests, named after what must never happen.
> `test_no_fail_open_action_is_reachable_by_any_grant`.
> `test_the_analyser_is_never_taken_out_of_service`.
> `test_unknown_lot_provenance_never_implicates_the_analyser` — that one is there because it did,
> once, when a record was missing and the code read the gap as evidence.
>
> Autonomy isn't the hard part. Deciding what the autonomy is forbidden to do, and proving it
> can't, is the hard part.

---

## If you overrun

Cut in this order — each is the least load-bearing remaining:

1. The Lyria alarm (skip it; the image and speech carry the multimodal point)
2. The "Seen before?" panel
3. The problem framing at 0:00 — open on the architecture diagram instead

**Never cut:** the live run, the escalation refusal, or the Google Cloud console. Those are
required or load-bearing for the rubric.

## Checklist before uploading

- [ ] Under 4:00
- [ ] Public on YouTube or Vimeo, not unlisted-only if the form asks for public
- [ ] Google Cloud console visible on screen
- [ ] No cuts inside a command's execution
- [ ] Audio audible, or subtitles burned in
- [ ] No API keys, tokens or project numbers you would rather not publish visible in the terminal
