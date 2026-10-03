"""
User feedback on results: what went wrong (or right) with a result or
with one site in it.

    save(entry) -> id
    entries(since_id, limit) -> [entry]

Each entry keeps the context it was given for (the question, how it was
interpreted, and the site or the top sites with their evidence), so a
report can be traced back to what SkyLens showed.

Storage:

    DATABASE_URL set (postgres://... or postgresql://...)  -> Postgres
    otherwise                                              -> SQLite file
                                                              (FEEDBACK_DB_PATH,
                                                              default .cache/feedback.sqlite3)

Hosts that wipe their disk on restart (Render's free tier) need
DATABASE_URL, or feedback is lost on every deploy.

Visitors may send FEEDBACK_PER_IP entries per 24 hours (default 20,
0 disables). IP addresses are used for that count only and never
stored.
"""

import json
import os
import sqlite3
import subprocess
import threading
import time

from collections import defaultdict, deque
from datetime import datetime, timezone


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Quick tags offered in the form. Anything else is rejected.
TAGS = {
    "has_building": "Has a building",
    "in_use": "Campus, protected or in use",
    "wrong_size": "Wrong size",
    "wrong_location": "Wrong location",
    "flood_terrain": "Flood or terrain wrong",
    "infrastructure": "Infrastructure wrong",
    "changes": "Changes wrong",
    "web_findings": "Web findings wrong",
    "slow_error": "Slow or error",
    "other": "Other",
}

SCOPES = ("result", "site")

VERDICTS = ("up", "down")

MAX_COMMENT = 2000

# The context snapshot sent by the page (question, interpretation,
# sites with their evidence). Generous; guards the database.
MAX_CONTEXT_BYTES = 256_000


class FeedbackError(Exception):
    """
    A problem the visitor should be told about (status, message).
    """

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ============================================================
# STORAGE
# ============================================================

COLUMNS = ("created_at", "scope", "verdict", "tags", "comment", "query", "use_case",
           "site_id", "result_status", "app_version", "logged_in", "context")

_lock = threading.Lock()


def _postgres_url():

    url = (os.getenv("DATABASE_URL") or "").strip()

    return url if url.startswith(("postgres://", "postgresql://")) else None


def _sqlite_path():

    return os.getenv("FEEDBACK_DB_PATH") or os.path.join(BASE_DIR, ".cache", "feedback.sqlite3")


def storage_name():

    return "postgres" if _postgres_url() else "sqlite"


def _connect():
    """
    (connection, placeholder) for the configured database, with the
    table created if needed.
    """

    url = _postgres_url()

    if url:
        import psycopg

        connection = psycopg.connect(url, connect_timeout=10)
        placeholder = "%s"
        key = "id SERIAL PRIMARY KEY"
    else:
        path = _sqlite_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        placeholder = "?"
        key = "id INTEGER PRIMARY KEY AUTOINCREMENT"

    columns = ", ".join(f"{c} TEXT" for c in COLUMNS)

    connection.execute(f"CREATE TABLE IF NOT EXISTS feedback ({key}, {columns})")

    return connection, placeholder


