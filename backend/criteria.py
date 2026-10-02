"""
Criterion evaluators.

Each evaluator measures ONE criterion for ONE candidate from data
SkyLens actually has, and returns:

    {
        "score": 0-100,
        "evidence": "what was measured",
        "reasons": ["short human-readable reason", ...],
        "measurements": {"plain_value": 123, ...}
    }

or None when the evidence cannot be measured for this candidate.
Evaluators never invent values.

Every measurement key must be listed in evidence.MEASUREMENTS, which
gives its unit, claim and status for the evidence records.

Signature: evaluator(candidate, context, spec) -> dict | None
"""

import math

from change_detection import LABELS as CHANGE_LABELS
from change_detection import PARCEL_ALERT_SHARE

from terrain import LOW_LYING_M, NOISE_M

from flood import FLOODED_SHARE

from geodata import (
    CHARGER_RADIUS_M,
    building_cover,
    MAJOR_ROAD_RADIUS_M,
    PARKING_RADIUS_M,
    POI_RADIUS_M,
    ROAD_RADIUS_M,
    count_within,
    is_commercial_poi,
    is_dwell_poi,
    nearest_road,
)


def _m(distance):

    return f"{round(distance):,} m"


def _area(area_m2):

    return f"{round(area_m2):,} m²"


def _road_label(road):
    """
    "OMR (primary)" for named roads, "residential road" otherwise.
    """

    kind = road.highway.replace("_", " ")

    return f"{road.name} ({kind})" if road.name else f"{kind} road"


# ============================================================
# SOLAR (legacy scoring, expressed as criteria)
# ============================================================

# Points from the original solar scorer: size tiers + large-site
# bonus (max 70), building type (max 30). Also used by
# scoring.score_solar_candidate() for GET /solar/prospects.

def solar_footprint_size(candidate, context, spec):

    area = candidate.get("area_m2", 0)

    points = 0

    reasons = []

    if area >= 10000:
        points += 50
        reasons.append("Exceptional building footprint")
    elif area >= 5000:
        points += 40
        reasons.append("Very large building footprint")
    elif area >= 2000:
        points += 30
        reasons.append("Large building footprint")
    elif area >= 1000:
        points += 20
        reasons.append("Medium-large building footprint")
    elif area >= 500:
        points += 10
        reasons.append("Building exceeds 500 m² threshold")

    if area >= 10000:
        points += 20
        reasons.append("Exceptional footprint size")
    elif area >= 5000:
        points += 10
        reasons.append("Strong footprint size")

    return {
        "score": points / 70 * 100,
        "evidence": f"Footprint {_area(area)}",
        "reasons": reasons,
        "measurements": {"footprint_area_m2": area},
    }


SOLAR_HIGH_VALUE_TYPES = {
    "commercial", "industrial", "warehouse", "retail", "office",
}

SOLAR_INSTITUTIONAL_TYPES = {
    "school", "university", "hospital", "college", "institutional",
}

SOLAR_RESIDENTIAL_TYPES = {
    "apartments", "residential", "house", "detached",
}


def solar_building_type(candidate, context, spec):

    building_type = (
        candidate.get("building_type") or "unknown"
    ).lower()

    if building_type in SOLAR_HIGH_VALUE_TYPES:
        points = 30
        reason = f"Potentially relevant {building_type} building"
    elif building_type in SOLAR_INSTITUTIONAL_TYPES:
        points = 20
        reason = f"Large institutional {building_type} building"
    elif building_type in SOLAR_RESIDENTIAL_TYPES:
        points = 5
        reason = "Residential building requires additional qualification"
    else:
        points = 5
        reason = "Building type requires verification"

    return {
        "score": points / 30 * 100,
        "evidence": f"OSM building tag: {building_type}",
        "reasons": [reason],
        "measurements": {"building_type_tag": building_type},
    }


# ============================================================
# SIZE
# ============================================================

