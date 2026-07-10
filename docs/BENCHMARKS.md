# Benchmark Results

All 6 benchmark prompts, run end-to-end with the deterministic
pipeline (simulated NOW = 2025-05-15, see README
Assumptions). Regenerate with `python -m src.evaluation --report`.

---

## B01 — U01 (0.33s)

> I need to get from home to Tokyo next month, what do you suggest?

### Direct Business on KL · $6,550 total · 19h08m in the air · departing 2026-02-02 — the nearest date this route flies

Search funnel: 50,000 → 57 → 36 → 36 → 5 → 5 → 3 → 3 → 3 → 3

**How I read this traveler**
- What drives this traveler: convenience (0.30) > time (0.26) > comfort (0.22) > price (0.13) > loyalty (0.09)
- On connections: "always book business, hate connections"
- On cabin: "always book business, hate connections"
- On redeyes: "redeyes kill my mornings, avoid if possible"
- Hard limits: layovers ≤ 120 min (floor 45 min), 1 seat(s), Business-or-above only

**Why this pick**
- Every leg is direct — exactly how this traveler flies ("always book business, hate connections")
- Business cabin, as preferred ("always book business, hate connections")
- No redeyes ("redeyes kill my mornings, avoid if possible")

**The trade-offs**
- Cheapest: direct $5,964 (-587$, +0.0 hrs)
- Most convenient: direct $12,184 (+5,633$, +0.0 hrs)

**What had to give (honest negotiation)**
- no options in the asked dates (2025-06-01..2025-06-30) — nearest departure is 2026-02-02
- used airlines outside the preferred list (KL)

**Market context**
- CPT-NRT: summer peak fares run ~29% above the shoulder-season baseline; only 3 seat(s) left at this fare — this option can disappear

**Fine print**
- Noted but not bookable from this dataset (no seat-map/amenity data): "aisle seat, front of cabin"
- Preferred morning departures weren't available on every leg of this pool

**Itinerary**
- CPT→NRT · KLM Business · direct · 19h08m · $6,550 · departs 2026-02-02 21:00 local

**Rubric self-check**

| Expected behavior | How it is addressed |
|---|---|
| Infer from structured fields AND raw_history | 12 structured + 4 history signals extracted |
| Respect direct_preference and max_layover | satisfied by top pick |
| Weight cost vs convenience by price_sensitivity | weights: price 0.13 / convenience 0.30 / comfort 0.22 |
| Filter by home_airport and preferred airlines | origin CPT = home; airlines relaxed with disclosure |
| Surface cost-vs-time trade-off explicitly | 2 named alternative(s) with $/time deltas |
| Account for seasonal/holiday pricing and seat scarcity | CPT-NRT: summer peak fares run ~29% above the shoulder-season baseline; only 3 seat(s) left at this fare — this option can disappear |
| Explain WHY, citing evidence | 7 verbatim evidence quotes in the explanation |

---

## B02 — U02 (4.98s)

> Find me the best way to do a London + Paris + Rome trip in one journey.

### 2-stop Economy on KL/TG/QR/JL · $2,004 total · 38h52m in the air · departing 2025-07-29 — the nearest date this route flies

Search funnel: 50,000 → 58 → 36 → 36 → 36 → 36 → 36 → 36 → 36 → 20

**How I read this traveler**
- What drives this traveler: price (0.58) > time (0.21) > convenience (0.10) > comfort (0.08)
- On money: "cheapest fare wins, dont care about stops"
- On connections: "cheapest fare wins, dont care about stops"
- On redeyes: "ok with redeye if it's cheaper"
- Hard limits: layovers ≤ 420 min (floor 45 min), 1 seat(s)
- Revealed value of time: ~$17/hr ("took a 7hr layover in SIN to save $120")

**Why this pick**
- At most 2 stop(s), within the usual tolerance
- Economy cabin, as preferred
- It is the cheapest workable option found ("cheapest fare wins, dont care about stops")

**The trade-offs**
- Fastest: direct $2,603 (+599$, −10.4 hrs) — costs $599 more to save 10.4 hrs; at this traveler's ~$17/hr that time is worth $179 → not worth it
- Most convenient: direct $2,660 (+656$, −10.4 hrs) — costs $656 more to save 10.4 hrs; at this traveler's ~$17/hr that time is worth $179 → not worth it

