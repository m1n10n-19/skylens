import json
import os
import queue
import threading
import time

from dotenv import load_dotenv

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from typing import Optional

from pydantic import BaseModel

from openai import OpenAI

from geopy.geocoders import Nominatim

from satellite import (
    search_satellite,
    get_latest_satellite
)

from buildings import get_buildings

from scoring import score_solar_candidate

from planner import (
    PLANNER_SYSTEM,
    build_planner_prompt,
    plan_from_llm_json
)

from pipeline import (
    not_implemented_response,
    run_analysis,
    unsupported_response
)

from use_cases import (
    USE_CASES,
    describe_use_case
)

import auth

import ratelimit


# ============================================================
# ENVIRONMENT
# ============================================================

# .env lives in the repository root, one level above backend/.

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

ENV_FILE = os.path.join(
    BASE_DIR,
    ".env"
)

load_dotenv(ENV_FILE)


DEEPSEEK_API_KEY = os.getenv(
    "DEEPSEEK_API_KEY"
)


if not DEEPSEEK_API_KEY:

    raise RuntimeError(
        f"DEEPSEEK_API_KEY not found. "
        f"Expected .env at: {ENV_FILE}"
    )


# ============================================================
# DEEPSEEK CLIENT
# ============================================================

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)


def call_deepseek_json(system, prompt):
    """
    Call DeepSeek and parse its reply as JSON.
    Raises HTTPException with stage "deepseek" or "deepseek_json".
    """

    try:

        response = client.chat.completions.create(

            model="deepseek-chat",

            messages=[
                {
                    "role": "system",
                    "content": system
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0
        )

    except Exception as e:

        print(
            "DEEPSEEK ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "deepseek",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


    text = response.choices[0].message.content or ""

    text = (
        text
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )


    try:

        return json.loads(text)

    except json.JSONDecodeError:

        print(
            "DEEPSEEK RETURNED INVALID JSON:"
        )

        print(
            text
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "deepseek_json",
                "error": (
                    "DeepSeek returned "
                    "invalid JSON"
                ),
                "raw": text
            }
        )


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="SkyLens API",
    description=(
        "Reality intelligence API for "
        "physical-world questions"
    ),
    version="0.2.0"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ============================================================
# GEOCODER
# ============================================================

geolocator = Nominatim(
    user_agent="skylens-reality-intelligence",
    timeout=10
)


# ============================================================
# REQUEST MODEL
# ============================================================

class IntentRequest(BaseModel):

    query: str

    # Optional override of the search radius (km, max 2).
    radius_km: Optional[float] = None


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "SkyLens Reality Intelligence"
    }


# ============================================================
# TEAM LOGIN (credentials in .env; see auth.py)
# ============================================================

class LoginRequest(BaseModel):

    username: str

    password: str


@app.post("/auth/login")
def login(
    request: LoginRequest
):

    if not auth.login_enabled():

        raise HTTPException(
            status_code=503,
            detail={
                "stage": "auth",
                "error": (
                    "Login is not set up on this server. Add "
                    "SKYLENS_USERNAME and SKYLENS_PASSWORD to .env "
                    "and restart the backend."
                )
            }
        )

    token = auth.login(
        request.username,
        request.password
    )

    if not token:

        # Slow down password guessing.
        time.sleep(1)

        raise HTTPException(
            status_code=401,
            detail={
                "stage": "auth",
                "error": "Wrong username or password."
            }
        )

    return {
        "token": token,
        "username": request.username.strip(),
        "unlimited": True
    }


@app.get("/auth/me")
def me(
    authorization: Optional[str] = Header(default=None)
):

    username = auth.session_user(authorization)

    return {
        "login_enabled": auth.login_enabled(),
        "username": username,
        "unlimited": username is not None
    }


# ============================================================
# QUESTION LIMIT (anonymous visitors; see ratelimit.py)
# ============================================================

def enforce_question_limit(
    http_request,
    authorization
):
    """
    Raise 429 if an anonymous visitor is over the question limit.
    Signed-in team members are never limited.
    """

    if auth.session_user(authorization):
        return

    ip = ratelimit.client_ip(http_request)

    blocked = ratelimit.check(ip)

    if blocked:

        print(
            "RATE LIMITED:",
            ip
        )

        raise HTTPException(
            status_code=429,
            detail={
                "stage": "rate_limit",
                "error": blocked["error"],
                "retry_after": blocked["retry_after"]
            },
            headers={
                "Retry-After": str(blocked["retry_after"])
            }
        )


# ============================================================
# INTENT ENGINE
# ============================================================

@app.post("/intent")
def understand_intent(
    request: IntentRequest,
    http_request: Request,
    authorization: Optional[str] = Header(default=None)
):

    enforce_question_limit(
        http_request,
        authorization
    )

    print(
        "\nSTEP 1: Understanding intent..."
    )

    prompt = f"""
You are the intent engine for SkyLens.

SkyLens answers questions about physical places,
buildings, land, infrastructure and changes over time.

Convert the customer's natural-language request
into structured JSON.

Customer request:

{request.query}

Return ONLY valid JSON.

Required fields:

intent_type
location
latitude
longitude
radius_km
industry
requirements
data_needed
output

For example:

Customer:
"Find large roofs around Adyar suitable for solar."

Possible interpretation:

intent_type:
solar_prospecting

industry:
solar

requirements:
- large roof
- commercial or industrial
- suitable for solar

data_needed:
- satellite imagery
- building footprints
- roads
- land use

Do not invent precise coordinates.

Use null when uncertain.
"""

    result = call_deepseek_json(
        "You are the SkyLens "
        "intent engine. "
        "Return only valid JSON.",
        prompt
    )


    print(
        "STEP 1 COMPLETE:",
        result
    )

    return result


# ============================================================
# GEOCODE
# ============================================================

@app.get("/geocode")
def geocode(
    location: str
):

    print(
        "\nSTEP 2: Geocoding:",
        location
    )

    try:

        result = geolocator.geocode(
            location
        )

    except Exception as e:

        print(
            "GEOCODER ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "geocoding",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


    if not result:

        raise HTTPException(
            status_code=404,
            detail={
                "stage": "geocoding",
                "error": (
                    f"Location not found: "
                    f"{location}"
                )
            }
        )


    print(
        "STEP 2 COMPLETE:",
        result.latitude,
        result.longitude
    )


    return {
        "location": location,

        "latitude": result.latitude,

        "longitude": result.longitude,

        "display_name": result.address
    }


# ============================================================
# SATELLITE SEARCH
# ============================================================

@app.get("/satellite")
def satellite(
    latitude: float,
    longitude: float,
    radius_km: float = 5
):

    print(
        "\nSTEP 3: Searching satellite imagery..."
    )

    try:

        results = search_satellite(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km
        )

    except Exception as e:

        print(
            "SATELLITE ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "satellite_search",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


    print(
        "STEP 3 COMPLETE:",
        len(results),
        "images"
    )


    return {
        "latitude": latitude,

        "longitude": longitude,

        "radius_km": radius_km,

        "imagery_count": len(results),

        "imagery": results
    }


# ============================================================
# LATEST SATELLITE
# ============================================================

@app.get("/satellite/latest")
def latest_satellite(
    latitude: float,
    longitude: float,
    radius_km: float = 5
):

    print(
        "\nSTEP 3: Getting latest satellite image..."
    )

    try:

        result = get_latest_satellite(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km
        )

    except Exception as e:

        print(
            "SATELLITE ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "latest_satellite",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


    if not result:

        raise HTTPException(
            status_code=404,
            detail={
                "stage": "latest_satellite",
                "error": (
                    "No suitable satellite "
                    "imagery found."
                )
            }
        )


    print(
        "STEP 3 COMPLETE:",
        result["id"]
    )


    return result


# ============================================================
# BUILDINGS
# ============================================================

@app.get("/buildings")
def buildings(
    latitude: float,
    longitude: float,
    radius_km: float = 1,
    minimum_area_m2: float = 500
):

    print(
        "\nSTEP 4: Getting building footprints..."
    )

    try:

        results = get_buildings(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            minimum_area_m2=minimum_area_m2
        )

    except Exception as e:

        print(
            "BUILDING DATA ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "building_data",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


    print(
        "STEP 4 COMPLETE:",
        len(results),
        "buildings"
    )


    return {
        "latitude": latitude,

        "longitude": longitude,

        "radius_km": radius_km,

        "minimum_area_m2":
            minimum_area_m2,

        "candidate_count":
            len(results),

        "candidates":
            results
    }


# ============================================================
# SOLAR PROSPECTS
# ============================================================

@app.get("/solar/prospects")
def solar_prospects(
    latitude: float,
    longitude: float,
    radius_km: float = 1,
    minimum_area_m2: float = 500,
    limit: int = 20
):

    print(
        "\nSTEP 5: Finding solar prospects..."
    )

    try:

        buildings = get_buildings(

            latitude=latitude,

            longitude=longitude,

            radius_km=radius_km,

            minimum_area_m2=minimum_area_m2
        )


        scored = []

        for building in buildings:

            scored.append(
                score_solar_candidate(
                    building
                )
            )


        scored.sort(

            key=lambda x: (
                x["solar_score"],
                x["area_m2"]
            ),

            reverse=True
        )


        top = scored[:limit]


    except Exception as e:

        print(
            "SOLAR PROSPECTING ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "solar_prospecting",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


    print(
        "STEP 5 COMPLETE:",
        len(scored),
        "candidates"
    )


    return {

        "status": "success",

        "intent":
            "solar_prospecting",

        "location": {

            "latitude":
                latitude,

            "longitude":
                longitude,

            "radius_km":
                radius_km
        },

        "filters": {

            "minimum_area_m2":
                minimum_area_m2
        },

        "total_candidates":
            len(scored),

        "returned":
            len(top),

        "prospects":
            top
    }


# ============================================================
# ANALYSIS PLANNER
# ============================================================

def plan_analysis(query):
    """
    STEP 1: DeepSeek turns the query into an AnalysisSpec and
    SkyLens picks the matching use case from the registry.
    """

    print(
        "\nSTEP 1: Calling DeepSeek planner..."
    )

    intent = call_deepseek_json(
        PLANNER_SYSTEM,
        build_planner_prompt(query)
    )

    if not isinstance(intent, dict):

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "deepseek_json",
                "error": "DeepSeek did not return a JSON object",
                "raw": intent
            }
        )

    spec, use_case = plan_from_llm_json(
        intent,
        query
    )

    print(
        "STEP 1 COMPLETE"
    )

    print(
        "AnalysisSpec:",
        spec.model_dump()
    )

    print(
        "Use case:",
        use_case.id if use_case else "UNSUPPORTED"
    )

    return intent, spec, use_case


@app.get("/use-cases")
def use_cases():

    return {
        "use_cases": [
            describe_use_case(use_case)
            for use_case in USE_CASES.values()
        ]
    }


@app.post("/plan")
def plan(
    request: IntentRequest,
    http_request: Request,
    authorization: Optional[str] = Header(default=None)
):
    """
    Show the AnalysisSpec for a query without collecting data.
    """

    enforce_question_limit(
        http_request,
        authorization
    )

    intent, spec, use_case = plan_analysis(
        request.query
    )

    return {
        "query": request.query,
        "intent": intent,
        "analysis_spec": spec.model_dump(),
        "use_case": (
            describe_use_case(use_case)
            if use_case else None
        ),
        "supported": bool(
            use_case and use_case.implemented
        )
    }


# ============================================================
# COMPLETE SKY LENS ANALYSIS
# ============================================================

def run_query(query, radius_km=None, emit=None):
    """
    Plan the query with DeepSeek, then run the matching use case.
    emit(event, data) receives progress events (streaming only).
    """

    emit = emit or (lambda event, data: None)

    print(
        "\n"
        "========================================"
    )

    print(
        "SKYLENS ANALYSIS START"
    )

    print(
        "QUERY:",
        query
    )

    print(
        "========================================"
    )

    intent, spec, use_case = plan_analysis(
        query
    )

    # Explicit radius from the client ("find similar sites nearby")
    # overrides anything DeepSeek read from the text.
    if radius_km:
        spec.radius_km = radius_km

    emit("step", {
        "step": "intent",
        "status": "done",
        "intent_type": spec.intent_type,
        "use_case": use_case.id if use_case else None,
        "title": use_case.title if use_case else None,
        "implemented": bool(use_case and use_case.implemented),
        "location": spec.location,
        "criteria": len(use_case.criteria) if use_case else 0,
    })

    if use_case is None:

        result = unsupported_response(
            query,
            intent,
            spec
        )

    elif not use_case.implemented:

        result = not_implemented_response(
            query,
            intent,
            spec,
            use_case
        )

    else:

        result = run_analysis(
            query,
            intent,
            spec,
            use_case,
            geolocator,
            emit
        )

    print(
        "\n"
        "========================================"
    )

    print(
        "SKYLENS ANALYSIS COMPLETE:",
        result["status"]
    )

    print(
        "========================================\n"
    )

    return result


@app.post("/analyze")
def analyze(
    request: IntentRequest,
    http_request: Request,
    authorization: Optional[str] = Header(default=None)
):

    enforce_question_limit(
        http_request,
        authorization
    )

    return run_query(
        request.query,
        request.radius_km
    )


@app.post("/analyze/stream")
def analyze_stream(
    request: IntentRequest,
    http_request: Request,
    authorization: Optional[str] = Header(default=None)
):
    """
    Same as /analyze, streamed as server-sent events:

        event: step    {"step": "intent" | "location" | "evidence" |
                        "candidates" | "scoring" | "decision",
                        "status": "running" | "done", ...}
        event: layer   {"id": "<data layer>", "status": "used" | "failed"}
        event: candidates  {"count": n, "preview": [...]}
        event: result  <the /analyze response>
        event: error   {"status_code": ..., "detail": {...}}
    """

    # Checked before streaming starts, so a limited visitor gets a 429.
    enforce_question_limit(
        http_request,
        authorization
    )

    events = queue.Queue()

    def emit(event, data):
        events.put((event, data))

    def worker():

        try:

            emit("result", run_query(
                request.query,
                request.radius_km,
                emit
            ))

        except HTTPException as e:

            emit("error", {
                "status_code": e.status_code,
                "detail": e.detail
            })

        except Exception as e:

            print("ANALYSIS FAILED:", repr(e))

            emit("error", {
                "status_code": 500,
                "detail": {
                    "stage": "unknown",
                    "error_type": type(e).__name__,
                    "error": str(e)
                }
            })

        finally:

            events.put(None)

    threading.Thread(
        target=worker,
        daemon=True
    ).start()

    def stream():

        while True:

            item = events.get()

            if item is None:
                return

            event, data = item

            yield (
                f"event: {event}\n"
                f"data: {json.dumps(data, default=str)}\n\n"
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )


# ============================================================
# FRONTEND
# ============================================================

# Serve the frontend from the same origin (used on Render).
# Mounted last so the API routes above take precedence.

class FrontendFiles(StaticFiles):
    """
    Browsers must revalidate the frontend on every load (cheap: an
    unchanged file is a 304). Otherwise, after a deploy, a cached old
    script can run next to a new one and break the page.
    """

    async def get_response(self, path, scope):

        response = await super().get_response(path, scope)

        response.headers["Cache-Control"] = "no-cache"

        return response


app.mount(
    "/",
    FrontendFiles(
        directory=os.path.join(BASE_DIR, "frontend"),
        html=True
    ),
    name="frontend"
)
