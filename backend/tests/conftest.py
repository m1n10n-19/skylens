"""
Shared test fixtures.

No test touches the network: DeepSeek, Nominatim, Overpass and the
Planetary Computer are replaced with fakes that return fixed data.
"""

import math
import os

# main.py refuses to import without a key; set a dummy one first.
# load_dotenv() does not override variables that are already set.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

import pytest

from fastapi.testclient import TestClient

import buildings
import geodata
import main
import pipeline
import ratelimit


# ============================================================
# COORDINATES
# ============================================================

# Search centre used by every fake (Adyar, Chennai).
LAT = 13.0067

LON = 80.2575

# Degrees per metre near LAT, matching geodata.LocalProjection.
DEG_LAT_PER_M = 1 / 110574.0

DEG_LON_PER_M = 1 / (111320.0 * math.cos(math.radians(LAT)))


def offset(dx_m, dy_m):
    """
    (lat, lon) dx_m east and dy_m north of the search centre.
    """

    return LAT + dy_m * DEG_LAT_PER_M, LON + dx_m * DEG_LON_PER_M


def square(dx_m, dy_m, side_m):
    """
    Overpass "geometry" for a square whose south-west corner is
    dx_m east and dy_m north of the centre.
    """

    corners = [
        (dx_m, dy_m),
        (dx_m + side_m, dy_m),
        (dx_m + side_m, dy_m + side_m),
        (dx_m, dy_m + side_m),
        (dx_m, dy_m),
    ]

    return [
        {"lat": lat, "lon": lon}
        for lat, lon in (offset(x, y) for x, y in corners)
    ]


def line(*points_m):

    return [
        {"lat": lat, "lon": lon}
        for lat, lon in (offset(x, y) for x, y in points_m)
    ]


# ============================================================
# FAKE PROVIDERS
# ============================================================

class FakeLocation:

    def __init__(self, raw, latitude=LAT, longitude=LON, address=None):

        self.raw = raw

        self.latitude = latitude

        self.longitude = longitude

        self.address = address or raw.get("display_name", "")


def small_place_raw(name="Adyar"):
    """
    Nominatim result for a point-like place: resolves to a 2 x 2 km box.
    """

    return {
        "class": "place",
        "type": "suburb",
        "osm_type": "node",
        "display_name": f"{name}, Chennai, Tamil Nadu, India",
        "namedetails": {"name": name},
        "boundingbox": [str(LAT - 0.005), str(LAT + 0.005),
                        str(LON - 0.005), str(LON + 0.005)],
    }


def huge_place_raw():
    """
    Nominatim result for a whole state: larger than MAX_PLACE_KM2.
    """

    return {
        "class": "boundary",
        "type": "administrative",
        "osm_type": "relation",
        "display_name": "Tamil Nadu, India",
        "namedetails": {"name": "Tamil Nadu"},
        "boundingbox": ["8.0", "13.5", "76.2", "80.4"],
    }


class FakeGeolocator:

    def __init__(self, raw=None):

        self.raw = raw if raw is not None else small_place_raw()

        self.calls = []

    def geocode(self, query, **kwargs):

        self.calls.append(query)

        if self.raw is False:
            return None

        return FakeLocation(self.raw)


FAKE_SCENE = {
    "id": "S2B_MSIL2A_20260920T050649_TEST",
    "date": "2026-09-20T05:06:49+00:00",
    "cloud_cover": 3.2,
    "bbox": [80.0, 12.8, 80.5, 13.2],
    "visual_url": "https://example.test/visual.tif",
}


def building_elements():
    """
    Overpass buildings: one per solar tier, all inside the 1 km box.
    """

    specs = [
        (1001, "industrial", 120),  # 14,400 m²
        (1002, "commercial", 80),   #  6,400 m²
        (1003, "apartments", 50),   #  2,500 m²
        (1004, "school", 35),       #  1,225 m²
        (1005, "yes", 25),          #    625 m²
        (1006, "house", 10),        #    100 m², below the 500 m² default
    ]

    return [
        {
            "type": "way",
            "id": osm_id,
            "tags": {"building": kind},
            "geometry": square(-400 + i * 150, -300, side),
        }
        for i, (osm_id, kind, side) in enumerate(specs)
    ]


