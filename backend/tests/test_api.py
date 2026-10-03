"""
Existing endpoints keep working: response shapes, status values,
SSE progress events, error states, login and the question limit.
"""

import json

import pytest

import main
import pipeline

from overpass import OverpassError

from tests.conftest import (
    FAKE_SCENE,
    LAT,
    LON,
    FakeGeolocator,
    building_elements,
    huge_place_raw,
    land_elements,
    planner_reply,
)


# ============================================================
# SIMPLE ENDPOINTS
# ============================================================

def test_health(client):

    assert client.get("/health").json()["status"] == "ok"


def test_use_cases_lists_registry(client):

    ids = [u["id"] for u in client.get("/use-cases").json()["use_cases"]]

    assert ids == [
        "solar_prospecting",
        "ev_charging_site_selection",
        "commercial_site_selection",
        "land_acquisition",
        "construction_progress",
        "infrastructure_outlook",
    ]


def test_geocode(client):

    body = client.get("/geocode", params={"location": "Adyar"}).json()

    assert body["latitude"] == LAT
    assert body["longitude"] == LON
    assert body["location"] == "Adyar"


def test_geocode_not_found(client, geocoder):

    geocoder(False)

    response = client.get("/geocode", params={"location": "Nowhere"})

    assert response.status_code == 404
    assert response.json()["detail"]["stage"] == "geocoding"


def test_satellite_search(client, monkeypatch):

    monkeypatch.setattr(main, "search_satellite", lambda **kw: [dict(FAKE_SCENE)])

    body = client.get("/satellite", params={"latitude": LAT, "longitude": LON}).json()

    assert body["imagery_count"] == 1
    assert body["imagery"][0]["id"] == FAKE_SCENE["id"]


def test_satellite_latest(client, monkeypatch):

    monkeypatch.setattr(main, "get_latest_satellite", lambda **kw: dict(FAKE_SCENE))

    body = client.get("/satellite/latest", params={"latitude": LAT, "longitude": LON}).json()

    assert body["date"] == FAKE_SCENE["date"]
    assert body["cloud_cover"] == FAKE_SCENE["cloud_cover"]


def test_satellite_latest_none_is_404(client, monkeypatch):

    monkeypatch.setattr(main, "get_latest_satellite", lambda **kw: None)

    response = client.get("/satellite/latest", params={"latitude": LAT, "longitude": LON})

    assert response.status_code == 404


def test_satellite_provider_error_is_502(client, monkeypatch):

    def boom(**kw):
        raise TimeoutError("planetary computer down")

    monkeypatch.setattr(main, "search_satellite", boom)

    response = client.get("/satellite", params={"latitude": LAT, "longitude": LON})

    assert response.status_code == 502
    assert response.json()["detail"]["error_type"] == "TimeoutError"


def test_buildings(client, overpass):

    overpass(building_elements())

    body = client.get("/buildings", params={"latitude": LAT, "longitude": LON}).json()

    assert body["candidate_count"] == 5
    assert all("_polygon" not in c for c in body["candidates"])

    areas = [c["area_m2"] for c in body["candidates"]]

    assert areas == sorted(areas, reverse=True)


def test_buildings_overpass_failure_is_502(client, overpass):

    overpass(error=OverpassError("busy"))

    response = client.get("/buildings", params={"latitude": LAT, "longitude": LON})

    assert response.status_code == 502
    assert response.json()["detail"]["stage"] == "building_data"


# ============================================================
# PLANNING
# ============================================================

def test_plan_returns_spec_without_collecting_data(client, deepseek):

    deepseek(planner_reply(
        "ev_charging_site_selection",
        requirements={"area": {"target": 10, "unit": "cent", "as_stated": "10 cent"}},
    ))

    body = client.post("/plan", json={"query": "10 cent plots for EV in Adyar"}).json()

    assert body["supported"] is True
    assert body["use_case"]["id"] == "ev_charging_site_selection"
    assert body["analysis_spec"]["area"]["target_m2"] == 404.7
    assert body["analysis_spec"]["area"]["as_stated"] == "10 cent"


