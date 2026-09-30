"""
Overpass API client shared by buildings.py and geodata.py.

The public Overpass servers rate-limit per IP (a few "slots"; each
query holds a slot for a while) and return 429 when none is free and
504 when overloaded. This client:

- tries the servers that actually respond first, with a short
  connect timeout so a dead server costs seconds, not 30 s;
- on 429 asks the server's /status how long until a slot frees up
  and waits for it (bounded) instead of hopping to a dead mirror;
- retries 502/503/504 once with a short backoff;
- stops once an overall time budget is spent;
- caches successful responses on disk, so repeated queries are
  instant and do not use rate limit.

Settings (optional, via environment / .env, read on each query):

    OVERPASS_URLS            comma-separated interpreter URLs
    OVERPASS_BUDGET_SECONDS  total time allowed per query (default 100)
    OVERPASS_CACHE_HOURS     cache lifetime, 0 disables (default 24)
"""

import hashlib
import json
import os
import re
import time

import requests


# ============================================================
# SETTINGS
# ============================================================

DEFAULT_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Read at call time: main.py loads .env after importing modules.

def _urls():

    return [
        url.strip()
        for url in os.getenv("OVERPASS_URLS", "").split(",")
        if url.strip()
    ] or DEFAULT_URLS


def _float_env(name, default):

    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _budget_seconds():

    return _float_env("OVERPASS_BUDGET_SECONDS", "100")


def _cache_hours():

    return _float_env("OVERPASS_CACHE_HOURS", "24")

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".cache",
    "overpass",
)

CONNECT_TIMEOUT = 5

# Queries ask the server for [timeout:60]; allow a margin on top.
READ_TIMEOUT = 75

# Longest we will wait for a rate-limit slot before giving up on
# that server.
MAX_SLOT_WAIT = 25

HEADERS = {
    "User-Agent": "SkyLens/0.2 (physical-world site analysis MVP)"
}


class OverpassError(RuntimeError):
    pass


_session = requests.Session()


# ============================================================
# CACHE
# ============================================================

def _cache_key(query):

    normalized = "\n".join(
        line.strip()
        for line in query.strip().splitlines()
        if line.strip()
    )

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cache_get(key):

    hours = _cache_hours()

    if hours <= 0:
        return None

    path = os.path.join(CACHE_DIR, key + ".json")

    try:

        age = time.time() - os.path.getmtime(path)

        if age > hours * 3600:
            return None

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    except (OSError, ValueError):
        return None


def _cache_put(key, data):

    if _cache_hours() <= 0:
        return

    try:

        os.makedirs(CACHE_DIR, exist_ok=True)

        path = os.path.join(CACHE_DIR, key + ".json")

        tmp = path + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)

        os.replace(tmp, path)

    except OSError as e:
        print(f"Overpass cache write failed: {e}")


# ============================================================
# RATE LIMIT
# ============================================================

def _slot_wait_seconds(url):
    """
    Seconds until this server gives us a free slot, from its
    /status page. None if unknown.
    """

    status_url = url.rsplit("/", 1)[0] + "/status"

    try:
        text = _session.get(
            status_url, headers=HEADERS, timeout=(CONNECT_TIMEOUT, 5)
        ).text
    except requests.RequestException:
        return None

    match = re.search(r"(\d+) slots? available now", text)

    if match and int(match.group(1)) > 0:
        return 1

    waits = [int(s) for s in re.findall(r"in (-?\d+) seconds", text)]

    if waits:
        return max(1, min(waits) + 1)

    return None


# ============================================================
# QUERY
# ============================================================

def query_overpass(query):
    """
    Run an Overpass QL query and return the parsed JSON.
    Raises OverpassError if no server answered within the budget.
    """

    key = _cache_key(query)

    cached = _cache_get(key)

    if cached is not None:
        print("Overpass: cache hit")
        return cached

    deadline = time.monotonic() + _budget_seconds()

    errors = []

    for url in _urls():

        attempts = 0

        while attempts < 4:

            remaining = deadline - time.monotonic()

            if remaining < 5:
                errors.append("time budget exhausted")
                raise OverpassError(
                    "Map data provider (Overpass) is busy: "
                    + "; ".join(errors)
                )

            attempts += 1

            print(f"Trying Overpass: {url}")

            started = time.monotonic()

            try:

                response = _session.post(
                    url,
                    data={"data": query},
                    headers=HEADERS,
                    timeout=(CONNECT_TIMEOUT, min(READ_TIMEOUT, remaining)),
                )

            except requests.RequestException as e:

                errors.append(f"{url}: {type(e).__name__}")

                print(f"Overpass failed: {url} -> {type(e).__name__}")

                break

            elapsed = time.monotonic() - started

            code = response.status_code

            if code == 200:

                try:
                    data = response.json()
                except ValueError:
                    errors.append(f"{url}: invalid JSON")
                    break

                # Overpass reports server-side timeouts / memory
                # errors inside a 200 response, with partial data.
                remark = str(data.get("remark") or "")

                if "error" in remark.lower():
                    errors.append(f"{url}: {remark[:120]}")
                    print(f"Overpass incomplete result: {remark[:200]}")
                    break

                print(f"Overpass OK: {url} ({elapsed:.1f}s)")

                _cache_put(key, data)

                return data

            if code == 429:

                wait = _slot_wait_seconds(url) or 10

                remaining = deadline - time.monotonic()

                if wait > MAX_SLOT_WAIT or wait > remaining - 10:
                    errors.append(f"{url}: rate limited ({wait}s wait)")
                    print(f"Overpass rate limited: {url} (wait {wait}s, skipping)")
                    break

                print(f"Overpass rate limited: {url}; waiting {wait}s for a slot")

                time.sleep(wait)

                continue

            if code in (502, 503, 504):

                errors.append(f"{url}: HTTP {code}")

                print(f"Overpass busy: {url} -> HTTP {code} ({elapsed:.1f}s)")

                if attempts < 2 and deadline - time.monotonic() > 40:
                    time.sleep(3)
                    continue

                break

            errors.append(f"{url}: HTTP {code}")

            print(f"Overpass failed: {url} -> HTTP {code}")

            break

    raise OverpassError(
        "Map data provider (Overpass) is unavailable: "
        + "; ".join(errors)
    )
