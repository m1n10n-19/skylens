"""
Evidence records: every measurement traceable to a layer, source and
timestamps; tag readings marked as observed, not measured; unknown
never presented as zero.
"""

import json
import os
import time

import pytest

import evidence
import overpass

from analysis_spec import AnalysisSpec, AreaRequirement
from buildings import get_buildings, get_buildings_with_provenance
from criteria import EVALUATORS
from geodata import collect_candidates_and_context, new_context
from scoring import score_weighted_criteria
from use_cases import EV_CHARGING, SOLAR_PROSPECTING, USE_CASES

import search_area

from tests.conftest import (
    LAT,
    LON,
    OSM_BASE,
    RETRIEVED_AT,
    FakeGeolocator,
    building_elements,
    land_elements,
    planner_reply,
)


STATUSES = {"measured", "observed", "inferred", "not_measured", "verification_required"}


@pytest.fixture
def land(overpass):

    overpass(land_elements())

    area = search_area.resolve(FakeGeolocator(), "Adyar")

    candidates, context = collect_candidates_and_context(
        "land_parcels", area, 150, None,
        ["roads", "points_of_interest", "ev_chargers", "parking"], "Adyar",
    )

    return {c["osm_id"]: c for c in candidates}, context


# ============================================================
# CATALOGUE
# ============================================================

def test_every_evaluator_measurement_is_catalogued(land):
    """
    Each key an evaluator returns needs a unit, claim and status.
    """

    by_id, context = land

    specs = [
        AnalysisSpec(query="q", intent_type="x"),
        AnalysisSpec(query="q", intent_type="x", requirements={"business_type": "supermarket"},
                     area=AreaRequirement(target_m2=1000)),
        AnalysisSpec(query="q", intent_type="x", area=AreaRequirement(min_m2=1000)),
    ]

    scene = {"date": "2026-01-10", "clear_fraction": 0.9}

    candidates = list(by_id.values()) + [
        {"latitude": LAT, "longitude": LON, "area_m2": 2500, "building_type": "commercial"},
        {"latitude": LAT, "longitude": LON, "area_m2": 1200, "change": {
            "index": "NDVI", "before_mean": 0.7, "after_mean": 0.3, "delta_mean": -0.4,
            "min_change": 0.25, "full_scale": 0.8, "pixels": 12, "before": scene, "after": scene,
            "season_gap_days": 10,
        }},
    ]

    seen = set()

    for name, evaluator in EVALUATORS.items():

        produced = False

        for spec in specs:
            for candidate in candidates:

                result = evaluator(candidate, context, spec)

                if result is None:
                    continue

                assert result.get("measurements"), f"{name} returned no measurements"

                produced = True

                for key in result["measurements"]:
                    assert key in evidence.MEASUREMENTS, f"{name}: {key}"
                    seen.add(key)

        assert produced, name

    assert seen == set(evidence.MEASUREMENTS)


def test_catalogue_statuses_and_units_are_valid():

    for key, (claim, unit, status, method) in evidence.MEASUREMENTS.items():

        assert claim and method, key
        assert status in STATUSES, key

        if key.endswith("_m"):
            assert unit == "m", key
        if key.endswith("_m2"):
            assert unit == "m2", key
        if status == "observed":
            assert unit is None, key


# ============================================================
# ITEMS ON SCORED CANDIDATES
# ============================================================

def _ev_scored(land, candidate_id):

    by_id, context = land

    return score_weighted_criteria(
        by_id[candidate_id],
        AnalysisSpec(query="q", intent_type="ev_charging_site_selection"),
        EV_CHARGING, context,
    )