def size_fit(candidate, context, spec):
    """
    How well the site matches the requested size. A site near the
    target scores best; much larger sites are usable but wasteful.
    """

    area = candidate.get("area_m2")

    if not area:
        return None

    target = spec.area.target_m2

    minimum = spec.area.min_m2

    maximum = spec.area.max_m2

    stated = spec.area.as_stated

    if target:

        r = area / target

        if r < 0.5:
            score = 20
        elif r < 0.8:
            score = 40 + (r - 0.5) / 0.3 * 30
        elif r <= 1.5:
            score = 100
        elif r <= 3:
            score = 100 - (r - 1.5) / 1.5 * 25
        elif r <= 10:
            score = 75 - (r - 3) / 7 * 35
        else:
            score = 40

        wanted = stated or _area(target)

        evidence = f"{_area(area)} vs target {wanted} ({_area(target)})"

        if 0.8 <= r <= 1.5:
            reasons = [f"Size fits the requested {wanted}"]
        elif r < 0.8:
            reasons = [f"Smaller than the requested {wanted}"]
        else:
            reasons = [f"Larger than needed ({r:.1f}x the requested size)"]

        return {
            "score": score,
            "evidence": evidence,
            "reasons": reasons,
            "measurements": {"site_area_m2": area},
        }

    if minimum:

        if area < minimum:
            score = 30
            reasons = [f"Below the requested minimum of {_area(minimum)}"]
        elif maximum and area > maximum:
            score = 30
            reasons = [f"Above the requested maximum of {_area(maximum)}"]
        elif area <= minimum * 3:
            score = 100
            reasons = [f"Meets the requested minimum of {_area(minimum)}"]
        else:
            score = 80
            reasons = ["Well above the requested minimum"]

        return {
            "score": score,
            "evidence": f"{_area(area)} vs minimum {_area(minimum)}",
            "reasons": reasons,
            "measurements": {"site_area_m2": area},
        }

    # No size requested: medium-sized sites are the safe default.
    score = min(100, 40 + 60 * math.log10(max(area, 1) / 100) / 2)

    return {
        "score": max(score, 20),
        "evidence": f"{_area(area)} (no size requested)",
        "reasons": [],
        "measurements": {"site_area_m2": area},
    }


def parcel_size(candidate, context, spec):
    """
    For land acquisition: bigger is better once above the minimum.
    """

    area = candidate.get("area_m2")

    if not area:
        return None

    minimum = spec.area.min_m2 or spec.area.target_m2

    if not minimum:
        score = min(100, 30 + 70 * math.log10(max(area, 1) / 1000) / 2)
        return {
            "score": max(score, 10),
            "evidence": f"{_area(area)} (no minimum requested)",
            "reasons": [f"{_area(area)} parcel"],
            "measurements": {"site_area_m2": area},
        }

    if area < minimum:
        return {
            "score": 20,
            "evidence": f"{_area(area)} vs minimum {_area(minimum)}",
            "reasons": [f"Below the requested minimum of {_area(minimum)}"],
            "measurements": {"site_area_m2": area},
        }

    score = 60 + 40 * min(1, math.log(area / minimum) / math.log(4))

    return {
        "score": score,
        "evidence": f"{_area(area)} vs minimum {_area(minimum)}",
        "reasons": [
            f"{_area(area)} parcel, {area / minimum:.1f}x the requested minimum"
        ],
        "measurements": {"site_area_m2": area},
    }


# ============================================================
# ACCESS
# ============================================================

def road_access(candidate, context, spec):

    shape = context.shape_of(candidate)

    distance, road = nearest_road(context, shape, ROAD_RADIUS_M)

    if road is None:
        return {
            "score": 5,
            "evidence": "No mapped vehicle road within 500 m",
            "reasons": ["No mapped road access nearby"],
            "measurements": {"nearest_road_m": None},
        }

    if distance <= 15:
        score = 100
    elif distance <= 40:
        score = 85
    elif distance <= 80:
        score = 65
    elif distance <= 150:
        score = 45
    elif distance <= 300:
        score = 25
    else:
        score = 10

    if road.highway == "service":
        score *= 0.85

    label = road.name or road.highway.replace("_", " ") + " road"

    evidence = f"Nearest road: {_road_label(road)}, {_m(distance)}"

    if distance <= 15:
        reasons = [f"Direct frontage on {label}"]
    elif distance <= 80:
        reasons = [f"Road access via {label} ({_m(distance)})"]
    else:
        reasons = [f"Weak road access: nearest road {_m(distance)} away"]

    return {
        "score": score,
        "evidence": evidence,
        "reasons": reasons,
        "measurements": {
            "nearest_road_m": round(distance),
            "nearest_road": label,
            "nearest_road_type": road.highway,
        },
    }


