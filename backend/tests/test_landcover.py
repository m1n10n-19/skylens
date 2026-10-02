"""
Open land found in imagery (ESA WorldCover + a current Sentinel-2
check), and the OpenStreetMap protected-area check.
"""

import numpy as np
import pytest

from shapely.geometry import LineString, box

import geodata
import landcover
import pipeline

from analysis_spec import AnalysisSpec
from criteria import protected_status, vacancy_evidence
from geodata import ProtectedArea, Road, new_context
from tests.conftest import FakeAsset, LAT, LON, land_elements, offset, planner_reply, square


# ============================================================
# OPEN-LAND RULES
# ============================================================

def test_open_classes_only():

    classes = np.array([[10, 20, 30, 40, 50, 60, 80, 90, 95]], dtype="float32")

    assert landcover.open_mask(classes).tolist() == [[True] * 4 + [False, True] + [False] * 3]


def test_vegetated_in_2021_but_built_now_is_dropped_bare_is_kept():

    classes = np.array([[30, 30, 60, 60]], dtype="float32")

    built_now = np.array([[True, False, True, False]])

    clear_now = np.array([[True, True, True, True]])

    assert landcover.open_mask(classes, built_now, clear_now).tolist() == [[False, True, True, True]]


def test_cloudy_pixels_are_not_judged_now():

    classes = np.array([[30]], dtype="float32")

    assert landcover.open_mask(classes, np.array([[True]]), np.array([[False]])).tolist() == [[True]]


def test_removed_pixels_are_left_out():

    classes = np.array([[30, 30]], dtype="float32")

    assert landcover.open_mask(classes, removed=np.array([[True, False]])).tolist() == [[False, True]]


def test_class_shares_largest_first():

    classes = np.array([[30, 30, 40, 10]], dtype="float32")

    inside = np.array([[True, True, True, False]])

    assert landcover.class_shares(classes, inside) == {"grassland": 0.667, "cropland": 0.333}


# ============================================================
# DISCOVER (fake WorldCover)
# ============================================================