def test_measured_criteria_carry_traceable_evidence(land):

    result = _ev_scored(land, 2001)

    for criterion_id, entry in result["criteria"].items():

        items = entry["evidence_items"]

        assert items, criterion_id

        for item in items:

            assert item["status"] in STATUSES
            assert item["layer"]

            if entry["available"]:
                assert item["source"] == (
                    "OpenStreetMap land-use tags (not cadastral parcels)"
                    if item["layer"] == "land_parcels"
                    else "OpenStreetMap (Overpass)"
                )
                assert item["source_id"] == "osm_overpass"
                assert item["data_as_of"] == OSM_BASE
                assert item["retrieved_at"] == RETRIEVED_AT
                # Map data has no observation date.
                assert item["observed_at"] is None


def test_road_distance_is_measured_with_unit(land):

    items = _ev_scored(land, 2001)["criteria"]["road_access"]["evidence_items"]

    distance = next(i for i in items if i["measurement"]["unit"] == "m")

    assert distance["status"] == "measured"
    assert distance["measurement"] == {"value": 10, "unit": "m"}
    assert distance["layer"] == "roads"

    name = next(i for i in items if i["claim"] == "Nearest mapped vehicle road")

    assert name["status"] == "observed"
    assert name["measurement"]["value"] == "LB Road"


def test_land_use_tag_is_observed_not_measured(land):

    items = _ev_scored(land, 2001)["criteria"]["vacancy"]["evidence_items"]

    assert [i["status"] for i in items] == ["observed"]
    assert items[0]["measurement"]["value"] == "vacant"
    assert "not verified" in items[0]["method"]


def test_unavailable_layer_gives_one_not_measured_item(land):

    entry = _ev_scored(land, 2001)["criteria"]["flood_risk"]

    assert entry["evidence_items"] == [{
        "claim": "Flood risk",
        "status": "not_measured",
        "measurement": None,
        "layer": "flood_risk",
        "source": None,
        "source_id": None,
        "observed_at": None,
        "data_as_of": None,
        "retrieved_at": None,
        "method": None,
        "note": entry["note"],
    }]


def test_absence_is_none_not_zero(land):
    """
    With no mapped chargers, the distance is None (nothing found,
    with a note) while the count is a real zero.
    """

    by_id, context = land

    context.chargers = []

    result = score_weighted_criteria(
        by_id[2001],
        AnalysisSpec(query="q", intent_type="ev_charging_site_selection"),
        EV_CHARGING, context,
    )

    items = result["criteria"]["competition"]["evidence_items"]

    nearest = next(i for i in items if i["claim"].startswith("Distance to the nearest mapped EV"))

    assert nearest["measurement"] == {"value": None, "unit": "m"}
    assert "does not prove absence" in nearest["note"]

    count = next(i for i in items if i["claim"] == "Mapped EV charging stations within 2 km")

    # A count of zero mapped chargers is a real measurement.
    assert count["measurement"] == {"value": 0, "unit": "count"}
    assert count["note"] is None


def test_evaluator_returning_none_is_not_measured(land):

    # No business type: competition cannot be measured.
    by_id, context = land

    result = score_weighted_criteria(
        by_id[2001],
        AnalysisSpec(query="q", intent_type="commercial_site_selection"),
        USE_CASES["commercial_site_selection"], context,
    )

    entry = result["criteria"]["competition"]

    assert entry["available"] is False
    assert [i["status"] for i in entry["evidence_items"]] == ["not_measured"]
    assert entry["evidence_items"][0]["note"] == "Could not be measured for this candidate."


def test_missing_provenance_stays_unknown():

    context = new_context(LAT, LON, 1)

    context.layer_status["building_footprints"] = "loaded"

    result = score_weighted_criteria(
        {"latitude": LAT, "longitude": LON, "area_m2": 6000, "building_type": "office"},
        AnalysisSpec(query="q", intent_type="solar_prospecting"),
        SOLAR_PROSPECTING, context,
    )

    item = result["criteria"]["footprint_size"]["evidence_items"][0]

    assert item["measurement"] == {"value": 6000, "unit": "m2"}
    assert item["data_as_of"] is None
    assert item["retrieved_at"] is None