def major_road_proximity(candidate, context, spec):

    shape = context.shape_of(candidate)

    distance, road = nearest_road(
        context, shape, MAJOR_ROAD_RADIUS_M, major_only=True
    )

    if road is None:
        return {
            "score": 5,
            "evidence": "No primary/secondary/trunk road within 1 km",
            "reasons": [],
            "measurements": {"nearest_major_road_m": None},
        }

    if distance <= 50:
        score = 100
    elif distance <= 150:
        score = 85
    elif distance <= 300:
        score = 65
    elif distance <= 600:
        score = 40
    else:
        score = 20

    label = road.name or road.highway.replace("_", " ") + " road"

    reasons = (
        [f"Close to major road {label} ({_m(distance)})"]
        if distance <= 150 else []
    )

    return {
        "score": score,
        "evidence": f"Nearest major road: {_road_label(road)}, {_m(distance)}",
        "reasons": reasons,
        "measurements": {
            "nearest_major_road_m": round(distance),
            "nearest_major_road": label,
        },
    }


# ============================================================
# ACTIVITY AND DEMAND (proxies from mapped places)
# ============================================================

def _saturating(count, full_at):

    return min(100, 100 * math.log1p(count) / math.log1p(full_at))


def demand_potential(candidate, context, spec):

    shape = context.shape_of(candidate)

    nearby = [
        poi for poi in count_within(context.pois, shape, POI_RADIUS_M)
        if is_dwell_poi(poi)
    ]

    n = len(nearby)

    score = _saturating(n, 40)

    reasons = (
        [f"{n} dwell-time destinations within 500 m"]
        if n >= 10 else
        ["Few mapped destinations nearby (low demand signal)"]
        if n <= 2 else []
    )

    return {
        "score": score,
        "evidence": (
            f"{n} mapped restaurants, malls, offices, hotels, "
            f"hospitals etc. within 500 m (OSM proxy)"
        ),
        "reasons": reasons,
        "measurements": {"dwell_destinations_500m": n},
    }


def commercial_activity(candidate, context, spec):

    shape = context.shape_of(candidate)

    n = len([
        poi for poi in count_within(context.pois, shape, POI_RADIUS_M)
        if is_commercial_poi(poi)
    ])

    score = _saturating(n, 80)

    reasons = (
        [f"Busy commercial area ({n} mapped businesses within 500 m)"]
        if n >= 25 else
        ["Little mapped commercial activity nearby"]
        if n <= 3 else []
    )

    return {
        "score": score,
        "evidence": f"{n} mapped shops, offices and amenities within 500 m",
        "reasons": reasons,
        "measurements": {"businesses_500m": n},
    }


# ============================================================
# COMPETITION
# ============================================================

def ev_competition(candidate, context, spec):

    shape = context.shape_of(candidate)

    within_2km = count_within(context.chargers, shape, CHARGER_RADIUS_M)

    within_1km = [
        poi for poi in within_2km
        if shape.distance(poi.point) <= 1000
    ]

    near = len(within_1km)

    far = len(within_2km) - near

    score = max(10, 100 - 20 * near - 8 * far)

    if not within_2km:
        return {
            "score": score,
            "evidence": (
                "No charging stations mapped in OSM within 2 km "
                "(OSM coverage of chargers may be incomplete)"
            ),
            "reasons": ["No mapped competing chargers within 2 km"],
            "measurements": {
                "chargers_2km": 0,
                "chargers_1km": 0,
                "nearest_charger_m": None,
            },
        }

    nearest = min(shape.distance(poi.point) for poi in within_2km)

    return {
        "score": score,
        "evidence": (
            f"{len(within_2km)} mapped charging station(s) within 2 km; "
            f"nearest {_m(nearest)}"
        ),
        "reasons": (
            [f"{near} existing charger(s) within 1 km"]
            if near else []
        ),
        "measurements": {
            "chargers_2km": len(within_2km),
            "chargers_1km": near,
            "nearest_charger_m": round(nearest),
        },
    }