**What had to give (honest negotiation)**
- no options in the asked dates (2025-05-22..2025-06-05) — nearest departure is 2025-07-29
- used airlines outside the preferred list (JL, KL, QR, TG)
- stayed 26 days in London to align with available flight dates

**Market context**
- MEX-LHR: summer peak fares run ~40% above the shoulder-season baseline
- LHR-FCO: summer peak fares run ~35% above the shoulder-season baseline; only 2 seat(s) left at this fare — this option can disappear
- CDG-MEX: only 3 seat(s) left at this fare — this option can disappear

**Itinerary**
- MEX→LHR · KLM Economy · 2 stop via FRA, CDG · 19h30m · $749 · departs 2025-07-29 12:00 local
- LHR→FCO · Thai Airways Economy · direct · 2h28m · $266 · departs 2025-08-25 07:00 local
- FCO→CDG · Qatar Airways Economy · direct · 2h02m · $154 · departs 2025-09-08 10:00 local
- CDG→MEX · Japan Airlines Economy · 1 stop via FRA · 14h52m · $835 · departs 2025-09-14 09:00 local

**Rubric self-check**

| Expected behavior | How it is addressed |
|---|---|
| Infer from structured fields AND raw_history | 11 structured + 8 history signals extracted |
| Respect direct_preference and max_layover | satisfied by top pick |
| Weight cost vs convenience by price_sensitivity | weights: price 0.58 / convenience 0.10 / comfort 0.08 |
| Filter by home_airport and preferred airlines | origin MEX = home; airlines relaxed with disclosure |
| Surface cost-vs-time trade-off explicitly | 2 named alternative(s) with $/time deltas + Worth-It math |
| Account for seasonal/holiday pricing and seat scarcity | MEX-LHR: summer peak fares run ~40% above the shoulder-season baseline; LHR-FCO: summer peak fares run ~35% above the shoulder-season baseline; only 2 seat(s) left at this fare — this option can disappear |
| Explain WHY, citing evidence | 5 verbatim evidence quotes in the explanation |

---

## B03 — U03 (0.06s)

> Cheapest option to Bali, I'm flexible on dates over the summer.

### Direct Economy on KE · $1,817 total · 15h51m in the air · departing 2025-08-28

Search funnel: 50,000 → 39 → 6 → 5 → 5 → 5 → 2 → 2 → 2 → 2

**How I read this traveler**
- What drives this traveler: price (0.40) > convenience (0.31) > time (0.13) > loyalty (0.09) > comfort (0.07)
- On money: "cheapest"
- On connections: "traveling w/ 2 kids, direct is worth paying for"
- On dates: "school breaks only so dates are fixed-ish"
- Hard limits: layovers ≤ 150 min (floor 45 min), 3 seat(s)
- Traveling with 2 kid(s) ("traveling w/ 2 kids, direct is worth paying for")

**Why this pick**
- Every leg is direct — exactly how this traveler flies ("traveling w/ 2 kids, direct is worth paying for")
- Economy cabin, as preferred
- It is the cheapest workable option found ("cheapest")

**The trade-offs**
- Most convenient: direct $1,869 (+52$, +0.0 hrs)

**What had to give (honest negotiation)**
- used airlines outside the preferred list (KE)

**Market context**
- AMS-DPS: summer peak fares run ~54% above the shoulder-season baseline

**Fine print**
- Preferred morning departures weren't available on every leg of this pool

**Itinerary**
- AMS→DPS · Korean Air Economy · direct · 15h51m · $1,817 · departs 2025-08-28 00:00 local

**Rubric self-check**

| Expected behavior | How it is addressed |
|---|---|
| Infer from structured fields AND raw_history | 12 structured + 6 history signals extracted |
| Respect direct_preference and max_layover | satisfied by top pick |
| Weight cost vs convenience by price_sensitivity | weights: price 0.40 / convenience 0.31 / comfort 0.07 |
| Filter by home_airport and preferred airlines | origin AMS = home; airlines relaxed with disclosure |
| Surface cost-vs-time trade-off explicitly | 1 named alternative(s) with $/time deltas |
| Account for seasonal/holiday pricing and seat scarcity | AMS-DPS: summer peak fares run ~54% above the shoulder-season baseline |
| Explain WHY, citing evidence | 6 verbatim evidence quotes in the explanation |

