"""
Sentinel-2 change detection: pixel maths, scene choice, patches,
the time range, and the construction_progress analysis end to end.

All imagery is synthetic: a fake STAC search returns fake items and
a fake band reader returns fixed arrays, so nothing touches the
network.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

import change_detection as cd

from analysis_spec import parse_time_range
from shapely.geometry import box

from tests.conftest import LAT, LON, FakeImagery, planner_reply


# ============================================================
# PIXEL MATHS
# ============================================================

def test_reflectance_applies_offset_by_processing_baseline():

    dn = np.array([[0, 1000, 2000, 11000]], dtype="uint16")

    old = cd.reflectance(dn, "03.01")
    new = cd.reflectance(dn, "05.11")

    assert np.isnan(old[0, 0]) and np.isnan(new[0, 0])
    assert old[0, 1:].tolist() == pytest.approx([0.1, 0.2, 1.1])
    # Same physical surface after the 2022 baseline change.
    assert new[0, 1:].tolist() == pytest.approx([0.0, 0.1, 1.0])


def test_spectral_indices():

    bands = {
        "green": np.array([[0.05]], dtype="float32"),
        "red": np.array([[0.05]], dtype="float32"),
        "nir": np.array([[0.45]], dtype="float32"),
        "swir": np.array([[0.15]], dtype="float32"),
    }

    idx = cd.spectral_indices(bands)

    assert idx["NDVI"][0, 0] == pytest.approx(0.8)
    assert idx["NDBI"][0, 0] == pytest.approx(-0.5)
    assert idx["NDWI"][0, 0] == pytest.approx(-0.8)


def test_zero_reflectance_is_nan_not_zero():

    zero = np.zeros((1, 1), dtype="float32")

    idx = cd.spectral_indices({"green": zero, "red": zero, "nir": zero, "swir": zero})

    assert np.isnan(idx["NDVI"][0, 0])


def test_clear_mask_excludes_clouds_and_shadows():

    scl = np.array([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]])

    assert cd.clear_mask(scl).tolist() == [[False] * 4 + [True] * 3 + [False] * 4 + [True]]


def _idx(ndvi, ndbi, ndwi):

    return {k: np.array(v, dtype="float32") for k, v in
            (("NDVI", ndvi), ("NDBI", ndbi), ("NDWI", ndwi))}


@pytest.mark.parametrize("before,after,expected", [
    # NDVI, NDBI, NDWI
    ((0.8, -0.3, -0.6), (0.3, -0.25, -0.4), "vegetation_loss"),
    ((0.2, 0.0, -0.3), (0.7, -0.3, -0.6), "vegetation_gain"),
    ((0.5, -0.2, -0.5), (0.1, 0.05, -0.2), "built_or_bare_increase"),
    ((0.2, -0.1, -0.2), (0.0, -0.2, 0.3), "water_gain"),
    ((0.0, -0.2, 0.3), (0.2, -0.1, -0.2), "water_loss"),
    # Below thresholds: no change.
    ((0.8, -0.3, -0.6), (0.65, -0.3, -0.6), None),
    # NDWI hovering around zero is not water change.
    ((0.1, -0.1, 0.03), (0.1, -0.1, -0.03), None),
])
def test_classify_rules(before, after, expected):

    b = _idx([[before[0]]], [[before[1]]], [[before[2]]])
    a = _idx([[after[0]]], [[after[1]]], [[after[2]]])

    labels = cd.classify(b, a, np.array([[True]]))

    found = cd.CHANGE_TYPES[labels[0, 0] - 1] if labels[0, 0] else None

    assert found == expected


def test_cloudy_pixels_are_never_change():

    b = _idx([[0.8]], [[-0.3]], [[-0.6]])
    a = _idx([[0.1]], [[0.1]], [[-0.1]])

    assert cd.classify(b, a, np.array([[False]]))[0, 0] == 0


def test_requested_change_types_only():

    b = _idx([[0.5]], [[-0.2]], [[-0.5]])
    a = _idx([[0.1]], [[0.05]], [[-0.2]])

    labels = cd.classify(b, a, np.array([[True]]), change_types=("vegetation_loss",))

    # Built-up rule not requested, so the vegetation rule applies.
    assert cd.CHANGE_TYPES[labels[0, 0] - 1] == "vegetation_loss"


def test_patches_group_connected_pixels_and_drop_specks():

    from rasterio.transform import from_origin

    labels = np.zeros((20, 20), dtype="uint8")
    labels[2:6, 2:6] = 4          # 16 px of vegetation loss
    labels[15, 15] = 4            # 1 px speck: dropped
    labels[10:12, 10:13] = 3      # 6 px of built-up increase

    ndvi = np.full((20, 20), 0.8, dtype="float32")
    after_ndvi = ndvi.copy()
    after_ndvi[labels == 4] = 0.3

    before = _idx(ndvi, np.zeros((20, 20)), np.zeros((20, 20)))
    after = _idx(after_ndvi, np.zeros((20, 20)), np.zeros((20, 20)))

    found = cd.patches(labels, from_origin(0, 200, 10, 10), before, after)

    by_type = {p["change_type"]: p for p in found}

    assert set(by_type) == {"vegetation_loss", "built_or_bare_increase"}
    assert by_type["vegetation_loss"]["pixels"] == 16
    assert by_type["vegetation_loss"]["delta_mean"] == pytest.approx(-0.5)
    assert by_type["built_or_bare_increase"]["pixels"] == 6


@pytest.mark.parametrize("a,b,gap", [
    (date(2025, 10, 6), date(2026, 9, 26), 10),
    (date(2025, 1, 1), date(2025, 12, 31), 1),
    (date(2025, 1, 1), date(2025, 7, 2), 182),
])
def test_season_gap(a, b, gap):

    assert cd.season_gap_days(a, b) == gap


# ============================================================
# SYNTHETIC SCENES
# ============================================================

AREA = box(LON - 0.004, LAT - 0.004, LON + 0.004, LAT + 0.004)

TODAY = date(2026, 10, 2)


def test_detects_clearing_between_clear_scenes():

    imagery = FakeImagery({
        "before": {"day": date(2025, 9, 28), "cloudy": False},
        "after": {"day": date(2026, 9, 26), "cloudy": False, "cleared": True},
    })

    result = cd.detect_changes(AREA, today=TODAY, search=imagery.search, reader=imagery.reader)

    assert result.before["id"] == "before"
    assert result.after["id"] == "after"
    assert result.season_gap_days == 2
    assert result.clear_fraction_both == pytest.approx(1.0, abs=0.01)

    assert len(result.observations) == 1

    obs = result.observations[0]

    assert obs["change_type"] == "built_or_bare_increase"
    # A quarter of the grid.
    assert obs["area_m2"] == pytest.approx(result.area_km2 * 1e6 / 4, rel=0.15)
    assert obs["delta_mean"] > 0.1
    assert obs["geometry"]["type"] == "Polygon"


def test_no_change_between_identical_scenes():

    imagery = FakeImagery({
        "before": {"day": date(2025, 9, 28), "cloudy": False},
        "after": {"day": date(2026, 9, 26), "cloudy": False},
    })

    result = cd.detect_changes(AREA, today=TODAY, search=imagery.search, reader=imagery.reader)

    assert result.observations == []


def test_cloudy_recent_scene_is_skipped_for_an_older_clear_one():

    imagery = FakeImagery({
        "before": {"day": date(2025, 9, 1), "cloudy": False},
        "cloudy": {"day": date(2026, 9, 30), "cloudy": True},
        "after": {"day": date(2026, 9, 10), "cloudy": False},
    })

    result = cd.detect_changes(AREA, today=TODAY, search=imagery.search, reader=imagery.reader)

    assert result.after["id"] == "after"
    # Bands are only read for the two chosen scenes.
    assert not any(h.startswith("cloudy/B") for h in imagery.reads)


def test_no_clear_scene_is_reported_not_guessed():

    imagery = FakeImagery({
        "cloudy": {"day": date(2026, 9, 30), "cloudy": True},
    })

    with pytest.raises(cd.ChangeDataUnavailable) as error:
        cd.detect_changes(AREA, today=TODAY, search=imagery.search, reader=imagery.reader)

    assert "clear of cloud" in error.value.reason
    assert error.value.tried["after"][0]["clear_fraction"] == 0


def test_before_date_targets_the_stated_year():

    imagery = FakeImagery({
        "old": {"day": date(2019, 1, 20), "cloudy": False},
        "after": {"day": date(2026, 9, 26), "cloudy": False},
    })

    result = cd.detect_changes(AREA, before_date=date(2019, 1, 1), today=TODAY,
                               search=imagery.search, reader=imagery.reader)

    assert result.before["date"] == "2019-01-20"
    # Searched within the season window around the target.
    assert imagery.searches[1] == (date(2018, 11, 17), date(2019, 2, 15))


# ============================================================
# TIME RANGE
# ============================================================

@pytest.mark.parametrize("raw,start,end", [
    ({"start": "2019", "as_stated": "since 2019"}, "2019-01-01", None),
    ({"start": "2020-06", "end": "2023"}, "2020-06-01", "2023-12-31"),
    ({"years_back": 5}, "2021-10-02", None),
    ({"months_back": 1}, "2026-09-02", None),
    ({"start": "2030"}, None, None),
    ({"start": "2023", "end": "2020"}, None, None),
    ({"start": "June 2019"}, None, None),
    (None, None, None),
])
def test_time_range_parsing(raw, start, end):

    result = parse_time_range(raw, today=TODAY)

    assert (result.start, result.end) == (start, end)


# ============================================================
# END TO END
# ============================================================

def test_analyze_change_end_to_end(client, deepseek, imagery):

    today = date.today()

    imagery({
        "before": {"day": today - timedelta(days=370), "cloudy": False},
        "after": {"day": today - timedelta(days=5), "cloudy": False, "cleared": True},
    })

    deepseek(planner_reply("construction_progress"))

    body = client.post("/analyze", json={"query": "What changed around Adyar?"}).json()

    assert body["status"] == "success"
    assert body["use_case"]["id"] == "construction_progress"

    meta = body["change_detection"]

    assert meta["before"]["id"] == "before"
    assert meta["after"]["id"] == "after"
    assert meta["resolution_m"] == 10

    top = body["top_prospects"]

    assert len(top) == 1

    change = top[0]

    assert change["change_type"] == "built_or_bare_increase"
    assert change["geometry"]["type"] == "Polygon"
    assert change["evidence_coverage"] == 1.0

    items = change["criteria"]["change_magnitude"]["evidence_items"]

    assert items[0]["source_id"] == "sentinel_2_planetary_computer"
    # Imagery evidence has a real observation date.
    assert items[0]["observed_at"].startswith((today - timedelta(days=5)).isoformat())

    verify = {v["id"] for v in change["assessment"]["verify"]}

    assert {"cause_of_change", "permits"} <= verify
    assert any(l.startswith("Compared Sentinel-2 images (10 m)") for l in body["limitations"])
    assert "between" in body["decision"]["summary"]


def test_analyze_change_without_clear_imagery(client, deepseek, imagery):

    imagery({"cloudy": {"day": date.today() - timedelta(days=3), "cloudy": True}})

    deepseek(planner_reply("construction_progress"))

    body = client.post("/analyze", json={"query": "What changed around Adyar?"}).json()

    assert body["status"] == "data_unavailable"
    assert "no change has been measured" in body["message"]
    assert "top_prospects" not in body
    assert body["scenes_checked"]["after"]


def test_analyze_change_provider_failure_is_502(client, deepseek, monkeypatch):

    def down(*args, **kwargs):
        raise TimeoutError("planetary computer down")

    monkeypatch.setattr(cd, "search_scenes", down)

    deepseek(planner_reply("construction_progress"))

    response = client.post("/analyze", json={"query": "What changed around Adyar?"})

    assert response.status_code == 502
    assert response.json()["detail"]["stage"] == "imagery_data"


def test_analyze_change_uses_stated_period(client, deepseek, imagery):
    """
    "since 2023" fixes the year only: the earlier image is taken from
    2023 at the same time of year as the later one.
    """

    after_day = date.today() - timedelta(days=5)

    target = cd.same_season(2023, after_day)

    fake = imagery({
        "jan": {"day": date(2023, 1, 15), "cloudy": False},
        "same-season": {"day": target + timedelta(days=4), "cloudy": False},
        "after": {"day": after_day, "cloudy": False},
    })

    deepseek(planner_reply("construction_progress",
                           time_range={"start": "2023", "as_stated": "since 2023"}))

    body = client.post("/analyze", json={"query": "What changed since 2023?"}).json()

    assert body["analysis_spec"]["time_range"]["start"] == "2023-01-01"
    assert body["analysis_spec"]["time_range"]["start_precision"] == "year"
    assert body["change_detection"]["before"]["id"] == "same-season"
    assert body["change_detection"]["season_gap_days"] <= 4
    assert fake.searches[1][0] == target - timedelta(days=45)


def test_stated_month_is_respected(client, deepseek, imagery):

    fake = imagery({
        "june": {"day": date(2023, 6, 10), "cloudy": False},
        "after": {"day": date.today() - timedelta(days=5), "cloudy": False},
    })

    deepseek(planner_reply("construction_progress", time_range={"start": "2023-06"}))

    body = client.post("/analyze", json={"query": "What changed since June 2023?"}).json()

    assert body["analysis_spec"]["time_range"]["start_precision"] == "month"
    assert body["change_detection"]["before"]["date"] == "2023-06-10"
    assert fake.searches[1][0] == date(2023, 6, 1) - timedelta(days=45)


@pytest.mark.parametrize("raw,precision", [
    ({"start": "2019"}, "year"),
    ({"start": "2019-06"}, "month"),
    ({"start": "2019-06-15"}, "day"),
    ({"years_back": 5}, "day"),
    ({"months_back": 3}, "day"),
    ({"end": "2022"}, None),
    (None, None),
])
def test_time_range_precision(raw, precision):

    assert parse_time_range(raw, today=TODAY).start_precision == precision


# ============================================================
# SCORING
# ============================================================

def _change_candidate(delta, area, change_type="vegetation_loss"):

    rule = cd.RULES[change_type]

    scene = {"date": "2026-01-01", "clear_fraction": 1.0}

    return {"area_m2": area, "change": {
        "index": rule["index"], "before_mean": 0.8, "after_mean": 0.8 + delta,
        "delta_mean": delta, "min_change": rule["min_change"],
        "full_scale": rule["full_scale"], "pixels": area // 100,
        "before": scene, "after": scene, "season_gap_days": 0,
    }}


@pytest.mark.parametrize("delta,expected", [(-0.25, 30), (-0.525, 65), (-0.8, 100), (-1.2, 100)])
def test_change_strength_scale(delta, expected):

    from criteria import change_magnitude

    assert change_magnitude(_change_candidate(delta, 1000), None, None)["score"] == pytest.approx(expected)


@pytest.mark.parametrize("area,expected", [(500, 20), (100_000, 100), (1_000_000, 100)])
def test_changed_area_scale(area, expected):

    from criteria import changed_area

    assert changed_area(_change_candidate(-0.4, area), None, None)["score"] == pytest.approx(expected)


def test_change_scores_discriminate_between_strong_changes():
    """
    Strong, large changes must not all collapse to the same score.
    """

    from criteria import change_magnitude, changed_area

    small = changed_area(_change_candidate(-0.4, 5_000), None, None)["score"]
    large = changed_area(_change_candidate(-0.4, 40_000), None, None)["score"]

    strong = change_magnitude(_change_candidate(-0.45, 1000), None, None)["score"]
    stronger = change_magnitude(_change_candidate(-0.7, 1000), None, None)["score"]

    assert small < large < 100
    assert strong < stronger < 100


# ============================================================
# LANDSAT AND TILES
# ============================================================

def test_landsat_reflectance_scaling():

    dn = np.array([[0, 7273, 21818]], dtype="uint16")

    out = cd.landsat_reflectance(dn)

    assert np.isnan(out[0, 0])
    assert out[0, 1:].tolist() == pytest.approx([0.0, 0.4], abs=1e-3)


@pytest.mark.parametrize("qa,clear", [
    (64, True),        # clear
    (128, True),       # water
    (1, False),        # fill
    (64 | 2, False),   # dilated cloud
    (64 | 4, False),   # cirrus
    (8, False),        # cloud
    (64 | 16, False),  # cloud shadow
    (0, False),        # nothing flagged clear
])
def test_landsat_clear_mask(qa, clear):

    assert cd.landsat_clear_mask(np.array([[qa]], dtype="uint16"))[0, 0] == clear


@pytest.mark.parametrize("target,sensor", [
    (date(2025, 10, 1), "sentinel-2"),
    (date(2017, 3, 1), "sentinel-2"),
    (date(2017, 1, 20), "landsat"),   # season window reaches into 2016
    (date(2010, 1, 1), "landsat"),
    (date(1990, 6, 1), "landsat"),
])
def test_sensor_choice(target, sensor):

    assert cd.choose_sensor(target).id == sensor


def test_tiles_from_the_same_day_are_combined():
    """
    An area on a tile edge: each tile covers half of it. Read alone,
    neither reaches the 60% clear threshold; combined they cover all.
    """

    day_before, day_after = date(2025, 9, 28), date(2026, 9, 26)

    imagery = FakeImagery({
        "before-w": {"day": day_before, "cloudy": False, "covers": "west"},
        "before-e": {"day": day_before, "cloudy": False, "covers": "east"},
        "after-w": {"day": day_after, "cloudy": False, "covers": "west", "cleared": "all"},
        "after-e": {"day": day_after, "cloudy": False, "covers": "east", "cleared": "all"},
    })

    result = cd.detect_changes(AREA, today=TODAY, search=imagery.search, reader=imagery.reader)

    assert result.clear_fraction_both == pytest.approx(1.0, abs=0.01)
    assert len(result.after["items"]) == 2
    assert result.after["id"] == "+".join(result.after["items"])

    # The whole area changed: one patch, both halves included.
    assert len(result.observations) == 1
    assert result.observations[0]["area_m2"] == pytest.approx(result.area_km2 * 1e6, rel=0.02)


def test_half_covered_area_is_not_enough():

    imagery = FakeImagery({
        "after-w": {"day": date(2026, 9, 26), "cloudy": False, "covers": "west"},
    })

    with pytest.raises(cd.ChangeDataUnavailable):
        cd.detect_changes(AREA, today=TODAY, search=imagery.search, reader=imagery.reader)


def test_pre_2017_comparison_uses_landsat_for_both_images():

    imagery = FakeImagery({
        "l5": {"day": date(2010, 1, 20), "cloudy": False, "platform": "landsat-5"},
        "l9": {"day": date(2026, 9, 20), "cloudy": False, "platform": "landsat-9", "cleared": "all"},
        # A clear Sentinel-2 image exists too, but is never mixed in.
        "s2": {"day": date(2026, 9, 26), "cloudy": False},
    })

    result = cd.detect_changes(AREA, before_date=date(2010, 1, 1), today=TODAY,
                               search=imagery.search, reader=imagery.reader)

    assert result.sensor == "Landsat"
    assert result.resolution_m == 30
    assert result.source == "landsat_c2_l2_planetary_computer"
    assert result.before["platform"] == "landsat-5"
    assert result.after["platform"] == "landsat-9"
    assert "30 m" in result.method and "4,500 m²" in result.method

    obs = result.observations[0]

    assert obs["change_type"] == "built_or_bare_increase"
    assert obs["area_m2"] % 900 == 0
    assert not any(h.startswith("s2/") for h in imagery.reads)


def test_landsat_7_gap_years_are_a_last_resort():

    imagery = FakeImagery({
        # Closest to the 2012 target, but Landsat 7 after 2003.
        "l7": {"day": date(2012, 1, 2), "cloudy": False, "platform": "landsat-7"},
        "l5": {"day": date(2011, 11, 25), "cloudy": False, "platform": "landsat-5"},
        "l8": {"day": date(2026, 9, 20), "cloudy": False, "platform": "landsat-8"},
    })

    result = cd.detect_changes(AREA, before_date=date(2012, 1, 1), today=TODAY,
                               search=imagery.search, reader=imagery.reader)

    assert result.before["platform"] == "landsat-5"
    assert result.before["last_resort"] is False


def test_landsat_7_used_when_nothing_else_is_clear():

    imagery = FakeImagery({
        "l7": {"day": date(2012, 1, 2), "cloudy": False, "platform": "landsat-7"},
        "l5-cloudy": {"day": date(2011, 12, 20), "cloudy": True, "platform": "landsat-5"},
        "l8": {"day": date(2026, 9, 20), "cloudy": False, "platform": "landsat-8"},
    })

    result = cd.detect_changes(AREA, before_date=date(2012, 1, 1), today=TODAY,
                               search=imagery.search, reader=imagery.reader)

    assert result.before["platform"] == "landsat-7"
    assert result.before["last_resort"] is True


def test_analyze_change_since_2010_uses_landsat(client, deepseek, imagery):

    after_day = date.today() - timedelta(days=6)

    imagery({
        "l5": {"day": cd.same_season(2010, after_day), "cloudy": False, "platform": "landsat-5"},
        "l9": {"day": after_day, "cloudy": False,
               "platform": "landsat-9", "cleared": "all"},
    })

    deepseek(planner_reply("construction_progress",
                           time_range={"start": "2010", "as_stated": "since 2010"}))

    body = client.post("/analyze", json={"query": "What changed since 2010?"}).json()

    assert body["status"] == "success"

    meta = body["change_detection"]

    assert meta["sensor"] == "Landsat"
    assert meta["resolution_m"] == 30

    change = body["top_prospects"][0]

    item = change["criteria"]["change_magnitude"]["evidence_items"][0]

    assert item["source_id"] == "landsat_c2_l2_planetary_computer"
    assert item["source"] == "Microsoft Planetary Computer (Landsat)"
    assert "pixels at 30 m" in change["criteria"]["changed_area"]["evidence"]


def test_landsat_7_last_resort_is_stated_in_limitations(client, deepseek, imagery):

    imagery({
        "l7": {"day": date(2012, 1, 2), "cloudy": False, "platform": "landsat-7"},
        "l8": {"day": date.today() - timedelta(days=6), "cloudy": False, "platform": "landsat-8"},
    })

    deepseek(planner_reply("construction_progress", time_range={"start": "2012-01-01"}))

    body = client.post("/analyze", json={"query": "What changed since 2012?"}).json()

    assert body["change_detection"]["before"]["last_resort"] is True
    assert any("Landsat 7 image" in l for l in body["limitations"])
    assert any(l.startswith("Compared Landsat images (30 m)") for l in body["limitations"])
