## Inspiration

There is a failure that happens in under-resourced laboratories, and it is almost never anybody's fault.

A refrigerator compressor starts to degrade overnight. It does not fail — it just stops holding temperature quite so well. The reagent stored inside slowly loses potency. Control results begin drifting downward, and here is the part that matters: **they stay inside two standard deviations for roughly forty hours.**

Forty hours of results that look fine on a Levey-Jennings chart, because they *are* fine on a Levey-Jennings chart. By the time Westgard multirules finally reject, a day and a half of patient results have gone out on a reagent that was already wrong.

The signal was real the whole time. It was just distributed across three systems that do not talk to each other, and too slow for one person covering four clinic sites to notice.

That person — a medical laboratory scientist who is simultaneously the QC officer, the inventory manager, the cold-chain custodian and the IT department — is who Praetor is built for.

## What it does

Five agents watch quality control, cold-chain telemetry and reagent lot performance across four clinic sites.

| Agent | Detects | May act on |
|---|---|---|
| ColdChainAgent | sustained temperature excursion | storage units, alerting |
| QCAgent | Westgard multirule violations | control runs, patient batches, analysers |
| LotAgent | storage/excursion correlation | reagent quarantine, reordering |
| Diagnostician | *reasons — does not detect* | may request a release |
| Scribe | *writes the report* | **nothing at all** |

In the scenario above the fleet quarantines the affected reagent **at hour 32** — a full day before QC produces a single rejection — because the cold chain is a leading indicator and quarantine fails closed.

### The judgement that matters

Two reagent lots reject on the same analyser. A naive system counts distinct lots, concludes the instrument is at fault, and takes a rural clinic's **only analyser** out of service.

Both lots were in the same failed fridge. The common factor is the refrigerator, not the instrument. Praetor sets aside lots whose storage unit is under an active excursion and implicates the analyser only by rejections nothing else explains. The instrument stays in service — correctly — and there is a test named after it.

## The one rule underneath it

Every action carries a **safety direction**, and the gate checks it before severity, reversibility, confidence or budget.

| | Actions | Autonomous? |
|---|---|---|
| **tightens** — fails closed | hold a batch, quarantine a lot, take an analyser offline | **yes** |
| **neutral** | observe, notify, reorder stock | yes |
| **loosens** — fails open | release a batch, clear a quarantine, return an instrument to service | **never** |

> The fleet may move the laboratory toward safety on its own, and may never move it away from safety, however confident it is.

Holding results is reversible: worst case, a courier run and a delay. Releasing results you should not have released is not an incident you can undo — those numbers are in a patient's chart and somebody has acted on them.

A test asserts the strong form: **no** combination of capability, incident severity, remaining budget or model confidence lets any fail-open action reach `ALLOW`. Not at 0.99 confidence. Not at SEV4. Never.

## How we built it

**Detection is deterministic, judgement is the model's, and permission is neither.**

Westgard rules, temperature thresholds and lot expiry have correct answers, so no model is asked — one that hallucinates a `2-2s` violation is worse than no automation, because it spends the scientist's trust on noise and you only get to spend that once. Correlating four signals into a root cause has no rule, which is exactly why it is worth a Gemini call.

Every proposed action passes through one deterministic component that is **not a language model**:

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

Agents cannot emit free-form commands — they emit typed actions from a closed catalogue, and an unrecognised action is a denial rather than an interpretation. Other brakes: **capability grants** scoped per agent, per action, per resource; a **blast-radius token bucket** per resource, so a confidently wrong model runs out of budget before it runs out of ideas; and a **circuit breaker** that stands the fleet down after repeated remediation that resolves nothing.

**Every decision — including every denial — is appended to a hash-chained, Ed25519-signed log in Firestore.** The console verifies the chain *in the browser* from the public key alone. It is a reader, not a trustee: nothing it needs would let it forge history. Human approvals are themselves audit entries, and anonymous ones are refused — for a fail-open action, the accountable event is the approval, not the request.

### Stack

