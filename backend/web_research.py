"""
Web research: what is reported about a place, with sources.

    research(location, use_case_id) -> {"status", "findings", ...}

runs a few fixed searches about the place (infrastructure projects,
flooding, land and approvals, plus one topic for the analysis type)
through a configurable provider and keeps, for every result that
mentions the place, the exact sentences that mention it.

Web findings are reported claims, not measurements: they are never
scored, never reworded by a model, and always carry their URL, title,
publication date (when the page gives one), retrieval date, domain and
source type. Only the place name and the topic are sent to the
provider: no coordinates and no customer data.

Settings (environment / .env, read on each call):

    WEB_RESEARCH_PROVIDER   "tavily" (default)
    TAVILY_API_KEY          key for Tavily (tvly-...)
    WEB_RESEARCH_CACHE_HOURS  cache lifetime per search (default 24)

Without a key, research() returns status "not_connected".
"""

import hashlib
import json
import os
import re
import time

from datetime import datetime, timezone
from urllib.parse import urlparse


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "web")

MAX_RESULTS = 6

MAX_QUOTES_PER_PAGE = 2

MAX_QUOTE_CHARS = 320

TIMEOUT_SECONDS = 20


# ============================================================
# TOPICS (fixed templates; "{place}" is the place name)
# ============================================================

# (topic id, label, search template, provider topic)
COMMON_TOPICS = (
    ("infrastructure", "Infrastructure projects",
     "{place} metro OR flyover OR road widening OR bridge project", "news"),
    ("flooding", "Flooding and waterlogging",
     "{place} flooding OR waterlogging OR inundation", "news"),
)

USE_CASE_TOPICS = {
    "ev_charging_site_selection": ("ev", "EV charging",
                                   "{place} EV charging station", "news"),
    "commercial_site_selection": ("commercial", "Commercial development",
                                  "{place} mall OR commercial complex OR new shops", "news"),
    "land_acquisition": ("land", "Land, encroachment and approvals",
                         "{place} encroachment OR water body OR land acquisition OR layout approval", "news"),
    "construction_progress": ("construction", "Construction and development",
                              "{place} construction OR development project", "news"),
}

DEFAULT_TOPIC = ("land", "Land, encroachment and approvals",
                 "{place} encroachment OR water body OR land acquisition OR layout approval", "news")


# A result is kept for a topic only if its title or a quote contains
# one of these (word starts, case-insensitive): a search engine's
# results can mention the place without being about the topic.
TOPIC_WORDS = {
    "infrastructure": ("metro", "flyover", "road", "bridge", "rail", "corridor", "widening",
                       "underpass", "highway", "project", "station"),
    "flooding": ("flood", "waterlog", "inundat", "rain", "drain", "submerge", "stagnant"),
    "land": ("encroach", "water body", "lake", "tank", "acquisition", "approval", "layout",
             "land", "patta"),
    "ev": ("charging", "charger", "electric vehicle", "ev "),
    "commercial": ("mall", "commercial", "shop", "retail", "complex", "market"),
    "construction": ("construct", "development", "project", "building", "apartment", "tower"),
}


def about_topic(topic_id, title, quotes):

    words = TOPIC_WORDS.get(topic_id)

    if not words:
        return True

    text = " ".join([title or ""] + list(quotes)).lower() + " "

    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def topics_for(use_case_id):

    return COMMON_TOPICS + (USE_CASE_TOPICS.get(use_case_id, DEFAULT_TOPIC),)


# ============================================================
# PROVIDERS
# ============================================================

class WebResearchProvider:
    """
    search(query, topic, max_results) -> [{"title", "url", "content",
    "raw_content", "published_date"}].
    """

    id = None

    name = None

    def search(self, query, topic="general", max_results=MAX_RESULTS):
        raise NotImplementedError


