import json
import os

from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from openai import OpenAI

from geopy.geocoders import Nominatim

from satellite import (
    search_satellite,
    get_latest_satellite
)

from buildings import get_buildings

from scoring import score_solar_candidate


# ============================================================
# ENVIRONMENT
# ============================================================

# .env is located at:
#
# D:\skylens\.env
#
# while this file is:
#
# D:\skylens\backend\main.py
#

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


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="SkyLens API",
    description=(
        "Reality intelligence API for "
        "physical-world questions"
    ),
    version="0.1.0"
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
# INTENT ENGINE
# ============================================================

@app.post("/intent")
def understand_intent(
    request: IntentRequest
):

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

    try:

        response = client.chat.completions.create(

            model="deepseek-chat",

            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the SkyLens "
                        "intent engine. "
                        "Return only valid JSON."
                    )
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


    text = response.choices[0].message.content

    text = (
        text
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )


    try:

        result = json.loads(text)

    except json.JSONDecodeError:

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
# LOCATION TEXT EXTRACTION (used by /analyze)
# ============================================================

# DeepSeek may return "location" either as a plain string:
#
#   "location": "Adyar, Chennai"
#
# or as an object:
#
#   "location": {"text": "Adyar, Chennai", "coordinates": null}
#
# Only the TEXT is used. Any coordinates DeepSeek returns are
# ignored; coordinates always come from Nominatim.

_LOCATION_TEXT_KEYS = (
    "text",
    "name",
    "query",
    "address",
    "location",
    "display_name"
)

# Joined in this order, e.g. area + city + country
# -> "Adyar, Chennai, India"

_LOCATION_PART_KEYS = (
    "street",
    "area",
    "neighborhood",
    "neighbourhood",
    "locality",
    "suburb",
    "district",
    "city",
    "county",
    "state",
    "country"
)


def extract_location_text(intent):

    raw = intent.get(
        "location"
    )

    text = None


    if isinstance(raw, str):

        text = raw

    elif isinstance(raw, dict):

        # Shape 1: {"text": "Adyar, Chennai", ...}

        for key in _LOCATION_TEXT_KEYS:

            value = raw.get(key)

            if isinstance(value, str) and value.strip():

                text = value

                break


        # Shape 2: {"area": "Adyar", "city": "Chennai", ...}

        if not text:

            parts = []

            for key in _LOCATION_PART_KEYS:

                value = raw.get(key)

                if isinstance(value, str) and value.strip():

                    value = value.strip()

                    if value not in parts:

                        parts.append(value)

            if parts:

                text = ", ".join(parts)


    if isinstance(text, str):

        text = text.strip()


    if not text:

        raise HTTPException(

            status_code=400,

            detail={

                "stage":
                    "location_extraction",

                "error":
                    (
                        "DeepSeek did not return "
                        "usable location text."
                    ),

                "raw_location":
                    raw
            }
        )


    return text


# ============================================================
# COMPLETE SKY LENS ANALYSIS
# ============================================================