# Business keyword -> OSM tags of similar businesses.
COMPETITOR_TAGS = (
    (("food court", "food_court", "restaurant", "eatery", "dining"),
     {"amenity": {"food_court", "restaurant", "fast_food"}}),
    (("cafe", "coffee", "tea"),
     {"amenity": {"cafe"}}),
    (("pharmacy", "chemist", "medical store"),
     {"amenity": {"pharmacy"}, "shop": {"chemist"}}),
    (("supermarket", "grocery", "convenience"),
     {"shop": {"supermarket", "convenience", "grocery"}}),
    (("clinic", "hospital", "diagnostic"),
     {"amenity": {"clinic", "hospital", "doctors"}}),
    (("bank",),
     {"amenity": {"bank"}}),
    (("petrol", "fuel", "gas station"),
     {"amenity": {"fuel"}}),
    (("hotel", "lodge"),
     {"tourism": {"hotel", "motel"}}),
    (("salon", "beauty", "spa"),
     {"shop": {"hairdresser", "beauty"}}),
    (("bakery",),
     {"shop": {"bakery"}}),
    (("clothing", "apparel", "boutique", "textile"),
     {"shop": {"clothes", "boutique", "fabric"}}),
    (("electronics", "mobile"),
     {"shop": {"electronics", "mobile_phone"}}),
    (("showroom", "car dealer"),
     {"shop": {"car", "motorcycle"}}),
)


def _business_text(spec):

    parts = [spec.industry or ""]

    for key in ("business_type", "business", "use", "purpose", "facility"):

        value = spec.requirements.get(key)

        if isinstance(value, str):
            parts.append(value)

    return " ".join(parts).lower().replace("_", " ")


def business_competition(candidate, context, spec):

    text = _business_text(spec)

    matched = None

    for keywords, tags in COMPETITOR_TAGS:
        if any(k.replace("_", " ") in text for k in keywords):
            matched = tags
            break

    if matched is None:
        # We do not know which businesses compete; do not guess.
        return None

    shape = context.shape_of(candidate)

    competitors = [
        poi for poi in count_within(context.pois, shape, POI_RADIUS_M)
        if any(poi.value(key) in values for key, values in matched.items())
    ]

    n = len(competitors)

    score = max(10, 100 - 12 * n)

    return {
        "score": score,
        "evidence": f"{n} similar mapped businesses within 500 m",
        "reasons": (
            [f"Crowded: {n} similar businesses within 500 m"]
            if n >= 5 else
            ["Few similar businesses nearby"]
            if n <= 1 else []
        ),
        "measurements": {"competitors_500m": n},
    }


# ============================================================
# PARKING
# ============================================================

def parking_potential(candidate, context, spec):

    shape = context.shape_of(candidate)

    lots = count_within(context.parking, shape, PARKING_RADIUS_M)

    n = len(lots)

    score = min(100, 20 + 30 * n)

    reasons = []

    needed = spec.area.target_m2 or spec.area.min_m2

    area = candidate.get("area_m2") or 0

    if (
        candidate.get("site_kind") == "open_land"
        and needed
        and area >= 2 * needed
    ):
        score = min(100, score + 40)
        reasons.append("Spare land for on-site parking")

    if n:
        reasons.append(f"{n} mapped parking area(s) within 300 m")

    return {
        "score": score,
        "evidence": (
            f"{n} mapped parking area(s) within 300 m; "
            f"site {_area(area)}"
        ),
        "reasons": reasons,
        "measurements": {"parking_areas_300m": n},
    }


# ============================================================
# LAND
# ============================================================

