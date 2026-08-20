# The agent may move the lab toward safety, and never away from it

*Building an autonomous agent fleet you can actually point at a working clinical laboratory.*

---

There is a failure that happens in under-resourced laboratories and it is almost never anybody's
fault.

A refrigerator compressor starts to degrade overnight. Not dramatically — it does not fail, it
just stops holding temperature quite as well. The reagent stored inside slowly loses potency.
Control results begin drifting downward, and here is the part that matters: they stay inside two
standard deviations for roughly forty hours.

Forty hours of results that look fine on a Levey-Jennings chart, because they *are* fine on a
Levey-Jennings chart. By the time Westgard multirules finally reject, a day and a half of patient
results have gone out on a reagent that was already wrong.

The signal was real the whole time. It was just distributed across three systems that do not talk
to each other, and too slow for one person covering four clinic sites to notice.

That is a good problem for an agent fleet. It is also a terrifying one, because the obvious next
sentence — "so let the agents fix it" — is how you get a language model invalidating patient
results.

## The thing I got right, eventually

I built Praetor for Google's All Things Agentic hackathon. Five agents watching quality control,
cold-chain telemetry and reagent lots across four clinics on behalf of one medical laboratory
scientist.

The design decision the whole system rests on took me three attempts to state properly. It ended
up as one sentence:

> **The fleet may move the laboratory toward safety on its own, and may never move it away from
> safety, however confident it is.**

Every action in the system carries a *safety direction*.

| | Actions | Autonomous? |
|---|---|---|
| **tightens** — fails closed | hold a batch, quarantine a lot, take an analyser offline | **yes** |
| **neutral** | observe, notify, reorder stock | yes |
| **loosens** — fails open | release a batch, clear a quarantine, return an instrument to service | **never** |

Holding results is reversible: worst case, a courier run and a delay. Releasing results you should
not have released is not an incident you can undo — those numbers are in a patient's chart and
somebody has acted on them.

So the gate checks safety direction *before* it checks severity, reversibility, confidence, or
budget. There is a test asserting the strong form: no combination of capability, incident
severity, remaining budget or model confidence lets any fail-open action reach `ALLOW`. Not at
0.99 confidence. Not at SEV4. Never.

This is what makes the rest of the autonomy defensible. The fleet handles the routine ninety per
cent unattended precisely *because* the ten per cent that matters is structurally out of reach.

## The gate is not a model

Everything an agent proposes passes through one deterministic component:

```
0. HALT      circuit breaker tripped
1. DENY      action outside the closed catalogue
2. DENY      malformed parameters
3. DENY      no capability grant
4. ESCALATE  action loosens the safety posture
5. ESCALATE  incident more severe than this agent's ceiling
6. ESCALATE  action is irreversible
7. ESCALATE  confidence below the floor
8. ESCALATE  blast-radius budget exhausted
9. ALLOW
```

Agents cannot emit free-form commands. They emit typed actions from a closed catalogue, and an
unrecognised action is a denial rather than an interpretation.

The distinction between `DENY` and `ESCALATE` turned out to matter more than I expected. `DENY`
means *never, re-plan*. `ESCALATE` means *plausibly right, but not yours to decide alone*. A
system with only one of those is either a straitjacket or a rubber stamp.

And a line I would now draw on any agent system: **detection is deterministic, judgement is the
model's, permission is neither.** Westgard rules have correct answers, so no model is asked
whether `2-2s` fired. A model that hallucinates a QC violation is worse than no automation,
because it spends the scientist's trust on noise, and you only get to spend that once.

## Four bugs that only appeared when I ran it

Every one of these was found by executing the thing, not by reading it.

**It pulled the analyser.** Two reagent lots rejecting on the same instrument, so my guard
concluded the instrument was at fault and took it out of service. But both lots were in the *same
failed fridge*. The common factor was the refrigerator. Counting distinct lots reads a reagent
problem as an instrument fault and removes a rural clinic's only analyser — turning a quality
incident into an outage.

**Then it pulled the analyser again, for a subtler reason.** I fixed the above by checking each
lot's storage location. But `lot_storage.get(lot)` returns `None` when the record is missing, and
`None` is not in the set of failed fridges — so a lot with unknown provenance counted as *having
no cold-chain explanation*. Two of those and the analyser comes out again, because of a gap in
the system's own bookkeeping. And that gap is not exotic: it is exactly what a mid-incident
restart looks like.

Missing data has to fail closed. If you do not know where a lot was stored, you cannot rule out
the cold chain, so you cannot blame the instrument. The fleet now says so out loud, too — failing
closed *silently* would look like ignoring a failing analyser.

**The circuit breaker ate the fleet.** Every flagged control run counted as an ineffective
remediation, so after three the fleet halted permanently. The fix was a real distinction rather
than a bigger threshold: actions are *remediating* or *bookkeeping*, and only remediating ones
count. Standing down should mean the fleet stops trying to fix things — not that it goes silent
on the person who now has to.

**The console reported tampering on an intact chain.** This one nearly made it to the demo video.
Every decision is appended to a hash-chained, Ed25519-signed audit log, and the console verifies
it in the browser from the public key alone. Except: JavaScript has one number type. A value
Python wrote as `1.0` parses indistinguishably from `1` and re-serialises as `"1"` — different
bytes, different SHA-256, *chain FAILED*. Live payloads carry `confidence: 1.0`.

The fix is to never parse numbers into JS numbers at all. The console ships a JSON parser that
keeps each number's original wire token, so Python's formatting survives verbatim. There is now a
test that extracts the canonicaliser out of the shipped console and diffs it against Python, so
the two cannot drift apart.

## Two things about cost

A clinic is not a demo budget, and building as though it were produces a system nobody can run.

The frontier model is called **once per incident**, not once per event — 735 events, one Gemini
call, and a test asserting that, because if it ever climbs the clinic's bill climbs with it.
High-volume cold-chain telemetry is triaged by a much cheaper tier. Incident memory is a linear
scan over a few hundred vectors rather than a vector index, because a four-site network produces a
few hundred incidents a year and the index would cost more than the scan.

The second thing: I could not get Veo working on this project at any published version. Rather
than claim an integration that does not run, I asked what it was *for* — a handover the scientist
can consume while driving between sites — and used speech synthesis instead, which serves that
need better anyway.

## The thing I would tell someone starting

Write the tests as sentences about what must never happen, and name them that way.

```
test_no_fail_open_action_is_reachable_by_any_grant
test_the_analyser_is_never_taken_out_of_service
test_unknown_lot_provenance_never_implicates_the_analyser
test_quarantine_lands_a_full_day_before_qc_ever_rejects
test_the_control_lot_in_the_healthy_fridge_never_rejects
```

That last one is the control that makes the whole scenario mean anything: an identical reagent lot
in a *working* fridge runs clean for all five days. Without it, the drift could be the analyte, the
instrument, or my simulator's own noise, and the system would prove nothing.

Autonomy is not the hard part. Deciding what the autonomy is structurally forbidden to do, and
then proving it cannot, is the hard part — and it is the only reason anyone would let a fleet of
language models near a working laboratory.

---

*Praetor is built on Gemini 3.7 Flash, the Google GenAI SDK, Cloud Run, Firestore, Pub/Sub and
Secret Manager. Source and architecture: [repository link]*

*#AllThingsAgentic*