Gemini 3.7 Flash (diagnosis, reporting) and Gemini 3.5 Flash-Lite (high-volume triage) via the Google GenAI SDK on Vertex AI · Cloud Run · Firestore · Pub/Sub with dead-lettering · Secret Manager · Gemini 3 Pro Image · Lyria · Chirp 3 HD · gemini-embedding-001.

### Data sources

Control results and cold-chain telemetry arrive as Pub/Sub messages. Development and the demo run against a deterministic five-day, four-clinic simulation that models the causal chain rather than injecting a fault — compressor degrades, reagent loses potency, controls drift — with a **control lot in a healthy fridge that runs clean for all five days**. Without that control the drift could be the analyte, the instrument, or the simulator's own noise, and the system would prove nothing.

## Challenges we ran into

Every one of these was found by running the thing, not by reading it.

**It pulled the analyser.** Described above. Counting distinct failing lots reads a reagent problem as an instrument fault.

**Then it pulled the analyser again, for a subtler reason.** The fix checks each lot's storage location — but `lot_storage.get(lot)` returns `None` when the record is missing, and `None` is not in the set of failed fridges, so a lot with unknown provenance counted as *having no cold-chain explanation*. Two of those and the analyser comes out again, because of a gap in the system's own bookkeeping. That gap is not exotic: it is exactly what a mid-incident restart looks like. **Missing data has to fail closed.**

**The console reported tampering on an intact chain.** This nearly reached the demo video. JavaScript has one number type, so a value Python wrote as `1.0` parses indistinguishably from `1` and re-serialises as `"1"` — different bytes, different SHA-256, *chain FAILED*. Live payloads carry `confidence: 1.0`. The fix is to never parse numbers into JS numbers: the console ships a JSON parser that keeps each number's original wire token.

**The audit chain dropped writes under load.** A hash chain is serial by construction, so the head document is a contention point by design. Firestore's default five retries are exhausted at eight concurrent writers and the append *throws* — losing a decision record, the one thing this component exists to prevent. Now threads serialise in-process first, cross-container contention retries with backoff, and if it still cannot commit the gate **raises rather than proceeding**.

**The blackboard would have outgrown Firestore.** It persists as one document, capped at 1 MiB, with an unbounded signal list. This would have started failing writes weeks into a deployment, in a clinic.

**Veo was inaccessible** on the project at every published version. Rather than claim an integration that does not run, we asked what it was *for* — a handover consumable while driving between sites — and used speech synthesis instead, which serves that need better.

## Accomplishments that we're proud of

- **100 tests**, named after what must never happen: `test_no_fail_open_action_is_reachable_by_any_grant`, `test_the_analyser_is_never_taken_out_of_service`, `test_unknown_lot_provenance_never_implicates_the_analyser`.
- A test extracts the canonicaliser **out of the shipped console** and diffs it against Python, so the two halves of the audit chain cannot silently drift apart.
- The frontier model is called **once** across 735 events — with a test asserting it, because if that ever climbs a clinic's cloud bill climbs with it.
- Least privilege throughout: the runtime account's secret access is scoped to a single secret, and the signing key never touches the repository, the logs or Firestore.

## What we learned

Autonomy is not the hard part. **Deciding what the autonomy is structurally forbidden to do, and then proving it cannot, is the hard part** — and it is the only reason anyone would let a fleet of language models near a working laboratory.

Two smaller lessons that cost real time: Vertex splits its catalogue across endpoints in a way the documentation does not state — Gemini text models serve from `global` and 404 in every region, while media models are the reverse. And a similarity threshold guessed by intuition returned three confident matches for a query about a *centrifuge grinding noise*, because all clinical English about equipment scores around 0.60. Calibrating it against real embeddings moved it to 0.72.

## What's next for Praetor

Persisting the blackboard behind the same transactional discipline as the chain, so the service can run more than one instance. Sharding the audit chain per site when the decision rate justifies it. And the obvious next agent: a scheduling agent that books the engineer visit the cold-chain agent's alert implies, instead of leaving a human to make the call.