VACANCY_SCORES = {
    "vacant": (90, "Tagged as vacant land"),
    "brownfield": (90, "Tagged as brownfield (cleared) land"),
    "greenfield": (80, "Tagged as greenfield land"),
    "meadow": (60, "Open grass; could be a park or verge, verify"),
    "grass": (55, "Grass area; could be a park or verge, verify"),
    "scrub": (65, "Scrub land"),
    "grassland": (60, "Open grassland"),
    "farmland": (45, "Farmland; conversion approvals likely needed"),
}


# Land cover of open land found in imagery (ESA WorldCover): lower
# than the matching OSM tags, as the map is from 2021 and unverified.
VACANCY_LANDCOVER = {
    "bare ground": (75, "Bare ground in the 2021 land-cover map"),
    "shrubland": (60, "Shrubland in the 2021 land-cover map"),
    "grassland": (55, "Grassland in the 2021 land-cover map; could be a park or verge, verify"),
    "cropland": (45, "Cropland in the 2021 land-cover map; conversion approvals likely needed"),
    "tree cover": (35, "Tree cover in the 2021 land-cover map; clearing and permissions likely needed"),
}


# Mapped buildings covering at least this share of open land make it
# "not vacant" for scoring (sites at 20% or more are not ranked).
BUILT_COVER_WARNING = 0.05

BUILT_COVER_SCORE = 30


def _with_buildings(result, candidate, context):
    """
    Add mapped building cover to a vacancy result for open land.
    """

    if context is None or not context.has_layer("building_footprints"):
        return result

    cover = building_cover(context, context.shape_of(candidate))

    result["measurements"]["building_cover_share"] = round(cover, 3)

    if cover >= BUILT_COVER_WARNING:
        result["score"] = min(result["score"], BUILT_COVER_SCORE)
        result["evidence"] += f"; mapped buildings cover {round(cover * 100)}% of it"
        result["reasons"].append(
            f"Warning: mapped buildings cover {round(cover * 100)}% of the site: it may not be vacant"
        )

    return result


def vacancy_evidence(candidate, context, spec):

    landcover = candidate.get("landcover")

    if landcover in VACANCY_LANDCOVER:

        score, reason = VACANCY_LANDCOVER[landcover]

        found = candidate.get("discovered") or {}

        return _with_buildings({
            "score": score,
            "evidence": (
                f"Land-cover map ({found.get('land_cover_year') or '2021'}): {landcover}; "
                f"not tagged on OpenStreetMap. {found.get('note', '')}".strip()
            ),
            "reasons": [reason, "Found in imagery, not on the map"],
            "measurements": {"landcover_class": landcover},
        }, candidate, context)

    landuse = candidate.get("landuse")

    if landuse not in VACANCY_SCORES:
        return None

    score, reason = VACANCY_SCORES[landuse]

    return _with_buildings({
        "score": score,
        "evidence": f"OSM land-use tag: {landuse} (not verified on imagery)",
        "reasons": [reason],
        "measurements": {"landuse_tag": landuse},
    }, candidate, context)


def location_proximity(candidate, context, spec):

    shape = context.shape_of(candidate)

    distance = shape.distance(context.center)

    radius_m = context.radius_km * 1000

    score = 100 - 80 * min(1, distance / max(radius_m, 1))

    place = context.location_name or "the requested location"

    return {
        "score": score,
        "evidence": f"{_m(distance)} from the centre of {place}",
        "reasons": (
            [f"Within {_m(distance)} of {place}"]
            if distance <= radius_m / 3 else []
        ),
        "measurements": {"distance_to_centre_m": round(distance)},
    }


# ============================================================
# CHANGE (patches from change_detection.py)
# ============================================================

def _day(iso):

    return iso[:10] if iso else "unknown date"