class FakeWorldCover:
    """
    West half grassland, east half built-up.
    """

    def search(self, bbox):
        tile = type("Tile", (), {})()
        tile.id = "ESA_WorldCover_TEST"
        tile.assets = {"map": FakeAsset("wc")}
        tile.properties = {"start_datetime": "2021-01-01T00:00:00Z"}
        return [tile]

    def reader(self, href, grid):
        out = np.full((grid.height, grid.width), 50, dtype="float32")
        out[:, : grid.width // 2] = 30
        return out


def _area(half_m=300):

    lat0, lon0 = offset(-half_m, -half_m)
    lat1, lon1 = offset(half_m, half_m)

    return box(lon0, lat0, lon1, lat1)


def _nothing_built(grid):

    shape = (grid.height, grid.width)

    return np.zeros(shape, dtype=bool), np.ones(shape, dtype=bool), {"date": "2026-09-26"}


def _discover(context, existing=(), current=_nothing_built, **kwargs):

    fake = FakeWorldCover()

    return landcover.discover(_area(), context, list(existing), search=fake.search,
                              reader=fake.reader, current=current, **kwargs)


def test_grassland_half_is_found_and_labelled():

    found, info = _discover(new_context(LAT, LON, 1))

    assert len(found) == 1

    patch = found[0]

    assert patch["landcover"] == "grassland"
    assert patch["site_type"] == "untagged open land (grassland)"
    assert patch["discovered"]["checked_on"] == "2026-09-26"
    assert "not tagged on OpenStreetMap" in patch["discovered"]["note"]
    assert patch["_sources"]["land_parcels"]["source_id"] == landcover.SOURCE_ID
    # About half of a 600 x 600 m area.
    assert patch["area_m2"] == pytest.approx(180_000, rel=0.05)
    assert info["land_cover_year"] == "2021"
    assert info["problems"] == []


def test_roads_split_patches():

    context = new_context(LAT, LON, 1)

    # A north-south road through the middle of the grassland half.
    line = LineString([context.proj.to_xy(*reversed(offset(-150, -400))),
                       context.proj.to_xy(*reversed(offset(-150, 400)))])

    context.roads = [Road(line, "residential", None, line.bounds)]

    found, _ = _discover(context)

    assert len(found) == 2


def test_osm_candidates_are_not_duplicated():

    context = new_context(LAT, LON, 1)

    existing = geodata._polygon(square(-300, -300, 300), context.proj)

    found, _ = _discover(context, existing=[existing])

    total = sum(p["area_m2"] for p in found)

    assert total == pytest.approx(90_000, rel=0.1)


def test_minimum_area_filters_patches():

    found, _ = _discover(new_context(LAT, LON, 1), min_area_m2=500_000)

    assert found == []


def test_failed_current_check_is_reported_and_discovery_continues():

    from change_detection import ChangeDataUnavailable

    def cloudy(grid):
        raise ChangeDataUnavailable("No clear image.")

    found, info = _discover(new_context(LAT, LON, 1), current=cloudy)

    assert len(found) == 1
    assert found[0]["discovered"]["checked_on"] is None
    assert info["problems"][0].startswith("Open land from the 2021 land-cover map could not be checked")


# ============================================================
# PROTECTED AREAS
# ============================================================

def test_protected_relation_is_assembled():

    proj = geodata.LocalProjection(LAT, LON)

    ring = square(-200, -200, 400)

    relation = {"type": "relation", "id": 1, "members": [
        {"type": "way", "role": "outer", "geometry": ring[:3]},
        {"type": "way", "role": "outer", "geometry": ring[2:]},
    ]}

    shape = geodata._protected_shape(relation, proj)

    assert shape.area == pytest.approx(160_000, rel=0.02)


@pytest.mark.parametrize("tags,kind", [
    ({"boundary": "protected_area"}, "protected_area"),
    ({"leisure": "nature_reserve"}, "protected_area"),
    ({"landuse": "forest", "name": "Nanmangalam Reserve Forest"}, "reserve_forest"),
    ({"landuse": "forest", "name": "Some Grove"}, None),
    ({"natural": "wetland"}, "wetland"),
    ({"landuse": "vacant"}, None),
])
def test_protected_kinds(tags, kind):

    assert geodata.protected_kind(tags) == kind


def _context_with(zones):

    context = new_context(LAT, LON, 1)

    context.protected = zones

    return context


def test_protected_overlap_is_measured_and_warned():

    context = _context_with([
        ProtectedArea(box(-1000, -1000, 30, 1000), "Pallikaranai Marsh", "protected_area", "Reserve Forest"),
    ])

    site = {"_shape": box(0, 0, 60, 60), "latitude": LAT, "longitude": LON}

    result = protected_status(site, context, None)

    assert result["measurements"]["protected_overlap_share"] == 0.5
    assert result["reasons"] == ["Warning: overlaps Pallikaranai Marsh (Reserve Forest) on 50% of the site"]


def test_no_mapped_protection_is_not_called_unprotected():

    result = protected_status({"_shape": box(0, 0, 60, 60)}, _context_with([]), None)

    assert "does not prove the land is unprotected" in result["evidence"]
    assert result["measurements"] == {"protected_overlap_share": 0.0, "wetland_overlap_share": 0.0}


def test_sites_inside_protected_areas_are_not_ranked_wetlands_are_kept():

    context = _context_with([
        ProtectedArea(box(-1000, -1000, 1000, 1000), "Marsh", "protected_area"),
    ])

    inside = {"_shape": box(0, 0, 50, 50)}
    outside = {"_shape": box(2000, 2000, 2050, 2050)}

    kept, dropped = pipeline._drop_protected([inside, outside], context)

    assert kept == [outside] and dropped == [inside]

    wet = _context_with([ProtectedArea(box(-1000, -1000, 1000, 1000), None, "wetland")])

    assert pipeline._drop_protected([inside], wet) == ([inside], [])


# ============================================================
# IN THE ANALYSIS
# ============================================================

def test_vacancy_from_land_cover_is_observed_and_sourced():

    candidate = {"landcover": "bare ground",
                 "discovered": {"land_cover_year": "2021", "note": "Found in the land-cover map."}}

    result = vacancy_evidence(candidate, None, AnalysisSpec(query="q", intent_type="x"))

    assert result["score"] == 75
    assert result["measurements"] == {"landcover_class": "bare ground"}
    assert "not tagged on OpenStreetMap" in result["evidence"]


def test_discovered_land_is_ranked_and_labelled(client, deepseek, overpass, imagery, dem,
                                                flood_evidence, open_land):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence()
    open_land(found=[(-400, -400, 70, "bare ground"), (400, -400, 50, "grassland")])

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    assert body["status"] == "success"
    assert body["analysis"]["found_in_imagery"] == 2
    assert body["analysis"]["total_candidates"] == 4

    found = [p for p in body["top_prospects"] if p.get("discovered")]

    assert len(found) == 2

    bare = next(p for p in found if p["landcover"] == "bare ground")

    vacancy = bare["criteria"]["vacancy"]

    assert vacancy["state"] == "measured"
    assert vacancy["evidence_items"][0]["source_id"] == landcover.SOURCE_ID
    assert vacancy["evidence_items"][0]["status"] == "observed"

    size = bare["criteria"]["parcel_size_fit"]["evidence_items"][0]

    assert size["source_id"] == landcover.SOURCE_ID

    assert any("not tagged on OpenStreetMap" in l for l in body["limitations"])
    assert body["completeness"]["status"] == "complete"


def test_discovery_problem_is_partial_and_osm_candidates_still_ranked(client, deepseek, overpass,
                                                                     imagery, dem, flood_evidence):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence()

    # landcover.search_tiles is blocked in tests: discovery fails.
    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    assert body["status"] == "success"
    assert len(body["top_prospects"]) == 2
    assert body["completeness"]["status"] == "partial"
    assert any(r.startswith("Open land could not be looked for in imagery")
               for r in body["completeness"]["reasons"])


def test_solar_does_not_discover_land(client, deepseek, overpass, monkeypatch):

    from tests.conftest import building_elements

    def forbidden(*args, **kwargs):
        raise AssertionError("solar must not discover land")

    monkeypatch.setattr(landcover, "discover", forbidden)

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    assert client.post("/analyze", json={"query": "solar in Adyar"}).json()["status"] == "success"


# ============================================================
# EVIDENCE STRENGTH
# ============================================================

def test_strips_narrower_than_two_pixels_are_removed():

    mask = np.zeros((6, 8), dtype=bool)
    mask[1, :] = True          # 1-pixel strip (a verge)
    mask[3:6, 2:5] = True      # 3 x 3 block (a plot)

    opened = landcover.without_strips(mask)

    assert not opened[1].any()
    assert opened[3:6, 2:5].all()


class TinyPatches(FakeWorldCover):
    """
    Built-up everywhere except one 2 x 2 and one 4 x 4 grass block.
    """

    def reader(self, href, grid):
        out = np.full((grid.height, grid.width), 50, dtype="float32")
        out[5:7, 5:7] = 30
        out[20:24, 20:24] = 30
        return out


def test_patches_under_nine_pixels_are_not_added():

    fake = TinyPatches()

    found, _ = landcover.discover(_area(), new_context(LAT, LON, 1), [], search=fake.search,
                                  reader=fake.reader, current=_nothing_built)

    assert [p["area_m2"] for p in found] == [1600]


def test_imagery_found_land_is_capped_at_medium_confidence(monkeypatch):

    import scoring

    from use_cases import LAND_ACQUISITION

    # A strong score with full evidence would be "high".
    monkeypatch.setitem(scoring.SCORERS, "weighted_criteria",
                        lambda c, s, u, ctx: {"score": 95, "confidence": "high", "evidence_coverage": 1.0})

    spec = AnalysisSpec(query="q", intent_type="land_acquisition")

    found = scoring.score_candidate({"_max_confidence": "medium"}, spec, LAND_ACQUISITION, None)

    mapped = scoring.score_candidate({}, spec, LAND_ACQUISITION, None)

    assert found["confidence"] == "medium"
    assert found["confidence_note"] == "Capped: found in imagery, not on the map."
    assert mapped["confidence"] == "high"
    assert "confidence_note" not in mapped
