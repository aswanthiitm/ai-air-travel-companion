# Traveler Twin — Architecture v3: the Living Twin

> **What changed since v2.** Three upgrades, one constraint. (1) Two equal
> entry points — a classic OTA form and a free-text ask — converge on one
> TripSpec. (2) `request_parser` is superseded by an **AI Planner**: an
> LLM-driven front door that detects intent, infers missing constraints,
> asks follow-ups, and selects the optimization strategy. (3) The Twin
> stops being a read-only profile: it is initialized from the historical
> dataset and then **evolves after every interaction** through an
> event-sourced update pipeline. The constraint: the deterministic
> recommendation engine — filters, relaxation ladder, scoring, routing,
> Worth-It math — is byte-for-byte unchanged. **The LLM never scores a
> flight.** It reasons, orchestrates, converses; Python computes.

## Positioning

Every flight tool answers *"what flights exist?"* — per query, stateless,
identical for everyone. Traveler Twin answers *"what should **I** do?"*:
persistent, personal, explanatory, honest about trade-offs — and now
**learning**: the twin you talk to next month is measurably different from
the one you talked to today, and can cite the interactions that changed it.

**Demo thesis:** *the query is not the input — the traveler is the input.*
And the traveler is not a snapshot — it's a living state.

## The whole system on one page

```
┌────────────────────────── EXPERIENCE LAYER ──────────────────────────────┐
│   Entry 1: OTA form                    Entry 2: free-text ask            │
│   From · To · Dates · Travellers      "I need to attend a conference    │
│   Cabin · "Tell us anything else"      in Tokyo next month"              │
└──────────────┬────────────────────────────────┬──────────────────────────┘
               │ structured slots               │ raw language
               ▼                                ▼
┌────────────────────────── REASONING LAYER (LLM) ─────────────────────────┐
│                        AI PLANNER  (LangChain agent)                     │
│   intent → slots → ambiguities → strategy → TripSpec                     │
│   reads the Twin to infer what the message doesn't say                   │
│   asks a follow-up when neither message nor Twin can fill a slot         │
│                                                                          │
│   tools it orchestrates (typed, deterministic, LLM-opaque internals):    │
│   get_traveler_twin · verify_slots · resolve_trip · search_flights       │
│   get_market_context · record_feedback · ask_clarifying_question         │
│   final reply grounded in Explanation → validate_grounding (fails        │
│   closed to template renderer)                                           │
└───────┬──────────────────────────────────────────────────┬──────────────┘
        │ tool calls                                        │ events
        ▼                                                   ▼
┌──────────────── DETERMINISTIC CORE ────────┐   ┌───── TWIN STORE ────────┐
│ preprocessing · FlightStore                │   │ SQLite, event-sourced   │
│ preference_extractor (historical corpus)   │   │ events (append-only)    │
│ traveler_profile (baseline build)          │   │ twin_snapshots (cache)  │
│ inference_engine (dates, regions, legs)    │   │ baseline ⊕ deltas =     │
│ recommendation_engine (filters, ladder,    │◀──│ effective profile       │
│   beam, scoring, Worth-It) — UNCHANGED     │   │ (pure merge function)   │
│ explanation_engine (evidence-cited facts)  │   └─────────────────────────┘
└────────────────────────────────────────────┘
```

Division of labor, stated once and enforced everywhere:

| Layer | Decides | Never does |
|---|---|---|
| AI Planner (LLM) | what the traveler means, what's missing, what to ask, which tools in which order, which strategy, how to phrase the reply | price/duration/score arithmetic, seat counts, seasonal math, inventing evidence |
| Deterministic core | every number: filtering, relaxation, scoring, routing, deltas, uplifts | natural-language interpretation |
| Twin store | what the system currently believes about the traveler, with provenance | recommending anything |

---

## 1 · Two entry points, one TripSpec

Both interfaces are first-class and converge before the pipeline begins.