def change_magnitude(candidate, context, spec):
    """
    How strongly the spectral index changed across the patch: 30 at
    the detection threshold, 100 at a full-scale change.
    """

    change = candidate.get("change")

    if not change:
        return None

    delta = change["delta_mean"]

    low, full = change["min_change"], change["full_scale"]

    score = 30 + 70 * min(1, max(0, (abs(delta) - low) / (full - low)))

    index = change["index"]

    return {
        "score": score,
        "evidence": (
            f"{index} {change['before_mean']:+.2f} -> {change['after_mean']:+.2f} "
            f"({delta:+.2f}), mean over the patch"
        ),
        "reasons": (
            [f"Strong {index} change ({delta:+.2f})"]
            if score >= 70 else []
        ),
        "measurements": {
            "index_before": change["before_mean"],
            "index_after": change["after_mean"],
            "index_delta": delta,
        },
    }


def changed_area(candidate, context, spec):
    """
    Size of the changed patch: 500 m² scores 20, 10 ha or more 100.
    """

    change = candidate.get("change")

    if not change:
        return None

    area = candidate["area_m2"]

    score = min(100, 20 + 80 * math.log10(max(area, 500) / 500) / math.log10(200))

    return {
        "score": score,
        "evidence": f"{_area(area)} ({change['pixels']} pixels at {change.get('resolution_m', 10)} m)",
        "reasons": [f"{_area(area)} changed"] if area >= 5000 else [],
        "measurements": {"changed_area_m2": area},
    }


def imagery_quality(candidate, context, spec):
    """
    How comparable the two scenes are: share of the area clear of
    cloud in each, and how far apart they are in the year (seasonal
    differences can look like change).
    """

    change = candidate.get("change")

    if not change:
        return None

    before = change["before"]

    after = change["after"]

    gap = change["season_gap_days"]

    clear = min(before["clear_fraction"], after["clear_fraction"])

    score = max(0, 100 * clear - max(0, gap - 45) / 30 * 20)

    return {
        "score": score,
        "evidence": (
            f"Clear of cloud: {round(before['clear_fraction'] * 100)}% on {_day(before['date'])}, "
            f"{round(after['clear_fraction'] * 100)}% on {_day(after['date'])}; "
            f"{gap} days apart in the year"
        ),
        "reasons": (
            [f"Scenes {gap} days apart in the year: seasonal change possible"]
            if gap > 45 else []
        ),
        "measurements": {
            "before_scene_date": before["date"],
            "after_scene_date": after["date"],
            "clear_fraction_before": before["clear_fraction"],
            "clear_fraction_after": after["clear_fraction"],
            "season_gap_days": gap,
        },
    }


# Changes on a site that may mean it is no longer what the map says.
ALERT_CHANGES = ("built_or_bare_increase", "vegetation_loss", "water_gain")


def recent_change(candidate, context, spec):
    """
    Share of the site that changed between two Sentinel-2 images
    (change_detection.measure_parcels, run for the shortlist only).
    Evidence only: the use cases give it weight 0.
    """

    measured = candidate.get("recent_change")

    if measured is None:
        return None

    if not measured["measurable"]:
        return {"not_measured": measured["reason"]}

    scenes = context.meta["recent_change"]

    before = _day(scenes["before"]["date"])

    after = _day(scenes["after"]["date"])

    shares = measured["shares"]

    parts = [
        f"{CHANGE_LABELS[t].lower()} on {round(share * 100)}%"
        for t, share in sorted(shares.items(), key=lambda kv: -kv[1])
        if share >= 0.05
    ]

    evidence = (
        f"{'; '.join(parts).capitalize()} of the site" if parts
        else "No change detected on the site"
    ) + f", {before} to {after}"

    reasons = [
        f"Warning: {CHANGE_LABELS[t].lower()} on {round(shares[t] * 100)}% of the site "
        f"between {before} and {after}"
        for t in ALERT_CHANGES
        if shares[t] >= PARCEL_ALERT_SHARE
    ]

    return {
        "score": 100 * (1 - measured["changed_share"]),
        "evidence": evidence,
        "reasons": reasons,
        "measurements": {
            "changed_share": measured["changed_share"],
            **{f"{t}_share": shares[t] for t in shares},
            "before_scene_date": scenes["before"]["date"],
            "after_scene_date": scenes["after"]["date"],
        },
    }


