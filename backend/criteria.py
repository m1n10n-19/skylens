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

from geodata import (
    CHARGER_RADIUS_M,
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


def vacancy_evidence(candidate, context, spec):

    landuse = candidate.get("landuse")

    if landuse not in VACANCY_SCORES:
        return None

    score, reason = VACANCY_SCORES[landuse]

    return {
        "score": score,
        "evidence": f"OSM land-use tag: {landuse} (not verified on imagery)",
        "reasons": [reason],
        "measurements": {"landuse_tag": landuse},
    }


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
}
