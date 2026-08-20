# Social posts

## X / LinkedIn — primary

> I built an agent fleet that runs a rural clinic laboratory. The design rule it rests on took
> three attempts to state:
>
> **The fleet may move the lab toward safety on its own. It may never move it away from safety,
> however confident it is.**
>
> Hold a batch, quarantine a reagent lot, pull a drifting analyser — all autonomous, all reversible,
> all fail closed.
>
> Release a patient result? Never. Not at 0.99 confidence. There's a test asserting no combination
> of permission, severity, budget or confidence can reach it.
>
> The gate that decides is deterministic. It is not a model. That's the whole point — you can
> reason about what the fleet is *permitted* to do without reasoning about what a model might say.
>
> Built on Gemini 3.7 Flash + Cloud Run + Firestore for #AllThingsAgentic
>
> [repo link]

## X — the bug thread (post separately, it travels further)

> An agent bug that nearly shipped, in four parts. 🧵

> **1/** Praetor watches QC across four rural clinics. Two reagent lots start failing on the same
> analyser. Obvious conclusion: the analyser is broken. Take it offline.
>
> Wrong. Both lots were in the *same failed fridge*. The common factor was the refrigerator.

> **2/** Counting distinct failing lots reads a reagent problem as an instrument fault — and takes
> a rural clinic's **only** analyser out of service. A quality incident becomes an outage.
>
> Fixed it by checking where each lot was stored.

> **3/** Then it pulled the analyser again.
>
> `lot_storage.get(lot)` returns `None` when the record is missing. `None` isn't in the set of
> failed fridges. So a lot with unknown provenance counted as "no cold-chain explanation."
>
> A gap in its own bookkeeping → destructive action.

> **4/** That gap isn't exotic. It's exactly what a mid-incident restart looks like: the lot
> registration events arrived before the container did.
>
> Missing data has to **fail closed**. Don't know where it was stored? Then you can't rule out the
> cold chain, so you can't blame the instrument.
>
> #AllThingsAgentic

## Short form — Bluesky / Mastodon / Threads

> Autonomy isn't the hard part of agent engineering.
>
> Deciding what the autonomy is structurally forbidden to do — and then proving it can't — is the
> hard part.
>
> It's also the only reason anyone would let a fleet of language models near a working clinical
> laboratory. #AllThingsAgentic

---

## Notes before posting

- Replace `[repo link]` once the repository is public.
- The bug thread outperforms the announcement post; post it as its own thread a day later.
- The hashtag is **#AllThingsAgentic** — required for the bonus point. It must appear in the post
  itself, not only in a reply.
- Screenshot to attach: the console showing `chain verified · N entries` beside a pending
  escalation. That single frame carries the whole thesis.
