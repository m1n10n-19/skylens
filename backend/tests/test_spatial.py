"""
Spatial correctness: search areas, candidate generation from OSM
geometry, and the criterion evaluators' measurements.

The fixture layout (tests/conftest.land_elements) is in metres from
the search centre, so expected distances and areas are known.
"""

import pytest

import search_area

from analysis_spec import AnalysisSpec, AreaRequirement
from criteria import (
    business_competition,
    commercial_activity,
    ev_competition,
    major_road_proximity,
    parking_potential,
    road_access,
    size_fit,
    vacancy_evidence,
)
from geodata import (
    LocalProjection,
    bbox_around,
    collect_candidates_and_context,
)

from tests.conftest import (
    LAT,
    LON,
    FakeGeolocator,
    huge_place_raw,
    land_elements,
    small_place_raw,
)


# ============================================================
# PROJECTION AND BOXES
# ============================================================

def test_local_projection_round_trips():

    proj = LocalProjection(LAT, LON)

    x, y = proj.to_xy(LON + 0.01, LAT + 0.01)

    lon, lat = proj.to_lonlat(x, y)

    assert lon == pytest.approx(LON + 0.01, abs=1e-9)
    assert lat == pytest.approx(LAT + 0.01, abs=1e-9)
    # 0.01° of latitude is about 1.1 km.
    assert y == pytest.approx(1105.7, abs=1)


def test_bbox_around_is_centred_and_sized():

    south, west, north, east = bbox_around(LAT, LON, 1)

    assert (south + north) / 2 == pytest.approx(LAT)
    assert (west + east) / 2 == pytest.approx(LON)

    proj = LocalProjection(LAT, LON)

    x1, y1 = proj.to_xy(west, south)
    x2, y2 = proj.to_xy(east, north)

    # About 2 km on each side.
    assert x2 - x1 == pytest.approx(2000, rel=0.01)
    assert y2 - y1 == pytest.approx(2000, rel=0.01)


# ============================================================
# SEARCH AREA
# ============================================================

def test_point_place_resolves_to_default_box():

    area = search_area.resolve(FakeGeolocator(), "Adyar, Chennai")

    assert area.kind == "radius"
    assert area.reach_km == search_area.DEFAULT_RADIUS_KM
    assert area.area_km2 == 4
    assert area.name == "Adyar"
    assert len(area.overpass_filters) == 1
    assert area.geometry["type"] == "Polygon"


def test_stated_radius_is_capped():

    area = search_area.resolve(FakeGeolocator(), "Adyar", stated_radius_km=50)

    assert area.reach_km == search_area.MAX_RADIUS_KM


def test_huge_place_is_refused_not_silently_shrunk():

    with pytest.raises(search_area.AreaTooLarge) as error:
        search_area.resolve(FakeGeolocator(huge_place_raw()), "Tamil Nadu")

    assert error.value.area_km2 > search_area.MAX_PLACE_KM2


def test_unknown_place_is_a_404():

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        search_area.resolve(FakeGeolocator(False), "Nowhere")

    assert error.value.status_code == 404
    assert error.value.detail["stage"] == "geocoding"


# ============================================================
# CANDIDATES AND CONTEXT
# ============================================================

@pytest.fixture
def land(overpass):

    fake = overpass(land_elements())

    area = search_area.resolve(FakeGeolocator(small_place_raw()), "Adyar")

    candidates, context = collect_candidates_and_context(
        "land_parcels", area, 150, None,
        ["roads", "points_of_interest", "ev_chargers", "parking"], "Adyar",
    )

    by_id = {c["osm_id"]: c for c in candidates}

    return by_id, context, fake


def test_land_candidates_are_parsed_with_metric_areas(land):

    by_id, context, _ = land

    assert set(by_id) == {2001, 2002}

    assert by_id[2001]["area_m2"] == pytest.approx(3600, rel=0.01)
    assert by_id[2002]["area_m2"] == pytest.approx(2025, rel=0.01)

    assert by_id[2001]["site_kind"] == "open_land"
    assert by_id[2001]["landuse"] == "vacant"


