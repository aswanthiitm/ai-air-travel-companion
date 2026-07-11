# Traveler Twin — Architecture v4: the AI Air Travel Companion

> **Design principle (the whole system in four clauses):**
> **AI reasons. Python computes. AI communicates. The Traveler Twin remembers.**
>
> Four components, four distinct responsibilities, one hard rule: the LLM is
> never the source of truth for a number or an optimization decision. Every
> price, duration, score, ranking, and trade-off is produced by the
> deterministic engine; every sentence the user reads is grounded in a
> validated Evidence Bundle; and the intelligence — understanding, judgment,
> orchestration, conversation — runs through the workflow from the first
> keystroke to the final reply.

## What changed since v3

| v3 | v4 |
|---|---|
| Two interaction modes (OTA form vs. chat) | **One AI-assisted planning surface**: optional structured fields + optional natural-language box, three usage patterns (form only / text only / both), one workflow |
| Fully-specified form bypassed the LLM | **AI always participates** — it never re-parses validated fields, but it always *reasons over* them together with the Twin |
| "AI Planner" (front-door parser-plus) | **Travel Intelligence Agent** — the system's central intelligence: judgment, contradiction detection, result validation, evidence collection, orchestration |
| Explanation composed from the engine's output | **Evidence Bundle** — a structured merge of agent reasoning + engine findings + Twin evidence, and the *only* input the final stage receives |
| Single LLM role | **Two LLM stages with different jobs**: the Agent (reasons, decides, orchestrates) and the AI Companion (communicates) — deliberately separated so neither prompt does double duty |

Unchanged from v3 and carried forward: the Living Twin (event-sourced
learning over the dataset baseline, same `TravelerProfile` interface into
the engine), LangChain as orchestration-only, the grounding validator that
fails closed, the untouched deterministic core, and the Groq/Llama free
LLM stack decision.

---

## 1 · System overview

```
                         ┌─────────────────────────────────────────┐
                         │      SINGLE PLANNING SURFACE (UI)       │
                         │  structured fields (all optional):      │
                         │   origin · destination · dates ·        │
                         │   travellers · cabin · budget           │
                         │  + one natural-language box (optional)  │
                         │  "I don't like overnight flights. This  │
                         │   is our honeymoon. I don't mind paying │
                         │   extra for comfort."                   │
                         └───────────────────┬─────────────────────┘
                                             │  POST /api/plan
                                             ▼
                         ┌─────────────────────────────────────────┐
                         │      VALIDATION / NORMALIZATION         │
                         │  (deterministic; planner_validators)    │
                         │  form fields → verified Slots (conf 1.0)│
                         │  gazetteer, date arithmetic, vocabulary │
                         └───────┬─────────────────────┬───────────┘
                                 │                     │
                 semantic path   │                     │   computational path
                                 ▼                     ▼
              ┌──────────────────────────┐   ┌──────────────────────────┐
              │ TRAVEL INTELLIGENCE      │   │ DETERMINISTIC ENGINE     │
              │ AGENT (LLM, LangChain)   │   │ (Python — unchanged)     │
              │                          │   │                          │
              │ reads the Twin, reasons  │   │ eager baseline search    │
              │ over request + history,  │   │ launches as soon as      │
              │ detects ambiguity and    │   │ validated slots suffice; │
              │ contradiction, infers    │◀─▶│ agent-directed refined   │
              │ purpose & flexibility,   │   │ searches follow          │
              │ picks strategy, directs  │   │ (filters, ladder, beam,  │
              │ tools, validates results │   │  scoring, Worth-It,      │
              │                          │   │  seasonality)            │
              └────────────┬─────────────┘   └────────────┬─────────────┘
                           │        ┌────────────────┐    │
                           │        │ LIVING TWIN    │    │
                           └───────▶│ (event-sourced │◀───┘
                                    │  memory store) │
                                    └───────┬────────┘
                                            │  all three outputs
                                            ▼
                         ┌─────────────────────────────────────────┐
                         │            EVIDENCE BUNDLE              │
                         │  agent reasoning ⊕ engine findings ⊕    │
                         │  twin evidence ⊕ orchestration trace ⊕  │
                         │  grounding index (the fact whitelist)   │
                         └───────────────────┬─────────────────────┘
                                             ▼
                         ┌─────────────────────────────────────────┐
                         │        AI COMPANION (LLM, final)        │
                         │  composes the personalized reply from   │
                         │  the Bundle and nothing but the Bundle  │
                         │  → validate_grounding (fails closed to  │
                         │    the deterministic template renderer) │
                         └───────────────────┬─────────────────────┘
                                             ▼
                                  reply + twin_updates → UI
```

