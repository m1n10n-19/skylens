"""
Terrain from the Copernicus DEM: an evidence-only criterion of its
own, never presented as flood risk; its relative height also feeds the
flood exposure score.
"""

import numpy as np
import pytest

from shapely.geometry import box

import terrain

from tests.conftest import FakeDem, LAT, LON, land_elements, offset, planner_reply


# ============================================================
# PURE MATHS
# ============================================================

def test_slope_of_a_plane():

    # Rising 3 m per 30 m pixel eastwards: 10% slope.
    dem = np.tile(np.arange(10, dtype="float32") * 3, (10, 1))

    assert terrain.slope_percent(dem)[5, 5] == pytest.approx(10.0)


def test_site_lower_than_its_ring():

    dem = np.full((20, 20), 6.0, dtype="float32")
    dem[8:12, 8:12] = 3.0

    site = np.zeros((20, 20), dtype=bool)
    site[8:12, 8:12] = True

    ring = ~site

    result = terrain.site_terrain(dem, terrain.slope_percent(dem), site, ring)

    assert result["elevation_m"] == 3.0
    assert result["surroundings_m"] == 6.0
    assert result["relative_elevation_m"] == -3.0


def test_missing_dem_is_none_not_zero():

    dem = np.full((10, 10), np.nan, dtype="float32")

    site = np.zeros((10, 10), dtype=bool)
    site[4:6, 4:6] = True

    assert terrain.site_terrain(dem, terrain.slope_percent(dem), site, ~site) is None


# ============================================================
# MEASURE SITES
# ============================================================

def _site(dx, dy, side):

    lat0, lon0 = offset(dx, dy)
    lat1, lon1 = offset(dx + side, dy + side)

    return box(lon0, lat0, lon1, lat1)


def _ring(shape_ll, metres=500):

    # ~500 m in degrees near the test latitude; precise enough here.
    return shape_ll.buffer(metres / 111_000)


def test_flat_ground_has_no_meaningful_difference():

    fake = FakeDem(ground=6.0)

    site = _site(0, 0, 60)

    results, info = terrain.measure_sites([(site, _ring(site), False)],
                                          search=fake.search, reader=fake.reader)

    assert results[0]["measurable"] is True
    assert results[0]["elevation_m"] == 6.0
    assert results[0]["relative_elevation_m"] == 0.0
    assert results[0]["slope_pct"] == 0.0
    assert info["tiles"] == ["Copernicus_DSM_TEST"]
    assert fake.reads == 1


def test_depression_is_measured():

    fake = FakeDem(ground=6.0, low_by=3.0, radius_m=150)

    site = _site(-30, -30, 60)

    results, _ = terrain.measure_sites([(site, _ring(site), False)],
                                       search=fake.search, reader=fake.reader)

    assert results[0]["relative_elevation_m"] == pytest.approx(-3.0, abs=0.3)


def test_buildings_are_not_measurable():

    fake = FakeDem()

    site = _site(0, 0, 60)

    results, _ = terrain.measure_sites([(site, _ring(site), True)],
                                       search=fake.search, reader=fake.reader)

    assert results[0] == {"measurable": False, "reason": terrain.BUILDING_REASON}


def test_tiny_plot_still_measured():
    """
    A 10-cent plot (~20 x 20 m) is smaller than a 30 m pixel; the
    pixels it touches are used.
    """

    fake = FakeDem(ground=5.0)

    site = _site(0, 0, 20)

    results, _ = terrain.measure_sites([(site, _ring(site), False)],
                                       search=fake.search, reader=fake.reader)

    assert results[0]["measurable"] is True
    assert results[0]["elevation_m"] == 5.0


def test_no_tile_is_reported():

    with pytest.raises(terrain.TerrainUnavailable):
        site = _site(0, 0, 60)
        terrain.measure_sites([(site, _ring(site), False)],
                              search=lambda bbox: [], reader=None)


# ============================================================
# IN THE ANALYSIS
# ============================================================

def _ev(client, deepseek, overpass):

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    return client.post("/analyze", json={"query": "EV plots in Adyar"}).json()