def test_intent_passes_through_model_json(client, deepseek):

    deepseek({"intent_type": "solar_prospecting", "location": "Adyar"})

    body = client.post("/intent", json={"query": "solar in Adyar"}).json()

    assert body == {"intent_type": "solar_prospecting", "location": "Adyar"}


# ============================================================
# ANALYZE: STATUS VALUES
# ============================================================

def test_analyze_unsupported_use_case(client, deepseek):

    deepseek(planner_reply("railway_inspection"))

    body = client.post("/analyze", json={"query": "inspect railways"}).json()

    assert body["status"] == "unsupported_use_case"
    assert body["detected_requirements"]["intent_type"] == "railway_inspection"
    assert len(body["supported_use_cases"]) == 6


def test_analyze_not_yet_implemented_produces_no_result(client, deepseek, monkeypatch):

    import dataclasses
    import use_cases

    # Every registered module is implemented now; register a placeholder.
    placeholder = dataclasses.replace(
        use_cases.LAND_ACQUISITION, id="future_module", implemented=False,
        candidate_source=None, data_layers=("zoning",), aliases=(),
    )

    monkeypatch.setitem(use_cases.USE_CASES, "future_module", placeholder)

    deepseek(planner_reply("future_module"))

    body = client.post("/analyze", json={"query": "something new"}).json()

    assert body["status"] == "not_yet_implemented"
    assert "top_prospects" not in body
    assert "score" not in body
    assert "Zoning / permitted land use" in body["missing_data"]


def test_analyze_area_too_large(client, deepseek, geocoder):

    deepseek(planner_reply("land_acquisition", location="Tamil Nadu"))
    geocoder(huge_place_raw())

    body = client.post("/analyze", json={"query": "land in Tamil Nadu"}).json()

    assert body["status"] == "area_too_large"
    assert body["area_km2"] > body["max_area_km2"]
    assert "top_prospects" not in body


def test_analyze_without_location_is_400(client, deepseek):

    deepseek(planner_reply("land_acquisition", location=None))

    response = client.post("/analyze", json={"query": "find land"})

    assert response.status_code == 400
    assert response.json()["detail"]["stage"] == "location_extraction"


def test_analyze_map_data_failure_is_502_with_stage(client, deepseek, overpass):

    deepseek(planner_reply("land_acquisition"))
    overpass(error=OverpassError("Map data provider (Overpass) is busy"))

    response = client.post("/analyze", json={"query": "land in Adyar"})

    assert response.status_code == 502
    assert response.json()["detail"]["stage"] == "candidate_data"


def test_analyze_bad_model_json_is_502(client, monkeypatch):

    monkeypatch.setattr(main, "call_deepseek_json", lambda s, p: ["not", "a", "dict"])

    response = client.post("/analyze", json={"query": "x"})

    assert response.status_code == 502
    assert response.json()["detail"]["stage"] == "deepseek_json"


# ============================================================
# ANALYZE: SUCCESSFUL RESULTS
# ============================================================

SUCCESS_KEYS = {
    "status", "query", "intent", "analysis_spec", "use_case",
    "resolved_location", "search_area", "satellite", "evidence",
    "analysis", "top_prospects", "decision", "confidence",
    "missing_data", "limitations",
}


