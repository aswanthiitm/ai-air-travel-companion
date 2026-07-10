"""Typed loaders for the three hackathon datasets.

Loaders parse columns to natural dtypes and nothing more — all enrichment
and index building lives in preprocessing.py, so raw data stays inspectable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config


def load_flights(path: Path | None = None) -> pd.DataFrame:
    """50,000 flight offers, one row per (itinerary, cabin) with UTC times."""
    return pd.read_csv(
        path or config.FLIGHTS_CSV,
        parse_dates=["departure_utc", "arrival_utc"],
        dtype={"stops": "int64", "layover_minutes": "int64"},
    )


def load_users(path: Path | None = None) -> pd.DataFrame:
    """50 traveler records with structured fields plus free-text raw_history."""
    return pd.read_csv(path or config.USERS_CSV)


def load_benchmarks(path: Path | None = None) -> list[dict]:
    """The 6 benchmark prompts (prompt_id, user_id, request, expected_behavior)."""
    with open(path or config.BENCHMARKS_JSON) as f:
        return json.load(f)
