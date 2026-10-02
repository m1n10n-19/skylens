"""
AnalysisSpec: the structured plan DeepSeek produces for a query.

DeepSeek decides WHAT should be measured. This module turns its
JSON into a validated AnalysisSpec and converts any sizes the user
stated into square metres deterministically (the model is asked to
copy numbers and units from the request, never to convert them).
"""

import calendar
import re

from datetime import date, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================
# AREA UNITS
# ============================================================

SQFT_M2 = 0.09290304

AREA_UNITS_M2 = {

    "m2": 1.0,
    "sqm": 1.0,
    "sq m": 1.0,
    "square metre": 1.0,
    "square meter": 1.0,

    "sqft": SQFT_M2,
    "sq ft": SQFT_M2,
    "ft2": SQFT_M2,
    "square foot": SQFT_M2,
    "square feet": SQFT_M2,
    "sq feet": SQFT_M2,
    "feet": SQFT_M2,

    "sq yd": 0.83612736,
    "sqyd": 0.83612736,
    "square yard": 0.83612736,

    # Indian land units
    "cent": 40.468564,
    "ground": 2400 * SQFT_M2,

    "acre": 4046.8564,

    "hectare": 10000.0,
    "ha": 10000.0,

    "sq km": 1000000.0,
    "km2": 1000000.0,
    "square kilometre": 1000000.0,
    "square kilometer": 1000000.0,
}


def _normalize_unit(unit):

    u = str(unit).strip().lower()

    u = (
        u.replace("²", "2")
        .replace("^2", "2")
        .replace(".", " ")
        .replace("_", " ")
    )

    u = re.sub(r"\s+", " ", u).strip()

    # plurals: "acres", "cents", "square metres"
    if u not in AREA_UNITS_M2 and u.endswith("s"):
        u = u[:-1]

    return u


def area_to_m2(value, unit):
    """
    Convert value+unit to m². Returns None if either is unusable.
    """

    if isinstance(value, bool):
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if value <= 0:
        return None

    if unit is None:
        return None

    factor = AREA_UNITS_M2.get(
        _normalize_unit(unit)
    )

    if factor is None:
        return None

    return round(value * factor, 1)


# ============================================================
# LOCATION TEXT
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


def location_text(raw):
    """
    Searchable location text from DeepSeek's "location" value,
    or None.
    """

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

        if text.lower() in ("null", "none", "unknown"):
            text = None

    return text or None


# ============================================================
# TIME RANGE
# ============================================================

# DeepSeek copies the dates the user stated; they are turned into
# calendar dates here. "2019" as a start means 1 Jan 2019, as an end
# 31 Dec 2019; "2019-06" means 1 / 30 June.

_DATE = re.compile(r"^\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?\s*$")


def _stated_date(value, end=False):
    """
    date for "YYYY", "YYYY-MM" or "YYYY-MM-DD", or None.
    """

    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)

    if not isinstance(value, str):
        return None

    match = _DATE.match(value)

    if not match:
        return None

    year = int(match.group(1))

    month = int(match.group(2)) if match.group(2) else (12 if end else 1)

    try:

        if match.group(3):
            return date(year, month, int(match.group(3)))

        day = calendar.monthrange(year, month)[1] if end else 1

        return date(year, month, day)

    except ValueError:
        return None


def _precision(value):
    """
    "year", "month" or "day" for a stated "YYYY[-MM[-DD]]".
    """

    match = _DATE.match(str(value))

    if not match:
        return None

    return "day" if match.group(3) else "month" if match.group(2) else "year"


def _years_ago(today, years):

    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def parse_time_range(raw, today=None):
    """
    TimeRange from DeepSeek's "time_range", or an empty TimeRange.

    Accepted: {"start": "2019", "end": null, "years_back": null,
               "months_back": null, "as_stated": "since 2019"}
    Relative ranges ("last 5 years") come as years_back / months_back.
    Ranges in the future or ending before they start are dropped.
    """

    today = today or date.today()

    result = TimeRange()

    if not isinstance(raw, dict):
        return result

    start = _stated_date(raw.get("start"))

    precision = _precision(raw.get("start")) if start else None

    end = _stated_date(raw.get("end"), end=True)

    years = _as_float(raw.get("years_back"))

    months = _as_float(raw.get("months_back"))

    if start is None and years:
        start = _years_ago(today, int(round(years)))
        precision = "day"

    if start is None and months:
        start = today - timedelta(days=round(months * 30.44))
        precision = "day"

    if end and end > today:
        end = today

    if start and (start >= (end or today)):
        return result

    result.start = start.isoformat() if start else None

    result.start_precision = precision if start else None

    result.end = end.isoformat() if end else None

    as_stated = raw.get("as_stated")

    if (result.start or result.end) and isinstance(as_stated, str) and as_stated.strip():
        result.as_stated = as_stated.strip()

    return result


# ============================================================
# MODEL
# ============================================================

class AreaRequirement(BaseModel):

    min_m2: Optional[float] = None

    max_m2: Optional[float] = None

    target_m2: Optional[float] = None

    # What the user said, e.g. "10 cents"
    as_stated: Optional[str] = None


class TimeRange(BaseModel):

    # ISO dates; None when the user did not state them.
    start: Optional[str] = None

    end: Optional[str] = None

    # What the user said, e.g. "since 2019"
    as_stated: Optional[str] = None

    # How precisely the start was stated: "year" ("since 2019"),
    # "month" ("since June 2019") or "day". A year-only start leaves
    # the time of year open, so change detection can match seasons.
    start_precision: Optional[str] = None