@app.post("/analyze")
def analyze(
    request: IntentRequest
):

    print(
        "\n"
        "========================================"
    )

    print(
        "SKYLENS ANALYSIS START"
    )

    print(
        "QUERY:",
        request.query
    )

    print(
        "========================================"
    )


    # ========================================================
    # STEP 1 — DEEPSEEK INTENT
    # ========================================================

    print(
        "\nSTEP 1: Calling DeepSeek..."
    )


    prompt = f"""
You are the intent engine for SkyLens.

SkyLens analyzes physical places,
buildings, land, infrastructure and
changes over time.

Convert this customer request into
structured JSON.

Customer request:

{request.query}

Return ONLY valid JSON.

Required fields:

intent_type
location
industry
requirements
data_needed
output

Important:

The location field must be a single
plain-text string that can be searched
on a map, for example:
"Adyar, Chennai, Tamil Nadu, India".
Do not return location as an object.

Do not invent precise coordinates.

If a value is uncertain,
use null.

For requirements, preserve
specific numbers from the
customer request.
"""


    try:

        response = client.chat.completions.create(

            model="deepseek-chat",

            messages=[

                {
                    "role": "system",

                    "content": (
                        "You are the SkyLens "
                        "intent engine. "
                        "Return only valid JSON."
                    )
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
            "\nDEEPSEEK FAILED"
        )

        print(
            repr(e)
        )

        raise HTTPException(

            status_code=502,

            detail={

                "stage":
                    "deepseek",

                "error_type":
                    type(e).__name__,

                "error":
                    str(e)
            }
        )


    print(
        "STEP 1: DeepSeek response received."
    )


    text = response.choices[0].message.content


    text = (
        text
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )


    try:

        intent = json.loads(
            text
        )

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

                "stage":
                    "deepseek_json",

                "error":
                    "Invalid JSON",

                "raw":
                    text
            }
        )


    print(
        "STEP 1 COMPLETE"
    )

    print(
        "Intent:",
        intent
    )


    # ========================================================
    # STEP 2 — GEOCODING (Nominatim only)
    # ========================================================

    location_name = extract_location_text(
        intent
    )


    print(
        "\nSTEP 2: Geocoding:",
        repr(location_name)
    )


    try:

        location_result = (
            geolocator.geocode(
                location_name
            )
        )


    except Exception as e:

        print(
            "\nGEOCODING FAILED"
        )

        print(
            repr(e)
        )

        raise HTTPException(

            status_code=502,

            detail={

                "stage":
                    "geocoding",

                "location_text":
                    location_name,

                "error_type":
                    type(e).__name__,

                "error":
                    str(e)
            }
        )


    if not location_result:

        raise HTTPException(

            status_code=404,

            detail={

                "stage":
                    "geocoding",

                "location_text":
                    location_name,

                "error":
                    f"Location not found: {location_name}"
            }
        )


    latitude = (
        location_result.latitude
    )

    longitude = (
        location_result.longitude
    )


    print(
        "STEP 2 COMPLETE"
    )

    print(
        "Coordinates (Nominatim):",
        latitude,
        longitude
    )


    # ========================================================
    # STEP 3 — SEARCH RADIUS
    # ========================================================

    radius_km = intent.get(
        "radius_km",
        1
    )


    if not radius_km:

        radius_km = 1


    try:

        radius_km = float(
            radius_km
        )

    except (
        TypeError,
        ValueError
    ):

        radius_km = 1


    # MVP safety limit

    radius_km = min(
        radius_km,
        2
    )


    print(
        "\nSTEP 3: Search radius:",
        radius_km,
        "km"
    )


    # ========================================================
    # STEP 4 — SATELLITE
    # ========================================================

    print(
        "STEP 4: Getting satellite imagery..."
    )


    try:

        satellite = (
            get_latest_satellite(

                latitude=latitude,

                longitude=longitude,

                radius_km=radius_km
            )
        )


    except Exception as e:

        print(
            "\nSATELLITE FAILED"
        )

        print(
            repr(e)
        )

        raise HTTPException(

            status_code=502,

            detail={

                "stage":
                    "satellite",

                "error_type":
                    type(e).__name__,

                "error":
                    str(e)
            }
        )


    if not satellite:

        raise HTTPException(

            status_code=404,

            detail={

                "stage":
                    "satellite",

                "error":
                    "No suitable satellite imagery found."
            }
        )


    print(
        "STEP 4 COMPLETE"
    )

    print(
        "Satellite:",
        satellite["id"]
    )


    # ========================================================
    # STEP 5 — BUILDING DATA
    # ========================================================

    print(
        "\nSTEP 5: Getting building footprints..."
    )


    requirements = (
        intent.get(
            "requirements"
        )
        or {}
    )


    if not isinstance(requirements, dict):

        requirements = {}


    minimum_area = (
        requirements.get(
            "minimum_roof_area_m2"
        )
    )


    if minimum_area is None:

        minimum_area = (
            requirements.get(
                "minimum_area_m2"
            )
        )


    if minimum_area is None:

        # DeepSeek's key names vary, e.g.
        # "min_usable_roof_area_sqm"

        for key, value in requirements.items():

            if (
                "area" in str(key).lower()
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):

                minimum_area = value

                break


    if minimum_area is None:

        minimum_area = 500


    try:

        minimum_area = float(
            minimum_area
        )

    except (
        TypeError,
        ValueError
    ):

        minimum_area = 500


    print(
        "Minimum area:",
        minimum_area
    )


    try:

        buildings = get_buildings(

            latitude=latitude,

            longitude=longitude,

            radius_km=radius_km,

            minimum_area_m2=minimum_area
        )


    except Exception as e:

        print(
            "\nBUILDING DATA FAILED"
        )

        print(
            repr(e)
        )

        raise HTTPException(

            status_code=502,

            detail={

                "stage":
                    "building_data",

                "error_type":
                    type(e).__name__,

                "error":
                    str(e)
            }
        )


    print(
        "STEP 5 COMPLETE"
    )

    print(
        "Buildings found:",
        len(buildings)
    )


    # ========================================================
    # STEP 6 — SCORE
    # ========================================================

    print(
        "\nSTEP 6: Scoring candidates..."
    )


    try:

        scored = [

            score_solar_candidate(
                building
            )

            for building
            in buildings
        ]


        scored.sort(

            key=lambda x: (

                x["solar_score"],

                x["area_m2"]

            ),

            reverse=True
        )


    except Exception as e:

        print(
            "\nSCORING FAILED"
        )

        print(
            repr(e)
        )

        raise HTTPException(

            status_code=500,

            detail={

                "stage":
                    "scoring",

                "error_type":
                    type(e).__name__,

                "error":
                    str(e)
            }
        )


    top_prospects = (
        scored[:10]
    )


    print(
        "STEP 6 COMPLETE"
    )

    print(
        "Top prospects:",
        len(top_prospects)
    )


    # ========================================================
    # STEP 7 — FINAL DECISION
    # ========================================================

    print(
        "\nSTEP 7: Creating final decision..."
    )


    if len(scored) == 0:

        summary = (
            "No buildings matched "
            "the requested minimum area."
        )

    else:

        summary = (
            f"Found {len(scored)} "
            f"candidate buildings. "
            f"The top {len(top_prospects)} "
            f"have been ranked for "
            f"solar prospecting."
        )


    decision = {

        "summary":
            summary,

        "recommended_action":
            (
                "Prioritize the highest-scoring "
                "sites for roof-level verification."
            ),

        "confidence":
            (
                "medium"
                if len(scored) > 0
                else "low"
            )
    }


    print(
        "STEP 7 COMPLETE"
    )


    # ========================================================
    # FINAL RESPONSE
    # ========================================================

    result = {

        "status":
            "success",

        "query":
            request.query,

        "intent":
            intent,

        "resolved_location": {

            "name":
                location_result.address,

            "latitude":
                latitude,

            "longitude":
                longitude
        },

        "search_area": {

            "radius_km":
                radius_km
        },

        "satellite":
            satellite,

        "analysis": {

            "total_candidates":
                len(scored),

            "shortlisted":
                len(top_prospects),

            "minimum_area_m2":
                minimum_area
        },

        "top_prospects":
            top_prospects,

        "decision":
            decision,

        "limitations": [

            (
                "Building footprint is not "
                "equivalent to usable roof area."
            ),

            (
                "Solar suitability has not "
                "been verified from "
                "high-resolution imagery."
            ),

            (
                "Ownership, roof condition, "
                "structural suitability and "
                "shading require additional "
                "verification."
            )
        ]
    }


    print(
        "\n"
        "========================================"
    )

    print(
        "SKYLENS ANALYSIS COMPLETE"
    )

    print(
        "========================================\n"
    )


    return result