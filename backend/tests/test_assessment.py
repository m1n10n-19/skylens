"""
Missing-data states, the per-candidate known / unknown / verify
assessment, and run completeness.
"""

import pytest

import pipeline
import search_area

from analysis_spec import AnalysisSpec
from assessment import METHOD_TYPES, VERIFY, assess
from geodata import collect_candidates_and_context, new_context
from scoring import score_candidate
from use_cases import EV_CHARGING, LAND_ACQUISITION, SOLAR_PROSPECTING, USE_CASES

from tests.conftest import (
    LAT,
    LON,
    FakeGeolocator,
    building_elements,
    land_elements,
    planner_reply,
)


@pytest.fixture
def land(overpass):

    overpass(land_elements())

    area = search_area.resolve(FakeGeolocator(), "Adyar")

    candidates, context = collect_candidates_and_context(
        "land_parcels", area, 150, None,
        ["roads", "points_of_interest", "ev_chargers", "parking"], "Adyar",
    )

    return {c["osm_id"]: c for c in candidates}, context


def _scored(candidate, use_case, context, **spec):

    return score_candidate(
        dict(candidate),
        AnalysisSpec(query="q", intent_type=use_case.id, **spec),
        use_case, context,
    )


# ============================================================
# CRITERION STATES
# ============================================================

def test_each_missing_reason_has_its_own_state(land):

    by_id, context = land

    # Commercial: competition needs a business type (none given).
    scored = _scored(by_id[2001], USE_CASES["commercial_site_selection"], context)

    states = {cid: c["state"] for cid, c in scored["criteria"].items()}

    assert states["road_access"] == "measured"
    assert states["competition"] == "not_measurable"
    # No population data provider.
    assert states["population"] == "data_unavailable"


def test_not_implemented_means_data_exists_but_no_evaluator():

    context = new_context(LAT, LON, 1)

    context.layer_status["building_footprints"] = "loaded"

    scored = _scored({"latitude": LAT, "longitude": LON, "area_m2": 900}, SOLAR_PROSPECTING, context)

    # Roads have a provider; solar accessibility has no evaluator yet.
    assert scored["criteria"]["accessibility"]["state"] == "not_implemented"
    assert scored["criteria"]["shading"]["state"] == "data_unavailable"


def test_unavailable_layer_state():

    context = new_context(LAT, LON, 1)

    context.layer_status["land_parcels"] = "loaded"

    scored = _scored({"latitude": LAT, "longitude": LON, "area_m2": 900, "landuse": "vacant"},
                     LAND_ACQUISITION, context)

    # No zoning provider; roads has a provider but wasn't loaded.
    assert scored["criteria"]["land_use_compatibility"]["state"] == "data_unavailable"
    assert scored["criteria"]["road_access"]["state"] == "data_not_loaded"


def test_disabled_provider_is_data_unavailable(monkeypatch):

    import dataclasses
    import data_registry

    monkeypatch.setitem(
        data_registry.SOURCES, "osm_overpass",
        dataclasses.replace(data_registry.OSM_OVERPASS, available=False),
    )

    scored = _scored({"latitude": LAT, "longitude": LON, "area_m2": 900},
                     EV_CHARGING, new_context(LAT, LON, 1))

    assert scored["criteria"]["road_access"]["state"] == "data_unavailable"


def test_state_and_available_agree(land):

    by_id, context = land

    for use_case in USE_CASES.values():

        scored = _scored(by_id[2001], use_case, context)

        for entry in scored["criteria"].values():
            assert entry["available"] == (entry["state"] == "measured")


# ============================================================
# ASSESSMENT
# ============================================================

def test_known_and_unknown_cover_everything_once(land):

    by_id, context = land

    for use_case in USE_CASES.values():

        result = assess(_scored(by_id[2001], use_case, context), use_case)

        known = [k["id"] for k in result["known"]]

        unknown = [u["id"] for u in result["unknown"]]

        expected = [c.id for c in use_case.criteria] + list(use_case.unassessed)

        assert sorted(known + unknown) == sorted(expected), use_case.id
        assert not set(known) & set(unknown)


def test_tag_based_knowledge_is_marked_observed(land):

    by_id, context = land

    result = assess(_scored(by_id[2001], EV_CHARGING, context), EV_CHARGING)

    basis = {k["id"]: k["basis"] for k in result["known"]}

    # Only a tag: observed.
    assert basis["vacancy"] == "observed"
    # Measured distance; the observed road name is context only.
    assert basis["road_access"] == "measured"
    assert basis["major_road_proximity"] == "measured"
    assert basis["commercial_activity"] == "measured"


def test_unknown_items_explain_why(land):

    by_id, context = land

    result = assess(_scored(by_id[2001], EV_CHARGING, context), EV_CHARGING)

    unknown = {u["id"]: u for u in result["unknown"]}

    # Flood exposure has a provider, but nothing was read in this test.
    assert unknown["flood_risk"]["state"] == "data_not_loaded"
    assert unknown["flood_risk"]["reason"]
    assert unknown["ownership"]["state"] == "not_assessed"
    assert unknown["grid_connection_capacity"]["label"] == "Grid connection capacity"