**Entry 1 — Classic search (OTA form).** From (defaults to the Twin's home
airport, editable) · To · Dates (range + "± flexible" toggle) · Travellers ·
Cabin · optional free-text *"Tell us anything else"*.

**Entry 2 — Just ask (prompt box).** Anything: *"I need to attend a
conference in Tokyo next month"* · *"I have two weeks in Europe and want to
visit Paris, Rome and London"* · *"I hate long layovers and don't mind
paying extra"* (note: that last one contains **no trip at all** — intent
detection matters, see §2).

The unifier is **slot provenance**. TripSpec v3 wraps every field in a
`Slot` that records where its value came from and how sure we are:

```python
@dataclass(frozen=True)
class Slot:
    value: object | None
    source: SlotSource   # FORM | TEXT | TWIN_DEFAULT | LLM_INFERRED
    confidence: float    # FORM = 1.0 always; others as assessed
    evidence: str        # form field name, message span, or twin trait cited

@dataclass
class TripSpec:                      # v3
    intent: Intent                   # SEARCH | REFINE | PREFERENCE_UPDATE
                                     # | ADVICE | OTHER
    origin: Slot; destinations: Slot; date_hint: Slot
    party: Slot; cabin: Slot
    purpose: Slot                    # business | leisure | mixed
    flexibility_days: Slot
    strategy: Strategy               # see §2 — picked, not computed, by LLM
    request_signals: list[PreferenceSignal]   # source=REQUEST, conf 1.0
    ambiguities: list[Ambiguity]     # slot, why, proposed follow-up question
```

Rules that keep this honest and cheap:

- **Form fields never pass through the LLM.** They are already structured;
  re-extracting them with a model adds hallucination risk for zero value.
  They enter as `source=FORM, confidence=1.0`. Only the "tell us anything
  else" note (if present) goes to the Planner — for *signals*, not slots.
- **Free text goes to the Planner's UNDERSTAND phase** (one structured-
  output LLM call), producing candidate slots with `source=TEXT/LLM_INFERRED`
  and per-slot confidence.
- **Gaps are filled from the Twin** (`source=TWIN_DEFAULT`): purpose from
  `trip_purpose`, flexibility from `date_flexibility_days`, party from the
  profile, cabin from `preferred_cabin`. A gap that neither message nor
  Twin can fill, on a required slot (destination, rough dates), becomes an
  `Ambiguity` → clarifying question.
- **A fully-specified form skips the LLM entirely** — fast path, zero
  tokens, and the offline story for free.

### API flow

```
POST /api/plan
  { user_id, conversation_id,
    mode: "form" | "text",
    form?:   { from?, to?, dates?, travellers?, cabin?, notes? },
    message?: str }

  → { status: "clarify",  question, missing_slots }        # turn ends, UI
                                                            # renders it as
                                                            # the agent's msg
  → { status: "complete", trip_spec, resolved, recommendation,
      explanation, narrative, twin_updates }                # full result
  → { status: "acknowledged", twin_updates }                # intent was
                                                            # PREFERENCE_UPDATE:
                                                            # no search ran —
                                                            # the Twin learned

POST /api/feedback   { user_id, conversation_id, event }   # §4 events
  → { twin_updates: [TraitChange] }

GET  /api/twin/{user_id}
  → effective profile + changelog (recent TraitChanges with citing events)

POST /api/recommend   # v1 endpoint, kept verbatim: deterministic one-shot,
                      # no LLM, no key — still powers python -m src.evaluation
```

`twin_updates` in every response is deliberate UI surface: the frontend
toasts *"Twin updated: layover tolerance ↓ (you said you hate long
layovers)"* — learning made visible, same glass-box ethos as everything else.

---

## 2 · The AI Planner (supersedes `request_parser`)

Not a parser with more regexes — a different kind of module. The Planner is
the agent's first reasoning phase, with `request_parser`'s deterministic
machinery demoted to what it should have been all along: a **validation
library** (`planner_validators.py` — the city gazetteer, alias table, date
arithmetic, and dataset-existence checks live on unchanged; only their
role changes from "the parser" to "the checker of the model's homework").