class ProviderError(Exception):

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class TavilyProvider(WebResearchProvider):

    id = "tavily"

    name = "Tavily"

    URL = "https://api.tavily.com/search"

    def __init__(self, api_key):
        self.api_key = api_key

    def search(self, query, topic="general", max_results=MAX_RESULTS):

        import requests

        try:
            response = requests.post(self.URL, headers={"Authorization": f"Bearer {self.api_key}"}, json={
                "query": query,
                "topic": topic,
                "search_depth": "basic",
                "max_results": max_results,
                "include_raw_content": "text",
                "include_published_date": True,
            }, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as e:
            raise ProviderError("provider_timeout", f"Tavily did not respond ({type(e).__name__}).")

        if response.status_code == 429:
            raise ProviderError("provider_rate_limited", "Tavily's rate or credit limit was reached.")

        if response.status_code in (401, 403):
            raise ProviderError("provider_error", "Tavily rejected the API key.")

        if response.status_code != 200:
            raise ProviderError("provider_error", f"Tavily returned HTTP {response.status_code}.")

        return [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "content": r.get("content") or "",
                "raw_content": r.get("raw_content") or "",
                "published_date": r.get("published_date"),
            }
            for r in response.json().get("results", [])
        ]


def provider_from_env():
    """
    The configured provider, or None when no key is set.
    """

    name = (os.getenv("WEB_RESEARCH_PROVIDER") or "tavily").strip().lower()

    if name == "tavily":
        key = (os.getenv("TAVILY_API_KEY") or "").strip()
        return TavilyProvider(key) if key else None

    return None


# ============================================================
# CACHE
# ============================================================

def _cache_hours():

    try:
        return float(os.getenv("WEB_RESEARCH_CACHE_HOURS", "24"))
    except ValueError:
        return 24.0


def _cached_search(provider, query, topic):

    key = hashlib.sha256(f"{provider.id}\0{topic}\0{query}".encode("utf-8")).hexdigest()

    path = os.path.join(CACHE_DIR, key + ".json")

    hours = _cache_hours()

    try:
        if hours > 0 and time.time() - os.path.getmtime(path) < hours * 3600:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            return cached["results"], cached["retrieved_at"], True
    except (OSError, ValueError, KeyError):
        pass

    results = provider.search(query, topic=topic)

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if hours > 0:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"results": results, "retrieved_at": retrieved_at}, f)
        except OSError:
            pass

    return results, retrieved_at, False


# ============================================================
# QUOTES (pure; tested)
# ============================================================

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

# A full stop after these does not end a sentence ("St. Thomas Mount",
# "Rs. 200 crore").
_ABBREVIATIONS = re.compile(
    r"\b(St|Dr|Mr|Mrs|Ms|Rs|No|Nos|Govt|Dept|Ltd|Co|Jr|Sr|Prof|approx|vs|e\.g|i\.e)\.$", re.I,
)


def _looks_like_menu(sentence):
    """
    Site navigation and headers ("Videos Podcast Photos Visual Stories
    Specials ...") rather than prose: mostly capitalised words.
    """

    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", sentence)

    if len(words) < 12:
        return False

    capitalised = sum(1 for w in words if w[0].isupper())

    return capitalised / len(words) > 0.6


def _sentences(text):

    merged = []

    for piece in _SENTENCE.split(text):
        if merged and _ABBREVIATIONS.search(merged[-1]):
            merged[-1] += " " + piece
        else:
            merged.append(piece)

    return merged


def place_name(location):
    """
    "Velachery, Chennai, Tamil Nadu, India" -> ("Velachery, Chennai", "Velachery").
    """

    parts = [p.strip() for p in (location or "").split(",") if p.strip()]

    if not parts:
        return None, None

    return ", ".join(parts[:2]), parts[0]