def terrain(candidate, context, spec):
    """
    Elevation, height relative to the surroundings and slope from the
    Copernicus DEM (terrain.measure_sites, run for the shortlist only).
    Evidence only: the use cases give it weight 0. Terrain is not a
    flood-risk measurement.
    """

    measured = candidate.get("terrain")

    if measured is None:
        return None

    if not measured["measurable"]:
        return {"not_measured": measured["reason"]}

    relative = measured["relative_elevation_m"]

    ring = context.meta["terrain"]["ring_m"]

    if abs(relative) < NOISE_M:
        compared = (
            f"no meaningful height difference from the ground within {ring} m "
            f"(within ±{NOISE_M:g} m)"
        )
    else:
        compared = (
            f"about {abs(relative):.1f} m {'lower' if relative < 0 else 'higher'} "
            f"than the ground within {ring} m"
        )

    slope = measured["slope_pct"]

    evidence = (
        f"Surface about {measured['elevation_m']:.0f} m above sea level; {compared}"
        + (f"; mean slope {slope:.1f}%" if slope is not None else "")
    )

    reasons = (
        [f"Warning: low-lying, about {abs(relative):.1f} m below the ground within {ring} m"]
        if relative <= -LOW_LYING_M else []
    )

    return {
        "score": 100 if relative > -LOW_LYING_M else 30,
        "evidence": evidence,
        "reasons": reasons,
        "measurements": {
            "elevation_m": measured["elevation_m"],
            "relative_elevation_m": relative,
            "slope_pct": slope,
        },
    }


# Flood exposure score: 100 minus these, never below 0. Fixed so the
# same observations always give the same score.
FLOOD_PENALTIES = {
    "flooded_once": 50,          # standing water seen in a wet season
    "flooded_again": 20,         # ... in two or more wet seasons
    "water_history_major": 30,   # >= 20% of the site was ever open water
    "water_history_minor": 15,   # 5-20%
    "low_lying": 15,             # >= LOW_LYING_M below the surroundings
}

WATER_HISTORY_MAJOR = 0.2

WATER_HISTORY_MINOR = 0.05


def _when(dates):

    return ", ".join(d[:7] for d in dates)


def protected_status(candidate, context, spec):
    """
    Overlap with protected areas, reserved forests and wetlands mapped
    on OpenStreetMap. Evidence only (weight 0). Absence on the map does
    not prove the land is unprotected.
    """

    shape = context.shape_of(candidate)

    area = shape.area

    overlaps = []

    for zone in context.protected:

        if not shape.intersects(zone.shape):
            continue

        share = (shape.intersection(zone.shape).area / area) if area else 1.0

        overlaps.append((zone, share))

    protected = [(z, s) for z, s in overlaps if z.kind != "wetland"]

    wetland = [(z, s) for z, s in overlaps if z.kind == "wetland"]

    in_use = []

    for place in getattr(context, "in_use", []):
        if shape.intersects(place.shape):
            in_use.append((place, (shape.intersection(place.shape).area / area) if area else 1.0))

    def label(zone):
        name = zone.name or {"wetland": "a mapped wetland", "reserve_forest": "a reserved forest",
                             "protected_area": "a mapped protected area"}[zone.kind]
        return f"{name} ({zone.title})" if zone.title else name

    def use_label(place):
        use = place.use.replace("_", " ")
        return f"{place.name} ({use})" if place.name else f"a mapped {use}"

    named = [(label(z), s) for z, s in protected + wetland] + [(use_label(p), s) for p, s in in_use]

    reasons = [
        f"Warning: overlaps {name} on {round(s * 100)}% of the site"
        for name, s in named if s >= 0.01
    ]

    if named:
        evidence = "Overlaps " + "; ".join(f"{name} ({round(s * 100)}%)" for name, s in named)
    else:
        evidence = (
            "No protected area, reserved forest, wetland, campus, park or other land in use "
            "mapped on OpenStreetMap overlaps the site (absence on the map does not prove "
            "the land is available or unprotected)"
        )

    return {
        "score": 0 if protected or in_use else 50 if wetland else 100,
        "evidence": evidence,
        "reasons": reasons,
        "measurements": {
            "protected_overlap_share": round(min(1.0, sum(s for _, s in protected)), 3),
            "wetland_overlap_share": round(min(1.0, sum(s for _, s in wetland)), 3),
            "in_use_overlap_share": round(min(1.0, sum(s for _, s in in_use)), 3),
        },
    }


