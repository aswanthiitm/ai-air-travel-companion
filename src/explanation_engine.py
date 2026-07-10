"""Explanation engine: RecommendationSet -> evidence-cited narrative.

Every sentence maps to something the pipeline actually derived: a
PreferenceSignal's verbatim evidence, a concession from the post-hoc audit,
a computed delta, or a route statistic. Nothing is asserted that cannot be
traced — the templates only arrange facts, they never invent them.

Deterministic by design (the demo must be reproducible offline); an LLM
prose-polish pass can be layered on top of the Explanation structure later
without touching the facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .preference_extractor import Source
from .recommendation_engine import Itinerary, RecommendationSet
from .traveler_profile import TravelerProfile


@dataclass
class Explanation:
    headline: str
    traveler_reading: list[str] = field(default_factory=list)
    why_top: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    concessions: list[str] = field(default_factory=list)
    market_context: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    itinerary: list[str] = field(default_factory=list)
    funnel_line: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evidence(profile: TravelerProfile, dimension: str, value=None) -> str | None:
    """Verbatim evidence for a dimension, preferring raw history for color."""
    matches = [s for s in profile.signals
               if s.dimension == dimension and (value is None or s.value == value)]
    for source in (Source.RAW_HISTORY, Source.REQUEST, Source.STRUCTURED_FIELD):
        for s in matches:
            if s.source is source:
                return s.evidence
    return None


def _fmt_minutes(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _fmt_hours(minutes: int) -> str:
    return f"{abs(minutes) / 60:.1f} hrs"


def _leg_line(leg) -> str:
    stops = "direct" if leg["stops"] == 0 else (
        f"{leg['stops']} stop via {', '.join(leg['layover_airports_list'])}")
    return (f"{leg['origin']}→{leg['destination']} · {leg['airline_name']} "
            f"{leg['cabin_class']} · {stops} · {_fmt_minutes(int(leg['duration_minutes']))} · "
            f"${leg['price']:,.0f} · departs {leg['departure_date_local'].isoformat()} "
            f"{int(leg['departure_local_hour']):02d}:00 local")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _headline(rec: RecommendationSet) -> str:
    top = rec.top
    routing = "Direct" if top.max_stops == 0 else f"{top.max_stops}-stop"
    airlines = "/".join(dict.fromkeys(leg["airline_code"] for leg in top.legs))
    cabins = "/".join(dict.fromkeys(leg["cabin_class"] for leg in top.legs))
    date = top.legs[0]["departure_date_local"].isoformat()
    suffix = " — the nearest date this route flies" if any(
        "asked dates" in c for c in rec.relaxations) else ""
    return (f"{routing} {cabins} on {airlines} · ${top.total_price:,.0f} total · "
            f"{_fmt_minutes(top.total_minutes)} in the air · departing {date}{suffix}")


def _traveler_reading(profile: TravelerProfile) -> list[str]:
    w = profile.weights.as_dict()
    ranked = sorted(w, key=w.get, reverse=True)
    lines = ["What drives this traveler: "
             + " > ".join(f"{k} ({w[k]:.2f})" for k in ranked if w[k] > 0.05)]
    for dimension, label in (("budget", "On money"), ("stops", "On connections"),
                             ("cabin_strict", "On cabin"), ("redeye", "On redeyes"),
                             ("dates", "On dates"), ("connection_anxiety", "On risk")):
        ev = _evidence(profile, dimension)
        if ev and "=" not in ev:  # prefer human snippets over column dumps
            lines.append(f'{label}: "{ev}"')
    hard = profile.hard
    cabin_note = ", Business-or-above only" if hard.cabin_strict else ""
    lines.append(f"Hard limits: layovers ≤ {hard.max_layover_minutes} min "
                 f"(floor {hard.min_layover_minutes} min), "
                 f"{hard.required_seats} seat(s){cabin_note}")
    if profile.party.children:
        ev = _evidence(profile, "party")
        lines.append(f'Traveling with {profile.party.children} kid(s) ("{ev}")')
    vot = profile.flexibility.value_of_time_usd_per_hr
    if vot:
        lines.append(f'Revealed value of time: ~${vot:.0f}/hr ("{_evidence(profile, "value_of_time")}")')
    return lines


def _why_top(rec: RecommendationSet) -> list[str]:
    top, p = rec.top, rec.trip.profile
    lines = []

    def cite(text: str, dimension: str, value=None):
        ev = _evidence(p, dimension, value)
        lines.append(f'{text} ("{ev}")' if ev and "=" not in ev else text)

    if top.max_stops == 0:
        cite("Every leg is direct — exactly how this traveler flies", "stops")
    elif top.max_stops <= p.soft.max_stops:
        lines.append(f"At most {top.max_stops} stop(s), within the usual tolerance")
    if any(leg["cabin_class"] == p.soft.cabin for leg in top.legs):
        cite(f"{p.soft.cabin} cabin, as preferred", "cabin_strict" if p.hard.cabin_strict else "cabin")
    elif p.hard.cabin_strict and all(
            leg["cabin_class"] in ("Business", "First") for leg in top.legs):
        cite("Premium cabin throughout — within this traveler's Business-or-above rule",
             "cabin_strict")
    used_preferred = sorted({leg["airline_code"] for leg in top.legs} & set(p.soft.airlines))
    if used_preferred:
        lines.append(f"Flies {', '.join(used_preferred)} from the preferred list")
    if p.soft.departure_time and all(
            leg["time_of_day"] == p.soft.departure_time for leg in top.legs):
        cite(f"All departures in the {p.soft.departure_time}", "departure_time")
    if p.soft.redeye_policy == "avoid" and not any(leg["is_redeye"] for leg in top.legs):
        cite("No redeyes", "redeye")
    if any(s.dimension == "connection_anxiety" for s in p.signals):
        min_otp = min(int(leg["on_time_performance"]) for leg in top.legs)
        cite(f"On-time performance ≥ {min_otp}% on every leg — no tight-connection stress",
             "connection_anxiety")
    if p.soft.checked_bags > 0 and all(leg["baggage_included"] for leg in top.legs):
        lines.append(f"Baggage included on every leg ({p.soft.checked_bags} checked needed)")
    if p.weights.price >= 0.35:
        cheapest = min(rec.ranked, key=lambda it: it.total_price)
        if top.flight_ids == cheapest.flight_ids:
            cite("It is the cheapest workable option found", "budget")
    return lines


def _tradeoffs(rec: RecommendationSet) -> list[str]:
    top, p = rec.top, rec.trip.profile
    lines = []
    for alt in rec.alternatives:
        it = alt.itinerary
        label = alt.label.replace("_", " ")
        desc = (f"{label.capitalize()}: "
                f"{'direct' if it.max_stops == 0 else f'{it.max_stops}-stop'} "
                f"${it.total_price:,.0f} ({alt.delta_price:+,.0f}$, "
                f"{'+' if alt.delta_minutes >= 0 else '−'}{_fmt_hours(alt.delta_minutes)})")
        if alt.worth_it:
            wi = alt.worth_it
            verdict = "worth it" if wi["verdict"] == "worth_it" else "not worth it"
            rate = f"~${wi['value_of_time_usd_per_hr']:.0f}/hr"
            hours = f"{abs(wi['extra_hours']):.1f} hrs"
            time_usd = f"${abs(wi['time_cost_usd']):,.0f}"
            money = f"${abs(wi['savings_usd']):,.0f}"
            if wi["savings_usd"] >= 0:  # slower but cheaper
                desc += (f" — saves {money} for {hours} more travel; at this traveler's "
                         f"{rate} that time costs {time_usd} → {verdict}")
            else:  # faster but pricier
                desc += (f" — costs {money} more to save {hours}; at this traveler's "
                         f"{rate} that time is worth {time_usd} → {verdict}")
        lines.append(desc)
    if not lines:
        lines.append("No meaningfully different alternative exists in the pool — "
                     "the top pick dominates on every axis")
    return lines


def _market_context(rec: RecommendationSet) -> list[str]:
    lines = []
    for ann in rec.top.annotations:
        bits = []
        if ann["seasonal_uplift"] is not None and abs(ann["seasonal_uplift"]) >= 0.10:
            pct = ann["seasonal_uplift"] * 100
            direction = "above" if pct > 0 else "below"
            bits.append(f"{ann['season'].replace('_', ' ')} fares run ~{abs(pct):.0f}% "
                        f"{direction} the shoulder-season baseline")
        if ann["is_holiday_season"]:
            bits.append("holiday-season pricing in effect")
        if ann["seats_available"] <= 3:
            bits.append(f"only {ann['seats_available']} seat(s) left at this fare — "
                        "this option can disappear")
        if bits:
            lines.append(f"{ann['route']}: " + "; ".join(bits))
    if rec.trip.advise_only and lines:
        lines.insert(0, "What to expect for these dates:")
    return lines


def _caveats(rec: RecommendationSet) -> list[str]:
    p = rec.trip.profile
    lines = [f'Noted but not bookable from this dataset (no seat-map/amenity data): "{ev}"'
             for ev in dict.fromkeys(s.evidence for s in p.unsupported)]
    if p.soft.departure_time and rec.top and not all(
            leg["time_of_day"] == p.soft.departure_time for leg in rec.top.legs):
        lines.append(f"Preferred {p.soft.departure_time} departures weren't available "
                     "on every leg of this pool")
    lines += [f"Profile conflict resolved: {c.reason}" for c in p.conflicts]
    lines += [n for n in rec.trip.notes if "region trip" in n or "could not resolve" in n]
    return lines


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def explain(rec: RecommendationSet) -> Explanation:
    if not rec.feasible or rec.top is None:
        return Explanation(
            headline="No itinerary exists in this dataset, even with every constraint relaxed",
            concessions=list(rec.relaxations),
            caveats=list(rec.trip.notes),
        )
    funnel = " → ".join(f"{n:,}" for _, n in rec.funnel)
    return Explanation(
        headline=_headline(rec),
        traveler_reading=_traveler_reading(rec.trip.profile),
        why_top=_why_top(rec),
        tradeoffs=_tradeoffs(rec),
        concessions=list(rec.relaxations),
        market_context=_market_context(rec),
        caveats=_caveats(rec),
        itinerary=[_leg_line(leg) for leg in rec.top.legs],
        funnel_line=funnel,
    )


_SECTIONS = [
    ("How I read this traveler", "traveler_reading"),
    ("Why this pick", "why_top"),
    ("The trade-offs", "tradeoffs"),
    ("What had to give (honest negotiation)", "concessions"),
    ("Market context", "market_context"),
    ("Fine print", "caveats"),
    ("Itinerary", "itinerary"),
]


def render_text(expl: Explanation) -> str:
    out = [f"### {expl.headline}"]
    if expl.funnel_line:
        out.append(f"\nSearch funnel: {expl.funnel_line}")
    for title, attr in _SECTIONS:
        items = getattr(expl, attr)
        if items:
            out.append(f"\n**{title}**")
            out.extend(f"- {item}" for item in items)
    return "\n".join(out)
