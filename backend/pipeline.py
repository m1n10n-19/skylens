"""
Generic SkyLens analysis pipeline.

AnalysisSpec + UseCase
    -> location -> data collection -> candidate generation
    -> deterministic scoring -> ranking -> decision -> report
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from fastapi import HTTPException

from shapely.ops import transform as reproject

import data_registry

from assessment import assess

import change_detection

import terrain

import flood

import landcover

# A candidate at least this much inside a protected area is not ranked.
PROTECTED_DROP_SHARE = 0.95

from buildings import get_buildings_with_provenance

from geodata import (
    CONTEXT_LAYERS,
    candidate_geojson,
    collect_candidates_and_context,
    nearby_features,
    new_context,
)

from satellite import get_latest_satellite

import search_area

from scoring import score_candidate

from use_cases import (
    USE_CASES,
    data_layer_available,
    describe_use_case,
)


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
        data_registry.layer_label(layer)
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


def area_too_large_response(query, intent, spec, use_case, error):

    return {

        "status": "area_too_large",

        "query": query,

        "intent": intent,

        "analysis_spec": spec.model_dump(),

        "use_case": describe_use_case(use_case),

        "message": (
            f"{error.name} covers about {error.area_km2:,.0f} km², which is "
            f"too large to analyse in one go: SkyLens searches up to about "
            f"{search_area.MAX_SEARCH_KM2} km² at a time. Name a "
            f"neighbourhood, a road or a landmark instead, e.g. "
            f"\"Thoraipakkam, Chennai\" or \"along ECR near Kovalam\"."
        ),

        "location": error.name,

        "area_km2": round(error.area_km2),

        "max_area_km2": search_area.MAX_SEARCH_KM2,
    }


def data_unavailable_response(query, intent, spec, use_case, area, error):

    return {

        "status": "data_unavailable",

        "query": query,

        "intent": intent,

        "analysis_spec": spec.model_dump(),

        "use_case": describe_use_case(use_case),

        "message": (
            "SkyLens could not find clear enough satellite imagery to "
            "compare for this place and period, so no change has been "
            "measured. " + error.reason
        ),

        "suggestions": [
            "Try a different period: monsoon months are often cloudy.",
            "Ask about a longer period, e.g. \"since 2023\".",
        ],

        "search_area": area.public(),

        "scenes_checked": error.tried,
    }


# ============================================================
# HELPERS
# ============================================================

def candidate_key(candidate):
    """
    Identity of a candidate: its id, or its OSM type and id.
    """

    return candidate.get("candidate_id") or (candidate.get("osm_type"), candidate.get("osm_id"))


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


def _discover_open_land(area, context, osm_candidates, minimum, maximum):
    """
    Open land found in imagery (landcover.discover), added to the OSM
    candidates. Problems never stop the analysis: they are reported in
    completeness and the OSM candidates are still ranked.
    """

    from shapely.geometry import shape as to_shape

    print("\nSTEP 5a: Finding open land in imagery...")

    try:

        found, info = landcover.discover(
            to_shape(area.geometry), context,
            [c["_shape"] for c in osm_candidates], minimum, maximum,
        )

    except Exception as e:

        print("OPEN LAND DISCOVERY FAILED:", repr(e))

        context.meta["land_cover_problem"] = (
            f"Open land could not be looked for in imagery ({type(e).__name__}: {e}); "
            "only land tagged on OpenStreetMap was considered."
        )

        return []

    context.layer_status["land_cover"] = "loaded"

    context.layer_provenance["land_cover"] = {
        "source_id": info["source"], "observed_at": None, "data_as_of": info["land_cover_year"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    context.meta["land_cover"] = info

    if info["problems"]:
        context.meta["land_cover_problem"] = " ".join(info["problems"])

    print("STEP 5a COMPLETE:", len(found), "patches added")

    return found


def _drop_protected(candidates, context):
    """
    (kept, dropped): candidates at least PROTECTED_DROP_SHARE inside a
    mapped protected area or reserved forest are not ranked.
    """

    zones = [z for z in context.protected if z.kind != "wetland"]

    if not zones:
        return candidates, []

    kept, dropped = [], []

    for candidate in candidates:

        shape = context.shape_of(candidate)

        inside = sum(shape.intersection(z.shape).area for z in zones if shape.intersects(z.shape))

        share = inside / shape.area if shape.area else (1.0 if any(z.shape.contains(shape) for z in zones) else 0.0)

        (dropped if share >= PROTECTED_DROP_SHARE else kept).append(candidate)

    return kept, dropped


def _candidates(use_case, spec, area, minimum, maximum, place):
    """
    Candidates plus the scoring context. For land and sites the
    context layers come from the same Overpass request; for changes
    the candidates are patches found by comparing satellite scenes.
    Raises ChangeDataUnavailable when no clear imagery exists.
    """

    print(
        f"\nSTEP 5: Generating candidates "
        f"({use_case.candidate_source})..."
    )

    source = use_case.candidate_source

    stage = {"buildings": "building_data", "changes": "imagery_data"}.get(source, "candidate_data")

    try:

        if source == "buildings":

            candidates, provenance = get_buildings_with_provenance(
                latitude=area.latitude,
                longitude=area.longitude,
                radius_km=area.reach_km,
                minimum_area_m2=minimum,
                include_geometry=True,
                # Boxes keep the original query (and its cache).
                area_filters=None if area.kind == "radius" else area.overpass_filters,
            )

            if maximum:
                candidates = [
                    c for c in candidates if c["area_m2"] <= maximum
                ]

            context = new_context(area.latitude, area.longitude, area.reach_km, place)

            context.layer_status["building_footprints"] = "loaded"

            context.layer_provenance["building_footprints"] = provenance

        elif source == "changes":

            candidates, context = change_detection.collect_changes(
                area, spec, place, min_area_m2=minimum,
            )

        else:

            layers = {
                c.data_layer
                for c in use_case.criteria
                if c.evaluator and c.data_layer in CONTEXT_LAYERS
            }

            print("Context layers requested:", sorted(layers))

            candidates, context = collect_candidates_and_context(
                source, area, minimum, maximum, layers, place
            )

            if use_case.discover_open_land:
                candidates += _discover_open_land(area, context, candidates, minimum, maximum)

    except change_detection.ChangeDataUnavailable:
        raise

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
            "label": data_registry.layer_label(layer),
            "source": data_registry.source_label(layer),
            "status": state,
        }

        if state == "failed":
            entry["detail"] = status[len("failed: "):]

        evidence.append(entry)

    return evidence


def _recent_change(use_case, spec, context, top, originals, emit):
    """
    For use cases with a recent_change criterion: compare satellite
    images over the shortlisted sites only (one read for all) and
    attach each site's measurement to its candidate. Imagery problems
    never stop the analysis; they are reported in completeness.

    Returns True if the measurement was attached.
    """

    if not top or not any(c.evaluator == "recent_change" for c in use_case.criteria):
        return False

    print("\nSTEP 6b: Checking recent change on the shortlist...")

    proj = context.proj

    shortlist = [originals[candidate_key(c)] for c in top]

    geometries = [
        reproject(lambda x, y, z=None: proj.to_lonlat(x, y), context.shape_of(c))
        for c in shortlist
    ]

    time_range = spec.time_range

    try:

        parcels, scenes = change_detection.measure_parcels(
            geometries,
            before_date=date.fromisoformat(time_range.start) if time_range.start else None,
            after_date=date.fromisoformat(time_range.end) if time_range.end else None,
            match_season=time_range.start_precision == "year",
        )

    except change_detection.ChangeDataUnavailable as e:

        print("RECENT CHANGE: no clear imagery:", e.reason)

        context.meta["recent_change_problem"] = (
            f"Recent change on the shortlisted sites could not be checked: {e.reason}"
        )

        emit("layer", {"id": "historical_imagery", "status": "failed"})

        return False

    except Exception as e:

        print("RECENT CHANGE FAILED:", repr(e))

        context.meta["recent_change_problem"] = (
            "Recent change on the shortlisted sites could not be checked "
            f"({type(e).__name__}: {e})."
        )

        emit("layer", {"id": "historical_imagery", "status": "failed"})

        return False

    for candidate, parcel in zip(shortlist, parcels):
        candidate["recent_change"] = parcel

    context.layer_status["historical_imagery"] = "loaded"

    context.layer_provenance["historical_imagery"] = {
        "source_id": scenes["source"],
        "observed_at": scenes["after"]["acquired_at"],
        "data_as_of": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    context.meta["recent_change"] = scenes

    emit("layer", {"id": "historical_imagery", "status": "used"})

    print("STEP 6b COMPLETE")

    return True


def _environment(use_case, context, candidates, emit):
    """
    For use cases with terrain or flood-exposure criteria: measure
    every candidate before scoring (flood exposure is scored, so sites
    outside the shortlist must not rank without it). One read per
    source for the whole set. Problems never stop the analysis; they
    are reported in completeness.
    """

    evaluators = {c.evaluator for c in use_case.criteria}

    wants_terrain = "terrain" in evaluators or "flood_exposure" in evaluators

    wants_flood = "flood_exposure" in evaluators

    if not (wants_terrain or wants_flood):
        return

    if not candidates:
        # Nothing to measure: these layers are not missing.
        for layer, wanted in (("terrain", wants_terrain), ("flood_risk", wants_flood)):
            if wanted:
                context.layer_status[layer] = "not_needed"
        return

    print("\nSTEP 5b: Measuring terrain and flood exposure...")

    proj = context.proj

    to_lonlat = lambda shape: reproject(lambda x, y, z=None: proj.to_lonlat(x, y), shape)

    shapes = [context.shape_of(c) for c in candidates]

    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")

    jobs = ThreadPoolExecutor(max_workers=2)

    terrain_job = jobs.submit(terrain.measure_sites, [
        (to_lonlat(shape), to_lonlat(shape.buffer(terrain.RING_M)), c.get("site_kind") == "building")
        for c, shape in zip(candidates, shapes)
    ]) if wants_terrain else None

    flood_job = jobs.submit(
        flood.measure_sites, [to_lonlat(shape) for shape in shapes],
        area_center=(context.proj.lat0, context.proj.lon0),
    ) if wants_flood else None

    jobs.shutdown(wait=False)

    if terrain_job is not None:

        try:

            results, info = terrain_job.result()

            for candidate, result in zip(candidates, results):
                candidate["terrain"] = result

            context.layer_status["terrain"] = "loaded"

            context.layer_provenance["terrain"] = {
                "source_id": info["source"], "observed_at": None,
                "data_as_of": None, "retrieved_at": retrieved,
            }

            context.meta["terrain"] = info

            emit("layer", {"id": "terrain", "status": "used"})

        except Exception as e:

            print("TERRAIN FAILED:", repr(e))

            context.meta["terrain_problem"] = (
                f"Terrain could not be measured ({type(e).__name__}: {e})."
            )

            emit("layer", {"id": "terrain", "status": "failed"})

    if flood_job is not None:

        try:

            results, info = flood_job.result()

            radar = "observed_flooding" in info

            if not radar and "water_history" not in info:
                raise RuntimeError("; ".join(info["problems"]) or "no flood evidence")

            for candidate, result in zip(candidates, results):
                candidate["flood"] = result

            context.layer_status["flood_risk"] = "loaded"

            context.layer_provenance["flood_risk"] = {
                "source_id": flood.S1_SOURCE_ID if radar else flood.JRC_SOURCE_ID,
                "observed_at": None, "data_as_of": None, "retrieved_at": retrieved,
            }

            context.meta["flood"] = info

            if info["problems"]:
                context.meta["flood_problem"] = " ".join(info["problems"])

            emit("layer", {"id": "flood_risk", "status": "used"})

        except Exception as e:

            print("FLOOD EXPOSURE FAILED:", repr(e))

            context.meta["flood_problem"] = (
                f"Flood exposure could not be measured ({type(e).__name__}: {e})."
            )

            emit("layer", {"id": "flood_risk", "status": "failed"})

    print("STEP 5b COMPLETE")


def _completeness(use_case, area, context, satellite_status):
    """
    Whether the analysis ran everything it planned with the providers
    SkyLens has. Layers with no provider at all do not make a result
    partial (they are reported in missing_data); failures, skipped
    layers and partially searched places do.
    """

    reasons = []

    if satellite_status != "loaded":
        detail = satellite_status.removeprefix("failed: ")
        reasons.append(f"Satellite imagery could not be retrieved ({detail}).")

    skipped = sorted({
        c.data_layer
        for c in use_case.criteria
        if c.evaluator
        and c.weight > 0
        and data_registry.is_available(c.data_layer)
        and not context.has_layer(c.data_layer)
        and context.layer_status.get(c.data_layer) != "not_needed"
    })

    for layer in skipped:
        reasons.append(
            f"Not loaded for this analysis: {data_registry.layer_label(layer)}."
        )

    for problem in ("land_cover_problem", "recent_change_problem", "terrain_problem", "flood_problem"):
        if context.meta.get(problem):
            reasons.append(context.meta[problem])

    if area.place_area_km2:
        reasons.append(
            f"Only part of {area.name} was searched "
            f"({area.area_km2:,.0f} of about {area.place_area_km2:,.0f} km²)."
        )

    return {
        "status": "partial" if reasons else "complete",
        "reasons": reasons,
    }


def _change_decision(use_case, scored, top, context):

    meta = context.meta["change_detection"]

    period = f"between {meta['before']['date']} and {meta['after']['date']}"

    if not scored:
        return {
            "summary": (
                f"No change above the detection thresholds was found {period}. "
                "This does not rule out changes smaller than about 500 m²."
            ),
            "recommended_action": (
                "Try a longer period, or check smaller sites on "
                "high-resolution imagery."
            ),
            "confidence": "low",
        }

    return {
        "summary": (
            f"Found {len(scored)} changed areas {period}. The top {len(top)} "
            f"are ranked by how strong and how large the change is, and "
            f"how comparable the two images are."
        ),
        "recommended_action": use_case.recommended_action,
        "confidence": top[0]["confidence"],
    }


def _decision(use_case, scored, top, context=None):

    if use_case.candidate_source == "changes":
        return _change_decision(use_case, scored, top, context)

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

    print("\nSTEP 2: Resolving search area:", repr(spec.location))

    try:
        area = search_area.resolve(geolocator, spec.location, spec.radius_km)
    except search_area.AreaTooLarge as e:
        print("AREA TOO LARGE:", e)
        return area_too_large_response(query, intent, spec, use_case, e)

    latitude = area.latitude

    longitude = area.longitude

    print("STEP 2 COMPLETE:", area.description)

    step(
        "location", "done",
        name=area.address,
        latitude=latitude,
        longitude=longitude,
        kind=area.kind,
        description=area.description,
        geometry=area.geometry,
    )

    step("evidence", "done", layers=describe_use_case(use_case)["data_layers"])

    step("candidates", "running")

    # --------------------------------------------------------
    # Data collection
    # --------------------------------------------------------

    satellite, satellite_status = _satellite(latitude, longitude, min(area.reach_km, 5))

    emit("layer", {
        "id": "satellite_imagery",
        "status": "used" if satellite else "failed",
    })

    minimum, maximum = area_filter(spec, use_case)

    print("Minimum area:", minimum, "Maximum area:", maximum)

    place = spec.location.split(",")[0].strip()

    try:
        candidates, context = _candidates(use_case, spec, area, minimum, maximum, place)
    except change_detection.ChangeDataUnavailable as e:
        print("NO CLEAR IMAGERY:", e.reason)
        return data_unavailable_response(query, intent, spec, use_case, area, e)

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

    candidates, protected_dropped = _drop_protected(candidates, context)

    step("candidates", "done", count=len(candidates))

    _environment(use_case, context, candidates, emit)

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

    originals = {candidate_key(c): c for c in candidates}

    # Evidence only (weight 0): rescoring the shortlist adds it
    # without changing scores or ranks.
    if _recent_change(use_case, spec, context, top, originals, emit):
        top = [
            score_candidate(originals[candidate_key(c)], spec, use_case, context)
            for c in top
        ]

    for rank, candidate in enumerate(top, start=1):

        original = originals[candidate_key(candidate)]

        candidate["rank"] = rank

        # Outline and surroundings for the maps.
        candidate["geometry"] = candidate_geojson(original, context)

        candidate["nearby"] = nearby_features(context, original)

        # Known / unknown / what to verify, from the evidence above.
        candidate["assessment"] = assess(candidate, use_case)

    step("scoring", "done", ranked=len(top))

    print("STEP 6 COMPLETE")
    print("Top prospects:", len(top))

    # --------------------------------------------------------
    # Decision and report
    # --------------------------------------------------------

    print("\nSTEP 7: Creating final decision...")

    decision = _decision(use_case, scored, top, context)

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

    limitations = [f"Searched {area.description}."] + list(use_case.limitations)

    if protected_dropped:
        names = sorted({z.name for z in context.protected if z.name and z.kind != "wetland"})
        limitations.append(
            f"{len(protected_dropped)} candidate(s) lying inside mapped protected areas"
            + (f" ({', '.join(names)})" if names else "")
            + " were not ranked."
        )

    found_in_imagery = sum(1 for c in candidates if c.get("discovered"))

    if found_in_imagery:
        limitations.append(
            f"{found_in_imagery} candidate(s) are open land found in the 2021 land-cover map "
            "and not tagged on OpenStreetMap: patches split at roads, not legal plots."
        )

    change_meta = context.meta.get("change_detection")

    if change_meta:

        before, after = change_meta["before"], change_meta["after"]

        limitations.insert(1, (
            f"Compared {change_meta['sensor']} images ({change_meta['resolution_m']} m) "
            f"from {before['date']} and {after['date']} "
            f"({change_meta['season_gap_days']} days apart in the year)."
        ))

        if before.get("last_resort") or after.get("last_resort"):
            limitations.insert(2, (
                "A Landsat 7 image from after May 2003 was used because no other "
                "clear image existed; its permanent striped data gaps count as "
                "missing data, not as change."
            ))

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
            "name": area.address,
            "latitude": latitude,
            "longitude": longitude,
        },

        "search_area": area.public(),

        "satellite": satellite,

        "evidence": evidence,

        "analysis": {
            "candidate_type": use_case.candidate_type,
            "total_candidates": len(scored),
            "shortlisted": len(top),
            "minimum_area_m2": minimum,
            "maximum_area_m2": maximum,
            "target_area_m2": spec.area.target_m2,
            "found_in_imagery": sum(1 for c in candidates if c.get("discovered")),
            "excluded_as_protected": len(protected_dropped),
            "evidence_coverage": (
                top[0]["evidence_coverage"] if top else 0.0
            ),
        },

        "top_prospects": top,

        "decision": decision,

        "confidence": decision["confidence"],

        "missing_data": missing_data,

        "limitations": limitations,

        "completeness": _completeness(use_case, area, context, satellite_status),

        # Which imagery a change analysis compared; absent otherwise.
        **({"change_detection": change_meta} if change_meta else {}),
    }