def test_unknown_measurement_key_is_kept_as_observed():

    items = evidence.from_measurements({"mystery_value": 7}, "roads")

    assert items[0].status == "observed"
    assert items[0].measurement.unit is None
    assert items[0].claim == "Mystery value"


# ============================================================
# END TO END
# ============================================================

def test_analyze_solar_evidence_timestamps(client, deepseek, overpass):

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    body = client.post("/analyze", json={"query": "solar in Adyar"}).json()

    for prospect in body["top_prospects"]:

        size = prospect["criteria"]["footprint_size"]["evidence_items"]

        assert size[0]["measurement"]["value"] == prospect["area_m2"]
        assert size[0]["data_as_of"] == OSM_BASE
        assert size[0]["retrieved_at"] == RETRIEVED_AT

        kind = prospect["criteria"]["building_type"]["evidence_items"][0]

        assert kind["status"] == "observed"


def test_analyze_ev_every_criterion_has_evidence(client, deepseek, overpass):

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    for prospect in body["top_prospects"]:
        for criterion_id, entry in prospect["criteria"].items():
            statuses = {i["status"] for i in entry["evidence_items"]}
            if entry["available"]:
                assert "not_measured" not in statuses, criterion_id
            else:
                assert statuses == {"not_measured"}, criterion_id


# ============================================================
# OVERPASS PROVENANCE
# ============================================================

def test_provenance_reads_overpass_fields():

    assert overpass.provenance({
        "osm3s": {"timestamp_osm_base": "2026-01-01T00:00:00Z"},
        "_retrieved_at": "2026-01-02T00:00:00+00:00",
    }) == {
        "data_as_of": "2026-01-01T00:00:00Z",
        "retrieved_at": "2026-01-02T00:00:00+00:00",
    }

    assert overpass.provenance({}) == {"data_as_of": None, "retrieved_at": None}
    assert overpass.provenance(None) == {"data_as_of": None, "retrieved_at": None}


class _Response:

    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def real_overpass(monkeypatch, tmp_path):
    """
    The real query_overpass with a temporary cache and a fake HTTP
    session.
    """

    monkeypatch.setattr(overpass, "CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OVERPASS_CACHE_HOURS", "24")

    calls = []

    class Session:
        def post(self, url, **kwargs):
            calls.append(url)
            return _Response({"osm3s": {"timestamp_osm_base": OSM_BASE}, "elements": []})

    monkeypatch.setattr(overpass, "_session", Session())

    return calls, tmp_path


def test_fresh_fetch_is_stamped_and_cache_hit_keeps_original_time(real_overpass, monkeypatch):

    calls, _ = real_overpass

    first = overpass.query_overpass("[out:json]; way(1);")

    assert len(calls) == 1
    assert first["_retrieved_at"]

    stamped = first["_retrieved_at"]

    # Later, a cache hit must report the original fetch time.
    real_time = time.time
    monkeypatch.setattr(overpass.time, "time", lambda: real_time() + 3600)

    second = overpass.query_overpass("[out:json]; way(1);")

    assert len(calls) == 1
    assert second["_retrieved_at"] == stamped
    assert overpass.provenance(second)["data_as_of"] == OSM_BASE


def test_old_cache_files_use_file_time(real_overpass):

    _, cache_dir = real_overpass

    query = "[out:json]; way(2);"

    path = os.path.join(str(cache_dir), overpass._cache_key(query) + ".json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"elements": []}, f)

    written = time.time() - 600

    os.utime(path, (written, written))

    data = overpass.query_overpass(query)

    assert data["_retrieved_at"] == overpass._iso(written)


def test_get_buildings_return_value_unchanged(overpass):

    overpass(building_elements())

    plain = get_buildings(LAT, LON)

    with_provenance, provenance = get_buildings_with_provenance(LAT, LON)

    assert isinstance(plain, list)
    assert plain == with_provenance
    assert provenance == {"data_as_of": OSM_BASE, "retrieved_at": RETRIEVED_AT}
