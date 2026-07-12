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
import re
from dataclasses import dataclass, field, replace
from datetime import date

from . import config
from .companion import compose
from .evidence_bundle import AgentReasoning, EvidenceBundle, build_bundle
from .explanation_engine import Explanation, explain
from .inference_engine import ResolvedTrip, resolve
from .input_normalizer import (normalize_budget, normalize_date, normalize_party,
                               normalize_tone)
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

# Hybrid gate: cue detectors. A cue means "the user probably expressed this,
# so if the deterministic layer resolved nothing for it, the LLM should try."
# The LLM is invoked ONLY when a cue is present but unresolved.
_DATE_CUE = re.compile(
    r"\b(week|fortnight|month|day|weekend|tomorrow|today|tonight|"
    r"mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"after|before|around|late|mid|early|beginning|end of|sometime|some time|"
    r"next|this|coming|diwali|pongal|christmas|xmas|new year|holiday|"
    r"summer|winter|spring|autumn|fall|season)\b")
# Money cue: currency symbols, budget slang, or an amount qualifier IMMEDIATELY
# before a number. "around"/"about" alone are date qualifiers too ("around the
# holidays", "about three weeks"), so they only count when a digit follows.
_MONEY_CUE = re.compile(
    r"[$€£₹]"
    r"|\b(?:budget|grand|lakh)\b"
    r"|\b(?:under|below|less than|within|up to|no more than|max(?:imum)?|around|about)"
    r"\s*\$?\s*\d"
    r"|\d\s*k\b"
    r"|\$?\d{3,}")
_PEOPLE_CUE = re.compile(
    r"\b(me and|my (?:wife|husband|partner|spouse|kids?|children|family)|family|"
    r"adults?|children|child|kids?|infants?|passengers?|people|of us|couple|"
    r"solo|alone|just me|two of us|group of)\b")


def get_llm():
    """The configured chat model, else None (deterministic fallback).

    Providers, by env key: GROQ_API_KEY -> ChatGroq (llama-3.3-70b);
    CEREBRAS_API_KEY -> ChatOpenAI against api.cerebras.ai (default
    gpt-oss-120b — wafer-speed inference, ~1-2s turns);
    OPENROUTER_API_KEY -> ChatOpenAI against openrouter.ai (default model
    tencent/hy3:free — a reasoning model, so token budgets are generous and
    reasoning effort is pinned low). Override the model with TWIN_LLM_MODEL.
    """
    if os.environ.get("GROQ_API_KEY"):
        try:
            from langchain_groq import ChatGroq
            return ChatGroq(model=os.environ.get("TWIN_LLM_MODEL",
                                                 "llama-3.3-70b-versatile"),
                            temperature=0.2)
        except ImportError:
            return None
    if os.environ.get("CEREBRAS_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=os.environ.get("TWIN_LLM_MODEL", "gpt-oss-120b"),
                api_key=os.environ["CEREBRAS_API_KEY"],
                base_url="https://api.cerebras.ai/v1",
                temperature=0.2,
                max_tokens=6000,  # these are reasoning models too
            )
        except ImportError:
            return None
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=os.environ.get("TWIN_LLM_MODEL", "tencent/hy3:free"),
                api_key=os.environ["OPENROUTER_API_KEY"],
                base_url="https://openrouter.ai/api/v1",
                temperature=0.2,
                max_tokens=6000,  # reasoning models spend tokens thinking first
                extra_body={"reasoning": {"effort": "low"}},
            )
        except ImportError:
            return None
    return None


