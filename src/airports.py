"""Static reference data for the 35 airports in the flight dataset.

The dataset stores departure/arrival times in UTC only. Preferences like
"morning departures" refer to *local* time, so each airport carries a fixed
UTC offset (standard time, DST ignored — documented in README Assumptions).
Coordinates enable the route map in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Airport:
    iata: str
    city: str
    country: str
    utc_offset_hours: float
    lat: float
    lon: float


AIRPORTS: dict[str, Airport] = {
    a.iata: a
    for a in [
        Airport("AKL", "Auckland", "New Zealand", 12.0, -37.008, 174.792),
        Airport("AMS", "Amsterdam", "Netherlands", 1.0, 52.310, 4.768),
        Airport("BCN", "Barcelona", "Spain", 1.0, 41.297, 2.078),
        Airport("BKK", "Bangkok", "Thailand", 7.0, 13.690, 100.750),
        Airport("BOM", "Mumbai", "India", 5.5, 19.089, 72.868),
        Airport("CDG", "Paris", "France", 1.0, 49.010, 2.548),
        Airport("CPT", "Cape Town", "South Africa", 2.0, -33.965, 18.602),
        Airport("DEL", "Delhi", "India", 5.5, 28.556, 77.100),
        Airport("DOH", "Doha", "Qatar", 3.0, 25.273, 51.608),
        Airport("DPS", "Bali", "Indonesia", 8.0, -8.748, 115.167),
        Airport("DXB", "Dubai", "UAE", 4.0, 25.253, 55.366),
        Airport("FCO", "Rome", "Italy", 1.0, 41.800, 12.239),
        Airport("FRA", "Frankfurt", "Germany", 1.0, 50.038, 8.562),
        Airport("GIG", "Rio de Janeiro", "Brazil", -3.0, -22.810, -43.251),
        Airport("GRU", "Sao Paulo", "Brazil", -3.0, -23.436, -46.473),
        Airport("HKG", "Hong Kong", "Hong Kong", 8.0, 22.308, 113.918),
        Airport("ICN", "Seoul", "South Korea", 9.0, 37.469, 126.451),
        Airport("IST", "Istanbul", "Turkey", 3.0, 41.276, 28.752),
        Airport("JFK", "New York", "USA", -5.0, 40.640, -73.779),
        Airport("KUL", "Kuala Lumpur", "Malaysia", 8.0, 2.746, 101.710),
        Airport("LAX", "Los Angeles", "USA", -8.0, 33.942, -118.408),
        Airport("LHR", "London", "UK", 0.0, 51.470, -0.454),
        Airport("LIS", "Lisbon", "Portugal", 0.0, 38.774, -9.134),
        Airport("MAA", "Chennai", "India", 5.5, 12.990, 80.169),
        Airport("MEL", "Melbourne", "Australia", 10.0, -37.673, 144.843),
        Airport("MEX", "Mexico City", "Mexico", -6.0, 19.436, -99.072),
        Airport("NRT", "Tokyo", "Japan", 9.0, 35.772, 140.393),
        Airport("ORD", "Chicago", "USA", -6.0, 41.979, -87.904),
        Airport("PEK", "Beijing", "China", 8.0, 40.080, 116.585),
        Airport("PVG", "Shanghai", "China", 8.0, 31.144, 121.808),
        Airport("SFO", "San Francisco", "USA", -8.0, 37.619, -122.375),
        Airport("SIN", "Singapore", "Singapore", 8.0, 1.359, 103.989),
        Airport("SVO", "Moscow", "Russia", 3.0, 55.973, 37.413),
        Airport("SYD", "Sydney", "Australia", 10.0, -33.946, 151.177),
        Airport("YYZ", "Toronto", "Canada", -5.0, 43.677, -79.625),
    ]
}

# City-name -> IATA, from the dataset's own city labels.
CITY_TO_IATA: dict[str, str] = {a.city.lower(): a.iata for a in AIRPORTS.values()}

# Common alternate names users type that differ from the dataset labels.
CITY_ALIASES: dict[str, str] = {
    "nyc": "JFK",
    "new york city": "JFK",
    "bombay": "BOM",
    "denpasar": "DPS",
    "narita": "NRT",
    "heathrow": "LHR",
    "sao paolo": "GRU",
    "rio": "GIG",
    "hongkong": "HKG",
    "kl": "KUL",
    "san fran": "SFO",
}


# Region pools for requests like "plan a multi-city Asia trip" that name a
# region instead of cities. Middle East kept separate from Asia deliberately.
REGIONS: dict[str, set[str]] = {
    "asia": {"BKK", "BOM", "DEL", "DPS", "HKG", "ICN", "KUL", "MAA", "NRT", "PEK", "PVG", "SIN"},
    "middle_east": {"DOH", "DXB"},
    "europe": {"AMS", "BCN", "CDG", "FCO", "FRA", "IST", "LHR", "LIS", "SVO"},
    "oceania": {"AKL", "MEL", "SYD"},
    "africa": {"CPT"},
    "americas": {"GIG", "GRU", "JFK", "LAX", "MEX", "ORD", "SFO", "YYZ"},
}

REGION_ALIASES: dict[str, str] = {
    "asia": "asia",
    "europe": "europe",
    "euro": "europe",
    "middle east": "middle_east",
    "oceania": "oceania",
    "africa": "africa",
    "americas": "americas",
    "america": "americas",
}


def resolve_city(name: str) -> str | None:
    """Resolve a city name or IATA code to an IATA code, else None."""
    key = name.strip().lower()
    if key.upper() in AIRPORTS:
        return key.upper()
    return CITY_TO_IATA.get(key) or CITY_ALIASES.get(key)
