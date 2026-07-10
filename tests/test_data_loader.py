"""Loader contract: shapes, dtypes, and cross-file references."""
import pandas as pd
import pytest

from src import data_loader


@pytest.fixture(scope="module")
def flights():
    return data_loader.load_flights()


@pytest.fixture(scope="module")
def users():
    return data_loader.load_users()


@pytest.fixture(scope="module")
def benchmarks():
    return data_loader.load_benchmarks()


def test_flights_shape_and_dtypes(flights):
    assert flights.shape == (50000, 26)
    assert pd.api.types.is_datetime64_any_dtype(flights["departure_utc"])
    assert pd.api.types.is_datetime64_any_dtype(flights["arrival_utc"])
    assert flights["departure_utc"].dt.tz is not None, "timestamps must stay tz-aware"


def test_users_shape(users):
    assert users.shape == (50, 17)
    assert users["user_id"].str.match(r"U\d\d").all()


def test_benchmarks_reference_real_users(benchmarks, users):
    assert len(benchmarks) == 6
    assert {b["user_id"] for b in benchmarks} <= set(users["user_id"])
    assert all(b["request"] for b in benchmarks)
