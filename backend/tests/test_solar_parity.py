"""
Solar results must stay identical to the original scorer
(commit cae97c9, frozen in tests/legacy/).

- GET /solar/prospects uses scoring.score_solar_candidate and must
  match the legacy output exactly: score, confidence and reasons.
- /analyze scores solar through the generic weighted-criteria
  engine; its score must equal the legacy score for every building.
"""

import itertools

import pytest

from analysis_spec import AnalysisSpec
from geodata import new_context
from scoring import score_candidate, score_solar_candidate
from use_cases import SOLAR_PROSPECTING

from tests.conftest import LAT, LON, building_elements, planner_reply
from tests.legacy.solar_scorer_cae97c9 import (
    score_solar_candidate as legacy_score,
)


# Every tier boundary, either side of it.
AREAS = [
    0, 1, 499.9, 500, 500.1, 999.9, 1000, 1999.9, 2000,
    4999.9, 5000, 5000.1, 9999.9, 10000, 10000.1, 50000, 1e6,
]

BUILDING_TYPES = [
    # high value
    "commercial", "industrial", "warehouse", "retail", "office",
    # institutional
    "school", "university", "hospital", "college", "institutional",
    # residential
    "apartments", "residential", "house", "detached",
    # other / odd inputs
    "yes", "unknown", "garage", "", None,
    "Commercial", "INDUSTRIAL", "Office",
]


def _building(area, building_type):

    candidate = {
        "osm_id": 1,
        "osm_type": "way",
        "latitude": LAT,
        "longitude": LON,
        "area_m2": area,
        "name": None,
    }

    if building_type is not None:
        candidate["building_type"] = building_type

    return candidate


GRID = list(itertools.product(AREAS, BUILDING_TYPES))


@pytest.mark.parametrize("area,building_type", GRID)
def test_legacy_endpoint_scorer_matches_cae97c9(area, building_type):

    candidate = _building(area, building_type)

    assert score_solar_candidate(dict(candidate)) == legacy_score(dict(candidate))


def _analyze_context(*extra_layers):

    context = new_context(LAT, LON, 1)

    context.layer_status["building_footprints"] = "loaded"

    for layer in extra_layers:
        context.layer_status[layer] = "loaded"

    return context


def _solar_spec():

    return AnalysisSpec(
        query="Find large roofs in Adyar for solar",
        intent_type="solar_prospecting",
        use_case="solar_prospecting",
    )


@pytest.mark.parametrize("area,building_type", GRID)
def test_analyze_solar_score_matches_cae97c9(area, building_type):

    candidate = _building(area, building_type)

    legacy = legacy_score(dict(candidate))

    scored = score_candidate(
        dict(candidate), _solar_spec(), SOLAR_PROSPECTING, _analyze_context()
    )

    assert scored["score"] == legacy["solar_score"]
    assert scored["solar_score"] == legacy["solar_score"]

    # Same reasons; /analyze lists them per criterion, so the
    # large-site bonus reason comes before the building-type reason.
    assert sorted(scored["reasons"]) == sorted(legacy["reasons"])


@pytest.mark.parametrize("area,building_type", GRID[::7])
def test_analyze_solar_score_unchanged_when_more_layers_load(area, building_type):
    """
    Guards against drift: if roads (or any other layer) are loaded for
    a solar analysis, the solar score must still equal the legacy one.
    """

    candidate = _building(area, building_type)

    scored = score_candidate(
        dict(candidate), _solar_spec(), SOLAR_PROSPECTING,
        _analyze_context("roads", "points_of_interest", "satellite_imagery"),
    )

    assert scored["score"] == legacy_score(dict(candidate))["solar_score"]


def test_solar_prospects_endpoint_matches_legacy_ranking(client, overpass):

    overpass(building_elements())

    response = client.get("/solar/prospects", params={
        "latitude": LAT, "longitude": LON,
    })

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "success"
    assert body["intent"] == "solar_prospecting"

    prospects = body["prospects"]

    # 100 m² house is below the 500 m² default.
    assert body["total_candidates"] == 5
    assert all(p["area_m2"] >= 500 for p in prospects)

    for prospect in prospects:

        building = {
            key: prospect[key]
            for key in ("osm_id", "osm_type", "latitude", "longitude",
                        "area_m2", "building_type", "name")
        }

        assert prospect == legacy_score(building)

    keys = [(p["solar_score"], p["area_m2"]) for p in prospects]

    assert keys == sorted(keys, reverse=True)


def test_analyze_solar_end_to_end_scores_match_legacy(client, deepseek, overpass):

    deepseek(planner_reply("solar_prospecting", industry="solar"))

    overpass(building_elements())

    body = client.post("/analyze", json={"query": "Big roofs in Adyar for solar"}).json()

    assert body["status"] == "success"

    for prospect in body["top_prospects"]:

        legacy = legacy_score({
            "area_m2": prospect["area_m2"],
            "building_type": prospect["building_type"],
        })

        assert prospect["solar_score"] == legacy["solar_score"]
        assert prospect["score"] == legacy["solar_score"]