def land_elements():
    """
    One Overpass response for land-parcel analyses: two vacant
    parcels, roads, POIs, a charger and a parking lot.
    """

    return [
        # 60 x 60 m vacant parcel right next to a primary road.
        {"type": "way", "id": 2001,
         "tags": {"landuse": "vacant"},
         "geometry": square(0, 10, 60)},
        # 45 x 45 m grass area further from roads.
        {"type": "way", "id": 2002,
         "tags": {"landuse": "grass"},
         "geometry": square(300, 300, 45)},
        # Primary road running east-west along y = 0.
        {"type": "way", "id": 3001,
         "tags": {"highway": "primary", "name": "LB Road"},
         "geometry": line((-800, 0), (800, 0))},
        {"type": "way", "id": 3002,
         "tags": {"highway": "residential"},
         "geometry": line((280, 280), (280, 600))},
        # POIs.
        {"type": "node", "id": 4001, "lat": offset(20, 100)[0],
         "lon": offset(20, 100)[1], "tags": {"amenity": "restaurant"}},
        {"type": "node", "id": 4002, "lat": offset(40, 120)[0],
         "lon": offset(40, 120)[1], "tags": {"shop": "supermarket"}},
        {"type": "node", "id": 4003, "lat": offset(60, 90)[0],
         "lon": offset(60, 90)[1], "tags": {"office": "company"}},
        # One existing charger 600 m away.
        {"type": "node", "id": 5001, "lat": offset(600, 30)[0],
         "lon": offset(600, 30)[1], "tags": {"amenity": "charging_station"}},
        {"type": "way", "id": 5002, "center": {
            "lat": offset(-50, 60)[0], "lon": offset(-50, 60)[1]},
         "tags": {"amenity": "parking"}},
    ]


class FakeOverpass:
    """
    Records queries; returns `elements` or raises `error`.
    """

    def __init__(self, elements=None, error=None):

        self.elements = elements or []

        self.error = error

        self.queries = []

    def __call__(self, query):

        self.queries.append(query)

        if self.error:
            raise self.error

        return {"elements": self.elements}


class FakeDeepSeek:
    """
    Stands in for main.call_deepseek_json; returns `reply`.
    """

    def __init__(self, reply):

        self.reply = reply

        self.prompts = []

    def __call__(self, system, prompt):

        self.prompts.append(prompt)

        return self.reply


def planner_reply(intent_type, location="Adyar, Chennai", **extra):

    reply = {
        "intent_type": intent_type,
        "location": location,
        "radius_km": None,
        "candidate_type": None,
        "industry": None,
        "requirements": {"area": {
            "min": None, "max": None, "target": None,
            "unit": None, "as_stated": None,
        }},
        "data_needed": [],
        "criteria": [],
        "constraints": [],
        "desired_output": None,
        "confidence": "high",
    }

    reply.update(extra)

    return reply


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """
    No login, no question limit, fresh rate-limit counters, and no
    real network providers, whatever the local .env says.
    """

    monkeypatch.delenv("SKYLENS_USERNAME", raising=False)
    monkeypatch.delenv("SKYLENS_PASSWORD", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_IP", "0")
    monkeypatch.setenv("RATE_LIMIT_DAILY_TOTAL", "0")

    ratelimit._by_ip.clear()
    ratelimit._total.clear()

    def no_network(*args, **kwargs):
        raise AssertionError("test tried to reach a real provider")

    monkeypatch.setattr(main, "call_deepseek_json", no_network)
    monkeypatch.setattr(main, "geolocator", FakeGeolocator())
    monkeypatch.setattr(buildings, "query_overpass", no_network)
    monkeypatch.setattr(geodata, "_query_overpass", no_network)
    monkeypatch.setattr(pipeline, "get_latest_satellite", lambda **kw: dict(FAKE_SCENE))
    monkeypatch.setattr(main, "get_latest_satellite", no_network)
    monkeypatch.setattr(main, "search_satellite", no_network)

    yield

    ratelimit._by_ip.clear()
    ratelimit._total.clear()


@pytest.fixture
def client():

    return TestClient(main.app)


@pytest.fixture
def deepseek(monkeypatch):
    """
    deepseek(reply) makes the planner return `reply`.
    """

    def install(reply):
        fake = FakeDeepSeek(reply)
        monkeypatch.setattr(main, "call_deepseek_json", fake)
        return fake

    return install


@pytest.fixture
def overpass(monkeypatch):
    """
    overpass(elements=..., error=...) installs a fake Overpass for
    both building and land/site queries.
    """

    def install(elements=None, error=None):
        fake = FakeOverpass(elements, error)
        monkeypatch.setattr(buildings, "query_overpass", fake)
        monkeypatch.setattr(geodata, "_query_overpass", fake)
        return fake

    return install


@pytest.fixture
def geocoder(monkeypatch):

    def install(raw=None):
        fake = FakeGeolocator(raw)
        monkeypatch.setattr(main, "geolocator", fake)
        return fake

    return install