| Responsibility | Mechanism |
|---|---|
| Intent detection | UNDERSTAND call classifies: `SEARCH`, `REFINE` (follow-up on an existing result: "actually, cheaper"), `PREFERENCE_UPDATE` ("I hate long layovers…" — no search, route to Twin), `ADVICE` ("what should I expect?"), `OTHER` |
| Ambiguity detection | any required slot left `value=None` or below confidence threshold after Twin-default fill → `Ambiguity(slot, why)` |
| Follow-up question generation | the model drafts the question from the ambiguity (personalized: "…or should I pick somewhere from your usual haunts?"); asking is a tool call that ends the turn |
| City normalization | model proposes candidates → `verify_slots` confirms against the gazetteer/alias table; unresolvable → ambiguity, never a guess |
| Airport inference | "Europe trip" → region pool; "conference in Tokyo" → NRT via gazetteer; home side defaults from Twin |
| Travel-purpose detection | "conference/meeting/client" → business; feeds the purpose slot AND a REQUEST signal (time-weight bump, refundability bias — existing mechanics) |
| Flexibility estimation | "two weeks in Europe", "sometime this summer", "the 14th, non-negotiable" → `flexibility_days` slot with confidence; Twin default otherwise |
| Business-vs-leisure inference | purpose slot + Twin's `trip_purpose` prior; disagreement is fine — REQUEST outranks history (existing precedence rule) |
| Optimization-strategy selection | model picks a **label**; a deterministic table maps it to engine inputs (below) |
| Tool selection | the Planner is the LangChain agent: it decides which of §3's tools to call, in what order, how many times |
| TripSpec construction | UNDERSTAND output + FORM slots + Twin defaults + verification results, assembled into TripSpec v3 |

**Strategy is a label, not a computation.** The LLM chooses; Python maps.
The engine API doesn't change — strategies are presets over inputs it
already accepts:

| Strategy | Deterministic mapping |
|---|---|
| `CHEAPEST_FIRST` | weight override: price ↑ (same mechanism as UI sliders) |
| `SCHEDULE_FIRST` | time/convenience ↑; weekday constraints honored strictly before relaxing |
| `COMFORT_FIRST` | comfort ↑; cabin floor respected longest in the ladder |
| `MULTI_CITY` | multi-city assembly path (permutations + backward feasibility + beam) |
| `ADVISE_ONLY` | full search, explanation leads with market context (existing `advise_only` flag) |

**Two-phase discipline — UNDERSTAND, then VERIFY:**

```
message ──► UNDERSTAND (1 LLM call, structured output: PlannerOutput)
                 │  intent, candidate slots + confidence + evidence spans,
                 │  purpose, flexibility, strategy, draft questions
                 ▼
            VERIFY (deterministic, planner_validators.py)
                 │  every slot checked: city→IATA resolvable? date arithmetic
                 │  sane vs simulated NOW? party ≤ 9? cabin in vocabulary?
                 │  failure → downgraded to Ambiguity (never silently kept)
                 ▼
            TripSpec v3 ──► inference_engine.resolve(...)   (unchanged)
```

The model proposes; the validators dispose. An LLM slip — a hallucinated
IATA code, a date resolved against the wrong "today" — is caught before it
touches the engine. This is the same "freedom over reasoning, never over
facts" contract as everywhere else.

**Interaction with the Twin and the engine:** the Planner *reads* the
effective Twin (context for inference and follow-up phrasing), *writes* to
it only via `record_feedback` events (§4), and *invokes* the engine only
through `search_flights` with a complete, verified TripSpec. It cannot
touch weights derivation, scoring, or the ladder.

**Fallback:** with no API key, form mode works fully (it never needed the
LLM) and text mode falls back to the v1 rule-based parser — reduced
understanding, zero regression on the six benchmarks, evaluation stays
reproducible and free.

---

