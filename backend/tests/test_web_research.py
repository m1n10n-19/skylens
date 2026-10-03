"""
Web research on request: exact quotes about the place with their
sources, never scored, never reworded; provider errors are explicit.
"""

import pytest

import web_research

from web_research import place_name, quotes_about, research, source_type


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch, tmp_path):
    """
    These tests count searches, so each gets an empty cache.
    """

    monkeypatch.setattr(web_research, "CACHE_DIR", str(tmp_path))


# ============================================================
# QUOTES AND SOURCES
# ============================================================

def test_place_name():

    assert place_name("Velachery, Chennai, Tamil Nadu, India") == ("Velachery, Chennai", "Velachery")
    assert place_name("") == (None, None)


def test_quotes_are_verbatim_sentences_that_name_the_place():

    text = ("The state cabinet met on Monday. Work on the Velachery to St. Thomas Mount MRTS "
            "extension will finish by March 2027, officials said. Residents of Adyar complained. "
            "VELACHERY lake was desilted in June.")

    assert quotes_about(text, "Velachery") == [
        "Work on the Velachery to St. Thomas Mount MRTS extension will finish by March 2027, officials said.",
        "VELACHERY lake was desilted in June.",
    ]


def test_words_containing_the_place_name_do_not_count():

    assert quotes_about("The Velacheryan festival drew large crowds this weekend.", "Velachery") == []


def test_menu_text_is_not_quoted():

    menu = ("Videos Podcast Photos Visual Stories Specials TH Explains eBooks TH Games Newsletter "
            "Lit For Life The Huddle Bridge to replace part of Velachery Road Published Chennai.")

    assert quotes_about(menu, "Velachery") == []


def test_cut_snippets_are_marked():

    assert quotes_about("Officials said 7.4 km of Velachery Road from the bridge to", "Velachery") == [
        "Officials said 7.4 km of Velachery Road from the bridge to\u2026",
    ]


@pytest.mark.parametrize("url,expected", [
    ("https://www.newindianexpress.com/states/tamil-nadu/2023/Dec/07/stench-snakes", "2023-12-07"),
    ("https://example.com/news/2024/03/15/story.html", "2024-03-15"),
    ("https://example.com/news/story-2024", None),
])
def test_dates_in_urls(url, expected):

    assert web_research.url_date(url) == expected


@pytest.mark.parametrize("topic,title,quotes,expected", [
    ("ev", "In city plan: bus hubs", ["Bus termini in Velachery and Adyar"], False),
    ("ev", "New EV charging hub", ["Velachery gets a charging hub"], True),
    ("flooding", "Rain update", ["Velachery streets were waterlogged"], True),
    ("unknown_topic", "x", ["y"], True),
])
def test_results_must_be_about_the_topic(topic, title, quotes, expected):

    assert web_research.about_topic(topic, title, quotes) is expected


def test_long_sentences_are_cut_around_the_mention_and_marked():

    long = "Officials said " + "the plan " * 60 + "includes Velachery junction improvements " + "and more " * 60 + "."

    quote = quotes_about(long, "Velachery")[0]

    assert "Velachery" in quote
    assert quote.startswith("…") and quote.endswith("…")
    assert len(quote) <= web_research.MAX_QUOTE_CHARS + 2


@pytest.mark.parametrize("url,topic,expected", [
    ("https://www.cmdachennai.gov.in/x", "news", "government"),
    ("https://tnswa.nic.in/report", "general", "government"),
    ("https://www.thehindu.com/news", "news", "news"),
    ("https://example.com", "general", "web"),
])
def test_source_type(url, topic, expected):

    assert source_type(url, topic) == expected


# ============================================================
# RESEARCH (fake provider)
# ============================================================

class FakeProvider(web_research.WebResearchProvider):

    id = "fake"

    name = "Fake search"

    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.queries = []

    def search(self, query, topic="general", max_results=6):
        self.queries.append((query, topic))
        if self.error:
            raise self.error
        for key, results in self.results.items():
            if key in query:
                return results
        return []


METRO = {"title": "MRTS link to open", "url": "https://news.example/mrts",
         "content": "Short snippet.", "published_date": "Tue, 15 Sep 2026 10:00:00 GMT",
         "raw_content": "Chennai news. The Velachery MRTS link will open in December, the railway said."}

UNRELATED = {"title": "Adyar bridge", "url": "https://news.example/adyar",
             "content": "Work on the Adyar bridge continues for another month.", "raw_content": ""}

