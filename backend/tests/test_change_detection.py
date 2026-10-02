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

from tests.conftest import LAT, LON, planner_reply


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
        "B03": np.array([[0.05]], dtype="float32"),
        "B04": np.array([[0.05]], dtype="float32"),
        "B08": np.array([[0.45]], dtype="float32"),
        "B11": np.array([[0.15]], dtype="float32"),
    }

    idx = cd.spectral_indices(bands)

    assert idx["NDVI"][0, 0] == pytest.approx(0.8)
    assert idx["NDBI"][0, 0] == pytest.approx(-0.5)
    assert idx["NDWI"][0, 0] == pytest.approx(-0.8)


def test_zero_reflectance_is_nan_not_zero():

    zero = np.zeros((1, 1), dtype="float32")

    idx = cd.spectral_indices({"B03": zero, "B04": zero, "B08": zero, "B11": zero})

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

class FakeAsset:

    def __init__(self, href):
        self.href = href


class FakeItem:

    def __init__(self, scene_id, day, cloud=5.0, baseline="05.11"):
        self.id = scene_id
        self.datetime = datetime(day.year, day.month, day.day, 5, tzinfo=timezone.utc)
        self.properties = {"eo:cloud_cover": cloud, "s2:processing_baseline": baseline}
        self.assets = {b: FakeAsset(f"{scene_id}/{b}") for b in ("B03", "B04", "B08", "B11", "SCL")}


class FakeImagery:
    """
    search(bbox, start, end) and reader(href, grid) over scenes defined
    as {scene_id: {"day": date, "cloudy": bool, "cleared": bool}}.
    "cleared" scenes have bare ground in the north-west quarter where
    the others have dense vegetation.
    """

    def __init__(self, scenes):
        self.scenes = scenes
        self.reads = []
        self.searches = []

    def search(self, bbox, start, end):
        self.searches.append((start, end))
        return [FakeItem(sid, s["day"]) for sid, s in self.scenes.items() if start <= s["day"] <= end]

    def reader(self, href, grid):
        scene_id, band = href.split("/")
        self.reads.append(href)
        scene = self.scenes[scene_id]
        h, w = grid.height, grid.width
        if band == "SCL":
            return np.full((h, w), 9 if scene["cloudy"] else 4, dtype="uint8")
        # DN with the 1000 offset (baseline 05.11): dense vegetation.
        value = {"B03": 1500, "B04": 1400, "B08": 5000, "B11": 2500}[band]
        out = np.full((h, w), value, dtype="uint16")
        if scene.get("cleared"):
            bare = {"B03": 2200, "B04": 2600, "B08": 3000, "B11": 4200}[band]
            out[: h // 2, : w // 2] = bare
        return out


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

@pytest.fixture
def imagery(monkeypatch):

    def install(scenes):
        fake = FakeImagery(scenes)
        monkeypatch.setattr(cd, "search_scenes", fake.search)
        monkeypatch.setattr(cd, "read_band", fake.reader)
        return fake

    return install


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
    assert any("Compared Sentinel-2 scenes" in l for l in body["limitations"])
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

    fake = imagery({
        "old": {"day": date(2023, 1, 15), "cloudy": False},
        "after": {"day": date.today() - timedelta(days=5), "cloudy": False},
    })

    deepseek(planner_reply("construction_progress",
                           time_range={"start": "2023", "as_stated": "since 2023"}))

    body = client.post("/analyze", json={"query": "What changed since 2023?"}).json()

    assert body["analysis_spec"]["time_range"]["start"] == "2023-01-01"
    assert body["change_detection"]["before"]["date"] == "2023-01-15"
    assert fake.searches[1][0] == date(2023, 1, 1) - timedelta(days=45)


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
