"""
Team login.

One username/password pair lives in .env:

    SKYLENS_USERNAME=...
    SKYLENS_PASSWORD=...

Signing in returns a session token that the frontend sends as
"Authorization: Bearer <token>". Signed-in users have no question
limit in the app; everyone else gets the demo's free questions.

Tokens are signed with a key derived from the credentials, so
changing the password signs everyone out. No database is needed.
"""

import base64
import hashlib
import hmac
import json
import os
import time


SESSION_DAYS = 30


def _credentials():
    """
    (username, password) from .env, or None if login isn't set up.
    Read on each call: main.py loads .env after importing modules.
    """

    username = (os.getenv("SKYLENS_USERNAME") or "").strip()

    password = os.getenv("SKYLENS_PASSWORD") or ""

    return (username, password) if username and password else None


def login_enabled():

    return _credentials() is not None


def _signature(payload, credentials):

    key = hashlib.sha256(
        "skylens-session\0{}\0{}".format(*credentials).encode("utf-8")
    ).digest()

    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def login(username, password):
    """
    A session token if the username and password match .env,
    otherwise None.
    """

    credentials = _credentials()

    if credentials is None:
        return None

    # Compare both in constant time, without short-circuiting.
    user_ok = hmac.compare_digest(username.strip().encode("utf-8"), credentials[0].encode("utf-8"))

    pass_ok = hmac.compare_digest(password.encode("utf-8"), credentials[1].encode("utf-8"))

    if not (user_ok and pass_ok):
        return None

    payload = base64.urlsafe_b64encode(json.dumps({
        "u": credentials[0],
        "exp": int(time.time()) + SESSION_DAYS * 86400,
    }).encode("utf-8")).decode("ascii").rstrip("=")

    return f"{payload}.{_signature(payload, credentials)}"


def session_user(authorization):
    """
    Username for a valid "Bearer <token>" header, otherwise None.
    """

    credentials = _credentials()

    if credentials is None or not authorization or not authorization.startswith("Bearer "):
        return None

    payload, _, signature = authorization[len("Bearer "):].strip().partition(".")

    if not payload or not hmac.compare_digest(signature, _signature(payload, credentials)):
        return None

    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None

    if data.get("u") != credentials[0] or data.get("exp", 0) < time.time():
        return None

    return data["u"]