## 3 · LangChain: where it earns its place, and where it's banned

LangChain is used for exactly one thing it's genuinely good at: a
**tool-calling agent loop** with typed schemas, parsed function-call
output, retries, and per-conversation message history (`ChatGroq` +
`create_tool_calling_agent` + `AgentExecutor`; LangGraph noted as the
upgrade path if clarification ever needs multi-slot interrupt/resume state,
not built now). No chains-for-the-sake-of-chains, no vector stores, no
retrieval — 50 users and 50k rows still need indices, not embeddings.

**Tool catalog** (`src/agent_tools.py` — thin `@tool` wrappers; every
implementation is an existing, tested function):

| Tool | Wraps (unchanged code) | The agent calls it when… |
|---|---|---|
| `get_traveler_twin` | twin store: baseline ⊕ deltas merge | turn start — grounds all inference about this traveler |
| `verify_slots` | `planner_validators` (ex-request_parser internals) | after UNDERSTAND, before constructing TripSpec |
| `resolve_trip` | `inference_engine.resolve` | TripSpec complete → concrete airports/windows/legs |
| `search_flights` | `recommendation_engine.recommend` | ResolvedTrip in hand; again with strategy/weight presets if the traveler pushes back |
| `get_market_context` | `FlightStore.seasonal_uplift` + scarcity annotations | "should I wait?" / ADVICE intent |
| `record_feedback` | twin store event append (§4) | PREFERENCE_UPDATE intent, or feedback embedded in any message |
| `ask_clarifying_question` | control flow only — ends turn | required slot unfillable from message + Twin |
| `respond` | `explanation_engine.explain` → LLM composition → `validate_grounding` | a RecommendationSet exists and nothing needs asking |

**Deliberately NOT tools / NOT LangChain** — deterministic computation the
model must never re-do or re-interpret:

- `preprocessing` / `FlightStore` internals (indices, seasonal medians)
- scoring, weights derivation, the relaxation ladder, beam search,
  backward feasibility propagation, Worth-It arithmetic
- the historical-corpus lexicon extraction (100% coverage, tested — an LLM
  re-reading the same 50 histories adds variance, not information)
- `validate_grounding` itself (the checker cannot be the checked)

**Grounding guarantee (carried from v2, enforced on every reply):** the
`respond` tool hands the model the `Explanation` object as its *only*
permitted fact source; a post-generation validator extracts every number
and quoted string from the reply and checks membership; any mismatch
discards the prose and serves the deterministic `render_text()` instead.
Fail closed: a bad turn degrades from "conversational" to "templated,"
never from "correct" to "wrong."

---

## 4 · The Living Twin

**Principle: event-sourced learning over a frozen baseline.** The
historical dataset initializes; interactions evolve; nothing is ever
overwritten in place, so every current belief can cite the events that
produced it — the Twin's answer to "why do you think I hate redeyes?" is a
list of receipts, exactly like the v1 evidence chips.

### Architecture & data flow

```
historical dataset ──► preference_extractor ──► BASELINE profile   (built
   (user_data.csv)        (unchanged)             once; immutable)
                                                      │
interactions ──► InteractionEvent ──► events table    │
 accepted/rejected/ignored recs        (append-only)  │
 alternative_chosen, weights_steered        │         │
 free-text feedback, bookings               ▼         ▼
                                    SIGNAL DERIVER ──► TwinDelta overlay
                                    (deterministic     (per-dimension value,
                                     rules; LLM only   confidence, receipts)
                                     for free text)         │
                                                            ▼
                                          EFFECTIVE PROFILE = merge(baseline,
                                          overlay) → derive_weights (existing
                                          function, re-run on merged signals)
                                                            │
                                                            ▼
                                          recommendation_engine  (unchanged —
                                          it consumes a TravelerProfile and
                                          has no idea the profile now breathes)
```

The engine-compatibility trick is that **merge is a pure function producing
the same `TravelerProfile` dataclass the engine has always consumed**. No
engine change, no schema change downstream, all 79 existing tests still
meaningful.

