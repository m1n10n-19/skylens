"""
Observed flood exposure: wet seasons chosen per location, radar
standing water against a dry reference, JRC water history, and the
fixed scoring formula. Observations, never a flood probability.
"""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from shapely.geometry import box

import flood

from criteria import FLOOD_PENALTIES, flood_exposure
from tests.conftest import (
    FakeAsset,
    flood_result,
    land_elements,
    offset,
    planner_reply,
)


# ============================================================
# SEASONS
# ============================================================

CHENNAI = [0.5, 0.2, 0.5, 0.9, 1.8, 2.3, 3.5, 4.3, 4.8, 8.5, 9.7, 4.1]

KOCHI = [0.4, 0.9, 1.8, 4.3, 9.2, 16.5, 14.2, 11.3, 9.8, 11.5, 5.8, 1.2]


@pytest.mark.parametrize("monthly,wet,dry", [
    (CHENNAI, (9, 10, 11), (1, 2, 3)),
    (KOCHI, (6, 7, 8), (12, 1, 2)),
])
def test_wet_and_dry_season_follow_local_rainfall(monthly, wet, dry):

    assert flood.season_window(monthly, wettest=True) == wet
    assert flood.season_window(monthly, wettest=False) == dry


def test_climatology_failure_falls_back_and_says_so():

    def down(lat, lon):
        raise TimeoutError("power down")

    wet, dry, chosen = flood.seasons_for(13.0, 80.2, climate=down)

    assert wet == flood.FALLBACK_WET
    assert dry == flood.FALLBACK_DRY
    assert "unavailable" in chosen


def test_season_ranges_include_the_current_season_so_far():

    runs = flood.season_ranges((9, 10, 11), date(2026, 10, 2), 3)

    assert [label for label, _, _ in runs] == [
        "Sep-Nov 2026 (so far)", "Sep-Nov 2025", "Sep-Nov 2024", "Sep-Nov 2023",
    ]
    assert runs[0][2] == date(2026, 10, 2)


def test_season_wrapping_the_year_end():

    label, start, end = flood.season_ranges((12, 1, 2), date(2026, 10, 2), 1)[0]

    assert (label, start, end) == ("Dec-Feb 2026", date(2025, 12, 1), date(2026, 3, 1))


# ============================================================
# RADAR MATHS
# ============================================================

WATER = 10 ** (-25 / 10)   # -25 dB, calm water

LAND = 10 ** (-8 / 10)     # -8 dB, ordinary ground


def test_to_db_marks_missing_data():

    out = flood.to_db(np.array([[WATER, LAND, 0.0, np.nan]], dtype="float32"))

    assert out[0, :2].tolist() == pytest.approx([-25.0, -8.0])
    assert np.isnan(out[0, 2]) and np.isnan(out[0, 3])


def test_smoothing_removes_single_dark_speckles():

    db = flood.to_db(np.full((5, 5), LAND, dtype="float32"))
    db[2, 2] = -30.0

    assert not flood.water_mask(db).any()

    db[1:4, 1:4] = -25.0

    assert flood.water_mask(db)[2, 2]


def test_permanent_dark_ground_is_not_flooding():

    wet = flood.to_db(np.full((6, 6), LAND, dtype="float32"))
    wet[:, :3] = flood.to_db(np.array([[WATER]]))[0, 0]

    dry = wet.copy()

    # Dark in both seasons (a lake or tarmac): not flooding.
    water, valid = flood.new_water(wet, dry)

    assert valid.all()
    assert not water.any()

    # Dark only in the wet season: flooding.
    dry[:, :] = flood.to_db(np.array([[LAND]]))[0, 0]

    water, _ = flood.new_water(wet, dry)

    assert water[:, :2].all()


def test_site_observations_count_flooded_images_and_seasons():

    site = np.zeros((4, 4), dtype=bool)
    site[:2, :2] = True

    valid = np.ones((4, 4), dtype=bool)

    flooded = np.zeros((4, 4), dtype=bool)
    flooded[:2, :2] = True

    dry = np.zeros((4, 4), dtype=bool)

    images = [
        (date(2025, 11, 1), "Sep-Nov 2025", flooded, valid),
        (date(2025, 11, 13), "Sep-Nov 2025", flooded, valid),
        (date(2024, 10, 2), "Sep-Nov 2024", dry, valid),
        (date(2023, 10, 2), "Sep-Nov 2023", flooded, valid),
    ]

    result = flood.site_observations(images, site)

    assert result["images"] == 4
    assert result["flooded_images"] == 3
    assert result["flooded_seasons"] == 2
    assert result["max_share"] == 1.0