def test_analyze_solar_success_shape(client, deepseek, overpass):

    deepseek(planner_reply("solar_prospecting", industry="solar"))
    overpass(building_elements())

    body = client.post("/analyze", json={"query": "solar roofs in Adyar"}).json()

    assert body["status"] == "success"
    assert SUCCESS_KEYS <= set(body)

    top = body["top_prospects"]

    assert [p["rank"] for p in top] == list(range(1, len(top) + 1))
    assert body["analysis"]["total_candidates"] == 5

    for p in top:
        assert not any(key.startswith("_") for key in p)
        assert p["geometry"]["type"] == "Polygon"
        # Irradiance, shading and accessibility are not measured.
        for missing in ("solar_suitability", "shading", "accessibility",
                        "roof_condition", "ownership"):
            assert missing in p["missing_data"]
        assert p["evidence_coverage"] == 0.5
        assert p["confidence"] in ("low", "medium")  # capped by coverage

    layers = {e["id"]: e["status"] for e in body["evidence"]}

    assert layers == {"satellite_imagery": "used", "building_footprints": "used"}
    assert body["satellite"]["id"] == FAKE_SCENE["id"]


def test_analyze_ev_measures_available_layers_only(client, deepseek, overpass):
    """
    Without imagery, terrain or flood data (all blocked in tests), the
    criteria that need them are left out, not scored as zero.
    """

    deepseek(planner_reply(
        "ev_charging_site_selection",
        requirements={"area": {"target": 30, "unit": "cent", "as_stated": "30 cents"}},
    ))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "30 cent plots for EV in Adyar"}).json()

    assert body["status"] == "success"

    best = body["top_prospects"][0]

    assert best["osm_id"] == 2001
    assert best["criteria"]["road_access"]["available"] is True
    assert best["measurements"]["nearest_road_m"] == 10

    flood = best["criteria"]["flood_risk"]

    assert flood["available"] is False
    assert flood["state"] == "data_not_loaded"
    assert flood["score"] is None
    assert "flood_risk" in best["missing_data"]
    assert best["evidence_coverage"] < 1

    layers = {e["id"]: e["status"] for e in body["evidence"]}

    assert layers["flood_risk"] == "not_loaded"
    assert layers["roads"] == "used"
    assert body["completeness"]["status"] == "partial"


def test_analyze_ranking_is_deterministic(client, deepseek, overpass):

    deepseek(planner_reply("land_acquisition"))
    overpass(land_elements())

    first = client.post("/analyze", json={"query": "land in Adyar"}).json()
    second = client.post("/analyze", json={"query": "land in Adyar"}).json()

    assert first["top_prospects"] == second["top_prospects"]


def test_analyze_no_candidates_is_honest(client, deepseek, overpass):

    deepseek(planner_reply("land_acquisition"))
    overpass([])

    body = client.post("/analyze", json={"query": "land in Adyar"}).json()

    assert body["status"] == "success"
    assert body["top_prospects"] == []
    assert body["confidence"] == "low"
    assert "does not prove none exists" in body["decision"]["summary"]
    assert "land_use_compatibility" in body["missing_data"]


def test_satellite_failure_degrades_gracefully(client, deepseek, overpass, monkeypatch):

    deepseek(planner_reply("solar_prospecting"))
    overpass(building_elements())

    def boom(**kw):
        raise TimeoutError("stac down")

    monkeypatch.setattr(pipeline, "get_latest_satellite", boom)

    body = client.post("/analyze", json={"query": "solar in Adyar"}).json()

    assert body["status"] == "success"
    assert body["satellite"] is None

    imagery = next(e for e in body["evidence"] if e["id"] == "satellite_imagery")

    assert imagery["status"] == "failed"
    assert "TimeoutError" in imagery["detail"]
    assert any("satellite scene" in item for item in body["limitations"])


def test_client_radius_overrides_planner(client, deepseek, overpass):

    deepseek(planner_reply("land_acquisition"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "land in Adyar", "radius_km": 0.5}).json()

    assert body["search_area"]["radius_km"] == 0.5


# ============================================================
# STREAMING
# ============================================================

def _events(response):

    events = []

    for block in response.text.strip().split("\n\n"):

        lines = dict(line.split(": ", 1) for line in block.splitlines())

        events.append((lines["event"], json.loads(lines["data"])))

    return events