---

## B04 — U04 (0.27s)

> Book me something to New York for a Tuesday meeting, back Thursday.

### Direct Economy on AI/UA · $3,225 total · 43h04m in the air · departing 2025-08-08 — the nearest date this route flies

Search funnel: 50,000 → 46 → 22 → 22 → 22 → 22 → 22 → 22 → 22 → 20

**How I read this traveler**
- What drives this traveler: price (0.44) > time (0.24) > convenience (0.22) > comfort (0.08)
- On money: "value matters but i'll pay to skip a 10hr layover"
- Hard limits: layovers ≤ 300 min (floor 45 min), 1 seat(s)

**Why this pick**
- Every leg is direct — exactly how this traveler flies
- Economy cabin, as preferred

**The trade-offs**
- Cheapest: 1-stop $2,334 (-891$, +17.6 hrs)
- Most convenient: direct $3,631 (+406$, +0.0 hrs)

**What had to give (honest negotiation)**
- no options in the asked dates (2025-05-17..2025-06-21) — nearest departure is 2025-08-08
- used airlines outside the preferred list (AI, UA)
- could not match the requested arrival weekday
- could not match the requested return weekday
- stayed 24 days in New York to align with available flight dates

**Market context**
- MEL-JFK: winter low fares run ~26% below the shoulder-season baseline

**Itinerary**
- MEL→JFK · Air India Economy · direct · 21h32m · $1,529 · departs 2025-08-08 04:00 local
- JFK→MEL · United Airlines Economy · direct · 21h32m · $1,695 · departs 2025-09-01 16:00 local

**Rubric self-check**

| Expected behavior | How it is addressed |
|---|---|
| Infer from structured fields AND raw_history | 11 structured + 4 history signals extracted |
| Respect direct_preference and max_layover | satisfied by top pick |
| Weight cost vs convenience by price_sensitivity | weights: price 0.44 / convenience 0.22 / comfort 0.08 |
| Filter by home_airport and preferred airlines | origin MEL = home; airlines relaxed with disclosure |
| Surface cost-vs-time trade-off explicitly | 2 named alternative(s) with $/time deltas |
| Account for seasonal/holiday pricing and seat scarcity | MEL-JFK: winter low fares run ~26% below the shoulder-season baseline |
| Explain WHY, citing evidence | 1 verbatim evidence quotes in the explanation |

---

## B05 — U05 (0.06s)

> I want to visit Sydney around the holidays — what should I expect?

### 1-stop Business on QF · $10,162 total · 26h10m in the air · departing 2025-12-19

Search funnel: 50,000 → 9 → 3 → 3 → 1 → 1 → 1 → 1 → 1 → 1

**How I read this traveler**
- What drives this traveler: convenience (0.37) > comfort (0.32) > time (0.16) > loyalty (0.10) > price (0.05)
- On money: "happy in peak season, money's not the constraint"
- On connections: "direct whenever it exists"
- On cabin: "first or business only, comfort over cost"
- Hard limits: layovers ≤ 90 min (floor 45 min), 1 seat(s), Business-or-above only

**Why this pick**
- Premium cabin throughout — within this traveler's Business-or-above rule ("first or business only, comfort over cost")
- Baggage included on every leg (3 checked needed)

**The trade-offs**
- No meaningfully different alternative exists in the pool — the top pick dominates on every axis

**What had to give (honest negotiation)**
- used airlines outside the preferred list (QF)
- accepted 1 stop(s) vs the usual max of 0
- accepted a 110-minute layover, above the usual 90-minute cap

**Market context**
- What to expect for these dates:
- LIS-SYD: year end holidays fares run ~74% above the shoulder-season baseline; holiday-season pricing in effect; only 3 seat(s) left at this fare — this option can disappear

**Fine print**
- Noted but not bookable from this dataset (no seat-map/amenity data): "spa lounge, chauffeur transfer, the works"

**Itinerary**
- LIS→SYD · Qantas Business · 1 stop via CDG · 26h10m · $10,162 · departs 2025-12-19 09:00 local

**Rubric self-check**

