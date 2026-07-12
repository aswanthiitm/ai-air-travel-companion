"""Travel Intelligence Agent: the reasoning layer of the v4 architecture.

Understands the request (intent, slots, purpose, contradictions), reads the
Living Twin, selects an optimization strategy, orchestrates the
deterministic tools, validates results (bounded refinement), and assembles
the Evidence Bundle. It performs no deterministic computation — every
number comes from the engine.

Two modes behind one interface:
- **LLM mode** (GROQ_API_KEY set): the UNDERSTAND phase is a structured-
  output call to the model; everything it proposes is verified by
  planner_validators before it can touch the engine.
- **Fallback mode** (no key): the v1 rule-based parser plus Twin defaults —
  reduced language understanding, identical downstream flow, fully offline.
  This keeps evaluation reproducible and the demo network-proof.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import date

from . import config
from .companion import compose
from .evidence_bundle import AgentReasoning, EvidenceBundle, build_bundle
from .explanation_engine import Explanation, explain
from .inference_engine import ResolvedTrip, resolve
from .planner_validators import Slot, validate_fields
from .preference_extractor import PreferenceSignal, Source, extract_history
from .preprocessing import FlightStore
from .recommendation_engine import RecommendationSet, recommend
from .request_parser import DatePhrase, TripSpec, parse_request
from .twin_store import TwinStore

# Strategy labels the agent may choose; deterministic mapping to engine
# inputs the engine already accepts. The LLM picks; Python maps; the engine
# computes.
STRATEGY_PRESETS: dict[str, dict] = {
    "CHEAPEST_FIRST": {"price": 0.25},
    "SCHEDULE_FIRST": {"time": 0.15, "convenience": 0.10},
    "COMFORT_FIRST": {"comfort": 0.20, "convenience": 0.05},
    "MULTI_CITY": {},
    "ADVISE_ONLY": {},
    "BALANCED": {},
}

MAX_REFINEMENTS = 1

# Dimensions worth carrying as REQUEST signals when stated in the message.
_REQUEST_DIMS = {"budget", "stops", "redeye", "layover_tolerance",
                 "departure_time", "occasion", "cabin_strict", "season"}


def get_llm():
    """ChatGroq when a key is configured and langchain is importable, else None."""
    if not os.environ.get("GROQ_API_KEY"):
        return None
    try:
        from langchain_groq import ChatGroq
        return ChatGroq(model=os.environ.get("TWIN_LLM_MODEL", "llama-3.3-70b-versatile"),
                        temperature=0.2)
    except ImportError:
        return None


@dataclass
class Understanding:
    intent: str                       # SEARCH | PREFERENCE_UPDATE | ADVICE
    destinations: list[str] = field(default_factory=list)   # IATA
    region: str | None = None
    date_phrase: DatePhrase | None = None
    explicit_window: tuple | None = None
    purpose: str | None = None
    strategy: str = "BALANCED"
    strategy_rationale: str = ""
    round_trip: bool = False
    multi_city: bool = False
    advise_only: bool = False
    request_signals: list[PreferenceSignal] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    ambiguities: list[dict] = field(default_factory=list)   # {slot, question}


@dataclass
class PlanOutcome:
    status: str                       # complete | clarify | acknowledged | error
    question: str | None = None
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    reasoning: AgentReasoning | None = None
    trip: ResolvedTrip | None = None
    recommendation: RecommendationSet | None = None
    explanation: Explanation | None = None
    bundle: EvidenceBundle | None = None
    narrative: str = ""
    llm_used: bool = False
    grounding_violations: list[str] = field(default_factory=list)
    twin_updates: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


class TravelIntelligenceAgent:
    def __init__(self, store: FlightStore, twin: TwinStore, llm=None):
        self.store = store
        self.twin = twin
        self.llm = llm if llm is not None else get_llm()
        self._conversations: dict[str, dict] = {}

    # ------------------------------------------------------------------ plan

    def plan(self, user_id: str, fields: dict | None = None, message: str = "",
             conversation_id: str | None = None,
             now: date = config.SIMULATED_NOW) -> PlanOutcome:
        trace: list[dict] = []
        slots, errors = validate_fields(fields)
        if errors:
            return PlanOutcome(status="error", errors=errors,
                               slots={k: s.as_dict() for k, s in slots.items()})
        trace.append({"step": "validate", "detail": f"{len(slots)} form slot(s) verified"})

        # clarify-resume: merge with what this conversation already knew
        if conversation_id and conversation_id in self._conversations:
            prev = self._conversations[conversation_id]
            message = (prev["message"] + " " + message).strip()
            slots = {**prev["slots"], **slots}
            trace.append({"step": "resume", "detail": "merged with earlier turn"})

        profile = self.twin.effective_profile(user_id)
        trace.append({"step": "get_traveler_twin",
                      "detail": f"{len(profile.signals)} signals, "
                                f"{self.twin.event_count(user_id)} live events"})

        und = self._understand(message, slots, profile, trace)

        if und.intent == "PREFERENCE_UPDATE":
            changes = self.twin.record(user_id, "preference_stated",
                                       {"message": message}, conversation_id)
            trace.append({"step": "record_feedback",
                          "detail": f"{len(changes)} trait change(s)"})
            return PlanOutcome(status="acknowledged",
                               slots={k: s.as_dict() for k, s in slots.items()},
                               twin_updates=[c.as_dict() for c in changes],
                               narrative=self._ack_text(changes), trace=trace)

        if und.ambiguities:
            if conversation_id:
                self._conversations[conversation_id] = {"slots": slots, "message": message}
            first = und.ambiguities[0]
            trace.append({"step": "ask_clarifying_question", "detail": first["slot"]})
            return PlanOutcome(status="clarify", question=first["question"],
                               missing=[a["slot"] for a in und.ambiguities],
                               slots={k: s.as_dict() for k, s in slots.items()},
                               trace=trace)

        # ----- profile adjustments the slots/strategy imply (copies only) ----
        run_profile = profile
        # stated constraints bind BEFORE the first search (request > history)
        stated = {(s.dimension, s.value) for s in und.request_signals}
        soft_updates = {}
        if ("redeye", "avoid") in stated:
            soft_updates["redeye_policy"] = "avoid"
        if ("layover_tolerance", "avoid_long") in stated:
            soft_updates["layover_tolerance"] = "avoid_long"
        if ("departure_time", "morning") in stated:
            soft_updates["departure_time"] = "morning"
        if soft_updates:
            run_profile = replace(run_profile,
                                  soft=replace(run_profile.soft, **soft_updates))
        if "origin" in slots:
            run_profile = replace(run_profile, home_airport=slots["origin"].value)
        if "travellers" in slots:
            run_profile = replace(run_profile, hard=replace(
                run_profile.hard, required_seats=int(slots["travellers"].value)))
        if "cabin" in slots:
            run_profile = replace(run_profile, soft=replace(
                run_profile.soft, cabin=slots["cabin"].value))
        preset = STRATEGY_PRESETS[und.strategy]
        if preset:
            run_profile = replace(
                run_profile,
                weights=run_profile.weights.adjusted(**preset),
                weight_rationale=run_profile.weight_rationale
                + [f"strategy {und.strategy}: {und.strategy_rationale}"])

        spec = TripSpec(
            raw_text=message or "(structured form request)",
            destination_names=und.destinations, region=und.region,
            date_phrase=und.date_phrase, explicit_window=und.explicit_window,
            round_trip=und.round_trip, multi_city=und.multi_city,
            advise_only=und.advise_only, signals=und.request_signals)

        try:
            trip = resolve(spec, run_profile, self.store, now)
        except ValueError as e:
            return PlanOutcome(status="error", errors=[str(e)], trace=trace)
        trace.append({"step": "resolve_trip",
                      "detail": f"{trip.origin}→{'+'.join(trip.destinations)} "
                                f"{trip.depart_window}"})

        rec = recommend(trip, self.store, now)
        trace.append({"step": "search_flights",
                      "detail": f"pool {len(rec.ranked)}, "
                                f"{len(rec.relaxations)} concession(s)"})

        refinements: list[str] = []
        rec, trip = self._validate_results(und, trip, rec, refinements, trace, now)

        expl = explain(rec)
        reasoning = AgentReasoning(
            intent=und.intent, purpose=und.purpose, strategy=und.strategy,
            strategy_rationale=und.strategy_rationale,
            flexibility_days=trip.profile.flexibility.date_flexibility_days,
            contradictions=und.contradictions,
            planning_context=self._planning_context(trip.profile, und, slots),
            refinements=refinements)

        bundle = build_bundle(trip, rec, expl, reasoning, slots, message,
                              self.twin.changelog(user_id, limit=6), trace)
        narrative, llm_used, violations = compose(bundle, expl, self.llm)
        trace.append({"step": "respond",
                      "detail": "LLM prose" if llm_used else "template renderer"})

        updates = []
        if rec.top is not None:
            updates = self.twin.record(user_id, "recommendation_shown", {
                "itinerary": _itinerary_summary(rec)}, conversation_id)
        if conversation_id:
            self._conversations.pop(conversation_id, None)

        return PlanOutcome(
            status="complete", slots={k: s.as_dict() for k, s in slots.items()},
            reasoning=reasoning, trip=trip, recommendation=rec, explanation=expl,
            bundle=bundle, narrative=narrative, llm_used=llm_used,
            grounding_violations=violations,
            twin_updates=[c.as_dict() for c in updates], trace=trace)

    # ---------------------------------------------------------- understand

    def _understand(self, message: str, slots: dict[str, Slot], profile,
                    trace: list[dict]) -> Understanding:
        if self.llm is not None and message:
            try:
                und = self._understand_llm(message, slots, profile)
                trace.append({"step": "understand", "detail": "LLM structured output"})
                return self._finish_understanding(und, message, slots, profile)
            except Exception as e:  # noqa: BLE001 — LLM path must never break planning
                trace.append({"step": "understand",
                              "detail": f"LLM failed ({type(e).__name__}); rule fallback"})
        else:
            trace.append({"step": "understand", "detail": "rule-based (offline mode)"})
        return self._finish_understanding(
            self._understand_rules(message, slots), message, slots, profile)

    def _understand_rules(self, message: str, slots: dict[str, Slot]) -> Understanding:
        und = Understanding(intent="SEARCH")
        if message:
            spec = parse_request(message)
            und.region = spec.region
            und.date_phrase = spec.date_phrase
            und.round_trip = spec.round_trip
            und.multi_city = spec.multi_city
            und.advise_only = spec.advise_only
            und.request_signals = list(spec.signals)
            from .airports import resolve_city
            und.destinations = [c for n in spec.destination_names
                                if (c := resolve_city(n))]
        return und

    def _understand_llm(self, message: str, slots: dict[str, Slot],
                        profile) -> Understanding:
        """UNDERSTAND via structured output; VERIFY happens in _finish."""
        from pydantic import BaseModel, Field

        class UnderstandModel(BaseModel):
            intent: str = Field(description="SEARCH, PREFERENCE_UPDATE, or ADVICE")
            destination_names: list[str] = Field(default_factory=list)
            region: str | None = None
            purpose: str | None = Field(None, description="business|leisure|mixed")
            wants_comfort: bool = False
            wants_cheapest: bool = False
            avoid_redeye: bool = False
            round_trip: bool = False
            multi_city: bool = False
            advise_only: bool = False

        known = {k: str(s.value) for k, s in slots.items()}
        prompt = (
            "You are the Travel Intelligence Agent. The following form fields are "
            f"VALIDATED FACTS — do not re-extract or second-guess them: {known}. "
            f"The traveler's profile: {profile.trip_purpose} traveler from "
            f"{profile.home_city}. Analyze ONLY the free-text message for intent "
            "and anything the form doesn't cover.\n\nMessage: " + message)
        out = self.llm.with_structured_output(UnderstandModel).invoke(prompt)

        und = self._understand_rules(message, slots)  # deterministic floor
        und.intent = out.intent if out.intent in ("SEARCH", "PREFERENCE_UPDATE",
                                                  "ADVICE") else "SEARCH"
        und.purpose = out.purpose
        und.round_trip = und.round_trip or out.round_trip
        und.multi_city = und.multi_city or out.multi_city
        und.advise_only = und.advise_only or out.advise_only
        from .airports import resolve_city
        for name in out.destination_names:      # VERIFY: gazetteer or it didn't happen
            code = resolve_city(name)
            if code and code not in und.destinations:
                und.destinations.append(code)
        if out.region and not und.region:
            from .airports import REGION_ALIASES
            und.region = REGION_ALIASES.get(out.region.lower())
        return und

    def _finish_understanding(self, und: Understanding, message: str,
                              slots: dict[str, Slot], profile) -> Understanding:
        # message-level preference signals (lexicon; REQUEST-scoped)
        if message:
            for s in extract_history([p.strip() for p in message.split(".") if p.strip()]):
                if s.dimension in _REQUEST_DIMS:
                    und.request_signals.append(PreferenceSignal(
                        s.dimension, s.value, Source.REQUEST, s.evidence, 0.9))

        # form slots outrank everything
        if "destination" in slots:
            und.destinations = list(slots["destination"].value)
        if "dates" in slots:
            v = slots["dates"].value
            if isinstance(v, DatePhrase):
                und.date_phrase = v
            else:
                und.explicit_window = tuple(v)
        und.multi_city = und.multi_city or len(und.destinations) > 1

        signal_values = {(s.dimension, s.value) for s in und.request_signals}
        has_dest = bool(und.destinations or und.region)

        if not has_dest:
            if message and signal_values and und.intent != "ADVICE" \
                    and not und.date_phrase:
                und.intent = "PREFERENCE_UPDATE"
            else:
                und.ambiguities.append({
                    "slot": "destination",
                    "question": "Where would you like to go — or should I suggest "
                                "somewhere based on your travel history?"})

        if und.advise_only:
            und.intent = "ADVICE"

        # strategy: the agent's judgment, as a label
        occasion = ("occasion", "special") in signal_values
        comfort_asked = occasion or ("budget", "unconstrained") in signal_values
        cheap_asked = ("budget", "minimize") in signal_values or "budget" in slots
        if und.advise_only:
            und.strategy, und.strategy_rationale = "ADVISE_ONLY", "expectation-setting ask"
        elif und.multi_city or und.region:
            und.strategy, und.strategy_rationale = "MULTI_CITY", "multiple cities requested"
        elif comfort_asked:
            und.strategy = "COMFORT_FIRST"
            und.strategy_rationale = ("special occasion — comfort explicitly outranks "
                                      "price" if occasion else "comfort explicitly requested")
        elif cheap_asked:
            und.strategy, und.strategy_rationale = "CHEAPEST_FIRST", "price explicitly leads"
        elif (und.purpose or profile.trip_purpose) == "business":
            und.strategy, und.strategy_rationale = "SCHEDULE_FIRST", "business trip: schedule fit leads"
        else:
            und.strategy, und.strategy_rationale = "BALANCED", "no dominant axis requested"

        # contradiction detection: request vs the Twin's standing habits
        w = profile.weights
        if und.strategy == "COMFORT_FIRST" and w.price >= 0.40:
            und.contradictions.append({
                "request_says": "comfort over cost, this time",
                "twin_says": f"price-driven traveler (price weight {w.price:.2f})",
                "resolution": "the current request outranks history for this trip; "
                              "tension recorded, not hidden"})
        if und.strategy == "CHEAPEST_FIRST" and w.comfort >= 0.30:
            und.contradictions.append({
                "request_says": "cheapest option, this time",
                "twin_says": f"comfort-driven traveler (comfort weight {w.comfort:.2f})",
                "resolution": "the current request outranks history for this trip"})
        return und

    # --------------------------------------------------- results validation

    def _validate_results(self, und: Understanding, trip: ResolvedTrip,
                          rec: RecommendationSet, refinements: list[str],
                          trace: list[dict], now: date):
        """Bounded post-search judgment: do the findings serve the intent?

        One refinement, agent-directed, engine-decided: for a comfort-led ask
        that still topped a redeye, re-search with stronger comfort emphasis
        AND a widened date window — trading date proximity for comfort is
        exactly the judgment call a human agent would make for a honeymoon.
        The engine's scoring still decides the winner; if only overnight
        options exist anywhere near the dates, that is stated, not hidden.
        """
        from .inference_engine import DateWindow
        from datetime import timedelta

        if (len(refinements) < MAX_REFINEMENTS and rec.top is not None
                and und.strategy == "COMFORT_FIRST"
                and any(leg["is_redeye"] for leg in rec.top.legs)):
            profile2 = replace(
                trip.profile,
                weights=trip.profile.weights.adjusted(comfort=0.10, convenience=0.10),
                soft=replace(trip.profile.soft, redeye_policy="avoid"))
            wide = DateWindow(trip.depart_window.start,
                              trip.depart_window.end + timedelta(days=90))
            trip2 = replace(trip, profile=profile2, depart_window=wide,
                            notes=trip.notes + [
                                "agent refinement: widened the window — for this "
                                "ask, a later date beats an overnight flight"])
            rec2 = recommend(trip2, self.store, now)
            if rec2.top is not None and not any(l["is_redeye"] for l in rec2.top.legs):
                refinements.append(
                    "first pass topped a redeye despite the comfort ask — "
                    "re-searched with stronger comfort emphasis and a wider "
                    "date window; found a daytime option")
                trace.append({"step": "search_flights (refine)",
                              "detail": "comfort refinement accepted"})
                return rec2, trip2
            refinements.append(
                "comfort refinement attempted (stronger comfort weighting, "
                "wider dates) — only overnight options exist near these dates; "
                "kept the original with the trade-off stated")
        return rec, trip

    # ----------------------------------------------------------- small bits

    @staticmethod
    def _planning_context(profile, und: Understanding, slots: dict) -> list[str]:
        w = profile.weights.as_dict()
        driver = max(w, key=w.get)
        ctx = [f"twin's dominant driver: {driver} ({w[driver]:.2f})",
               f"hard limits: layover ≤ {profile.hard.max_layover_minutes}min, "
               f"{profile.hard.required_seats} seat(s)"]
        if profile.flexibility.value_of_time_usd_per_hr:
            ctx.append(f"revealed value of time ~$"
                       f"{profile.flexibility.value_of_time_usd_per_hr:.0f}/hr")
        if "budget" in slots:
            ctx.append(f"stated budget ${slots['budget'].value:,.0f}")
        if und.contradictions:
            ctx.append("request contradicts a standing habit — resolved in the "
                       "request's favor, acknowledged in the reply")
        return ctx

    @staticmethod
    def _ack_text(changes) -> str:
        if not changes:
            return ("Noted — nothing new to learn from that, but it matches what "
                    "the Twin already believes.")
        lines = "\n".join(f"- {c.description}" for c in changes)
        return f"Got it — the Twin has been updated:\n{lines}"


def _itinerary_summary(rec: RecommendationSet) -> dict:
    top = rec.top
    return {
        "max_stops": int(top.max_stops),
        "is_redeye": bool(any(leg["is_redeye"] for leg in top.legs)),
        "airlines": sorted({leg["airline_code"] for leg in top.legs}),
        "cabins": sorted({leg["cabin_class"] for leg in top.legs}),
        "total_price": top.total_price,
    }