def _json_invoke(llm, prompt: str, schema_model):
    """Portable structured output: ask for pure JSON, parse, validate.

    Free-tier models often lack the OpenAI tools API that
    `with_structured_output` relies on; a JSON-only instruction plus
    pydantic validation works across all of them. Any failure raises, and
    the caller falls back to the rule-based path.
    """
    import json as _json
    import re as _re

    schema = {k: v.get("type", "any")
              for k, v in schema_model.model_json_schema()["properties"].items()}
    reply = llm.invoke(prompt + "\n\nReply with ONLY a JSON object, no prose, "
                                f"with exactly these keys: {_json.dumps(schema)}")
    text = reply.content if isinstance(reply.content, str) else str(reply.content)
    match = _re.search(r"\{.*\}", text, _re.S)
    if not match:
        raise ValueError(f"no JSON in model reply: {text[:120]!r}")
    return schema_model.model_validate(_json.loads(match.group(0)))


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
    harvested_budget: float | None = None   # NL budget found in the message
    harvested_party: int | None = None       # NL passenger count in the message


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

        # Fold message-harvested counts/budget into slots so they flow through
        # the identical downstream logic (required_seats, strategy) as the
        # form fields — never overriding a value the form already set.
        if "travellers" not in slots and und.harvested_party:
            slots["travellers"] = Slot(und.harvested_party, "text", 0.9,
                                       f"message: party of {und.harvested_party}")
            trace.append({"step": "normalize_input",
                          "detail": f"party of {und.harvested_party} from message"})
        if "budget" not in slots and und.harvested_budget:
            slots["budget"] = Slot(und.harvested_budget, "text", 0.9,
                                   f"message: budget ${und.harvested_budget:.0f}")
            trace.append({"step": "normalize_input",
                          "detail": f"budget ${und.harvested_budget:.0f} from message"})

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
        """Hybrid pipeline: deterministic first, LLM only to fill real gaps.

        1. Fast deterministic parse (rules + normalizer harvest).
        2. If it resolved the request confidently -> DO NOT call the LLM.
        3. Otherwise the LLM fills ONLY the missing fields; every value it
           returns is re-validated by the same deterministic validators.
        """
        und = self._understand_rules(message, slots)
        self._deterministic_harvest(und, message, slots)

        gaps = self._gaps(und, message, slots)
        if gaps and self.llm is not None:
            try:
                self._llm_fill_gaps(und, message, slots, profile, gaps)
                trace.append({"step": "understand",
                              "detail": f"deterministic + LLM gap-fill ({', '.join(gaps)})"})
            except Exception as e:  # noqa: BLE001 — LLM must never break planning
                trace.append({"step": "understand",
                              "detail": f"deterministic; LLM gap-fill failed "
                                        f"({type(e).__name__})"})
        else:
            detail = ("deterministic (high confidence, no LLM)" if not gaps
                      else f"deterministic ({', '.join(gaps)} unresolved, no LLM available)")
            trace.append({"step": "understand", "detail": detail})
        return self._finish_understanding(und, message, slots, profile)

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

    def _deterministic_harvest(self, und: Understanding, message: str,
                               slots: dict[str, Slot]) -> None:
        """Fill dates/party/budget from free text via the deterministic
        normalizer. Confident matches only; never overrides a form value."""
        if not message:
            return
        if (und.date_phrase is None and und.explicit_window is None
                and "dates" not in slots):
            nd = normalize_date(message)
            if nd.window:
                und.explicit_window = nd.window
            elif nd.ambiguous:
                und.ambiguities.append({"slot": "dates", "question": nd.question})
        if "travellers" not in slots and und.harvested_party is None:
            und.harvested_party = normalize_party(message)
        if "budget" not in slots and und.harvested_budget is None:
            und.harvested_budget = normalize_budget(message)

    # ------- hybrid gate: what did deterministic parsing fail to resolve? ----

    def _gaps(self, und: Understanding, message: str, slots: dict[str, Slot]) -> list[str]:
        """List the field categories a cue implies but deterministic parsing
        left unresolved. Empty list -> the LLM is not needed."""
        if not message:
            return []
        text = message.lower()
        sig = {(s.dimension, s.value) for s in und.request_signals}
        gaps: list[str] = []
        if not (und.destinations or und.region or "destination" in slots):
            gaps.append("destination")
        has_date = bool(und.date_phrase or und.explicit_window or "dates" in slots
                        or any(a["slot"] == "dates" for a in und.ambiguities))
        if _DATE_CUE.search(text) and not has_date:
            gaps.append("dates")
        has_budget = bool(und.harvested_budget or "budget" in slots
                          or ("budget", "minimize") in sig)
        if _MONEY_CUE.search(text) and not has_budget:
            gaps.append("budget")
        has_party = bool(und.harvested_party or "travellers" in slots)
        if _PEOPLE_CUE.search(text) and not has_party:
            gaps.append("passengers")
        return gaps

    def _llm_fill_gaps(self, und: Understanding, message: str,
                       slots: dict[str, Slot], profile, gaps: list[str]) -> None:
        """Extend the EXISTING structured-output schema to cover the gap
        fields, then validate every value through the deterministic
        validators and fill ONLY what deterministic parsing missed. The LLM
        never overrides a confidently-parsed value and never touches ranking,
        the Twin, or explanations."""
        from pydantic import BaseModel, Field

        class UnderstandModel(BaseModel):
            intent: str = Field("SEARCH", description="SEARCH, PREFERENCE_UPDATE, or ADVICE")
            destination_names: list[str] = Field(default_factory=list)
            region: str | None = None
            dates: str | None = Field(None, description="ISO date/range or a short "
                                      "phrase like 'mid August'; null if unstated")
            budget: str | None = Field(None, description="numeric cap only, e.g. '2000'")
            passengers: int | None = Field(None, description="total travellers, 1-9")
            max_stops: int | None = Field(None, description="0, 1, or 2; null if unstated")
            purpose: str | None = Field(None, description="business|leisure|mixed")
            flexible_dates: bool = False
            wants_comfort: bool = False
            wants_cheapest: bool = False
            avoid_redeye: bool = False
            round_trip: bool = False
            multi_city: bool = False
            advise_only: bool = False

        known = {k: str(s.value) for k, s in slots.items()}
        prompt = (
            "You are the input-understanding layer of a flight planner. Convert "
            "the traveler's message into structured fields ONLY. Do not rank, "
            "recommend, or explain. These form fields are already VALIDATED — do "
            f"not restate or second-guess them: {known}. Unresolved gaps to try: "
            f"{gaps}. If a value is not clearly stated, return null (never guess). "
            "Normalize numbers: 'two grand'->2000, '60k'->60000. Message:\n" + message)
        out = _json_invoke(self.llm, prompt, UnderstandModel)

        from .airports import REGION_ALIASES, resolve_city

        # --- non-gap fields (shape/intent/purpose): merge, never override ----
        und.intent = out.intent if out.intent in ("SEARCH", "PREFERENCE_UPDATE",
                                                  "ADVICE") else und.intent
        if out.purpose in ("business", "leisure", "mixed") and not und.purpose:
            und.purpose = out.purpose
        und.round_trip = und.round_trip or bool(out.round_trip)
        und.multi_city = und.multi_city or bool(out.multi_city)
        und.advise_only = und.advise_only or bool(out.advise_only)

        # Once the LLM is invoked for any gap, let it fill ANY field the
        # deterministic layer still missed (each re-validated below). It never
        # overrides a value deterministic parsing already resolved.

        # --- destination: gazetteer VERIFY ----------------------------------
        if not (und.destinations or und.region):
            for name in out.destination_names:
                code = resolve_city(name)
                if code and code not in und.destinations:
                    und.destinations.append(code)
            if out.region and not und.region:
                und.region = REGION_ALIASES.get(out.region.lower())

        # --- dates: validate via the existing date validator ----------------
        if out.dates and not (und.date_phrase or und.explicit_window):
            win, phrase, _err = parse_dates_field(out.dates)
            if win:
                und.explicit_window = win
            elif phrase:
                und.date_phrase = phrase

        # --- budget: validate via normalize_budget + range ------------------
        if out.budget and und.harvested_budget is None:
            b = normalize_budget(str(out.budget))
            if b is None:
                try:
                    v = float(str(out.budget).replace(",", "").replace("$", ""))
                    b = v if 20 <= v <= 50000 else None
                except (TypeError, ValueError):
                    b = None
            und.harvested_budget = b

        # --- passengers: validate range -------------------------------------
        if out.passengers and und.harvested_party is None:
            if 1 <= int(out.passengers) <= 9:
                und.harvested_party = int(out.passengers)

        # --- discrete preference hints -> REQUEST signals (validated) -------
        # These reuse the SAME downstream machinery as deterministic signals;
        # previously the model returned wants_* booleans that were discarded.
        sig = {(s.dimension, s.value) for s in und.request_signals}

        def add_signal(dim, val, evidence):
            if (dim, val) not in sig:
                und.request_signals.append(
                    PreferenceSignal(dim, val, Source.REQUEST, evidence, 0.85))
                sig.add((dim, val))

        if out.max_stops is not None and out.max_stops in (0, 1, 2):
            if not any(d == "stops" for d, _ in sig):
                add_signal("stops", "avoid" if out.max_stops == 0 else "limit_1",
                           f"max_stops={out.max_stops} (interpreted)")
        if out.avoid_redeye:
            add_signal("redeye", "avoid", "avoid overnight flights (interpreted)")
        if out.wants_cheapest:
            add_signal("budget", "minimize", "wants the cheapest option (interpreted)")
        if out.wants_comfort:
            add_signal("budget", "unconstrained", "comfort over cost (interpreted)")
        if out.flexible_dates:
            add_signal("dates", "flexible", "flexible on dates (interpreted)")

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
        # sanity guards against model over-generation: explicit destinations
        # make a volunteered region redundant, and one destination is not a
        # multi-city trip — a phantom loop can make a feasible ask infeasible
        if und.destinations:
            und.region = None
        und.multi_city = (und.multi_city and (len(und.destinations) > 1
                                              or bool(und.region))) \
            or len(und.destinations) > 1

        signal_values = {(s.dimension, s.value) for s in und.request_signals}
        has_dest = bool(und.destinations or und.region)

        # a known destination means a trip is being planned, whatever the
        # model thought the message alone implied — the form outranks it
        if has_dest and und.intent == "PREFERENCE_UPDATE":
            und.intent = "SEARCH"

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

        # strategy: the agent's judgment, as a label. A conversational tone
        # hint ("fastest", "cheap", "comfortable") folds into the existing
        # detectors; only "fastest" adds behavior the system lacked.
        tone = normalize_tone(message) if message else None
        occasion = ("occasion", "special") in signal_values
        comfort_asked = (occasion or ("budget", "unconstrained") in signal_values
                         or tone == "comfort")
        cheap_asked = (("budget", "minimize") in signal_values or "budget" in slots
                       or und.harvested_budget is not None or tone == "cheapest")
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
        elif tone == "fastest":
            und.strategy, und.strategy_rationale = "SCHEDULE_FIRST", "fastest/quickest requested"
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