def test_low_lying_site_is_flagged_not_called_flood_risk(client, deepseek, overpass, imagery, dem,
                                                       flood_evidence):

    from tests.conftest import clear_year

    imagery(clear_year())
    flood_evidence()
    # Parcel 2001 sits 0-60 m east, 10-70 m north of the centre.
    dem(ground=6.0, low_by=3.0, radius_m=120)

    body = _ev(client, deepseek, overpass)

    assert body["completeness"]["status"] == "complete"

    site = next(p for p in body["top_prospects"] if p["osm_id"] == 2001)

    entry = site["criteria"]["terrain"]

    assert entry["state"] == "measured"
    assert entry["weight"] == 0
    assert "lower than the ground within 500 m" in entry["evidence"]
    assert "flood" not in entry["evidence"].lower()

    # Low-lying ground only lowers the flood exposure score, which is
    # built from observed water, not from terrain alone.
    flood = site["criteria"]["flood_risk"]

    assert flood["state"] == "measured"
    assert flood["score"] == 85
    assert "below the surrounding ground" in flood["evidence"]

    assert any(r.startswith("Warning: low-lying") for r in site["reasons"])

    verify = {v["id"]: v for v in site["assessment"]["verify"]}

    assert verify["terrain"]["why"] == entry["evidence"]
    assert verify["terrain"]["method_type"] == "field_visit"

    item = next(i for i in entry["evidence_items"] if i["claim"].startswith("Height of the site"))

    assert item["measurement"]["unit"] == "m"
    assert item["source_id"] == "copernicus_dem_glo30_planetary_computer"


def test_flat_site_gets_no_warning(client, deepseek, overpass, imagery, dem):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem(ground=6.0)

    body = _ev(client, deepseek, overpass)

    site = body["top_prospects"][0]

    assert "no meaningful height difference" in site["criteria"]["terrain"]["evidence"]
    assert "terrain" not in {v["id"] for v in site["assessment"]["verify"]}


def test_terrain_never_changes_scores(client, deepseek, overpass, imagery, dem, monkeypatch):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem(ground=6.0, low_by=3.0, radius_m=120)

    with_dem = _ev(client, deepseek, overpass)

    def down(*args, **kwargs):
        raise TimeoutError("dem down")

    monkeypatch.setattr(terrain, "search_tiles", down)

    without = _ev(client, deepseek, overpass)

    key = lambda body: [(p["osm_id"], p["score"], p["confidence"]) for p in body["top_prospects"]]

    assert key(with_dem) == key(without)

    assert without["completeness"]["status"] == "partial"
    assert any(r.startswith("Terrain could not be measured")
               for r in without["completeness"]["reasons"])
    assert without["top_prospects"][0]["criteria"]["terrain"]["state"] == "data_not_loaded"


def test_commercial_buildings_are_not_measured(client, deepseek, overpass, imagery, dem):

    from tests.conftest import clear_year, square

    imagery(clear_year())
    dem()

    elements = land_elements() + [{
        "type": "way", "id": 7001, "tags": {"building": "commercial"},
        "geometry": square(-200, -200, 50),
    }]

    deepseek(planner_reply("commercial_site_selection"))
    overpass(elements)

    body = client.post("/analyze", json={"query": "shop site in Adyar"}).json()

    building = next(p for p in body["top_prospects"] if p["osm_id"] == 7001)

    entry = building["criteria"]["terrain"]

    assert entry["state"] == "not_measurable"
    assert entry["note"] == terrain.BUILDING_REASON


def test_solar_never_reads_the_dem(client, deepseek, overpass, monkeypatch):

    from tests.conftest import building_elements

    def forbidden(*args, **kwargs):
        raise AssertionError("solar must not read the DEM")

    monkeypatch.setattr(terrain, "measure_sites", forbidden)

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    body = client.post("/analyze", json={"query": "solar in Adyar"}).json()

    assert body["status"] == "success"
    assert "terrain" not in body["top_prospects"][0]["criteria"]