def quotes_about(text, keyword, limit=MAX_QUOTES_PER_PAGE):
    """
    Up to `limit` verbatim sentences from text that mention keyword
    (whole word, case-insensitive), in their original order.
    """

    if not text or not keyword:
        return []

    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.I)

    found = []

    for sentence in _sentences(re.sub(r"\s+", " ", text)):

        sentence = sentence.strip()

        if not pattern.search(sentence) or len(sentence) < 25 or _looks_like_menu(sentence):
            continue

        # Snippets are often cut mid-sentence: say so.
        if not re.search(r"[.!?\"')\]]$", sentence):
            sentence += "\u2026"

        if len(sentence) > MAX_QUOTE_CHARS:
            # Keep the passage around the mention, marked as cut.
            at = pattern.search(sentence).start()
            start = max(0, at - MAX_QUOTE_CHARS // 2)
            sentence = ("…" if start else "") + sentence[start:start + MAX_QUOTE_CHARS] + "…"

        if sentence not in found:
            found.append(sentence)

        if len(found) == limit:
            break

    return found


GOVERNMENT_SUFFIXES = (".gov.in", ".nic.in", ".gov")


def source_type(url, topic):

    domain = (urlparse(url or "").hostname or "").lower()

    if domain.endswith(GOVERNMENT_SUFFIXES):
        return "government"

    return "news" if topic == "news" else "web"


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

_URL_DATE = re.compile(r"/(20\d{2})[/-](\d{1,2}|[A-Za-z]{3})[/-](\d{1,2})(?:/|$|[^\d])")


def url_date(url):
    """
    Publication date in a news URL (/2023/Dec/07/, /2024/03/15/), or None.
    """

    match = _URL_DATE.search(url or "")

    if not match:
        return None

    year, month, day = match.groups()

    month = _MONTHS.get(month.lower()) if month.isalpha() else int(month)

    try:
        return datetime(int(year), month, int(day)).date().isoformat()
    except (TypeError, ValueError):
        return None


def _day(value):

    if not value:
        return None

    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    match = re.match(r"\d{4}-\d{2}-\d{2}", value)

    return match.group(0) if match else None


# ============================================================
# RESEARCH
# ============================================================

def research(location, use_case_id=None, provider=None):
    """
    Findings about a place. provider defaults to provider_from_env().
    """

    provider = provider or provider_from_env()

    place, keyword = place_name(location)

    base = {
        "note": (
            "Reported by web sources and not verified by SkyLens. Quotes are "
            "exact; check the source before relying on them."
        ),
        "place": place,
    }

    if provider is None:
        return {**base, "status": "not_connected",
                "message": "Web research is not connected (no provider API key is set).",
                "findings": [], "searches": []}

    if not place:
        return {**base, "status": "unsupported", "message": "No place name to search for.",
                "findings": [], "searches": []}

    findings = []

    searches = []

    seen = set()

    for topic_id, label, template, provider_topic in topics_for(use_case_id):

        query = template.format(place=place)

        try:
            results, retrieved_at, cached = _cached_search(provider, query, provider_topic)
        except ProviderError as e:
            return {**base, "status": e.status, "message": str(e),
                    "findings": findings, "searches": searches}

        kept = 0

        for result in results:

            url = result.get("url")

            if not url or url in seen:
                continue

            # The provider's cleaned snippets first; the full page text
            # (with menus and boilerplate) only when they say nothing.
            quotes = quotes_about(result.get("content") or "", keyword) \
                or quotes_about(result.get("raw_content") or "", keyword)

            # Not about this place, or not about this topic: leave it out.
            if not quotes or not about_topic(topic_id, result.get("title"), quotes):
                continue

            in_url = url_date(url)

            seen.add(url)

            kept += 1

            findings.append({
                "topic": topic_id,
                "topic_label": label,
                "title": result.get("title"),
                "url": url,
                "domain": urlparse(url).hostname,
                "source_type": source_type(url, provider_topic),
                "published_at": in_url or _day(result.get("published_date")),
                # "url": the article's address; "provider": the search
                # provider's estimate, which can be wrong.
                "date_source": "url" if in_url else ("provider" if result.get("published_date") else None),
                "retrieved_at": retrieved_at,
                "quotes": quotes,
                "status": "reported",
                "provider": provider.name,
            })

        searches.append({"topic": topic_id, "label": label, "query": query,
                         "results": len(results), "kept": kept, "cached": cached})

    # Topics in their fixed order; within a topic newest first, undated last.
    order = [t[0] for t in topics_for(use_case_id)]

    findings.sort(key=lambda f: f["published_at"] or "", reverse=True)

    findings.sort(key=lambda f: (order.index(f["topic"]), f["published_at"] is None))

    return {**base, "status": "success", "provider": provider.name,
            "findings": findings, "searches": searches}
