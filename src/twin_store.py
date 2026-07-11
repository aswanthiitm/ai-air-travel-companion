"""Living Traveler Twin: event-sourced learning over the frozen baseline.

Baseline Profile ⊕ Interaction History → Current Traveler Twin.

The historical dataset builds an immutable baseline (existing extractor,
untouched). Every interaction appends an InteractionEvent to an append-only
SQLite log; a deterministic signal deriver folds events into a per-user
overlay (per-dimension belief with asymmetric confidence updates); and
`traveler_profile.apply_overlay` merges baseline ⊕ overlay into the SAME
TravelerProfile dataclass the recommendation engine has always consumed —
the engine cannot tell the profile now breathes.

Everything here is deterministic and replayable. Free-text feedback is
interpreted by the existing lexicon (`extract_history`), so even the
"LLM-ish" part of learning runs offline; an LLM can widen coverage later
behind the same signal shape.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config
from .preference_extractor import PreferenceSignal, Source, extract_history
from .traveler_profile import TravelerProfile, apply_overlay, build_profile

DEFAULT_DB = config.PROJECT_ROOT / "data" / "twin.db"

# Asymmetric by design: one contradiction dents a habit faster than one
# confirmation builds it — over-trusting a stale preference costs a bad
# recommendation; under-trusting merely asks once more.
# "stated" = 0.5 on purpose: an explicit statement crosses the act threshold
# immediately ("I told you I hate long layovers" must bind the NEXT search);
# behavioral evidence (rejections, clicks) accumulates more slowly.
ALPHA = {"stated": 0.5, "rejected": 0.18, "booked": 0.15, "clicked": 0.08, "ignored": 0.03}
BETA = 0.30

EVENT_TYPES = {
    "recommendation_shown", "recommendation_accepted", "recommendation_rejected",
    "recommendation_ignored", "alternative_chosen", "weights_steered",
    "preference_stated", "booking_completed",
}

# Trip-scoped dimensions never persist into the Twin (an occasion belongs to
# a trip, not to who the traveler is).
TRIP_SCOPED = {"occasion", "unclassified", "party"}


@dataclass
class TraitChange:
    dimension: str
    description: str
    event_id: str

    def as_dict(self) -> dict:
        return {"dimension": self.dimension, "description": self.description,
                "event_id": self.event_id}


@dataclass
class Observation:
    """One belief-relevant reading derived from an event."""
    dimension: str
    value: object
    alpha: float
    evidence: str


@dataclass
class Overlay:
    """Per-user learned state. JSON-serializable; folded from events."""
    dims: dict = field(default_factory=dict)       # dim -> {value,c,o,opp,receipts}
    weight_bias: dict = field(default_factory=dict)
    vot_obs: list = field(default_factory=list)
    airlines_seen: dict = field(default_factory=dict)
    flips: list = field(default_factory=list)      # {dim, old, new, evidence}


# ---------------------------------------------------------------------------
# Event -> observations (deterministic rules; lexicon for free text)
# ---------------------------------------------------------------------------

def _text_observations(text: str, alpha: float) -> list[Observation]:
    return [
        Observation(s.dimension, s.value, alpha, text if len(text) < 90 else s.evidence)
        for s in extract_history([text])
        if s.dimension not in ("unclassified",)
    ]


def derive_observations(event_type: str, payload: dict) -> tuple[list[Observation], dict]:
    """Map one event to belief observations + side effects (bias/vot/airlines)."""
    obs: list[Observation] = []
    side: dict = {}
    attrs = payload.get("itinerary", {})

    if event_type == "preference_stated":
        obs += _text_observations(payload.get("message", ""), ALPHA["stated"])

    elif event_type in ("recommendation_accepted", "booking_completed"):
        a = ALPHA["booked"] if event_type == "booking_completed" else ALPHA["clicked"]
        if attrs.get("is_redeye"):
            obs.append(Observation("redeye", "accept", a, "accepted a redeye itinerary"))
        for al in attrs.get("airlines", []):
            side.setdefault("airlines", []).append(al)
        if payload.get("worth_it_trade"):  # {"extra_hours": h, "savings": s}
            t = payload["worth_it_trade"]
            if t.get("extra_hours"):
                side["vot"] = round(abs(t["savings"]) / abs(t["extra_hours"]), 2)

    elif event_type in ("recommendation_rejected", "recommendation_ignored"):
        a = ALPHA["rejected"] if event_type == "recommendation_rejected" else ALPHA["ignored"]
        if attrs.get("is_redeye"):
            obs.append(Observation("redeye", "avoid", a, "turned down a redeye itinerary"))
        if attrs.get("max_stops", 0) > 0:
            obs.append(Observation("stops", "avoid", a * 0.6,
                                   f"turned down a {attrs['max_stops']}-stop itinerary"))
        reason = payload.get("reason", "")
        if reason:
            obs += _text_observations(reason, ALPHA["stated"])

    elif event_type == "alternative_chosen":
        label = payload.get("label")
        if label == "cheapest":
            obs.append(Observation("budget", "minimize", ALPHA["clicked"],
                                   "picked the cheapest alternative"))
        elif label == "most_convenient":
            obs.append(Observation("stops", "avoid", ALPHA["clicked"],
                                   "picked the most convenient alternative"))
        if payload.get("worth_it_trade", {}).get("extra_hours"):
            t = payload["worth_it_trade"]
            side["vot"] = round(abs(t["savings"]) / abs(t["extra_hours"]), 2)

    elif event_type == "weights_steered":
        side["bias"] = {k: float(v) * 0.5 for k, v in payload.get("deltas", {}).items()}

    return [o for o in obs if o.dimension not in TRIP_SCOPED], side


# ---------------------------------------------------------------------------
# Belief updates (the confidence math from the architecture doc)
# ---------------------------------------------------------------------------

def _update_dim(overlay: Overlay, ob: Observation) -> TraitChange | None:
    st = overlay.dims.get(ob.dimension)
    if st is None:
        overlay.dims[ob.dimension] = {
            "value": ob.value, "c": round(ob.alpha, 3), "o": 0.0,
            "opp": None, "receipts": [ob.evidence],
        }
        return TraitChange(ob.dimension, f"learned {ob.dimension} → {ob.value} "
                                         f"({ob.evidence})", "")
    st["receipts"] = (st["receipts"] + [ob.evidence])[-5:]
    if st["value"] == ob.value:
        before = st["c"]
        st["c"] = round(st["c"] + ob.alpha * (1 - st["c"]), 3)
        if before < 0.5 <= st["c"]:
            return TraitChange(ob.dimension,
                               f"{ob.dimension} → {ob.value} is now established "
                               f"(confidence {st['c']:.2f})", "")
        return None
    # contradiction
    st["c"] = round(st["c"] * (1 - BETA), 3)
    st["o"] = round(st["o"] + ob.alpha * (1 - st["o"]), 3)
    st["opp"] = ob.value
    if st["o"] > st["c"]:
        old = st["value"]
        overlay.flips.append({"dim": ob.dimension, "old": old, "new": ob.value,
                              "evidence": ob.evidence})
        st.update(value=ob.value, c=round(st["o"] * 0.5, 3), o=0.0, opp=None)
        return TraitChange(ob.dimension,
                           f"{ob.dimension} flipped {old} → {ob.value} ({ob.evidence})", "")
    return TraitChange(ob.dimension,
                       f"{ob.dimension} → {st['value']} weakening "
                       f"(evidence against: {ob.evidence})", "")


def fold_event(overlay: Overlay, event_type: str, payload: dict) -> list[TraitChange]:
    obs, side = derive_observations(event_type, payload)
    changes = [c for c in (_update_dim(overlay, ob) for ob in obs) if c]

    for al in side.get("airlines", []):
        n = overlay.airlines_seen[al] = overlay.airlines_seen.get(al, 0) + 1
        if n == 2:
            changes.append(TraitChange("airline_affinity",
                                       f"repeated choice of {al} ({n} bookings) — treating "
                                       f"it as a preferred airline", ""))
    if "vot" in side:
        overlay.vot_obs.append(side["vot"])
        changes.append(TraitChange("value_of_time",
                                   f"new revealed value-of-time observation "
                                   f"(${side['vot']}/hr)", ""))
    for k, v in side.get("bias", {}).items():
        overlay.weight_bias[k] = round(overlay.weight_bias.get(k, 0.0) + v, 3)
        changes.append(TraitChange("weights", f"{k} emphasis nudged by {v:+.2f} "
                                              "(slider steer remembered at half strength)", ""))
    return changes


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conversation_id TEXT,
  ts TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, ts);
CREATE TABLE IF NOT EXISTS twin_snapshots (
  user_id TEXT NOT NULL, version INTEGER NOT NULL, ts TEXT NOT NULL,
  overlay TEXT NOT NULL, event_watermark TEXT,
  PRIMARY KEY (user_id, version));
CREATE TABLE IF NOT EXISTS trait_changes (
  user_id TEXT NOT NULL, version INTEGER NOT NULL, ts TEXT NOT NULL,
  dimension TEXT NOT NULL, description TEXT NOT NULL, event_id TEXT);
"""


