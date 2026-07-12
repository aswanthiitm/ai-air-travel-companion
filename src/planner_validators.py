"""Deterministic validation/normalization of planning inputs.

The v1 request_parser's machinery in its v4 role: the checker of the
model's (and the form's) homework. Form fields never pass through an LLM —
they are validated here and enter the workflow as settled facts
(confidence 1.0). Anything unresolvable becomes an explicit error or
ambiguity, never a silent guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .airports import resolve_city
from .input_normalizer import normalize_budget, normalize_date, normalize_party
from .recommendation_engine import CABIN_ORDER
from .request_parser import DatePhrase, _find_date_phrase

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
_ISO_RANGE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s*(?:\.\.|to|-)\s*(\d{4}-\d{2}-\d{2})$", re.I)


@dataclass(frozen=True)
class Slot:
    value: object
    source: str        # form | text | twin_default | agent_inferred
    confidence: float
    evidence: str

    def as_dict(self) -> dict:
        v = self.value
        if isinstance(v, tuple) and v and isinstance(v[0], date):
            v = [d.isoformat() for d in v]
        elif isinstance(v, DatePhrase):
            v = v.kind.value
        return {"value": v, "source": self.source,
                "confidence": self.confidence, "evidence": self.evidence}


def _form_slot(value, field: str) -> Slot:
    return Slot(value, "form", 1.0, f"form field '{field}'")


def parse_dates_field(text: str) -> tuple[tuple[date, date] | None, DatePhrase | None, str | None]:
    """'2025-06' | '2025-06-10' | '2025-06-10 to 2025-06-20' | 'next month'…"""
    text = text.strip()
    m = _ISO_RANGE.match(text)
    if m:
        a, b = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        return ((a, b), None, None) if a <= b else (None, None, "end date before start date")
    m = _ISO_DATE.match(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        if not 1 <= mo <= 12:
            return None, None, f"invalid month in {text!r}"
        if d:  # exact day -> a small window around it
            day = date(y, mo, int(d))
            return ((day - timedelta(days=1), day + timedelta(days=2)), None, None)
        last = (date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)) - timedelta(days=1)
        return ((date(y, mo, 1), last), None, None)
    phrase = _find_date_phrase(text.lower())
    if phrase:
        return None, phrase, None
    # conversational dates ("mid August", "first week of July", "around
    # Christmas") -> a concrete window; anything not confidently understood
    # falls through to the existing error (never invented).
    normalized = normalize_date(text)
    if normalized.window:
        return normalized.window, None, None
    return None, None, f"could not understand dates {text!r}"


def validate_fields(fields: dict | None) -> tuple[dict[str, Slot], list[str]]:
    """Structured form fields -> verified Slots + human-readable errors."""
    slots: dict[str, Slot] = {}
    errors: list[str] = []
    fields = fields or {}

    for key in ("origin", "destination"):
        raw = (fields.get(key) or "").strip()
        if not raw:
            continue
        names = [n for n in re.split(r"[,+&]| and ", raw) if n.strip()]
        codes = [resolve_city(n) for n in names]
        if None in codes:
            bad = names[codes.index(None)].strip()
            errors.append(f"unknown {key} {bad!r} — not an airport this dataset serves")
        else:
            value = codes[0] if key == "origin" else codes
            slots[key] = _form_slot(value, key)

    raw_dates = (fields.get("dates") or "").strip()
    if raw_dates:
        window, phrase, err = parse_dates_field(raw_dates)
        if err:
            errors.append(err)
        else:
            slots["dates"] = _form_slot(window or phrase, "dates")

    raw_pax = fields.get("travellers")
    if raw_pax not in (None, ""):
        # conversational counts ("family of four", "just me") -> integer;
        # plain numbers keep the exact existing validation (incl. range error).
        nlp = normalize_party(raw_pax) if (isinstance(raw_pax, str)
              and not raw_pax.strip().lstrip("-").isdigit()) else None
        if nlp is not None:
            slots["travellers"] = _form_slot(nlp, "travellers")
        else:
            try:
                n = int(raw_pax)
                if 1 <= n <= 9:
                    slots["travellers"] = _form_slot(n, "travellers")
                else:
                    errors.append("travellers must be between 1 and 9 (GDS seat cap)")
            except (TypeError, ValueError):
                errors.append(f"travellers must be a number, got {raw_pax!r}")

    raw_cabin = (fields.get("cabin") or "").strip().lower()
    if raw_cabin:
        match = next((c for c in CABIN_ORDER if c.lower() == raw_cabin), None)
        if match:
            slots["cabin"] = _form_slot(match, "cabin")
        else:
            errors.append(f"unknown cabin {fields['cabin']!r} "
                          f"(one of: {', '.join(CABIN_ORDER)})")

    raw_budget = fields.get("budget")
    if raw_budget not in (None, ""):
        def _plain_number(v) -> bool:
            try:
                float(v)
                return True
            except (TypeError, ValueError):
                return False
        # conversational budgets ("under $500", "around 1000") -> number;
        # plain numbers keep the exact existing validation (incl. positivity).
        nlp = normalize_budget(raw_budget) if (isinstance(raw_budget, str)
              and not _plain_number(raw_budget)) else None
        if nlp is not None:
            slots["budget"] = _form_slot(round(nlp, 2), "budget")
        else:
            try:
                b = float(raw_budget)
                if b > 0:
                    slots["budget"] = _form_slot(round(b, 2), "budget")
                else:
                    errors.append("budget must be positive")
            except (TypeError, ValueError):
                errors.append(f"budget must be a number, got {raw_budget!r}")

    return slots, errors
