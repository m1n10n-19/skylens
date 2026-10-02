"""
Infrastructure from OpenStreetMap: existing infrastructure, projects
under construction or proposed, per-site evidence (not scored) and the
"what infrastructure is coming near X?" area report.
"""

import pytest

from shapely.geometry import LineString, Point, box

import infrastructure as infra

from geodata import LocalProjection
from tests.conftest import LAT, LON, line, land_elements, offset, planner_reply, square


# ============================================================
# CLASSIFY AND PARSE
# ============================================================

@pytest.mark.parametrize("tags,expected", [
    ({"railway": "station", "station": "subway"}, ("rail_station", "existing", "metro")),
    ({"railway": "station", "construction": "yes"}, ("rail_station", "under_construction", None)),
    ({"railway": "construction", "construction": "subway"}, ("rail_project", "under_construction", "subway")),
    ({"highway": "proposed", "proposed": "primary"}, ("road_project", "proposed", "primary")),
    ({"highway": "trunk"}, ("major_road", "existing", "trunk")),
    ({"highway": "residential"}, None),
    ({"power": "line", "voltage": "230000"}, ("power_line", "existing", "230000")),
    ({"power": "substation"}, ("substation", "existing", None)),
    ({"aeroway": "aerodrome"}, ("airport", "existing", None)),
    ({"amenity": "bus_station"}, ("bus_station", "existing", None)),
    ({"landuse": "construction", "name": "Sattva Knowledge City", "construction": "commercial"},
     ("development", "under_construction", "commercial")),
    ({"landuse": "construction"}, None),          # unnamed construction sites are too noisy
    # Parts of a campus, not developments.
    ({"building": "construction", "name": "Tower 3", "construction": "commercial"}, None),
    ({"building": "construction", "name": "T3 (Under Construction)"}, None),
    ({"building": "construction", "name": "Utility"}, None),
    # Unnamed works on minor roads are mostly resurfacing; on major roads they are listed.
    ({"highway": "construction"}, None),
    ({"highway": "construction", "construction": "residential"}, None),
    ({"highway": "construction", "construction": "primary"}, ("road_project", "under_construction", "primary")),
    ({"highway": "construction", "name": "Pandian Salai"}, ("road_project", "under_construction", None)),
])
def test_classify(tags, expected):

    assert infra.classify(tags) == expected


def test_both_directions_of_a_line_are_one_project():

    assert infra.project_key("Line 5: Madhavaram → Shozhinganallur (u/c)") == \
        infra.project_key("Line 5: Shozhinganallur → Madhavaram (u/c)")
    assert infra.project_key("Line 4") != infra.project_key("Line 5")

    # Spelling variants and sections of one line.
    assert infra.project_key("Teynampet - Saidapet steel flyover") == \
        infra.project_key("Teynampet-Saidapet steel flyover")
    assert infra.project_key("Yellow Line (Underground1): Light House → Poonamallee (u/c)") == \
        infra.project_key("Yellow Line (Underground 2): Poonamallee → Light House (u/c)")


ELEMENTS = [
    {"type": "node", "id": 1, "lat": offset(0, 600)[0], "lon": offset(0, 600)[1],
     "tags": {"railway": "station", "station": "subway", "name": "Velachery"}},
    {"type": "way", "id": 2, "tags": {"power": "line", "voltage": "110000"},
     "geometry": line((-500, 30), (500, 30))},
    {"type": "way", "id": 3, "tags": {"railway": "construction", "construction": "subway",
                                      "name": "Line 5: A → B (u/c)"},
     "geometry": line((1000, -2000), (1000, 2000))},
    {"type": "way", "id": 4, "tags": {"railway": "construction", "construction": "subway",
                                      "name": "Line 5: B → A (u/c)"},
     "geometry": line((1010, -2000), (1010, 2000))},
    {"type": "way", "id": 5, "tags": {"highway": "proposed", "proposed": "trunk", "name": "Outer link"},
     "geometry": line((-3000, -100), (-3000, 100))},
    {"type": "node", "id": 6, "lat": offset(0, 30000)[0], "lon": offset(0, 30000)[1],
     "tags": {"aeroway": "aerodrome", "name": "Far Airport"}},
]


def _features():

    return infra.parse({"elements": ELEMENTS}, LocalProjection(LAT, LON))


def test_parse_builds_metric_shapes():

    features = _features()

    kinds = sorted((f.kind, f.status) for f in features)

    assert ("power_line", "existing") in kinds
    assert all(not f.shape.is_empty for f in features)


def test_projects_near_lists_each_project_once_nearest_first():

    near = infra.projects_near(_features(), Point(0, 0))

    assert [f.name for _, f in near] == ["Line 5: A → B (u/c)", "Outer link"]
    assert round(near[0][0]) == 1000


