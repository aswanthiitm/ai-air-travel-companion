# Traveler Twin — AI Air Travel Companion

> **Google Flights knows flights. Expedia knows travelers.**
> A glass-box digital twin of each traveler that negotiates flight trade-offs
> on their behalf — and shows its work.

Built for the **Expedia Group Innovation Hackathon** (Problem Statement 1:
AI Air Travel Companion).

## The idea

Every flight search engine treats the query as the input. Traveler Twin treats
the **traveler** as the input. It fuses structured profile fields with messy
free-text booking history into an evidence-backed *Traveler Twin*, then uses a
deterministic reasoning engine to filter, score, and negotiate trade-offs —
producing recommendations where **every claim is traceable to a piece of
evidence**.

Signature capabilities (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):

- **Traveler DNA** — a visual preference signature extracted from structured
  fields *and* raw history, with verbatim evidence behind every trait.
- **Reasoning Funnel** — watch 50,000 flights collapse to a top pick, every
  cut labeled with the trait that caused it.
- **Worth-It Math** — a personal value-of-time extracted from behavior
  ("took a 7hr layover to save $120" → ~$17/hr) used to price every trade-off
  in the traveler's own currency.
- **Honest negotiation** — when constraints are unsatisfiable (they sometimes
  are, by design of the dataset), the system relaxes them step by step and
  reports exactly what each concession cost.
- **Season & scarcity awareness** — real per-route seasonal price uplifts and
  seat-scarcity signals drive "book now vs. wait" advice.

## Project structure

```
data/          Official hackathon datasets (flights, users, benchmark prompts)
src/           Deterministic Python backend
  config.py            Paths + global assumptions (simulated NOW)
  airports.py          Static reference for the 35 airports (tz offsets, coords)
  data_loader.py       Typed CSV/JSON loaders
  preprocessing.py     Enrichment, route indices, seasonal stats, validation
ui/            React frontend (later milestone)
docs/          Architecture and design documentation
notebooks/     Exploration notebooks
tests/         Pytest suite — dataset invariants + module contracts
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest            # verifies dataset invariants + module contracts
```

Requires Python 3.10+.

## Assumptions

Documented as they are made (hackathon FAQ #6):

1. **Simulated "today" = 2025-05-15.** The flight data covers
   2025-01-01 → 2026-07-01. Benchmark prompts use relative dates ("next
   month", "over the summer", "around the holidays") that must resolve inside
   that window. With NOW = 2025-05-15, every forward-looking phrase lands in
   fully covered data. Only relative-date resolution uses this constant
   (`src/config.py`); absolute dates are used as-is.
2. **Local times via fixed UTC offsets.** The dataset stores UTC only, but
   preferences like "morning departures" are local-time concepts. Each
   airport carries its standard-time UTC offset (DST ignored — acceptable
   error for time-of-day *bucketing*).
3. **City → airport mapping is 1:1.** The dataset uses one airport per city
   (e.g. Tokyo = NRT), so city names in requests resolve unambiguously.
4. **`seats_available` is GDS-style capped at 9**; values ≤ 3 are treated as
   a scarcity signal.
5. **`demand_level` and `is_holiday_season` are derived from `season`**
   (verified: perfectly determined by it), so `season` is treated as the
   canonical pricing-context field.
6. **Prices are static per offer** (no fare simulation); "seasonal pricing
   awareness" is computed from real per-route median differences by season.

## Status

| Milestone | Scope | State |
|---|---|---|
| 1 | Scaffold, data loading, preprocessing, validation | ✅ done |
| 2 | Preference extraction → Traveler Profile | ✅ done |
| 3 | Request parsing + inference engine | ⏳ next |
| 4 | Recommendation engine (filters, relaxation, scoring, multi-city) | — |
| 5 | Explanation engine + benchmark runner | — |
| 6 | FastAPI layer + React UI (flight-deck) | — |

## Limitations & future work

Tracked in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); updated as
milestones land.
