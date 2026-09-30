"""
Generic SkyLens analysis pipeline.

AnalysisSpec + UseCase
    -> location -> data collection -> candidate generation
    -> deterministic scoring -> ranking -> decision -> report
"""

from fastapi import HTTPException

from buildings import get_buildings

from geodata import (
    CONTEXT_LAYERS,
    candidate_geojson,
    collect_candidates_and_context,
    nearby_features,
    new_context,
)

from satellite import get_latest_satellite

from scoring import score_candidate

from use_cases import (
    DATA_LAYERS,
    USE_CASES,
    data_layer_available,
    describe_use_case,
)


DEFAULT_RADIUS_KM = 1

# MVP safety limit
MAX_RADIUS_KM = 2

TOP_N = 10

# Candidates sent to the progress map while the analysis runs.
PREVIEW_N = 60


def _no_emit(event, data):
    pass


# ============================================================
# NON-ANALYSIS RESPONSES
# ============================================================

def _supported_list():

    return [
        {
            "id": use_case.id,
            "title": use_case.title,
            "implemented": use_case.implemented,
        }
        for use_case in USE_CASES.values()
    ]


def unsupported_response(query, intent, spec):

    return {

        "status": "unsupported_use_case",

        "query": query,

        "intent": intent,

        "analysis_spec": spec.model_dump(),

        "message": (
            "This intent has been understood, but SkyLens does not "
            "yet have an analysis module for it."
        ),

        "detected_requirements": {
            "intent_type": spec.intent_type,
            "candidate_type": spec.candidate_type,
            "industry": spec.industry,
            "requirements": spec.requirements,
            "data_needed": spec.data_needed,
            "criteria": spec.criteria,
            "constraints": spec.constraints,
            "desired_output": spec.desired_output,
        },

        "supported_use_cases": _supported_list(),
    }


def not_implemented_response(query, intent, spec, use_case):

    missing_layers = [
        DATA_LAYERS[layer]["label"]
        for layer in use_case.data_layers
        if not data_layer_available(layer)
    ]

    return {

        "status": "not_yet_implemented",

        "query": query,

        "intent": intent,

        "analysis_spec": spec.model_dump(),

        "use_case": describe_use_case(use_case),

        "message": (
            f"SkyLens understood this as "
            f"{use_case.title.lower()}, but that analysis is not "
            f"implemented yet. No result has been produced."
        ),

        "missing_data": missing_layers,

        "limitations": list(use_case.limitations),
    }


# ============================================================
# HELPERS
# ============================================================

def area_filter(spec, use_case):
    """
    (min_m2, max_m2) used to drop candidates before scoring.
    """

    area = spec.area

    if area.min_m2:
        minimum = area.min_m2
    elif area.target_m2:
        minimum = area.target_m2 * use_case.target_tolerance
    else:
        minimum = use_case.default_min_area_m2

    return float(minimum), area.max_m2


def _radius(spec):

    radius_km = spec.radius_km or DEFAULT_RADIUS_KM

    return min(float(radius_km), MAX_RADIUS_KM)


