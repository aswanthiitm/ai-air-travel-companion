"""Central configuration: paths and global assumptions.

Every hard assumption the system makes lives here so it is visible,
documented, and changeable in one place.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

FLIGHTS_CSV = DATA_DIR / "flights_data.csv"
USERS_CSV = DATA_DIR / "user_data.csv"
BENCHMARKS_JSON = DATA_DIR / "benchmark_prompts.json"

# ---------------------------------------------------------------------------
# Simulated "today".
#
# The flight dataset covers 2025-01-01 .. 2026-07-01. Benchmark prompts use
# relative dates ("next month", "over the summer", "around the holidays"),
# which must resolve *inside* that window to return results. We therefore fix
# a reference date that leaves every forward-looking phrase fully covered:
#   next month        -> Jun 2025
#   over the summer   -> Jun-Aug 2025
#   around the holidays -> Dec 2025
# Documented in README.md under Assumptions. Only relative-date resolution
# reads this constant; absolute dates in requests are used as-is.
# ---------------------------------------------------------------------------
SIMULATED_NOW = date(2025, 5, 15)