def test_context_layers_are_split_by_kind(land):

    _, context, _ = land

    assert len(context.roads) == 2
    assert len(context.pois) == 3
    assert len(context.chargers) == 1
    assert len(context.parking) == 1

    for layer in ("land_parcels", "roads", "points_of_interest", "ev_chargers", "parking"):
        assert context.has_layer(layer)

    assert not context.has_layer("flood_risk")


def test_one_overpass_request_for_candidates_and_context(land):

    _, _, fake = land

    assert len(fake.queries) == 1
    assert "->.cands" in fake.queries[0]


def test_area_limits_filter_candidates(overpass):

    overpass(land_elements())

    area = search_area.resolve(FakeGeolocator(), "Adyar")

    candidates, _ = collect_candidates_and_context("land_parcels", area, 2500, 3000)

    assert candidates == []

    candidates, _ = collect_candidates_and_context("land_parcels", area, 2000, 3000)

    assert [c["osm_id"] for c in candidates] == [2002]


def test_unloaded_context_layers_are_not_marked_loaded(overpass):

    overpass(land_elements())

    area = search_area.resolve(FakeGeolocator(), "Adyar")

    _, context = collect_candidates_and_context("land_parcels", area, 150, None, ["roads"])

    assert context.has_layer("roads")
    assert not context.has_layer("points_of_interest")
    assert not context.has_layer("ev_chargers")


# ============================================================
# EVALUATORS
# ============================================================

SPEC = AnalysisSpec(query="q", intent_type="x")


def test_road_access_measures_distance_to_nearest_road(land):

    by_id, context, _ = land

    near = road_access(by_id[2001], context, SPEC)

    # Parcel's southern edge is 10 m from LB Road.
    assert near["measurements"]["nearest_road_m"] == 10
    assert near["measurements"]["nearest_road"] == "LB Road"
    assert near["score"] == 100

    far = road_access(by_id[2002], context, SPEC)

    # Residential road at x = 280; parcel starts at x = 300.
    assert far["measurements"]["nearest_road_m"] == 20
    assert far["measurements"]["nearest_road_type"] == "residential"


def test_major_road_proximity_ignores_minor_roads(land):

    by_id, context, _ = land

    result = major_road_proximity(by_id[2002], context, SPEC)

    # Primary road at y = 0; parcel starts at y = 300.
    assert result["measurements"]["nearest_major_road_m"] == 300


def test_ev_competition_counts_mapped_chargers(land):

    by_id, context, _ = land

    result = ev_competition(by_id[2001], context, SPEC)

    m = result["measurements"]

    assert m["chargers_2km"] == 1
    assert m["chargers_1km"] == 1
    assert m["nearest_charger_m"] == 540


def test_commercial_activity_counts_pois_within_500m(land):

    by_id, context, _ = land

    assert commercial_activity(by_id[2001], context, SPEC)["measurements"]["businesses_500m"] == 3


def test_parking_counts_lots_within_300m(land):

    by_id, context, _ = land

    assert parking_potential(by_id[2001], context, SPEC)["measurements"]["parking_areas_300m"] == 1


def test_vacancy_evidence_says_it_is_an_unverified_tag(land):

    by_id, context, _ = land

    result = vacancy_evidence(by_id[2001], context, SPEC)

    assert "not verified" in result["evidence"]
    assert result["measurements"] == {"landuse_tag": "vacant"}


def test_vacancy_unknown_for_buildings():

    assert vacancy_evidence({"landuse": None}, None, SPEC) is None


def test_business_competition_does_not_guess_competitors(land):

    by_id, context, _ = land

    # No business type: SkyLens doesn't know what competes.
    assert business_competition(by_id[2001], context, SPEC) is None

    spec = AnalysisSpec(query="q", intent_type="x", requirements={"business_type": "supermarket"})

    result = business_competition(by_id[2001], context, spec)

    assert result["measurements"] == {"competitors_500m": 1}


@pytest.mark.parametrize("target,area,expected", [
    (1000, 1000, 100),
    (1000, 1500, 100),
    (1000, 400, 20),
    (1000, 20000, 40),
])
def test_size_fit_against_target(target, area, expected):

    spec = AnalysisSpec(query="q", intent_type="x",
                        area=AreaRequirement(target_m2=target, as_stated="x"))

    assert size_fit({"area_m2": area}, None, spec)["score"] == expected


def test_size_fit_unknown_without_area():

    assert size_fit({"area_m2": None}, None, SPEC) is None