def test_stream_emits_progress_then_result(client, deepseek, overpass):

    deepseek(planner_reply("land_acquisition"))
    overpass(land_elements())

    response = client.post("/analyze/stream", json={"query": "land in Adyar"})

    assert response.headers["content-type"].startswith("text/event-stream")

    events = _events(response)

    names = [name for name, _ in events]

    steps = [(d["step"], d["status"]) for name, d in events if name == "step"]

    assert steps == [
        ("intent", "done"),
        ("location", "done"),
        ("evidence", "done"),
        ("candidates", "running"),
        ("candidates", "done"),
        ("scoring", "running"),
        ("scoring", "done"),
        ("decision", "done"),
    ]

    assert "layer" in names
    assert "candidates" in names
    assert names[-1] == "result"

    result = events[-1][1]

    assert result["status"] == "success"

    preview = next(d for name, d in events if name == "candidates")

    assert preview["count"] == 2
    assert len(preview["preview"]) == 2


def test_stream_reports_errors_as_events(client, deepseek, overpass):

    deepseek(planner_reply("land_acquisition"))
    overpass(error=OverpassError("busy"))

    events = _events(client.post("/analyze/stream", json={"query": "land in Adyar"}))

    name, data = events[-1]

    assert name == "error"
    assert data["status_code"] == 502
    assert data["detail"]["stage"] == "candidate_data"


def test_stream_unsupported_is_a_result(client, deepseek):

    deepseek(planner_reply("railway_inspection"))

    events = _events(client.post("/analyze/stream", json={"query": "railways"}))

    assert events[0][1]["implemented"] is False
    assert events[-1][0] == "result"
    assert events[-1][1]["status"] == "unsupported_use_case"


# ============================================================
# LOGIN AND QUESTION LIMIT
# ============================================================

@pytest.fixture
def team_login(monkeypatch):

    monkeypatch.setenv("SKYLENS_USERNAME", "team")
    monkeypatch.setenv("SKYLENS_PASSWORD", "test-password")


def test_login_not_configured_is_503(client):

    response = client.post("/auth/login", json={"username": "a", "password": "b"})

    assert response.status_code == 503


def test_login_and_me(client, team_login):

    token = client.post("/auth/login", json={
        "username": "team", "password": "test-password",
    }).json()["token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()

    assert me == {"login_enabled": True, "username": "team", "unlimited": True}

    anon = client.get("/auth/me").json()

    assert anon["username"] is None


def test_wrong_password_is_401(client, team_login, monkeypatch):

    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    response = client.post("/auth/login", json={"username": "team", "password": "nope"})

    assert response.status_code == 401


def test_tampered_token_is_rejected(client, team_login):

    token = client.post("/auth/login", json={
        "username": "team", "password": "test-password",
    }).json()["token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}x"}).json()

    assert me["username"] is None


def test_anonymous_question_limit(client, deepseek, monkeypatch):

    monkeypatch.setenv("RATE_LIMIT_PER_IP", "2")

    deepseek(planner_reply("railway_inspection"))

    for _ in range(2):
        assert client.post("/plan", json={"query": "q"}).status_code == 200

    response = client.post("/plan", json={"query": "q"})

    assert response.status_code == 429
    assert response.json()["detail"]["stage"] == "rate_limit"
    assert int(response.headers["Retry-After"]) > 0

    stream = client.post("/analyze/stream", json={"query": "q"})

    assert stream.status_code == 429


def test_signed_in_users_are_not_limited(client, deepseek, team_login, monkeypatch):

    monkeypatch.setenv("RATE_LIMIT_PER_IP", "1")

    deepseek(planner_reply("railway_inspection"))

    token = client.post("/auth/login", json={
        "username": "team", "password": "test-password",
    }).json()["token"]

    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(3):
        assert client.post("/plan", json={"query": "q"}, headers=headers).status_code == 200