### Event taxonomy → deterministic update rules

| Event | Derived update (examples) |
|---|---|
| `recommendation_accepted` | confirm every attribute of the accepted itinerary that matched a profile trait (stops, cabin, airline, time-of-day); one revealed-preference observation for value_of_time if a Worth-It trade was on the table |
| `recommendation_rejected(reason?)` | contradict the distinguishing attributes of the rejected option; free-text reason → LLM-extracted signal (`source=FEEDBACK`) |
| `recommendation_ignored` | very weak negative on shown-but-untouched options (α=0.03 — ignoring is noisy) |
| `alternative_chosen("cheapest")` | price-weight evidence; repeated k times → sustained budget shift |
| `weights_steered` | explicit: strong single observation on the steered dimension |
| repeated airline choice | loyalty signal strengthens toward that airline (3+ distinct bookings → treat like a preferred airline) |
| repeated redeye avoidance | redeye_policy: avoid — confidence climbs per instance |
| budget deviation | booked price vs pool median tracked as a running distribution → price_sensitivity drift |
| travel frequency | events per month updates `multi_city_tendency` / frequency prior |
| `PREFERENCE_UPDATE` message | "I hate long layovers and don't mind paying extra" → two signals (`layover_tolerance: avoid_long`, `budget: value`), `source=FEEDBACK`, confidence 0.9 — stated preferences are strong evidence |

### Confidence updating (concrete, implementable)

Each Twin dimension holds `(value, confidence c ∈ [0,1], receipts)`.

```
confirming event:      c ← c + α·(1−c)         α: 0.25 stated, 0.15 booked,
                                                   0.08 clicked, 0.03 ignored-inverse
contradicting event:   c ← c·(1−β)              β = 0.30
                       and opposing accumulator o ← o + α_event·(1−o)
value flip:            when o > c → swap value, c ← o·0.5, o ← 0,
                       old belief retained as a Conflict record (the same
                       structure v1 used for 'gold status vs none')
recency decay:         baseline-derived confidences decay toward a floor:
                       c ← max(0.3, c·λ^Δmonths), half-life ≈ 12 months —
                       dataset habits fade unless behavior keeps confirming them
weights:               re-run existing derive_weights() over merged signals;
                       behavior-derived signals enter with their current c
value_of_time:         running median over all revealed trades (initialized
                       by the '$120 for 7hr' extraction; every accepted or
                       declined Worth-It trade appends an observation)
```

Asymmetry is deliberate (α up-weights slower than β cuts): one
contradiction should dent a habit faster than one confirmation builds it,
because the cost of over-trusting a stale preference is a bad
recommendation, while under-trusting merely asks once more.

### Storage design

Hackathon scope: **SQLite via stdlib `sqlite3`** — one file, zero infra,
transactional, replayable. Two tables:

```sql
events(          -- append-only; the source of truth
  event_id TEXT PRIMARY KEY, user_id TEXT, conversation_id TEXT,
  ts TEXT, type TEXT, payload TEXT/*JSON*/)

twin_snapshots(  -- pure cache; safe to delete and rebuild anytime
  user_id TEXT, version INTEGER, ts TEXT,
  profile TEXT/*JSON: TwinDelta overlay + merged weights*/,
  event_watermark TEXT,          -- last event folded in
  PRIMARY KEY (user_id, version))
```

Read path: latest snapshot + fold any events past the watermark (usually
zero) → effective profile, cached in-process. Write path: append event,
fold incrementally, bump snapshot version. Full replay from the baseline is
always possible — which is also the audit story: `GET /api/twin` serves the
changelog by diffing snapshot versions and citing event receipts.
Production note: same shape on Postgres + an event stream; nothing in the
design assumes SQLite semantics beyond a transaction.

---

## 5 · LLM stack: the free-tier decision

Requirement: genuinely free for the hackathon, tool-calling capable (the
whole architecture rides on function calling), low latency (live demo),
LangChain-supported. Verified July 2026:

| Stack | Free access | Tool calling | Reasoning | Latency | LangChain | Verdict |
|---|---|---|---|---|---|---|
| **Llama 3.3-70B @ Groq** | Free tier, no card: 30 RPM / **1,000 req/day** | Native, reliable | Strong for orchestration | **~300+ tok/s (LPU) — best-in-class** | `langchain-groq` (`ChatGroq`), first-class | ✅ **primary** |
| **Qwen3-32B @ Groq** | Same free key | Excellent (Qwen's strength) | Strong; thinking mode available | Same LPU speed | Same `ChatGroq` | ✅ fallback, one-line swap |
| DeepSeek V3/R1 | 5M signup tokens (30 days) then paid-but-cheap; free variants on OpenRouter at 20 RPM / **50 req/day** | Good (V3) | R1 excellent but verbose/slow in tool loops | R1 chains too slow for live demo | Supported | Dev-loop viable; demo-risky |
| Llama 4 Scout/Maverick | On Groq free (Maverick at reduced 500 req/day) | Native | Good | Fast | Same | Fine; 3.3-70B better quota |
| Gemma 3 | Free via AI Studio / local Ollama | Weakest of the set | Mid | Local-dependent | Supported | ❌ tool-calling is the dealbreaker |
| Grok (xAI) | $25 signup credits, then **paid** | Good | Strong | Good | Supported | ❌ fails "completely free" |

*(Honest footnote: Google's Gemini 2.5 Flash free tier — 1,500 req/day, no
card — is the strongest free quota anywhere, but it's a proprietary model,
not Gemma; listed since the user's shortlist named Gemma and the two get
conflated.)*

**Production choice for the hackathon: Llama 3.3-70B on Groq**, because the
agent loop makes 2–4 LLM calls per traveler turn and the demo lives or dies
on latency — Groq's LPU inference means a full plan→search→respond turn
stays interactive, 1,000 requests/day is roughly 300 demo conversations,
tool calling is native, and `ChatGroq` drops into the LangChain agent with
one import. Risk mitigation, both one-line changes: Qwen3-32B on the same
key if Llama's tool-call formatting misbehaves; local Ollama
(`llama3.1:8b`) as the offline insurance policy for a no-network demo room.

---

## Deterministic core (Milestones 1–5 — implemented, UNCHANGED)

The sections below are the v1/v2 text, kept verbatim as the contract of
record: data layer (`data_loader`, `airports`, `preprocessing` with
`FlightStore`), the dataset facts table (route month-clustering, 18 missing
OD pairs, the B05 trap, the $17/hr revealed value of time), module
contracts (`PreferenceSignal`, `TravelerProfile`, `resolve()`), and the
recommendation engine's seven mechanisms: tiered hard filters,
two-level relaxation ladder with open-jaw fallback, post-hoc concession
audit, weighted scoring, multi-city assembly via backward feasibility
propagation + date-diverse beam, structural trade-off surfacing with
Worth-It math, and season/scarcity annotations. See git history
(`docs/ARCHITECTURE.md` @ v2) for the full prose; nothing in v3 alters a
line of that code, and `python -m src.evaluation` still proves the six
benchmarks against it with no LLM in the loop.

## Limitations (v3)

- Static flight dataset — no live fares or booking; Twin learning is real,
  inventory is not.
- Agent mode needs a Groq API key + network; the deterministic path needs
  neither and remains the graded, reproducible baseline.
- Model phrasing varies run-to-run; facts cannot (grounding validator,
  fail-closed).
- Twin updates from *simulated* interactions during the demo are honest
  about being demo-time learning, not months of real behavior; the decay
  half-life and α/β constants are stated assumptions, tunable, documented.
- SQLite twin store is single-process; production would want Postgres and
  an event stream.
- Free-tier rate limits (30 RPM) are far above demo needs but below
  production needs — by design, this is a hackathon stack.