### The dual-path contract, stated precisely

The brief asks for two complementary reasoning paths in parallel. The
precise semantics (because "parallel" has an engineering edge case): the
computational path can only run once a searchable trip exists. So:

- **When validated slots already describe a searchable trip** (origin +
  destination + dates — the filled-form case), the engine launches an
  **eager baseline search** immediately, with Twin-derived weights, *in
  parallel* with the Agent's reasoning pass. The Agent then either accepts
  the baseline findings or directs **refined searches** (strategy preset,
  adjusted constraints) — typically one, bounded at two, to keep latency
  interactive.
- **When they don't** (text-only or partial form), the Agent's semantic
  pass runs first, produces the missing slots (or a clarifying question),
  and then directs the search. The paths are still both exercised — just
  staggered instead of simultaneous.

Either way both paths always run, neither replaces the other, and their
outputs meet only in the Evidence Bundle. AI participates in 100% of
requests; the deterministic engine computes 100% of the numbers.

---

## 2 · Single planning surface

One experience, not modes. The form and the text box sit on the same
screen; users fill either or both. All three usage patterns produce the
same request shape:

```
POST /api/plan {
  user_id, conversation_id,
  fields:  { origin?, destination?, dates?, travellers?, cabin?, budget? },
  message?: str
}
```

Slot provenance (from v3, unchanged in spirit) is what makes "never feel
like a separate mode" real: every TripSpec field is a
`Slot(value, source, confidence, evidence)` with
`source ∈ {FORM, TEXT, TWIN_DEFAULT, AGENT_INFERRED}`. Form values arrive
pre-validated at confidence 1.0 and the Agent is instructed to treat them
as settled facts — **it reasons over them, never re-extracts or reinterprets
them**. What the Agent reasons *about*:

- validated form inputs (as fixed context: "they're going to Tokyo on the
  14th — what does that mean given who they are?")
- the natural-language box, if present (intent, constraints, emotion —
  "honeymoon" is a purpose signal, a comfort signal, and a stakes signal)
- the Traveler Twin (current effective profile with confidences)
- historical behaviour (recent TraitChanges, revealed value-of-time)
- current trip context (window seasonality, prior turns in this
  conversation)

Responses: `complete` (bundle → reply), `clarify` (agent's follow-up
question), or `acknowledged` (message was a preference statement, not a
trip — the Twin learned; no search ran).

---

## 3 · Travel Intelligence Agent

The central intelligence. It owns judgment and orchestration; it performs
no deterministic computation — every responsibility below either produces
*reasoning artifacts* or *directs tools*.

| # | Responsibility | Output artifact / tool directed |
|---|---|---|
| 1 | Understand user intent | `intent ∈ {SEARCH, REFINE, PREFERENCE_UPDATE, ADVICE, OTHER}` + rationale |
| 2 | Read the Traveler Twin | `get_traveler_twin` → grounding context for every judgment below |
| 3 | Combine current request with historical behaviour | planning context: which Twin traits are load-bearing for *this* trip |
| 4 | Detect ambiguity | `Ambiguity(slot, why)` list; empty = proceed |
| 5 | Detect contradictions | `Contradiction(request_says, twin_says, resolution, rationale)` — request outranks history (existing precedence rule), but the tension is *recorded* so the Companion can acknowledge it |
| 6 | Estimate flexibility | `flexibility_days` slot (message > Twin default), with confidence |
| 7 | Infer travel purpose | purpose slot + REQUEST signals ("honeymoon" → leisure + comfort ↑ + redeye avoid) |
| 8 | Select optimization strategy | strategy **label** — deterministically mapped to engine presets (`CHEAPEST_FIRST / SCHEDULE_FIRST / COMFORT_FIRST / MULTI_CITY / ADVISE_ONLY`); the agent picks, Python maps, the engine computes |
| 9 | Decide which deterministic tools to invoke | the LangChain tool loop itself: which tools, what order, how many refinements |
| 10 | Validate returned results | after `search_flights`: do the findings serve the intent? (honeymoon ask but top pick is a redeye → direct one refined search with the comfort preset; bounded at 2 refinements) |
| 11 | Decide whether follow-up questions are necessary | `ask_clarifying_question` tool — ends the turn; never guesses a destination or date |
| 12 | Collect evidence | selects *which* Twin signals, engine findings, and reasoning notes belong in the Bundle for this reply (relevance judgment — the one curation decision that is genuinely the LLM's) |
| 13 | Build planning context | the `AgentReasoning` block of the Evidence Bundle (§5) |

What it must never do — enforced by tool design, not by prompt hope: score
or re-rank flights, compute any delta or percentage, alter engine output,
fabricate evidence (it selects from typed evidence objects by id; it cannot
mint new ones), or exceed the refinement budget.

---

## 4 · Deterministic engine (unchanged — the source of truth)

Everything numerical remains exactly the Milestone 1–5 code:
recommendation generation, filtering, the two-level relaxation ladder with
post-hoc concession audit, weighted scoring, multi-city optimization
(order permutations, backward feasibility propagation, date-diverse beam
search), trade-off calculation, Worth-It math against revealed value of
time, seasonality analysis, scarcity flags, and constraint checking. The
agent reaches it only through `search_flights(ResolvedTrip, preset?)`; its
internals are LLM-opaque. `python -m src.evaluation` still proves the six
benchmarks against this engine with no LLM in the loop — the reproducible,
free-to-grade baseline.

---

## 5 · Evidence Bundle

The load-bearing boundary of v4. Raw flight rows never enter an LLM
context; both reasoning paths and the Twin emit **structured, typed
outputs**, merged into one object that becomes the *only* information the
final stage receives.

```python
@dataclass(frozen=True)
class EvidenceBundle:
    request: RequestSummary          # the slots as validated, with provenance

    reasoning: AgentReasoning        # ── from the Travel Intelligence Agent
      # intent + rationale
      # purpose, flexibility estimate (+ confidence each)
      # strategy chosen + why
      # ambiguities considered and how resolved
      # contradictions detected (request vs Twin) + resolution
      # planning_context: which Twin traits drove which decision
      # result_validation: refinements requested and why

    computation: EngineFindings      # ── from the deterministic engine
      # top itinerary + named alternatives, each with computed deltas
      # concessions (post-hoc audit, concrete numbers)
      # worth_it: the math objects, verbatim from the engine
      # seasonal uplift per leg, scarcity flags, funnel counts
      # window_used, strategy preset actually applied

    twin: TwinEvidence               # ── from the Living Twin
      # cited PreferenceSignals (id, dimension, value, confidence,
      #   verbatim evidence string, source incl. FEEDBACK/BEHAVIOR)
      # conflicts on record; unsupported wishes
      # recent TraitChanges relevant to this trip ("redeye aversion ↑
      #   after 3 rejections in June")

    trace: OrchestrationTrace        # tool calls in order, with inputs
                                     # summarized + timings (UI shows this —
                                     # the agent's thinking is demo surface)

    grounding_index: GroundingIndex  # precomputed whitelist of every number
                                     # and quotable string in the bundle —
                                     # what the validator checks prose against
```

Construction is deterministic (`evidence_bundle.py`): the engine's and
Twin's contributions are serialized mechanically; the Agent's contribution
is its typed reasoning output; the Agent's only discretion is *selection*
(responsibility 12) — it marks which evidence ids are relevant, it cannot
edit their contents. The `grounding_index` is built at merge time, which
makes the anti-hallucination check O(reply) and airtight: if a fact isn't
in the bundle, the Companion physically has no source for it and the
validator will catch it.

---

## 6 · AI Companion (final stage)

A second, separate LLM call with a deliberately narrow prompt: *here is an
Evidence Bundle; answer the traveler.* Separated from the Agent because
reasoning-and-orchestration and warm-user-facing-communication are
different jobs with different prompts, different temperatures, and
different failure modes — fusing them is how systems end up with prompts
that do everything badly.

Responsibilities: explain the recommendation naturally, in the register of
the ask (a honeymoon deserves different prose than a Tuesday-meeting run);
personalize by citing Twin evidence verbatim ("you told us 'redeyes kill my
mornings'"); explain trade-offs using the engine's Worth-It objects;
acknowledge recorded contradictions gracefully; summarize concessions
honestly; answer conversational follow-ups.

Hard limits: it never changes, re-ranks, or overrides a recommendation; it
introduces no number or quote absent from the `grounding_index`; on
validation failure the system discards its prose and serves the
deterministic `render_text()` renderer — a turn can degrade from
conversational to templated, never from correct to wrong.

---

## 7 · Living Traveler Twin (carried from v3)

`Baseline Profile ⊕ Interaction History → Current Traveler Twin.`

The historical dataset initializes an immutable baseline (existing
extractor, unchanged). Every interaction appends events — accepted /
rejected / ignored recommendations, changed preferences, repeated airline
choices, repeated redeye avoidance, travel-frequency shifts, explicit
statements. A deterministic signal deriver folds events into a TwinDelta
overlay (LLM interprets free-text feedback only); asymmetric confidence
updates (`c ← c + α(1−c)` confirm; `c ← c(1−β)` contradict, β > α; value
flips retained as Conflict records; 12-month recency decay on
baseline-derived beliefs; running-median value-of-time). Storage:
event-sourced SQLite (`events` append-only + `twin_snapshots` cache),
fully replayable, changelog with event receipts.

**The engine-compatibility invariant:** `merge(baseline, overlay)` is a
pure function returning the *same `TravelerProfile` dataclass* the
recommendation engine has always consumed. The engine cannot tell the
profile now breathes. Zero engine changes.

The Twin feeds all three actors: the Agent reads it to reason, the engine
consumes its merged profile to score, the Companion cites its evidence to
explain — and all three send events back through `record_feedback`.

---

## 8 · LangChain orchestration

LangChain remains orchestration-only: `ChatGroq` +
`create_tool_calling_agent` + `AgentExecutor`, typed pydantic tool schemas,
per-conversation memory. No chains around deterministic computation, no
vector stores, no retrieval. LangGraph stays the noted upgrade path if
multi-slot interrupt/resume clarification is ever needed.

| Tool | Wraps | Layer served |
|---|---|---|
| `get_traveler_twin` | twin store merge | memory → agent |
| `verify_slots` | `planner_validators` (deterministic) | validation |
| `resolve_trip` | `inference_engine.resolve` | computation |
| `search_flights(trip, preset?)` | `recommendation_engine.recommend` | computation |
| `get_market_context` | `FlightStore.seasonal_uplift` + scarcity | computation |
| `record_feedback` | twin event append | memory ← everyone |
| `ask_clarifying_question` | control flow (ends turn) | conversation |
| `finalize_bundle` | `evidence_bundle.build(...)` (deterministic merge) | hand-off to Companion |

Not tools, not LangChain, ever: scoring/ladder/beam/Worth-It internals,
weights derivation, the historical-corpus lexicon, `validate_grounding`.

The **AI Companion is not a tool of the agent** — it's a distinct pipeline
stage invoked by the API layer after `finalize_bundle`. The agent decides
*what is true and relevant*; the Companion decides *how to say it*; neither
can reach into the other's job.

---

## 9 · Sequence diagrams

### 9a — Form + text together (the honeymoon case; eager parallel paths)

```
User        API          Validators      Engine(Py)      TIA(LLM)      Twin      Companion
 │ form:MAA→DPS,Jun,2px │                │               │             │           │
 │ text:"no overnight   │                │               │             │           │
 │ flights…honeymoon…   │                │               │             │           │
 │ pay extra for comfort"                │               │             │           │
 ├──────────►│ validate slots            │               │             │           │
 │           ├──────────►│ ✓ conf 1.0    │               │             │           │
 │           │  slots suffice → EAGER    │               │             │           │
 │           ├───────────────────────────► baseline      │             │           │
 │           │           │               │ search runs   │             │           │
 │           ├───────────────────────── in parallel ─────► reason:     │           │
 │           │           │               │               │ get_twin ───►│          │
 │           │           │               │               │◄─ profile ──┤          │
 │           │           │               │               │ purpose=honeymoon      │
 │           │           │               │               │ CONTRADICTION:         │
 │           │           │               │               │ twin says budget-first,│
 │           │           │               │               │ request says comfort → │
 │           │           │               │               │ request wins, recorded │
 │           │           │               │               │ strategy=COMFORT_FIRST │
 │           │◄─ baseline findings ──────┤               │             │           │
 │           │           │               │◄─ refined search(COMFORT) ──┤           │
 │           │           │               ├─ findings ────►│ validate:  │           │
 │           │           │               │               │ no redeyes ✓│           │
 │           │           │  finalize_bundle(reasoning ⊕ findings ⊕ twin evidence)  │
 │           ├────────────────────────────────────────────────────────────────────►│
 │           │           │               │               │             │  compose  │
 │           │           │               │               │             │  reply    │
 │           │◄─────────────────────────────────── validate_grounding ✓ ──────────┤
 │◄─ reply + twin_updates (event: comfort shift recorded) ─┤           │           │
```

### 9b — Text only, under-specified (clarification)

```
User: "I want to get away for a few days next month"
  → validators: no slots to verify
  → TIA: get_twin → intent=SEARCH, destination unfillable
         (message ∅, twin has no standing destination habit strong enough)
  → ask_clarifying_question("Anywhere in mind — or shall I suggest from
    your usual short-haul haunts?")            [turn ends: status=clarify]
User: "somewhere warm, Bali maybe"
  → TIA resumes with conversation memory → slots complete → resolve_trip
  → search_flights → validate → finalize_bundle → Companion → reply
```

### 9c — Feedback loop (the Twin learns)

```
User rejects top pick: "too early in the morning"
  → POST /api/feedback {event: rejected, reason: "too early…"}
  → signal deriver: LLM interprets free text → departure_time signal
    (source=FEEDBACK, conf 0.9); deterministic rules log the rejection
    against the shown itinerary's attributes
  → twin store: event appended, overlay folded, snapshot v+1
  → response: twin_updates: ["departure-time preference → later mornings"]
  → next /api/plan turn: TIA reads updated twin; engine receives merged
    profile with the new signal; the same query now ranks differently —
    and the Companion can say why, citing the rejection event
```

---

## 10 · Module responsibilities (complete map)

| Module | Status | Responsibility |
|---|---|---|
| `data_loader.py`, `airports.py`, `preprocessing.py` | unchanged | data layer, FlightStore, validation invariants |
| `preference_extractor.py` | unchanged | historical corpus → baseline signals |
| `traveler_profile.py` | unchanged | baseline profile build; weights derivation (re-run on merged signals) |
| `inference_engine.py` | unchanged | TripSpec → ResolvedTrip |
| `recommendation_engine.py` | unchanged | all computation (source of truth) |
| `explanation_engine.py` | unchanged | typed findings + fallback renderer |
| `request_parser.py` → `planner_validators.py` | role change only | deterministic validation/normalization of slots; offline fallback parser |
| `twin_store.py` | new | events, snapshots, signal deriver, confidence updates, merge |
| `travel_intelligence.py` | new | the Agent: reasoning pass, tool loop, result validation, evidence selection |
| `agent_tools.py` | new | thin `@tool` wrappers (table in §8) |
| `evidence_bundle.py` | new | deterministic bundle merge + grounding index |
| `companion.py` | new | final composition stage + `validate_grounding` |
| `api.py` | extended | `/api/plan`, `/api/feedback`, `/api/twin/{id}`; `/api/recommend` kept verbatim for evaluation |
| `evaluation.py` | unchanged | six benchmarks, deterministic path, no key needed |

## 11 · Why this architecture is an AI system, not an engine with an LLM attached

1. **AI participates in every request.** There is no LLM-bypass path in
   the product flow anymore; even a fully-specified form gets a reasoning
   pass — because knowing *that* someone is flying MAA→DPS on the 14th is
   not the same as understanding *what that trip means for this traveler*.
2. **The intelligence sits where intelligence adds value.** Deciding what a
   request means, noticing that it contradicts a habit, choosing what to
   compute, judging whether the results serve the intent, knowing when to
   ask instead of guess — these are judgment tasks, and they're all LLM
   tasks here. Arithmetic is not a judgment task, and none of it is LLM
   work. The split isn't a limitation of the AI; it's the definition of
   using it well.
3. **Reasoning is a first-class artifact.** `AgentReasoning` and the
   orchestration trace are typed outputs rendered in the UI — the system
   *shows* its intelligence (contradiction noticed, strategy chosen,
   refinement requested) instead of asserting it.
4. **Grounding is structural, not aspirational.** The Companion can only
   see the Evidence Bundle; the bundle carries its own fact whitelist; the
   validator fails closed. Trust in the AI's voice is enforced by
   architecture, not by prompt-engineering hope.
5. **The Twin makes it a companion.** Memory that visibly evolves — with
   receipts — is what separates "a search you talk at" from "a system that
   knows you." Baseline ⊕ history → current Twin, and every actor reads
   from and writes to it.

## 12 · Limitations

- Static flight dataset — learning is real, inventory is not.
- Agent + Companion = 2–4 LLM calls/turn: needs a Groq key and network;
  the deterministic path (`/api/recommend`, `src.evaluation`) needs neither.
- Companion phrasing varies run-to-run; bundle facts cannot.
- Eager search may be discarded when the Agent overrides strategy — a
  deliberate latency-for-tokens trade, bounded by the 2-refinement budget.
- Twin store is single-process SQLite; production wants Postgres + stream.
- Demo-time Twin learning is honest about being minutes, not months, of
  behavior; α/β/decay constants are stated, tunable assumptions.
