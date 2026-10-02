"""
Server-side question limit for anonymous visitors.

Every query that reaches DeepSeek costs money, and the frontend's
free-question counter lives in the browser, so it is easy to reset.
This module caps anonymous queries on the server:

- per visitor IP, within a rolling window, and
- in total across all anonymous visitors, per rolling 24 hours,
  so a flood of visitors cannot run up an unbounded bill.

Signed-in team members (see auth.py) are not limited.

Settings (optional, via environment / .env, read on each query):

    RATE_LIMIT_PER_IP        queries per IP per window (default 1000, 0 disables)
    RATE_LIMIT_WINDOW_HOURS  length of the per-IP window (default 24)
    RATE_LIMIT_DAILY_TOTAL   anonymous queries per 24 h, all IPs (default 5000, 0 disables)

Counts are kept in memory, so they reset when the server restarts.
That is fine for a single uvicorn process (such as one Render
instance); several processes would each keep their own counts.
"""

import math
import os
import threading
import time
from collections import defaultdict, deque


DAY_SECONDS = 24 * 3600


_lock = threading.Lock()

# ip -> timestamps of that IP's recent queries
_by_ip = defaultdict(deque)

# timestamps of all recent anonymous queries
_total = deque()


# Read at call time: main.py loads .env after importing modules.
def _setting(name, default):

    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def client_ip(request):
    """
    The visitor's IP address.

    On Render, requests arrive through Cloudflare, which sets
    True-Client-IP / CF-Connecting-IP to the real client address.
    X-Forwarded-For is not used: Render passes through whatever
    the client sent in it, so it can be spoofed.
    Locally there are no such headers and the socket address is used.
    """

    for header in ("true-client-ip", "cf-connecting-ip"):

        value = (request.headers.get(header) or "").strip()

        if value:
            return value

    return request.client.host if request.client else "unknown"


def _prune(timestamps, cutoff):

    while timestamps and timestamps[0] <= cutoff:
        timestamps.popleft()


def check(ip):
    """
    Record one query from ip.

    Returns None if it is allowed, otherwise a dict with a
    user-facing "error" and "retry_after" seconds.
    """

    per_ip = int(_setting("RATE_LIMIT_PER_IP", 1000))

    window = _setting("RATE_LIMIT_WINDOW_HOURS", 24) * 3600

    daily_total = int(_setting("RATE_LIMIT_DAILY_TOTAL", 5000))

    now = time.time()

    with _lock:

        # Drop IPs with no recent queries so memory stays bounded.
        for key in [k for k, v in _by_ip.items() if not v or v[-1] <= now - window]:
            del _by_ip[key]

        mine = _by_ip[ip]

        _prune(mine, now - window)

        _prune(_total, now - DAY_SECONDS)

        if per_ip > 0 and len(mine) >= per_ip:

            return {
                "error": (
                    f"You have used all {per_ip} free questions for now. "
                    "Sign in with a team account for unlimited questions, "
                    "or try again later."
                ),
                "retry_after": math.ceil(mine[0] + window - now),
            }

        if daily_total > 0 and len(_total) >= daily_total:

            return {
                "error": (
                    "SkyLens has reached its daily limit of free questions. "
                    "Sign in with a team account, or try again later."
                ),
                "retry_after": math.ceil(_total[0] + DAY_SECONDS - now),
            }

        mine.append(now)

        _total.append(now)

    return None
