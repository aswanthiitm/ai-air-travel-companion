# Traveler Twin — Architecture

## Positioning

Every flight tool answers *"what flights exist?"* — per query, stateless,
identical for everyone. Traveler Twin answers *"what should **I** do?"*:
persistent, personal, explanatory, and honest about trade-offs. The
differentiator is the **identity layer + glass box**, not the ranking
algorithm.

**Demo thesis:** *the query is not the input — the traveler is the input.*
The same request ("get me to Tokyo next month") produces visibly different,
evidence-backed answers for different travelers.

## Design philosophy

- **Deterministic core.** Filtering, scoring, routing, trade-off math, and
  evidence tracking are plain Python — reproducible, testable, offline-safe.
- **LLM only at the language boundaries** (optional, with rule-based
  fallbacks): parsing the free-text request; polishing explanation prose.
- **No heavy frameworks.** 50 users and 50k rows need indices, not
  embeddings; ≤4-city trips need beam search over permutations, not OR-Tools.
- **Provenance is load-bearing.** Every preference signal carries its
  verbatim evidence; every filter/relaxation/score is recorded. The UI is a
  consumer of that audit trail, not a decoration.

## Pipeline

```
user_id + free-text request
        │
        ▼
┌─ request_parser ──────┐   NL → TripSpec (destinations, date hints,
│  rules; LLM optional  │   request-level constraints)
└──────────┬────────────┘
           ▼
┌─ preference_extractor ─┐  structured fields + raw_history snippets
│  lexicon rules         │  → list[PreferenceSignal] with evidence + confidence
└──────────┬─────────────┘
           ▼
┌─ traveler_profile ─────┐  signals → hard constraints, soft preferences,
│  conflict resolution   │  normalized scoring weights, value-of-time,
└──────────┬─────────────┘  conflicts + unsupported wishes retained
           ▼
┌─ inference_engine ─────┐  TripSpec × profile × NOW → ResolvedTrip
│  date windows, city→   │  (legs, date windows, ordering candidates)
│  IATA, leg planning    │
└──────────┬─────────────┘
           ▼
┌─ recommendation_engine ┐  hard filters → relaxation ladder → weighted
│  scoring → multi-city  │  scoring → named alternatives (cheapest/fastest/
│  beam search           │  most convenient) with computed deltas
└──────────┬─────────────┘
           ▼
┌─ explanation_engine ───┐  evidence-cited narrative; every sentence maps to
│  templates; LLM polish │  a signal or a computed number
└──────────┬─────────────┘
           ▼
   FastAPI (src/api.py) → React flight-deck UI
```

## Data layer (Milestone 1 — implemented)

- `data_loader.py` — typed loaders, no enrichment.
- `airports.py` — static reference for the 35 airports: fixed UTC offsets
  (local-time preferences) and coordinates (route map). City/alias → IATA.
- `preprocessing.py`
  - `enrich_flights`: route key, leg lists, local departure hour,
    time-of-day bucket, redeye flag, price-per-hour.
  - `FlightStore`: O(1) route lookup (position indices over a
    departure-sorted frame), OD-pair existence set (18 pairs are missing —
    a real multi-city ordering constraint), per-route seasonal price
    medians → `seasonal_uplift()`.
  - `parse_users`: airline lists, history snippets, baggage struct.
  - `validate_flights` / `validate_users`: the invariants everything
    depends on (verified during analysis: durations exactly match
    timestamps; layover fields perfectly consistent with stops; multi
    flight-numbers only on connections; all user references covered).

## Key dataset facts the design leans on

| Fact | Consequence |
|---|---|
| Median route has ~38 flights over 18 months | All searches are date-*windowed*, never exact-date |
| Routes cluster into disjoint months (e.g. CPT→NRT has zero flights May–Aug 2025; MEL→JFK and JFK→MEL barely overlap) | Date widening is a first-class relaxation; multi-leg assembly needs backward feasibility propagation + date-diverse beams |
| 18 OD pairs missing | Multi-city visit order is a real optimization variable |
| LIS→SYD has zero direct flights, but U05 demands direct + ≤90min layover | Relaxation ladder is mandatory (benchmark B05 trap) |
| `demand_level`/`is_holiday_season` fully determined by `season` | `season` is canonical; others are display denormalizations |
| Users are 10 archetypes × 5 noisy variants | Extractor is judged on archetype recognition + conflict handling |
| raw_history holds quantified trade-offs ("7hr layover to save $120") | Personal value-of-time (~$17/hr) → Worth-It Math |