def test_site_history_ignores_missing_data():

    occurrence = np.full((4, 4), np.nan, dtype="float32")
    occurrence[0, :] = [0, 0, 40, 80]

    site = np.zeros((4, 4), dtype=bool)
    site[:2, :] = True

    assert flood.site_history(occurrence, site) == {"share": 0.5, "mean_occurrence": 30.0}
    assert flood.site_history(np.full((2, 2), np.nan), np.ones((2, 2), dtype=bool)) is None


# ============================================================
# MEASURE SITES (fake providers)
# ============================================================

class FakeItem:

    def __init__(self, item_id, day, asset):
        self.id = item_id
        self.datetime = datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc)
        self.assets = {asset: FakeAsset(item_id)}


class FakeFloodData:
    """
    JRC occurrence and Sentinel-1 images: `flooded_days` are wet-season
    dates when the west half of the grid is under water.
    """

    def __init__(self, flooded_days=(), history=0.0, slow=()):
        self.flooded_days = set(flooded_days)
        self.history = history
        self.slow = set(slow)
        self.searches = []

    def search(self, collection, bbox, start=None, end=None):
        self.searches.append(collection)
        if collection == flood.JRC_COLLECTION:
            return [FakeItem("jrc", date(2020, 12, 31), "occurrence")]
        days = []
        for year in (2023, 2024, 2025, 2026):
            for month in (1, 2, 3, 9, 10, 11):
                for day in (5, 20):
                    d = date(year, month, day)
                    if start <= d < end:
                        days.append(d)
        return [FakeItem(f"s1-{d.isoformat()}", d, "vv") for d in days]

    def reader(self, href, grid):
        if href in self.slow:
            import time
            time.sleep(3)
        shape = (grid.height, grid.width)
        if href == "jrc":
            return np.full(shape, self.history, dtype="float32")
        day = date.fromisoformat(href[3:])
        out = np.full(shape, LAND, dtype="float32")
        if day in self.flooded_days:
            out[:, : grid.width // 2 + 2] = WATER
        return out


def _site():

    lat0, lon0 = offset(-40, -40)
    lat1, lon1 = offset(40, 40)

    return box(lon0, lat0, lon1, lat1)


TODAY = date(2026, 10, 2)


def _measure(data, **kwargs):

    return flood.measure_sites([_site()], today=TODAY, search_items=data.search,
                               reader=data.reader, climate=lambda lat, lon: CHENNAI, **kwargs)


def test_measure_sites_finds_wet_season_flooding():

    data = FakeFloodData(flooded_days={date(2025, 11, 20), date(2024, 11, 20)}, history=30.0)

    results, info = _measure(data)

    result = results[0]

    assert result["water_history"]["share"] == 1.0
    assert result["observed"]["flooded_images"] == 2
    assert result["observed"]["flooded_seasons"] == 2
    assert info["seasons"]["wet_months"] == ["Sep", "Oct", "Nov"]
    assert info["problems"] == []
    # One radar search for the whole period, one JRC search.
    assert data.searches.count(flood.S1_COLLECTION) == 1


def test_image_choice_is_deterministic():

    first = _measure(FakeFloodData())[1]["observed_flooding"]["wet_images"]
    second = _measure(FakeFloodData())[1]["observed_flooding"]["wet_images"]

    assert first == second
    assert len(first) == 2 * 4   # 2 per season, 3 full seasons + the current one


def test_slow_radar_drops_the_whole_component(monkeypatch):
    """
    Reading only some images would make scores vary between runs.
    """

    monkeypatch.setattr(flood, "RADAR_BUDGET_SECONDS", 1)

    data = FakeFloodData(slow={"s1-2025-11-20"}, history=0.0)

    results, info = _measure(data)

    assert results[0]["observed"] is None
    assert results[0]["water_history"] is not None
    assert "observed_flooding" not in info
    assert info["problems"][0].startswith("Radar flood observations (Sentinel-1) could not be read")


# ============================================================
# SCORING FORMULA
# ============================================================

def _score(flood_data, relative=None):

    candidate = {"flood": flood_data}

    if relative is not None:
        candidate["terrain"] = {"measurable": True, "relative_elevation_m": relative}

    return flood_exposure(candidate, None, None)


@pytest.mark.parametrize("seasons,history,relative,expected", [
    (0, 0.0, None, 100),
    (1, 0.0, None, 50),
    (2, 0.0, None, 30),
    (0, 0.1, None, 85),
    (0, 0.3, None, 70),
    (0, 0.0, -3.0, 85),
    (2, 0.3, -3.0, 0),
])
def test_flood_formula(seasons, history, relative, expected):

    assert _score(flood_result(flooded_seasons=seasons, history_share=history), relative)["score"] == expected


def test_formula_constants_are_the_documented_ones():

    assert FLOOD_PENALTIES == {
        "flooded_once": 50, "flooded_again": 20,
        "water_history_major": 30, "water_history_minor": 15, "low_lying": 15,
    }


def test_missing_radar_is_stated_not_assumed_dry():

    result = _score(flood_result(radar=False))

    assert result["score"] == 100
    assert "radar flood observations unavailable" in result["evidence"]
    assert "observed_flood_images" not in result["measurements"]


def test_nothing_readable_is_not_measured():

    assert _score(flood_result(radar=False, history=False)) == {
        "not_measured": "Neither radar flood observations nor water history could be read.",
    }


def test_evidence_never_claims_low_risk():

    text = _score(flood_result())["evidence"].lower()

    assert text.startswith("observed:")
    assert "low risk" not in text and "safe" not in text


# ============================================================
# IN THE ANALYSIS
# ============================================================

def _ev(client, deepseek, overpass):

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    return client.post("/analyze", json={"query": "EV plots in Adyar"}).json()


def test_flooding_lowers_score_and_rank(client, deepseek, overpass, imagery, dem, flood_evidence):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()

    flood_evidence()

    dry = _ev(client, deepseek, overpass)

    # Parcel 2001 is listed first by Overpass and flooded in two seasons.
    flood_evidence(by_site=lambda i: flood_result(flooded_seasons=2) if i == 0 else flood_result())

    wet = _ev(client, deepseek, overpass)

    score = lambda body, osm_id: next(p for p in body["top_prospects"] if p["osm_id"] == osm_id)

    assert score(wet, 2001)["criteria"]["flood_risk"]["score"] == 30
    assert score(wet, 2001)["score"] < score(dry, 2001)["score"]
    assert [p["osm_id"] for p in wet["top_prospects"]][0] != 2001

    site = score(wet, 2001)

    assert any(r.startswith("Warning: standing water seen on the site by radar") for r in site["reasons"])
    assert wet["completeness"]["status"] == "complete"


def test_water_history_raises_a_water_body_check(client, deepseek, overpass, imagery, dem, flood_evidence):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence(default=flood_result(history_share=0.3))

    body = _ev(client, deepseek, overpass)

    verify = {v["id"]: v for v in body["top_prospects"][0]["assessment"]["verify"]}

    assert verify["water_body"]["method_type"] == "records_check"
    assert "1984-2020" in verify["water_body"]["why"]
    # The flood-history check is always listed: satellites miss short floods.
    assert "flood_risk" in verify


def test_radar_problem_is_partial_and_named(client, deepseek, overpass, imagery, dem, flood_evidence):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence(default=flood_result(radar=False),
                   problems=["Radar flood observations (Sentinel-1) could not be read (TimeoutError: slow)."])

    body = _ev(client, deepseek, overpass)

    entry = body["top_prospects"][0]["criteria"]["flood_risk"]

    assert entry["state"] == "measured"
    assert "radar flood observations unavailable" in entry["evidence"]
    assert body["completeness"]["status"] == "partial"
    assert any("Sentinel-1" in r for r in body["completeness"]["reasons"])
    assert entry["evidence_items"][0]["source_id"] == "jrc_gsw_planetary_computer"


def test_solar_never_reads_flood_data(client, deepseek, overpass, monkeypatch):

    from tests.conftest import building_elements

    def forbidden(*args, **kwargs):
        raise AssertionError("solar must not read flood data")

    monkeypatch.setattr(flood, "measure_sites", forbidden)

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    assert client.post("/analyze", json={"query": "solar in Adyar"}).json()["status"] == "success"