def flood_exposure(candidate, context, spec):
    """
    Observed flood exposure from radar flood observations, the JRC
    water history and terrain (flood.measure_sites, terrain.measure_sites).
    This scores what was observed; it is not a flood probability.
    """

    measured = candidate.get("flood")

    if measured is None:
        return None

    observed = measured["observed"]

    history = measured["water_history"]

    if observed is None and history is None:
        return {"not_measured": "Neither radar flood observations nor water history could be read."}

    score = 100

    parts = []

    reasons = []

    measurements = {}

    if observed is not None:

        flooded = observed["flooded"]

        measurements.update({
            "observed_flood_images": observed["flooded_images"],
            "wet_season_images": observed["images"],
            "observed_flood_seasons": observed["flooded_seasons"],
            "max_flooded_share": observed["max_share"],
        })

        if flooded:
            score -= FLOOD_PENALTIES["flooded_once"]
            if observed["flooded_seasons"] >= 2:
                score -= FLOOD_PENALTIES["flooded_again"]
            when = _when([f["date"] for f in flooded])
            parts.append(
                f"standing water on at least {round(FLOODED_SHARE * 100)}% of the site in "
                f"{observed['flooded_images']} of {observed['images']} wet-season radar images ({when})"
            )
            reasons.append(f"Warning: standing water seen on the site by radar ({when})")
        else:
            parts.append(f"no standing water seen in {observed['images']} wet-season radar images")

    else:
        parts.append("radar flood observations unavailable")

    if history is not None:

        share = history["share"]

        measurements.update({
            "water_history_share": share,
            "water_occurrence_mean": history["mean_occurrence"],
        })

        if share >= WATER_HISTORY_MAJOR:
            score -= FLOOD_PENALTIES["water_history_major"]
        elif share >= WATER_HISTORY_MINOR:
            score -= FLOOD_PENALTIES["water_history_minor"]

        if share >= WATER_HISTORY_MINOR:
            parts.append(f"open water recorded on {round(share * 100)}% of the site at times in 1984-2020")
            reasons.append(
                f"Warning: open water on {round(share * 100)}% of the site at times in "
                f"1984-2020 (possible filled water body or wetland)"
            )
        else:
            parts.append("no open water recorded on the site in 1984-2020")

    else:
        parts.append("water history unavailable")

    ground = candidate.get("terrain") or {}

    if ground.get("measurable"):
        relative = ground["relative_elevation_m"]
        measurements["relative_elevation_m"] = relative
        if relative <= -LOW_LYING_M:
            score -= FLOOD_PENALTIES["low_lying"]
            parts.append(f"about {abs(relative):.1f} m below the surrounding ground")

    return {
        "score": max(0, score),
        "evidence": "Observed: " + "; ".join(parts),
        "reasons": reasons,
        "measurements": measurements,
    }


# ============================================================
# REGISTRY
# ============================================================

EVALUATORS = {
    "solar_footprint_size": solar_footprint_size,
    "solar_building_type": solar_building_type,
    "size_fit": size_fit,
    "parcel_size": parcel_size,
    "road_access": road_access,
    "major_road_proximity": major_road_proximity,
    "demand_potential": demand_potential,
    "commercial_activity": commercial_activity,
    "ev_competition": ev_competition,
    "business_competition": business_competition,
    "parking_potential": parking_potential,
    "vacancy_evidence": vacancy_evidence,
    "location_proximity": location_proximity,
    "change_magnitude": change_magnitude,
    "changed_area": changed_area,
    "imagery_quality": imagery_quality,
    "recent_change": recent_change,
    "terrain": terrain,
    "flood_exposure": flood_exposure,
    "protected_status": protected_status,
}