class TwinStore:
    """Event log + snapshot cache + baseline merge. One instance per process."""

    def __init__(self, users: pd.DataFrame, db_path: Path | str = DEFAULT_DB):
        self._users = users.set_index("user_id", drop=False)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._baseline: dict[str, TravelerProfile] = {}
        self._overlay_cache: dict[str, Overlay] = {}

    # -- baseline ------------------------------------------------------------

    def baseline(self, user_id: str) -> TravelerProfile:
        if user_id not in self._baseline:
            self._baseline[user_id] = build_profile(self._users.loc[user_id])
        return self._baseline[user_id]

    # -- events --------------------------------------------------------------

    def record(self, user_id: str, event_type: str, payload: dict,
               conversation_id: str | None = None) -> list[TraitChange]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        event_id = uuid.uuid4().hex
        ts = datetime.now(timezone.utc).isoformat()
        overlay = self._overlay(user_id)
        changes = fold_event(overlay, event_type, payload)
        for ch in changes:
            ch.event_id = event_id

        version = self._latest_version(user_id) + 1
        with self._db:
            self._db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (event_id, user_id, conversation_id, ts, event_type, json.dumps(payload)))
            self._db.execute(
                "INSERT INTO twin_snapshots VALUES (?,?,?,?,?)",
                (user_id, version, ts, json.dumps(overlay.__dict__), event_id))
            self._db.executemany(
                "INSERT INTO trait_changes VALUES (?,?,?,?,?,?)",
                [(user_id, version, ts, c.dimension, c.description, event_id)
                 for c in changes])
        return changes

    # -- reads ---------------------------------------------------------------

    def effective_profile(self, user_id: str) -> TravelerProfile:
        return apply_overlay(self.baseline(user_id), self._overlay(user_id))

    def changelog(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self._db.execute(
            "SELECT ts, dimension, description FROM trait_changes "
            "WHERE user_id=? ORDER BY version DESC LIMIT ?", (user_id, limit)).fetchall()
        return [{"ts": r[0], "dimension": r[1], "description": r[2]} for r in rows]

    def event_count(self, user_id: str) -> int:
        return self._db.execute("SELECT COUNT(*) FROM events WHERE user_id=?",
                                (user_id,)).fetchone()[0]

    def reset(self, user_id: str) -> None:
        with self._db:
            for table in ("events", "twin_snapshots", "trait_changes"):
                self._db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        self._overlay_cache.pop(user_id, None)

    # -- internals -----------------------------------------------------------

    def _latest_version(self, user_id: str) -> int:
        row = self._db.execute(
            "SELECT MAX(version) FROM twin_snapshots WHERE user_id=?",
            (user_id,)).fetchone()
        return row[0] or 0

    def _overlay(self, user_id: str) -> Overlay:
        if user_id not in self._overlay_cache:
            row = self._db.execute(
                "SELECT overlay FROM twin_snapshots WHERE user_id=? "
                "ORDER BY version DESC LIMIT 1", (user_id,)).fetchone()
            self._overlay_cache[user_id] = Overlay(**json.loads(row[0])) if row else Overlay()
        return self._overlay_cache[user_id]

    def replay(self, user_id: str) -> Overlay:
        """Rebuild the overlay from the raw event log (audit / cache check)."""
        overlay = Overlay()
        for etype, payload in self._db.execute(
                "SELECT type, payload FROM events WHERE user_id=? ORDER BY ts",
                (user_id,)):
            fold_event(overlay, etype, json.loads(payload))
        return overlay