def test_ev_verify_list(land):

    by_id, context = land

    result = assess(_scored(by_id[2001], EV_CHARGING, context), EV_CHARGING)

    verify = result["verify"]

    ids = [v["id"] for v in verify]

    assert set(ids) == {
        "parcel_size_fit", "road_access", "vacancy", "flood_risk",
        "ownership", "grid_connection_capacity", "zoning",
    }

    # Cheapest method types first.
    order = [METHOD_TYPES.index(v["method_type"]) for v in verify]

    assert order == sorted(order)

    for item in verify:
        assert item["status"] == "verification_required"
        assert item["question"].endswith("?")
        assert item["why"]

    flood = next(v for v in verify if v["id"] == "flood_risk")

    # Not measured: the reason is the criterion's own note.
    assert flood["why"] == result_note(by_id, context, "flood_risk")


def result_note(by_id, context, criterion_id):

    return _scored(by_id[2001], EV_CHARGING, context)["criteria"][criterion_id]["note"]


def test_market_questions_are_unknown_not_verify(land):

    by_id, context = land

    use_case = USE_CASES["commercial_site_selection"]

    result = assess(_scored(by_id[2001], use_case, context), use_case)

    assert "rent_or_price" in [u["id"] for u in result["unknown"]]
    assert "rent_or_price" not in [v["id"] for v in result["verify"]]


def test_solar_verify_list():

    context = new_context(LAT, LON, 1)

    context.layer_status["building_footprints"] = "loaded"

    scored = _scored({"latitude": LAT, "longitude": LON, "area_m2": 6000, "building_type": "office"},
                     SOLAR_PROSPECTING, context)

    verify = assess(scored, SOLAR_PROSPECTING)["verify"]

    assert [v["method_type"] for v in verify] == [
        "records_check",                               # ownership
        "imagery_review",                              # usable roof area
        "field_visit", "field_visit",                  # building use, accessibility
        "site_survey", "site_survey", "site_survey",   # suitability, shading, roof
    ]

    assert {v["id"] for v in verify} == {
        "footprint_size", "building_type", "solar_suitability", "shading",
        "accessibility", "roof_condition", "ownership",
    }


def test_verify_table_is_valid_and_has_no_dead_entries():

    from assessment import EXTRA

    referenced = {extra_id for items in EXTRA.values() for extra_id, _ in items}

    for use_case in USE_CASES.values():
        referenced |= {c.id for c in use_case.criteria}
        referenced |= set(use_case.unassessed)

    for item_id, (label, question, method_type, why) in VERIFY.items():

        assert item_id in referenced, item_id
        assert label and why
        assert question.endswith("?")
        assert method_type in METHOD_TYPES


def test_assessment_is_deterministic(land):

    by_id, context = land

    scored = _scored(by_id[2001], EV_CHARGING, context)

    assert assess(scored, EV_CHARGING) == assess(scored, EV_CHARGING)


# ============================================================
# API
# ============================================================

def test_analyze_attaches_assessment_to_ranked_candidates(client, deepseek, overpass):

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    for prospect in body["top_prospects"]:

        assessment = prospect["assessment"]

        assert set(assessment) == {"known", "unknown", "verify"}

        measured = [cid for cid, c in prospect["criteria"].items() if c["state"] == "measured"]

        assert [k["id"] for k in assessment["known"]] == measured


def test_complete_run(client, deepseek, overpass, imagery, dem, flood_evidence):

    from tests.conftest import clear_year

    deepseek(planner_reply("land_acquisition"))
    overpass(land_elements())
    imagery(clear_year())
    dem()
    flood_evidence()

    body = client.post("/analyze", json={"query": "land in Adyar"}).json()

    assert body["status"] == "success"
    assert body["completeness"] == {"status": "complete", "reasons": []}


def test_satellite_failure_is_partial_but_still_success(client, deepseek, overpass, monkeypatch):

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    def boom(**kw):
        raise TimeoutError("stac down")

    monkeypatch.setattr(pipeline, "get_latest_satellite", boom)

    body = client.post("/analyze", json={"query": "solar in Adyar"}).json()

    assert body["status"] == "success"
    assert body["completeness"]["status"] == "partial"
    assert body["completeness"]["reasons"] == [
        "Satellite imagery could not be retrieved (TimeoutError: stac down)."
    ]


def test_completeness_partial_reasons():

    area = search_area.SearchArea(
        kind="radius", name="Velachery", latitude=LAT, longitude=LON,
        reach_km=1, area_km2=4, description="", place_area_km2=31.6,
    )

    context = new_context(LAT, LON, 1)

    context.layer_status["land_parcels"] = "loaded"

    result = pipeline._completeness(EV_CHARGING, area, context, "loaded")

    assert result["status"] == "partial"
    # Skipped layers in layer-id order, then the partial search.
    assert result["reasons"] == [
        "Not loaded for this analysis: Existing EV charging stations.",
        "Not loaded for this analysis: Flood exposure (observed water and flooding).",
        "Not loaded for this analysis: Shops, offices and amenities.",
        "Not loaded for this analysis: Road network.",
        "Only part of Velachery was searched (4 of about 32 km²).",
    ]


def test_unavailable_layers_do_not_make_a_run_partial():

    area = search_area.SearchArea(
        kind="radius", name="Adyar", latitude=LAT, longitude=LON,
        reach_km=1, area_km2=4, description="",
    )

    context = new_context(LAT, LON, 1)

    for layer in ("land_parcels", "roads", "flood_risk", "terrain"):
        context.layer_status[layer] = "loaded"

    # Zoning has no provider; that is missing data, not a failure.
    assert pipeline._completeness(LAND_ACQUISITION, area, context, "loaded") == {
        "status": "complete", "reasons": [],
    }