def test_nearest_respects_each_kinds_radius():

    features = _features()

    distance, station = infra.nearest(features, Point(0, 0), "rail_station")

    assert station.name == "Velachery" and round(distance) == 600

    # 30 km away: beyond the 25 km airport radius.
    assert infra.nearest(features, Point(0, 0), "airport") == (None, None)


def test_summary_reports_unknowns_and_projects():

    report = infra.summary(_features(), box(-100, -100, 100, 100), LocalProjection(LAT, LON))

    assert [p["name"] for p in report["under_construction"]] == ["Line 5: A → B (u/c)"]
    assert [p["name"] for p in report["proposed"]] == ["Outer link"]
    assert report["power_lines_in_area"] == 1
    assert any("Completion dates" in u for u in report["unknowns"])
    assert report["existing"][0]["kind"] == "rail_station"


# ============================================================
# EVIDENCE ON SITES
# ============================================================

def _context(features):

    from geodata import new_context

    context = new_context(LAT, LON, 1)

    context.meta["infrastructure_features"] = features

    context.layer_status["infrastructure"] = "loaded"

    return context


def test_power_line_over_a_site_warns():

    from criteria import infrastructure_access

    site = {"_shape": box(-20, 0, 20, 60)}   # the line runs along y = 30

    result = infrastructure_access(site, _context(_features()), None)

    assert result["measurements"]["nearest_power_line_m"] == 0
    assert result["reasons"] == ["Warning: a mapped high-tension power line crosses the site"]
    assert result["measurements"]["nearest_station_m"] == 540
    assert result["measurements"]["projects_within_5km"] == 2


def test_nothing_nearby_is_none_not_zero():

    from criteria import infrastructure_access

    result = infrastructure_access({"_shape": box(-20, -20, 20, 20)}, _context([]), None)

    assert result["measurements"]["nearest_station_m"] is None
    assert result["evidence"] == "No mapped infrastructure within the search distances"


def test_analysis_adds_infrastructure_evidence_and_power_line_check(client, deepseek, overpass, imagery,
                                                                     dem, flood_evidence, open_land):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence()
    open_land()

    # A power line across parcel 2001 (0-60 m east, 10-70 m north).
    elements = land_elements() + [{"type": "way", "id": 9001, "tags": {"power": "line"},
                                   "geometry": line((-100, 40), (200, 40))}]

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(elements)

    body = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    assert body["completeness"]["status"] == "complete"
    assert "infrastructure" in body

    site = next(p for p in body["top_prospects"] if p["osm_id"] == 2001)

    entry = site["criteria"]["infrastructure"]

    assert entry["weight"] == 0
    assert "power line crosses the site" in entry["evidence"]

    verify = {v["id"]: v for v in site["assessment"]["verify"]}

    assert verify["power_line"]["method_type"] == "records_check"


def test_infrastructure_failure_is_partial(client, deepseek, overpass, imagery, dem, flood_evidence,
                                           open_land, monkeypatch):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence()
    open_land()

    def down(*args, **kwargs):
        raise TimeoutError("overpass busy")

    monkeypatch.setattr(infra, "collect", down)

    deepseek(planner_reply("land_acquisition"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "land in Adyar"}).json()

    assert body["status"] == "success"
    assert body["completeness"]["status"] == "partial"
    assert any(r.startswith("Infrastructure could not be read") for r in body["completeness"]["reasons"])
    assert body["top_prospects"][0]["criteria"]["infrastructure"]["state"] == "data_not_loaded"


# ============================================================
# "WHAT INFRASTRUCTURE IS COMING NEAR X?"
# ============================================================

def test_infrastructure_outlook_report(client, deepseek, overpass):

    deepseek(planner_reply("infrastructure_outlook"))
    overpass(ELEMENTS)

    body = client.post("/analyze", json={"query": "What infrastructure is coming near Adyar?"}).json()

    assert body["status"] == "success"
    assert body["use_case"]["id"] == "infrastructure_outlook"
    assert body["top_prospects"] == []

    report = body["infrastructure"]

    assert [p["name"] for p in report["under_construction"]] == ["Line 5: A → B (u/c)"]
    assert report["under_construction"][0]["geometry"]["type"] == "LineString"
    assert "1 project(s) mapped as under construction and 1 as proposed" in body["decision"]["summary"]
    assert any("Completion dates" in l for l in body["limitations"])


def test_planner_routes_infrastructure_questions():

    from use_cases import resolve_use_case

    assert resolve_use_case("upcoming_infrastructure").id == "infrastructure_outlook"