class AnalysisSpec(BaseModel):

    query: str

    # What DeepSeek called the intent.
    intent_type: str

    # Registered use case id, or None if unsupported.
    use_case: Optional[str] = None

    location: Optional[str] = None

    # Only set when the user stated a distance.
    radius_km: Optional[float] = None

    candidate_type: Optional[str] = None

    industry: Optional[str] = None

    requirements: dict[str, Any] = Field(default_factory=dict)

    area: AreaRequirement = Field(default_factory=AreaRequirement)

    time_range: TimeRange = Field(default_factory=TimeRange)

    data_needed: list[str] = Field(default_factory=list)

    criteria: list[str] = Field(default_factory=list)

    constraints: list[str] = Field(default_factory=list)

    desired_output: Optional[str] = None

    # DeepSeek's confidence in its interpretation of the request
    # (not in any result).
    confidence: Optional[str] = None


# ============================================================
# BUILD FROM DEEPSEEK JSON
# ============================================================

def _as_list(value):

    if value is None:
        return []

    if isinstance(value, str):
        return [value] if value.strip() else []

    if isinstance(value, dict):
        return [str(k) for k in value.keys()]

    if isinstance(value, (list, tuple)):

        items = []

        for item in value:

            if isinstance(item, str):
                items.append(item)

            elif isinstance(item, dict):
                name = (
                    item.get("id")
                    or item.get("name")
                    or item.get("criterion")
                    or item.get("layer")
                )
                if name:
                    items.append(str(name))

            elif item is not None:
                items.append(str(item))

        return items

    return [str(value)]


def _as_text(value):

    if isinstance(value, str) and value.strip():
        return value.strip()

    if isinstance(value, dict):
        for key in ("type", "name", "id", "format"):
            if isinstance(value.get(key), str):
                return value[key]

    return None


def _as_float(value):

    if isinstance(value, bool) or value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    return value if value > 0 else None


def _parse_area(requirements):
    """
    Read the user's size requirement.

    Preferred shape (requested in the planner prompt):

        "area": {"min": 10, "max": null, "target": null,
                 "unit": "cent", "as_stated": "10 cents"}

    Legacy/free-form keys such as "minimum_roof_area_m2" or
    "min_usable_roof_area_sqm" are also accepted.
    """

    area = AreaRequirement()

    raw = requirements.get("area")

    if isinstance(raw, dict):

        unit = raw.get("unit")

        area.min_m2 = area_to_m2(raw.get("min"), unit)

        area.max_m2 = area_to_m2(raw.get("max"), unit)

        area.target_m2 = area_to_m2(raw.get("target"), unit)

        as_stated = raw.get("as_stated")

        if isinstance(as_stated, str) and as_stated.strip():
            area.as_stated = as_stated.strip()

    if area.min_m2 or area.max_m2 or area.target_m2:
        return area

    # --------------------------------------------------------
    # Fallback: legacy keys
    # --------------------------------------------------------

    for key in ("minimum_roof_area_m2", "minimum_area_m2"):

        value = _as_float(requirements.get(key))

        if value:
            area.min_m2 = value
            return area

    unit_hints = (
        ("sqft", "sq ft"),
        ("sq_ft", "sq ft"),
        ("square_feet", "sq ft"),
        ("cent", "cent"),
        ("acre", "acre"),
        ("hectare", "hectare"),
    )

    for key, value in requirements.items():

        key_l = str(key).lower()

        if "area" not in key_l:
            continue

        number = _as_float(value)

        if not number:
            continue

        unit = "sqm"

        for hint, hint_unit in unit_hints:
            if hint in key_l:
                unit = hint_unit
                break

        converted = area_to_m2(number, unit)

        if key_l.startswith("max"):
            area.max_m2 = converted
        elif "target" in key_l:
            area.target_m2 = converted
        else:
            area.min_m2 = converted

        return area

    return area


def spec_from_llm(raw, query, use_case_id=None):
    """
    Build an AnalysisSpec from DeepSeek's JSON.
    use_case_id is the registry id resolved by the caller.
    """

    if not isinstance(raw, dict):
        raw = {}

    requirements = raw.get("requirements")

    if isinstance(requirements, list):
        requirements = {"notes": requirements}

    if not isinstance(requirements, dict):
        requirements = {}

    confidence = raw.get("confidence")

    if isinstance(confidence, str):
        confidence = confidence.strip().lower()

    if confidence not in ("high", "medium", "low"):
        confidence = None

    return AnalysisSpec(

        query=query,

        intent_type=(
            _as_text(raw.get("intent_type")) or "unknown"
        ),

        use_case=use_case_id,

        location=location_text(raw.get("location")),

        radius_km=_as_float(raw.get("radius_km")),

        candidate_type=_as_text(raw.get("candidate_type")),

        industry=_as_text(raw.get("industry")),

        requirements=requirements,

        area=_parse_area(requirements),

        time_range=parse_time_range(raw.get("time_range")),

        data_needed=_as_list(raw.get("data_needed")),

        criteria=_as_list(raw.get("criteria")),

        constraints=_as_list(raw.get("constraints")),

        desired_output=_as_text(
            raw.get("desired_output") or raw.get("output")
        ),

        confidence=confidence,
    )