## Module contracts (defined; later milestones implement)

```python
@dataclass(frozen=True)
class PreferenceSignal:
    dimension: str      # "stops", "budget", "redeye", "value_of_time", ...
    value: object       # normalized
    source: Source      # STRUCTURED_FIELD | RAW_HISTORY | REQUEST
    evidence: str       # verbatim column=value or history snippet
    confidence: float

@dataclass
class TravelerProfile:
    user_id: str
    home_airport: str
    party: Party
    hard: HardConstraints        # max layover, seats needed, layover floor
    soft: SoftPreferences        # airlines, cabin, dep window, redeye, bags…
    weights: Weights             # price/time/convenience/comfort/loyalty, Σ=1
    flexibility: Flexibility     # date window, multi-city prior, value_of_time
    signals: list[PreferenceSignal]     # full audit trail
    conflicts: list[Conflict]           # both sides + resolution + reason
    unsupported: list[PreferenceSignal] # aisle/wifi/lounge — acknowledged

def resolve(request: TripSpec, profile: TravelerProfile, now: date) -> ResolvedTrip
```

Request-level signals are appended with `source=REQUEST` and confidence 1.0 —
the current ask always outranks history. The inference engine consumes the
profile read-only via `profile.hard`, `profile.weights`,
`profile.flexibility`; it never touches raw user data.

## Recommendation engine (implemented)

1. **Hard filters:** route, date window, seats ≥ party, minimum-connection
   floor (never relaxed — it protects the traveler), cabin floor for
   strict-cabin travelers ("first or business only" ⇒ floor is Business).
2. **Two-level relaxation ladder** when nothing satisfies the traveler:
   *inner* = preference concessions in priority order (airlines → +stops →
   layover cap ×2 → uncapped → longer city stays → weekday pattern → cabin
   floor); *outer* = date-window widening (±2w → ±6w → full horizon).
   Trying the asked dates with relaxed preferences *before* moving dates
   keeps concessions minimal. For multi-city, a final **open-jaw pass**
   (skip the homebound leg) beats returning INFEASIBLE.
3. **Post-hoc concession audit:** the ladder is linear, so the winning
   search state may be more relaxed than the winner needs. The reported
   concessions are recomputed from what the top itinerary *actually*
   violates, with concrete numbers ("accepted a 110-minute layover, above
   the usual 90-minute cap").
4. **Scoring:** `Σ wᵢ · componentᵢ` over price, duration, convenience
   (stops, layover, departure-time fit, redeye, OTP, distance from asked
   dates), comfort (cabin distance, baggage, refundability for business),
   loyalty (preferred airline / same alliance) — weights from the profile.
5. **Multi-city assembly:** permute visit order (pruned by OD existence),
   then **backward feasibility propagation** — per-leg pools are pruned,
   last leg first, to exactly the flights from which the rest of the chain
   can still be completed. The forward beam (date-diverse picks, so sparse
   clustered routes don't collapse the beam onto one departure date) then
   only optimizes among provably completable chains: if any chain exists,
   one is found.
6. **Trade-off surfacing is structural:** top pick + named alternatives
   (cheapest / fastest / most convenient) with computed deltas and
   **Worth-It math** against the traveler's revealed value of time.
7. **Season & scarcity annotations** on every leg (route seasonal uplift vs
   shoulder baseline, holiday flag, seats ≤ 3, redeye).

## UI (design — later milestone)

Three-panel **flight-deck** (React + Vite; FastAPI backend), premium
editorial design (warm paper, serif display, boarding-pass motifs):

- **Left — the Twin:** Traveler DNA signature, evidence chips, conflict
  flags, live weight sliders (re-rank on drag: proves nothing is canned).
- **Center — the Reasoning:** funnel animation (50,000 → … → 3, each cut
  labeled), great-circle route map for multi-city.
- **Right — the Verdict:** boarding-pass recommendation cards with Worth-It
  math, alternative deltas, and a book-now-vs-wait meter.

## Limitations

- Static dataset — no live fares, availability, or booking.
- Fixed UTC offsets (no DST) for local-time bucketing.
- Seat-map-level wishes (aisle seat) and amenities (wifi, lounge) are
  captured in the profile but the dataset cannot satisfy them; the
  explanation engine acknowledges this rather than pretending.
- LLM components are optional; with them disabled, request parsing falls
  back to rules tuned to the benchmark phrasing patterns.