def save(entry):
    """
    Store one validated entry (see validate); returns its id.
    """

    row = dict(entry)

    row["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["app_version"] = app_version()

    for key in ("tags", "context"):
        row[key] = json.dumps(row.get(key), ensure_ascii=False)

    row["logged_in"] = "1" if row.get("logged_in") else "0"

    values = [row.get(c) for c in COLUMNS]

    with _lock:

        connection, ph = _connect()

        try:
            sql = (f"INSERT INTO feedback ({', '.join(COLUMNS)}) "
                   f"VALUES ({', '.join([ph] * len(COLUMNS))})")

            if ph == "%s":
                new_id = connection.execute(sql + " RETURNING id", values).fetchone()[0]
            else:
                new_id = connection.execute(sql, values).lastrowid

            connection.commit()
        finally:
            connection.close()

    return new_id


def entries(since_id=0, limit=500):
    """
    Stored entries with id > since_id, oldest first.
    """

    with _lock:

        connection, ph = _connect()

        try:
            rows = connection.execute(
                f"SELECT id, {', '.join(COLUMNS)} FROM feedback WHERE id > {ph} ORDER BY id LIMIT {ph}",
                (int(since_id), int(limit)),
            ).fetchall()
        finally:
            connection.close()

    found = []

    for row in rows:

        entry = dict(zip(("id",) + COLUMNS, row))

        for key in ("tags", "context"):
            entry[key] = json.loads(entry[key]) if entry[key] else None

        entry["logged_in"] = entry["logged_in"] == "1"

        found.append(entry)

    return found


# ============================================================
# VALIDATION
# ============================================================

def validate(data, logged_in):
    """
    A clean entry from a submitted form, or FeedbackError.
    """

    scope = data.get("scope")

    if scope not in SCOPES:
        raise FeedbackError(422, "Feedback must be about a result or a site.")

    verdict = data.get("verdict") or None

    if verdict is not None and verdict not in VERDICTS:
        raise FeedbackError(422, "Unknown rating.")

    tags = list(dict.fromkeys(data.get("tags") or []))

    unknown = [t for t in tags if t not in TAGS]

    if unknown:
        raise FeedbackError(422, f"Unknown tag(s): {', '.join(map(str, unknown))}.")

    comment = (data.get("comment") or "").strip()

    if len(comment) > MAX_COMMENT:
        raise FeedbackError(422, f"Comments are limited to {MAX_COMMENT} characters.")

    if not verdict and not tags and not comment:
        raise FeedbackError(422, "Choose a rating or a tag, or write a comment.")

    site_id = data.get("site_id")

    if scope == "site" and not site_id:
        raise FeedbackError(422, "Site feedback needs the site it is about.")

    context = data.get("context") or {}

    if not isinstance(context, dict):
        raise FeedbackError(422, "Context must be an object.")

    if len(json.dumps(context, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
        raise FeedbackError(413, "This result is too large to attach to feedback.")

    return {
        "scope": scope,
        "verdict": verdict,
        "tags": tags,
        "comment": comment,
        "query": str(context.get("query") or "")[:1000],
        "use_case": str(context.get("use_case") or "")[:100] or None,
        "site_id": str(site_id)[:200] if site_id else None,
        "result_status": str(context.get("status") or "")[:50] or None,
        "logged_in": logged_in,
        "context": context,
    }


# ============================================================
# LIMIT PER VISITOR
# ============================================================

DAY_SECONDS = 24 * 3600

_by_ip = defaultdict(deque)


def check_limit(ip):
    """
    Record one submission from ip; FeedbackError when over the limit.
    """

    try:
        per_ip = int(float(os.getenv("FEEDBACK_PER_IP", 20)))
    except ValueError:
        per_ip = 20

    if per_ip <= 0:
        return

    now = time.time()

    with _lock:

        for key in [k for k, v in _by_ip.items() if not v or v[-1] <= now - DAY_SECONDS]:
            del _by_ip[key]

        mine = _by_ip[ip]

        while mine and mine[0] <= now - DAY_SECONDS:
            mine.popleft()

        if len(mine) >= per_ip:
            raise FeedbackError(429, "Thanks, we have plenty of feedback from you today. Please try again tomorrow.")

        mine.append(now)


# ============================================================
# VERSION
# ============================================================

_version = None


def app_version():
    """
    The deployed commit (Render sets RENDER_GIT_COMMIT), else the local
    git commit, else "unknown".
    """

    global _version

    if os.getenv("RENDER_GIT_COMMIT"):
        return os.getenv("RENDER_GIT_COMMIT")[:12]

    if _version is None:
        try:
            _version = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"], cwd=BASE_DIR,
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            _version = "unknown"

    return _version
