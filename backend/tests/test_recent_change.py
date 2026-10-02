"""
Recent change on shortlisted sites (EV, commercial, land): one
imagery read for the top candidates, reported as evidence and
warnings, never changing scores or ranks, never blocking a result.
"""

from datetime import date, timedelta

import pytest

import change_detection as cd

from shapely.geometry import box

from tests.conftest import (
    LAT,
    LON,
    FakeImagery,
    building_elements,
    clear_year,
    land_elements,
    planner_reply,
)


def _site(dx, dy, side):
    """
    lon/lat square dx, dy metres from the centre.
    """

    from tests.conftest import offset

    lat0, lon0 = offset(dx, dy)
    lat1, lon1 = offset(dx + side, dy + side)

    return box(lon0, lat0, lon1, lat1)


TODAY = date(2026, 10, 2)

SCENES = {
    "before": {"day": date(2025, 9, 28), "cloudy": False},
    "after": {"day": date(2026, 9, 26), "cloudy": False, "cleared": "all"},
}


# ============================================================
# MEASURE PARCELS
# ============================================================

def test_measure_parcels_shares():

    imagery = FakeImagery(SCENES)

    parcels, scenes = cd.measure_parcels(
        [_site(0, 0, 60), _site(500, 500, 40)],
        today=TODAY, search=imagery.search, reader=imagery.reader,
    )

    assert scenes["before"]["id"] == "before"
    assert scenes["after"]["id"] == "after"

    for parcel in parcels:
        assert parcel["measurable"] is True
        assert parcel["shares"]["built_or_bare_increase"] == 1.0
        assert parcel["changed_share"] == 1.0

    # 60 x 60 m is about 36 pixels.
    assert 30 <= parcels[0]["pixels"] <= 42


def test_stable_parcels_have_zero_shares():

    imagery = FakeImagery({**SCENES, "after": {**SCENES["after"], "cleared": False}})

    parcels, _ = cd.measure_parcels([_site(0, 0, 60)], today=TODAY,
                                    search=imagery.search, reader=imagery.reader)

    assert parcels[0]["changed_share"] == 0
    assert set(parcels[0]["shares"].values()) == {0}


def test_tiny_parcel_is_not_measurable_not_zero():

    imagery = FakeImagery(SCENES)

    parcels, _ = cd.measure_parcels([_site(0, 0, 60), _site(300, 300, 8)], today=TODAY,
                                    search=imagery.search, reader=imagery.reader)

    tiny = parcels[1]

    assert tiny["measurable"] is False
    assert "Too small to measure" in tiny["reason"]
    assert "shares" not in tiny


def test_one_imagery_read_for_the_whole_shortlist():

    imagery = FakeImagery(SCENES)

    sites = [_site(i * 120, 0, 50) for i in range(10)]

    cd.measure_parcels(sites, today=TODAY, search=imagery.search, reader=imagery.reader)

    # Two searches (later + earlier scene), SCL per scene checked, 4 bands each.
    assert len(imagery.searches) == 2
    assert len(imagery.reads) == 2 * (1 + 4)


# ============================================================
# IN THE ANALYSIS
# ============================================================

def _ev(client, deepseek, overpass):

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    return client.post("/analyze", json={"query": "EV plots in Adyar"}).json()


def test_construction_on_shortlisted_site_is_flagged(client, deepseek, overpass, imagery, dem):

    dem()

    today = date.today()

    imagery({
        "before": {"day": today - timedelta(days=370), "cloudy": False},
        "after": {"day": today - timedelta(days=5), "cloudy": False, "cleared": "all"},
    })

    body = _ev(client, deepseek, overpass)

    assert body["status"] == "success"
    assert body["completeness"]["status"] == "complete"

    for site in body["top_prospects"]:

        entry = site["criteria"]["recent_change"]

        assert entry["state"] == "measured"
        assert entry["weight"] == 0
        assert "Built-up or bare surface increased on 100%" in entry["evidence"]
        assert entry["evidence_items"][0]["observed_at"]

        assert any(r.startswith("Warning: built-up or bare surface increased") for r in site["reasons"])

        verify = {v["id"]: v for v in site["assessment"]["verify"]}

        assert verify["recent_change"]["why"] == entry["evidence"]
        assert verify["recent_change"]["method_type"] == "field_visit"


def test_recent_change_never_changes_scores_or_ranks(client, deepseek, overpass, imagery, monkeypatch):

    imagery(clear_year(cleared="all"))

    with_change = _ev(client, deepseek, overpass)

    # Same analysis with imagery unavailable.
    def down(*args, **kwargs):
        raise TimeoutError("down")

    monkeypatch.setattr(cd, "search_scenes", down)

    without = _ev(client, deepseek, overpass)

    key = lambda body: [(p["osm_id"], p["score"], p["evidence_coverage"], p["confidence"])
                        for p in body["top_prospects"]]

    assert key(with_change) == key(without)


def test_stable_site_gets_no_warning_or_verify_item(client, deepseek, overpass, imagery):

    imagery(clear_year(cleared=False))

    body = _ev(client, deepseek, overpass)

    site = body["top_prospects"][0]

    assert site["criteria"]["recent_change"]["evidence"].startswith("No change detected")
    assert not any(r.startswith("Warning") for r in site["reasons"])
    assert "recent_change" not in {v["id"] for v in site["assessment"]["verify"]}


def test_imagery_problem_is_partial_not_failure(client, deepseek, overpass, imagery):

    imagery({"cloudy": {"day": date.today() - timedelta(days=3), "cloudy": True}})

    body = _ev(client, deepseek, overpass)

    assert body["status"] == "success"
    assert body["top_prospects"]
    assert body["completeness"]["status"] == "partial"
    assert body["completeness"]["reasons"][0].startswith(
        "Recent change on the shortlisted sites could not be checked"
    )

    entry = body["top_prospects"][0]["criteria"]["recent_change"]

    assert entry["state"] == "data_not_loaded"
    assert entry["score"] is None


def test_no_candidates_means_no_imagery_and_not_partial(client, deepseek, overpass, imagery):

    fake = imagery(clear_year())

    deepseek(planner_reply("land_acquisition"))
    overpass([])

    body = client.post("/analyze", json={"query": "land in Adyar"}).json()

    assert body["top_prospects"] == []
    assert fake.searches == []
    assert body["completeness"]["status"] == "complete"


def test_solar_never_checks_imagery(client, deepseek, overpass, monkeypatch):

    def forbidden(*args, **kwargs):
        raise AssertionError("solar must not run change detection")

    monkeypatch.setattr(cd, "measure_parcels", forbidden)

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    body = client.post("/analyze", json={"query": "solar in Adyar"}).json()

    assert body["status"] == "success"
    assert "recent_change" not in body["top_prospects"][0]["criteria"]


@pytest.mark.parametrize("use_case", [
    "ev_charging_site_selection", "commercial_site_selection", "land_acquisition",
])
def test_recent_change_is_evidence_only_in_each_use_case(use_case):

    from use_cases import USE_CASES

    criterion = next(c for c in USE_CASES[use_case].criteria if c.id == "recent_change")

    assert criterion.weight == 0
    assert criterion.evaluator == "recent_change"
    assert "historical_imagery" in USE_CASES[use_case].data_layers
