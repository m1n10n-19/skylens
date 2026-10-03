"""
Feedback on results and sites: validation, storage with the context it
was given for, the visitor limit, and reading it back (team only).
"""

import os
import re

import pytest

import feedback


SITE_CONTEXT = {
    "query": "10 cents of empty land in Velachery for food court",
    "use_case": "commercial_site_selection",
    "status": "complete",
    "site": {"candidate_id": "landcover-3", "lat": 12.98, "lon": 80.22, "score": 71},
}


def _site_feedback(**changes):

    body = {"scope": "site", "verdict": "down", "tags": ["has_building"],
            "comment": "There is a house here", "site_id": "landcover-3", "context": SITE_CONTEXT}
    body.update(changes)
    return body


@pytest.fixture
def team_token(client, monkeypatch):

    monkeypatch.setenv("SKYLENS_USERNAME", "team")
    monkeypatch.setenv("SKYLENS_PASSWORD", "test-password")

    return client.post("/auth/login", json={"username": "team", "password": "test-password"}).json()["token"]


def test_site_feedback_is_stored_with_its_context(client, team_token):

    response = client.post("/feedback", json=_site_feedback())

    assert response.status_code == 200

    body = client.get("/feedback", headers={"Authorization": f"Bearer {team_token}"}).json()

    assert body["storage"] == "sqlite" and body["count"] == 1

    entry = body["entries"][0]

    assert entry["id"] == response.json()["id"]
    assert entry["scope"] == "site" and entry["site_id"] == "landcover-3"
    assert entry["tags"] == ["has_building"] and entry["verdict"] == "down"
    assert entry["query"] == SITE_CONTEXT["query"]
    assert entry["use_case"] == "commercial_site_selection"
    assert entry["context"]["site"]["lat"] == 12.98
    assert entry["logged_in"] is False
    assert entry["created_at"] and entry["app_version"]


def test_result_feedback_needs_no_site(client, team_token):

    client.post("/feedback", json={"scope": "result", "verdict": "up", "context": {"query": "q"}})

    entry = client.get("/feedback", headers={"Authorization": f"Bearer {team_token}"}).json()["entries"][0]

    assert entry["scope"] == "result" and entry["site_id"] is None


def test_signed_in_feedback_is_marked(client, team_token):

    client.post("/feedback", json=_site_feedback(), headers={"Authorization": f"Bearer {team_token}"})

    entry = client.get("/feedback", headers={"Authorization": f"Bearer {team_token}"}).json()["entries"][0]

    assert entry["logged_in"] is True


@pytest.mark.parametrize("changes,message", [
    ({"scope": "page"}, "result or a site"),
    ({"verdict": "meh"}, "Unknown rating"),
    ({"tags": ["made_up"]}, "Unknown tag"),
    ({"site_id": None}, "needs the site"),
    ({"verdict": None, "tags": [], "comment": "  "}, "Choose a rating"),
    ({"comment": "x" * (feedback.MAX_COMMENT + 1)}, "limited to"),
])
def test_invalid_feedback_is_rejected(client, changes, message):

    response = client.post("/feedback", json=_site_feedback(**changes))

    assert response.status_code == 422
    assert message in response.json()["detail"]["error"]


def test_oversized_context_is_rejected(client):

    big = {"query": "q", "blob": "x" * (feedback.MAX_CONTEXT_BYTES + 10)}

    assert client.post("/feedback", json=_site_feedback(context=big)).status_code == 413


def test_reading_feedback_needs_sign_in(client):

    assert client.get("/feedback").status_code == 401


def test_visitors_have_a_daily_limit(client, monkeypatch):

    monkeypatch.setenv("FEEDBACK_PER_IP", "2")

    codes = [client.post("/feedback", json=_site_feedback()).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_team_members_are_not_limited(client, team_token, monkeypatch):

    monkeypatch.setenv("FEEDBACK_PER_IP", "1")

    headers = {"Authorization": f"Bearer {team_token}"}

    codes = [client.post("/feedback", json=_site_feedback(), headers=headers).status_code for _ in range(3)]

    assert codes == [200, 200, 200]


def test_storage_failure_is_503_and_names_no_internals(client, monkeypatch):

    def down(entry):
        raise ConnectionError("password authentication failed for user neondb")

    monkeypatch.setattr(feedback, "save", down)

    response = client.post("/feedback", json=_site_feedback())

    assert response.status_code == 503
    assert "neondb" not in response.text


def test_since_id_pages_through_entries(client, team_token):

    for _ in range(3):
        client.post("/feedback", json=_site_feedback())

    headers = {"Authorization": f"Bearer {team_token}"}

    first = client.get("/feedback?limit=2", headers=headers).json()["entries"]
    rest = client.get(f"/feedback?since_id={first[-1]['id']}", headers=headers).json()["entries"]

    assert len(first) == 2 and len(rest) == 1


def test_database_url_selects_postgres(monkeypatch):

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")

    assert feedback.storage_name() == "postgres"


def test_frontend_offers_exactly_the_backend_tags():

    path = os.path.join(feedback.BASE_DIR, "frontend", "js", "feedback.js")

    with open(path, encoding="utf-8") as f:
        source = f.read()

    block = re.search(r"SL\.FEEDBACK_TAGS = \[(.*?)\];", source, re.S).group(1)

    assert re.findall(r'\["(\w+)"', block) == list(feedback.TAGS)


def test_report_groups_tags_and_repeated_sites(client):

    import importlib.util

    for _ in range(2):
        client.post("/feedback", json=_site_feedback())

    client.post("/feedback", json={"scope": "result", "verdict": "up", "context": {"query": "q"}})

    path = os.path.join(feedback.BASE_DIR, "scripts", "feedback_report.py")
    spec = importlib.util.spec_from_file_location("feedback_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = module.report(module.from_database(0))

    assert "# Feedback: 3 entries" in text
    assert "| Has a building | 2 |" in text
    assert "landcover-3: 2 reports" in text
    assert '"There is a house here"' in text