FLOOD = {"title": "Rain update", "url": "https://gcc.gov.in/2025/12/03/flood", "published_date": "2025-12-02",
         "content": "Waterlogging was reported in Velachery and Pallikaranai after heavy rain.",
         "raw_content": ""}


def _provider():

    return FakeProvider({
        "metro": [METRO, UNRELATED],
        "flooding": [FLOOD, METRO],      # METRO again: kept once
    })


def test_findings_keep_quotes_and_provenance():

    result = research("Velachery, Chennai, Tamil Nadu, India", "commercial_site_selection", _provider())

    assert result["status"] == "success"
    assert result["place"] == "Velachery, Chennai"

    by_url = {f["url"]: f for f in result["findings"]}

    assert set(by_url) == {METRO["url"], FLOOD["url"]}

    metro = by_url[METRO["url"]]

    assert metro["quotes"] == ["The Velachery MRTS link will open in December, the railway said."]
    assert metro["published_at"] == "2026-09-15"
    assert metro["retrieved_at"]
    assert metro["status"] == "reported"
    assert metro["topic"] == "infrastructure"

    assert by_url[FLOOD["url"]]["source_type"] == "government"
    # The date in the URL beats the provider's estimate.
    assert by_url[FLOOD["url"]]["published_at"] == "2025-12-03"
    assert by_url[FLOOD["url"]]["date_source"] == "url"
    assert metro["date_source"] == "provider"
    assert "not verified" in result["note"]


def test_only_place_and_topic_are_sent():

    provider = _provider()

    research("Velachery, Chennai, Tamil Nadu, India", "land_acquisition", provider)

    assert len(provider.queries) == 3

    for query, _ in provider.queries:
        assert query.startswith("Velachery, Chennai ")
        assert not any(ch.isdigit() for ch in query)


def test_searches_are_cached():

    provider = _provider()

    research("Velachery, Chennai", "ev_charging_site_selection", provider)
    second = research("Velachery, Chennai", "ev_charging_site_selection", provider)

    assert len(provider.queries) == 3
    assert all(s["cached"] for s in second["searches"])


def test_not_connected_without_a_key():

    result = research("Velachery, Chennai")

    assert result["status"] == "not_connected"
    assert result["findings"] == []


@pytest.mark.parametrize("status", ["provider_timeout", "provider_rate_limited", "provider_error"])
def test_provider_errors_are_explicit(status):

    provider = FakeProvider(error=web_research.ProviderError(status, "nope"))

    result = research("Velachery, Chennai", None, provider)

    assert result["status"] == status
    assert result["message"] == "nope"


# ============================================================
# ENDPOINT
# ============================================================

def test_endpoint_not_connected_does_not_use_a_question(client, monkeypatch):

    monkeypatch.setenv("RATE_LIMIT_PER_IP", "1")

    for _ in range(3):
        body = client.post("/research", json={"location": "Velachery, Chennai"}).json()
        assert body["status"] == "not_connected"


def test_endpoint_with_provider_counts_against_the_limit(client, monkeypatch):

    monkeypatch.setenv("RATE_LIMIT_PER_IP", "1")
    monkeypatch.setattr(web_research, "provider_from_env", _provider)

    first = client.post("/research", json={"location": "Velachery, Chennai, Tamil Nadu",
                                           "use_case": "commercial_site_selection"})

    assert first.status_code == 200
    assert first.json()["status"] == "success"

    assert client.post("/research", json={"location": "Velachery, Chennai"}).status_code == 429


def test_tavily_request_shape(monkeypatch):

    import requests

    sent = {}

    class Response:
        status_code = 200

        def json(self):
            return {"results": [{"title": "t", "url": "https://x.example", "content": "c",
                                 "raw_content": "r", "published_date": "2026-01-01"}]}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json)
        return Response()

    monkeypatch.setattr(requests, "post", post)

    results = web_research.TavilyProvider("tvly-test").search("Velachery, Chennai flooding", topic="news")

    assert sent["url"] == "https://api.tavily.com/search"
    assert sent["headers"] == {"Authorization": "Bearer tvly-test"}
    assert sent["json"]["topic"] == "news"
    assert sent["json"]["include_published_date"] is True
    assert results[0]["published_date"] == "2026-01-01"


@pytest.mark.parametrize("code,status", [(429, "provider_rate_limited"), (401, "provider_error"), (500, "provider_error")])
def test_tavily_http_errors(monkeypatch, code, status):

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: type("R", (), {"status_code": code})())

    with pytest.raises(web_research.ProviderError) as error:
        web_research.TavilyProvider("tvly-test").search("q")

    assert error.value.status == status