def _geocode(geolocator, location_name):

    print("\nSTEP 2: Geocoding:", repr(location_name))

    try:

        result = geolocator.geocode(location_name)

    except Exception as e:

        print("\nGEOCODING FAILED")
        print(repr(e))

        raise HTTPException(
            status_code=502,
            detail={
                "stage": "geocoding",
                "location_text": location_name,
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )

    if not result:

        raise HTTPException(
            status_code=404,
            detail={
                "stage": "geocoding",
                "location_text": location_name,
                "error": f"Location not found: {location_name}",
            },
        )

    print("STEP 2 COMPLETE")
    print("Coordinates (Nominatim):", result.latitude, result.longitude)

    return result


def _satellite(latitude, longitude, radius_km):
    """
    Satellite imagery is context evidence. If it cannot be found
    the analysis continues and the report says so.
    Returns (satellite | None, status).
    """

    print("\nSTEP 4: Getting satellite imagery...")

    try:

        satellite = get_latest_satellite(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
        )

    except Exception as e:

        print("\nSATELLITE FAILED")
        print(repr(e))

        return None, f"failed: {type(e).__name__}: {e}"

    if not satellite:
        print("STEP 4: No suitable satellite imagery found.")
        return None, "failed: no scene under 20% cloud cover in 90 days"

    print("STEP 4 COMPLETE")
    print("Satellite:", satellite["id"])

    return satellite, "loaded"


def _candidates(use_case, latitude, longitude, radius_km, minimum, maximum,
                place):
    """
    Candidates plus the scoring context. For land and sites the
    context layers come from the same Overpass request.
    """

    print(
        f"\nSTEP 5: Generating candidates "
        f"({use_case.candidate_source})..."
    )

    source = use_case.candidate_source

    stage = "building_data" if source == "buildings" else "candidate_data"

    try:

        if source == "buildings":

            candidates = get_buildings(
                latitude=latitude,
                longitude=longitude,
                radius_km=radius_km,
                minimum_area_m2=minimum,
                include_geometry=True,
            )

            if maximum:
                candidates = [
                    c for c in candidates if c["area_m2"] <= maximum
                ]

            context = new_context(latitude, longitude, radius_km, place)

            context.layer_status["building_footprints"] = "loaded"

        else:

            layers = {
                c.data_layer
                for c in use_case.criteria
                if c.evaluator and c.data_layer in CONTEXT_LAYERS
            }

            print("Context layers requested:", sorted(layers))

            candidates, context = collect_candidates_and_context(
                source, latitude, longitude, radius_km,
                minimum, maximum, layers, place
            )

    except Exception as e:

        print("\nCANDIDATE DATA FAILED")
        print(repr(e))

        raise HTTPException(
            status_code=502,
            detail={
                "stage": stage,
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )

    print("STEP 5 COMPLETE")
    print("Candidates found:", len(candidates))

    return candidates, context


def _evidence(use_case, context, satellite_status):

    evidence = []

    for layer in use_case.data_layers:

        info = DATA_LAYERS.get(layer, {"label": layer})

        if layer == "satellite_imagery":
            status = satellite_status
        elif not data_layer_available(layer):
            status = "unavailable"
        else:
            status = context.layer_status.get(layer, "not loaded")

        if status == "loaded":
            state = "used"
        elif status.startswith("failed"):
            state = "failed"
        elif status == "unavailable":
            state = "unavailable"
        else:
            state = "not_loaded"

        entry = {
            "id": layer,
            "label": info.get("label", layer),
            "source": info.get("source"),
            "status": state,
        }

        if state == "failed":
            entry["detail"] = status[len("failed: "):]

        evidence.append(entry)

    return evidence


def _decision(use_case, scored, top):

    noun = use_case.candidate_noun

    if not scored:

        summary = f"No {noun}s matched the requested minimum area."

        if use_case.candidate_source != "buildings":
            summary += (
                " OpenStreetMap may not map open land in this area; "
                "this does not prove none exists."
            )

        return {
            "summary": summary,
            "recommended_action": (
                "Widen the search area or relax the size "
                "requirement, or verify on the ground."
            ),
            "confidence": "low",
        }

    summary = (
        f"Found {len(scored)} "
        f"candidate {noun}s. "
        f"The top {len(top)} "
        f"have been ranked for "
        f"{use_case.purpose or use_case.title.lower()}."
    )

    return {
        "summary": summary,
        "recommended_action": use_case.recommended_action,
        # Overall confidence follows the best candidate, whose
        # confidence already accounts for missing evidence.
        "confidence": top[0]["confidence"],
    }


# ============================================================
# RUN
# ============================================================

def run_analysis(query, intent, spec, use_case, geolocator, emit=None):
    """
    Run the analysis. emit(event, data) is called as each step
    finishes, for the streaming endpoint; see main.analyze_stream.
    """

    emit = emit or _no_emit

    def step(step_id, status, **data):
        emit("step", {"step": step_id, "status": status, **data})

    # --------------------------------------------------------
    # Location
    # --------------------------------------------------------

    if not spec.location:

        raise HTTPException(
            status_code=400,
            detail={
                "stage": "location_extraction",
                "error": "DeepSeek did not return usable location text.",
                "raw_location": intent.get("location"),
            },
        )

    location = _geocode(geolocator, spec.location)

    latitude = location.latitude

    longitude = location.longitude

    radius_km = _radius(spec)

    print("\nSTEP 3: Search radius:", radius_km, "km")

    step(
        "location", "done",
        name=location.address,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )

    step("evidence", "done", layers=describe_use_case(use_case)["data_layers"])

    step("candidates", "running")

    # --------------------------------------------------------
    # Data collection
    # --------------------------------------------------------

    satellite, satellite_status = _satellite(latitude, longitude, radius_km)

    emit("layer", {
        "id": "satellite_imagery",
        "status": "used" if satellite else "failed",
    })

    minimum, maximum = area_filter(spec, use_case)

    print("Minimum area:", minimum, "Maximum area:", maximum)

    place = spec.location.split(",")[0].strip()

    candidates, context = _candidates(
        use_case, latitude, longitude, radius_km, minimum, maximum, place
    )

    for layer in context.layer_status:
        emit("layer", {"id": layer, "status": "used"})

    emit("candidates", {
        "count": len(candidates),
        "preview": [
            {
                "latitude": c["latitude"],
                "longitude": c["longitude"],
                "geometry": candidate_geojson(c, context),
            }
            for c in candidates[:PREVIEW_N]
        ],
    })

    step("candidates", "done", count=len(candidates))

    step(
        "scoring", "running",
        criteria=len(use_case.criteria),
        measured=sum(
            1 for c in use_case.criteria
            if c.evaluator and context.has_layer(c.data_layer)
        ),
    )

    # --------------------------------------------------------
    # Scoring and ranking
    # --------------------------------------------------------

    print("\nSTEP 6: Scoring candidates...")

    try:

        scored = [
            score_candidate(candidate, spec, use_case, context)
            for candidate in candidates
        ]

        scored.sort(
            key=lambda x: (
                x["score"] if x["score"] is not None else -1,
                x["evidence_coverage"],
                x.get("area_m2") or 0,
            ),
            reverse=True,
        )

    except Exception as e:

        print("\nSCORING FAILED")
        print(repr(e))

        raise HTTPException(
            status_code=500,
            detail={
                "stage": "scoring",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )

    top = scored[:TOP_N]

    originals = {
        (c.get("osm_type"), c.get("osm_id")): c
        for c in candidates
    }

    for rank, candidate in enumerate(top, start=1):

        original = originals[(candidate.get("osm_type"), candidate.get("osm_id"))]

        candidate["rank"] = rank

        # Outline and surroundings for the maps.
        candidate["geometry"] = candidate_geojson(original, context)

        candidate["nearby"] = nearby_features(context, original)

    step("scoring", "done", ranked=len(top))

    print("STEP 6 COMPLETE")
    print("Top prospects:", len(top))

    # --------------------------------------------------------
    # Decision and report
    # --------------------------------------------------------

    print("\nSTEP 7: Creating final decision...")

    decision = _decision(use_case, scored, top)

    evidence = _evidence(use_case, context, satellite_status)

    missing_data = []

    for candidate in top:
        for item in candidate["missing_data"]:
            if item not in missing_data:
                missing_data.append(item)

    if not top:
        missing_data = [
            c.id for c in use_case.criteria
            if not (c.evaluator and data_layer_available(c.data_layer))
        ] + list(use_case.unassessed)

    limitations = list(use_case.limitations)

    # Map data failures abort the analysis (502), so imagery is the
    # only layer that can be missing from a successful report.
    if satellite is None:
        limitations.append(
            "No recent low-cloud satellite scene could be "
            "retrieved; imagery is context only and was not "
            "used in scoring."
        )

    print("STEP 7 COMPLETE")

    step("decision", "done")

    return {

        "status": "success",

        "query": query,

        "intent": intent,

        "analysis_spec": spec.model_dump(),

        "use_case": describe_use_case(use_case),

        "resolved_location": {
            "name": location.address,
            "latitude": latitude,
            "longitude": longitude,
        },

        "search_area": {
            "radius_km": radius_km,
        },

        "satellite": satellite,

        "evidence": evidence,

        "analysis": {
            "candidate_type": use_case.candidate_type,
            "total_candidates": len(scored),
            "shortlisted": len(top),
            "minimum_area_m2": minimum,
            "maximum_area_m2": maximum,
            "target_area_m2": spec.area.target_m2,
            "evidence_coverage": (
                top[0]["evidence_coverage"] if top else 0.0
            ),
        },

        "top_prospects": top,

        "decision": decision,

        "confidence": decision["confidence"],

        "missing_data": missing_data,

        "limitations": limitations,
    }
