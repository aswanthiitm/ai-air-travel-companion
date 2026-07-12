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
  explain a choice. Quotation marks are ONLY for verbatim quotes copied
  exactly from the bundle — never put a paraphrase inside quote marks.
- If reasoning.contradictions is non-empty, acknowledge the tension
  gracefully (e.g. their usual habit vs this request).
- Be honest about concessions (computation.recommendation.relaxations).
- Never mention internal JSON field names (is_redeye, max_stops, ...) —
  speak like a travel companion, not a payload.
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
            ("user", json.dumps(bundle.companion_view(), default=str)),
        ])
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        if not text.strip():  # reasoning models can burn the budget and say nothing
            return fallback, False, ["empty model reply"]
        violations = validate_grounding(text, bundle)
        if violations:
            return fallback, False, violations
        return text, True, []
    except Exception:  # noqa: BLE001 — the Companion must never break a turn
        return fallback, False, ["llm call failed"]
