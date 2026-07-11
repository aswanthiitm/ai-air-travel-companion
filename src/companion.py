"""AI Companion: the final, communication-only LLM stage.

Consumes the Evidence Bundle and nothing but the Evidence Bundle; produces
the user-facing reply. It never changes a recommendation and can introduce
no number or quote absent from the bundle's grounding index — a violation
discards its prose and serves the deterministic template renderer instead.
A turn can degrade from conversational to templated, never from correct to
wrong.
"""
from __future__ import annotations

import json

from .evidence_bundle import EvidenceBundle, validate_grounding
from .explanation_engine import Explanation, render_text

_SYSTEM = """You are the Traveler Twin's AI Companion for flight planning.
You will receive an EVIDENCE BUNDLE (JSON). Write the reply to the traveler.

Hard rules:
- Every price, duration, date, percentage, airline and quote MUST come from
  the bundle. Introduce nothing. If it's not in the bundle, don't say it.
- Never change, re-rank or second-guess the recommendation — the engine
  already decided; your job is to explain it well.
- Cite the traveler's own words (twin.cited_signals evidence) where they
  explain a choice; put verbatim quotes in double quotes.
- If reasoning.contradictions is non-empty, acknowledge the tension
  gracefully (e.g. their usual habit vs this request).
- Be honest about concessions (computation.recommendation.relaxations).
- Match the register of the occasion. Be warm, concrete, brief:
  120-220 words, short paragraphs, no bullet spam, no markdown headers.
"""


def compose(bundle: EvidenceBundle, explanation: Explanation,
            llm=None) -> tuple[str, bool, list[str]]:
    """-> (text, llm_used, grounding_violations). Fails closed to the template."""
    fallback = render_text(explanation)
    if llm is None:
        return fallback, False, []
    try:
        reply = llm.invoke([
            ("system", _SYSTEM),
            ("user", json.dumps(bundle.as_dict(), default=str)),
        ])
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        violations = validate_grounding(text, bundle)
        if violations:
            return fallback, False, violations
        return text, True, []
    except Exception:  # noqa: BLE001 — the Companion must never break a turn
        return fallback, False, ["llm call failed"]
