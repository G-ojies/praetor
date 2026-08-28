# Social posts

Character counts below are for X, which counts **every URL as 23 characters**
regardless of its real length. Verified with that rule, so they are safe to paste.

**Demo video:** https://youtu.be/1NHZ1pLM0KY (verified public on 28 Aug 2026).
Already filled in below, so every post pastes as is.

**Hashtags:** the Devpost field says `#AllThingsAgentic Hackathon` and the final
checklist says `#AllThingsAgenticHackathon`. Both are included below so the bonus
cannot be lost to that ambiguity. The tag must be in the post itself, never only
in a reply.

---

## 1. X, the announcement (278/280)

```
An agent fleet that runs a rural clinic lab.

One rule: it may move the lab toward safety on its own, and never away from it.

Quarantine a lot, pull an analyser: autonomous.
Release a patient result: never.

https://youtu.be/1NHZ1pLM0KY

#AllThingsAgentic #AllThingsAgenticHackathon
```

If it will not fit for any reason, drop `#AllThingsAgentic` and keep
`#AllThingsAgenticHackathon`. That variant is 267.

**Attach:** `docs/console-chart.png`. That single frame carries the whole thesis,
the drift and the verified chain in one image.

---

## 2. X, the bug thread (post a day later, it travels further)

Each post verified under 280.

**1/** (256)
```
An agent bug that nearly shipped, in four parts.

1/ Two reagent lots start failing on the same analyser. Obvious conclusion: the analyser is broken, take it offline.

Wrong. Both lots were in the same failed fridge. The common factor was the refrigerator.
```

**2/** (222)
```
2/ Counting distinct failing lots reads a reagent problem as an instrument fault, and takes a rural clinic's only analyser out of service.

A quality incident becomes an outage. Fixed by checking where each lot was stored.
```

**3/** (271)
```
3/ Then it pulled the analyser again.

lot_storage.get(lot) returns None when the record is missing. None isn't in the set of failed fridges. So a lot with unknown provenance counted as "no cold-chain explanation."

A gap in its own bookkeeping, and a destructive action.
```

**4/** (273)
```
4/ That gap isn't exotic. It's what a mid-incident restart looks like: the lot registration events arrived before the container did.

Missing data has to fail closed. Don't know where it was stored? Then you can't rule out the cold chain, so you can't blame the instrument.
```

**5/** (269)
```
Detection is deterministic. Judgement is the model's. Permission is neither: the gate is not a language model.

122 tests, named after what must never happen.

Write-up: https://g-ojies.github.io/praetor/
Code: https://github.com/G-ojies/praetor

#AllThingsAgentic #AllThingsAgenticHackathon
```

---

## 3. LinkedIn (no practical limit, so the argument gets room)

```
I built an agent fleet that runs a rural clinic laboratory, and the design rule it
rests on took three attempts to state properly.

The fleet may move the laboratory toward safety on its own. It may never move it
away from safety, however confident it is.

Hold a batch, quarantine a reagent lot, pull a drifting analyser: all autonomous,
all reversible, all fail closed. Release a patient result? Never. Not at 0.99
confidence. There is a test asserting that no combination of permission, severity,
budget or confidence can reach it.

The reason is asymmetry, not caution. Holding results is reversible: worst case, a
courier run and a delay. Releasing results you should not have released is not an
incident you can undo, because those numbers are in a patient's chart and somebody
has acted on them.

The part I would defend hardest: detection is deterministic, judgement is the
model's, and permission is neither. Westgard rules and temperature thresholds have
correct answers, so no model is asked. Correlating four signals into a root cause
has no rule, which is exactly why it is worth a Gemini call. And the gate that
decides what may happen is not a language model at all, which is what lets you
reason about what the fleet is permitted to do without reasoning about what a model
might say.

The problem it exists for is quiet. A fridge compressor degrades overnight. The
reagent inside loses potency. Control results drift downward and stay inside two
standard deviations for forty hours, which is to say invisible on a control chart.
By the time the rules finally reject, a day and a half of patient results have gone
out on a reagent that was already wrong. Nobody was negligent. The signal was just
spread across three systems that do not talk to each other.

Built on Gemini, Cloud Run, Firestore and Pub/Sub, with every decision, including
every denial, appended to a hash-chained signed log the console verifies in your
browser.

Demo: https://youtu.be/1NHZ1pLM0KY
Write-up: https://g-ojies.github.io/praetor/
Code: https://github.com/G-ojies/praetor

#AllThingsAgentic #AllThingsAgenticHackathon
```

---

## 4. Short form, Bluesky / Mastodon / Threads

```
Autonomy isn't the hard part of agent engineering.

Deciding what the autonomy is structurally forbidden to do, and then proving it
can't, is the hard part.

It's also the only reason anyone would let a fleet of language models near a
working clinical laboratory.

#AllThingsAgentic #AllThingsAgenticHackathon
```

---

## After posting

Paste the post URL into the Devpost field **"OPTIONAL for Bonus Points: Link to a
social media post"**. The submission stays editable until the deadline, so this can
be added after submitting. It is worth +0.2.

The blog post is the other half of the bonus and is already live and compliant:
https://g-ojies.github.io/praetor/
