"""Evidence Bundle: the only information the final AI stage receives.

Both reasoning paths and the Twin emit structured outputs; this module
merges them mechanically and precomputes the grounding index — the
whitelist of every number and quotable string the Companion's prose is
allowed to contain. If a fact isn't in the bundle, the Companion has no
source for it and validate_grounding will catch it.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .explanation_engine import Explanation
from .inference_engine import ResolvedTrip
from .preference_extractor import Source
from .recommendation_engine import RecommendationSet
from .serializers import _explanation_json, _recommendation_json, _trip_json


@dataclass
class AgentReasoning:
    intent: str
    purpose: str | None = None
    strategy: str = "BALANCED"
    strategy_rationale: str = ""
    flexibility_days: int | None = None
    contradictions: list[dict] = field(default_factory=list)
    planning_context: list[str] = field(default_factory=list)
    refinements: list[str] = field(default_factory=list)
    confidence: float = 0.8


@dataclass
class EvidenceBundle:
    request_summary: dict            # slots with provenance + raw message
    reasoning: AgentReasoning        # from the Travel Intelligence Agent
    computation: dict                # serialized RecommendationSet + Explanation
    twin: dict                       # cited signals, conflicts, recent changes
    trace: list[dict]                # orchestration steps, in order
    grounding_numbers: set = field(default_factory=set)
    grounding_quotes: set = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "request_summary": self.request_summary,
            "reasoning": asdict(self.reasoning),
            "computation": self.computation,
            "twin": self.twin,
            "trace": self.trace,
        }

    def companion_view(self) -> dict:
        """Trimmed bundle for the Companion's prompt.

        The full ranked pool (20 itineraries x legs) exists for the UI and
        the grounding index, but drowning a token-budgeted model in it
        starves the actual reply. The Companion gets exactly what it may
        talk about: the decision, the alternatives, the reasoning, and the
        Twin's evidence. Grounding is still validated against the FULL
        bundle, so nothing here loosens the anti-hallucination gate.
        """
        rec = self.computation["recommendation"]
        return {
            "request_summary": self.request_summary,
            "reasoning": asdict(self.reasoning),
            "twin": self.twin,
            "computation": {
                "trip": self.computation["trip"],
                "explanation": self.computation["explanation"],
                "recommendation": {
                    "top": rec["top"],
                    "alternatives": rec["alternatives"],
                    "relaxations": rec["relaxations"],
                    "window_used": rec["window_used"],
                    "feasible": rec["feasible"],
                },
            },
        }


_TYPOGRAPHY = str.maketrans({
    "’": "'", "‘": "'", "“": '"', "”": '"',
    " ": " ", " ": " ",            # narrow/regular no-break space
    "‑": "-", "–": "-", "—": "-",  # hyphen/dash variants
})


def _norm_quote(text: str) -> str:
    """Punctuation/typography-insensitive form for quote matching."""
    t = text.strip().lower().translate(_TYPOGRAPHY)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .,;:!?…\"'—-")


def _collect(value, numbers: set, quotes: set) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            quotes.add(_norm_quote(str(k)))  # field names are ours, not fabrications
            _collect(v, numbers, quotes)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _collect(v, numbers, quotes)
    elif isinstance(value, bool):
        return
    elif isinstance(value, (int, float)):
        numbers.add(round(float(value), 2))
        numbers.add(float(round(value)))
    elif isinstance(value, str):
        quotes.add(_norm_quote(value))
        for n in re.findall(r"\d[\d,]*(?:\.\d+)?", value):
            try:
                numbers.add(round(float(n.replace(",", "")), 2))
                numbers.add(float(round(float(n.replace(",", "")))))
            except ValueError:
                pass


def build_bundle(trip: ResolvedTrip, rec: RecommendationSet, expl: Explanation,
                 reasoning: AgentReasoning, slots: dict, message: str,
                 twin_changelog: list[dict], trace: list[dict]) -> EvidenceBundle:
    profile = trip.profile
    twin = {
        "cited_signals": [
            {"dimension": s.dimension, "value": s.value, "source": s.source.value,
             "evidence": s.evidence, "confidence": s.confidence}
            for s in profile.signals
            if s.source in (Source.RAW_HISTORY, Source.REQUEST, Source.FEEDBACK,
                            Source.BEHAVIOR)
        ],
        "conflicts": [{"dimension": c.dimension, "reason": c.reason}
                      for c in profile.conflicts],
        "unsupported": [s.evidence for s in profile.unsupported],
        "recent_changes": twin_changelog[:6],
        "weights": profile.weights.as_dict(),
        "value_of_time": profile.flexibility.value_of_time_usd_per_hr,
    }
    bundle = EvidenceBundle(
        request_summary={"slots": {k: s.as_dict() for k, s in slots.items()},
                         "message": message},
        reasoning=reasoning,
        computation={"trip": _trip_json(trip),
                     "recommendation": _recommendation_json(rec),
                     "explanation": _explanation_json(expl)},
        twin=twin,
        trace=trace,
    )
    _collect(bundle.as_dict(), bundle.grounding_numbers, bundle.grounding_quotes)
    return bundle


# ---------------------------------------------------------------------------
# Grounding validation — the anti-hallucination gate for LLM prose.
# ---------------------------------------------------------------------------

# Small integers (hours, stop counts, seat counts) appear naturally in prose
# arithmetic; the gate is for *facts*: prices, minutes, percentages, years.
_SIGNIFICANT = 100


def validate_grounding(text: str, bundle: EvidenceBundle) -> list[str]:
    """Return violations (empty list = grounded)."""
    violations: list[str] = []
    for raw in re.findall(r"\$?\d[\d,]*(?:\.\d+)?", text):
        n = float(raw.replace("$", "").replace(",", ""))
        if raw.startswith("$") or n >= _SIGNIFICANT:
            if round(n, 2) not in bundle.grounding_numbers \
                    and float(round(n)) not in bundle.grounding_numbers:
                violations.append(f"number {raw} not in evidence")
    for quote in re.findall(r'[“"]([^"”]{6,})[”"]', text):
        # an elided quote ("start … end") is fine iff every fragment is verbatim
        for fragment in re.split(r"…|\.\.\.", quote):
            q = _norm_quote(fragment)
            if len(q) >= 6 and not any(q in known for known in bundle.grounding_quotes):
                violations.append(f'quote "{fragment.strip()}" not in evidence')
    return violations