| Expected behavior | How it is addressed |
|---|---|
| Infer from structured fields AND raw_history | 11 structured + 7 history signals extracted |
| Respect direct_preference and max_layover | conceded transparently: accepted 1 stop(s) vs the usual max of 0; accepted a 110-minute layover, above the usual 90-minute cap |
| Weight cost vs convenience by price_sensitivity | weights: price 0.05 / convenience 0.37 / comfort 0.32 |
| Filter by home_airport and preferred airlines | origin LIS = home; airlines relaxed with disclosure |
| Surface cost-vs-time trade-off explicitly | 0 named alternative(s) with $/time deltas |
| Account for seasonal/holiday pricing and seat scarcity | What to expect for these dates:; LIS-SYD: year end holidays fares run ~74% above the shoulder-season baseline; holiday-season pricing in effect; only 3 seat(s) left at this fare — this option can disappear |
| Explain WHY, citing evidence | 5 verbatim evidence quotes in the explanation |

---

## B06 — U06 (3.22s)

> Plan a multi-city Asia trip, I have about three weeks of flexibility.

### 1-stop Economy on AF/TK/TG/LH · $1,498 total · 26h27m in the air · departing 2025-06-06

Search funnel: 50,000 → 55 → 9 → 9 → 9 → 9 → 9 → 9 → 9 → 20

**How I read this traveler**
- What drives this traveler: price (0.61) > time (0.17) > convenience (0.11) > comfort (0.09)
- On money: "broke student, absolute cheapest only"
- On connections: "2 stops fine, even overnight layovers"
- On dates: "huge date flexibility, whole summer free"
- Hard limits: layovers ≤ 480 min (floor 45 min), 1 seat(s)

**Why this pick**
- At most 1 stop(s), within the usual tolerance
- Economy cabin, as preferred

**The trade-offs**
- Cheapest: 1-stop $1,444 (-54$, +7.2 hrs)
- Fastest: direct $1,833 (+336$, −8.9 hrs)
- Most convenient: direct $1,961 (+464$, −8.9 hrs)

**What had to give (honest negotiation)**
- used airlines outside the preferred list (AF, LH, TG, TK)
- stayed 34 days in Singapore to align with available flight dates

**Market context**
- MAA-PVG: summer peak fares run ~48% above the shoulder-season baseline; only 3 seat(s) left at this fare — this option can disappear
- PVG-BKK: summer peak fares run ~75% above the shoulder-season baseline; only 2 seat(s) left at this fare — this option can disappear
- BKK-SIN: summer peak fares run ~32% above the shoulder-season baseline; only 2 seat(s) left at this fare — this option can disappear
- SIN-MAA: summer peak fares run ~33% above the shoulder-season baseline

**Fine print**
- 'asia' region trip — picked Singapore (SIN), Shanghai (PVG), Bangkok (BKK) by flight availability from MAA in the window

**Itinerary**
- MAA→PVG · Air France Economy · 1 stop via SIN · 10h59m · $486 · departs 2025-06-06 12:00 local
- PVG→BKK · Turkish Airlines Economy · 1 stop via ICN · 8h43m · $350 · departs 2025-06-08 12:00 local
- BKK→SIN · Thai Airways Economy · direct · 2h26m · $230 · departs 2025-06-10 16:00 local
- SIN→MAA · Lufthansa Economy · direct · 4h19m · $432 · departs 2025-07-14 09:00 local

**Rubric self-check**

| Expected behavior | How it is addressed |
|---|---|
| Infer from structured fields AND raw_history | 11 structured + 6 history signals extracted |
| Respect direct_preference and max_layover | satisfied by top pick |
| Weight cost vs convenience by price_sensitivity | weights: price 0.61 / convenience 0.11 / comfort 0.09 |
| Filter by home_airport and preferred airlines | origin MAA = home; airlines relaxed with disclosure |
| Surface cost-vs-time trade-off explicitly | 3 named alternative(s) with $/time deltas |
| Account for seasonal/holiday pricing and seat scarcity | MAA-PVG: summer peak fares run ~48% above the shoulder-season baseline; only 3 seat(s) left at this fare — this option can disappear; PVG-BKK: summer peak fares run ~75% above the shoulder-season baseline; only 2 seat(s) left at this fare — this option can disappear |
| Explain WHY, citing evidence | 3 verbatim evidence quotes in the explanation |
