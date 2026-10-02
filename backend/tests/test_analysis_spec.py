"""
AnalysisSpec: DeepSeek JSON -> validated spec.

Sizes are converted to m² by code (the model only copies numbers and
units), units are preserved as stated, and nothing the model says
about coordinates or measurements reaches the spec.
"""

import pytest

from analysis_spec import AnalysisSpec, area_to_m2, location_text, spec_from_llm


# ============================================================
# UNIT CONVERSION
# ============================================================

@pytest.mark.parametrize("value,unit,expected", [
    (1, "sqm", 1.0),
    (500, "m²", 500.0),
    (500, "square metres", 500.0),
    (4800, "sqft", 445.9),
    (4800, "sq. ft", 445.9),
    (4800, "square feet", 445.9),
    (10, "cent", 404.7),
    (10, "cents", 404.7),
    (30, "Cents", 1214.1),
    (1, "ground", 223.0),
    (2, "grounds", 445.9),
    (1, "acre", 4046.9),
    (2.5, "acres", 10117.1),
    (1, "hectare", 10000.0),
    (3, "ha", 30000.0),
    (1, "sq km", 1000000.0),
    (100, "sq yd", 83.6),
    ("10", "cent", 404.7),
])
def test_area_to_m2_converts_stated_units(value, unit, expected):

    assert area_to_m2(value, unit) == expected


@pytest.mark.parametrize("value,unit", [
    (None, "sqm"),
    (0, "sqm"),
    (-5, "acre"),
    (True, "acre"),
    ("ten", "cent"),
    (10, None),
    (10, "bigha"),        # unknown unit: never guessed
    (10, "furlong"),
])
def test_area_to_m2_refuses_unusable_input(value, unit):

    assert area_to_m2(value, unit) is None


# ============================================================
# LOCATION
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    ("Adyar, Chennai", "Adyar, Chennai"),
    ("  Adyar  ", "Adyar"),
    ({"text": "Adyar, Chennai", "coordinates": [13.0, 80.2]}, "Adyar, Chennai"),
    ({"area": "Adyar", "city": "Chennai", "country": "India"}, "Adyar, Chennai, India"),
    ({"city": "Chennai", "district": "Chennai"}, "Chennai"),
    ("null", None),
    ("Unknown", None),
    ("", None),
    (None, None),
    ({"latitude": 13.0, "longitude": 80.2}, None),
    (42, None),
])
def test_location_text_uses_text_only(raw, expected):

    assert location_text(raw) == expected


def test_spec_ignores_coordinates_from_the_model():

    spec = spec_from_llm({
        "intent_type": "solar_prospecting",
        "location": {"text": "Adyar", "latitude": 1.0, "longitude": 2.0},
        "latitude": 1.0,
        "longitude": 2.0,
    }, "q")

    dumped = spec.model_dump()

    assert spec.location == "Adyar"
    assert "latitude" not in dumped
    assert "longitude" not in dumped


# ============================================================
# AREA REQUIREMENTS
# ============================================================

def test_area_target_preserves_what_the_user_said():

    spec = spec_from_llm({
        "intent_type": "ev_charging_site_selection",
        "requirements": {"area": {
            "min": None, "max": None, "target": 10,
            "unit": "cent", "as_stated": "10 cents",
        }},
    }, "Find 10 cent plots")

    assert spec.area.target_m2 == 404.7
    assert spec.area.min_m2 is None
    assert spec.area.max_m2 is None
    assert spec.area.as_stated == "10 cents"
    # The raw requirement is kept as the model gave it.
    assert spec.requirements["area"]["unit"] == "cent"


def test_area_min_and_max_in_different_units_are_both_converted():

    spec = spec_from_llm({
        "intent_type": "land_acquisition",
        "requirements": {"area": {
            "min": 1, "max": 2, "target": None, "unit": "acre",
            "as_stated": "between 1 and 2 acres",
        }},
    }, "q")

    assert spec.area.min_m2 == 4046.9
    assert spec.area.max_m2 == 8093.7


def test_area_with_unknown_unit_is_left_empty():

    spec = spec_from_llm({
        "intent_type": "land_acquisition",
        "requirements": {"area": {"min": 5, "unit": "bigha"}},
    }, "q")

    assert spec.area.min_m2 is None
    assert spec.area.target_m2 is None


def test_no_size_stated_means_no_size_requirement():

    spec = spec_from_llm({
        "intent_type": "commercial_site_selection",
        "requirements": {"area": {
            "min": None, "max": None, "target": None,
            "unit": None, "as_stated": None,
        }},
    }, "q")

    assert spec.area.model_dump() == {
        "min_m2": None, "max_m2": None, "target_m2": None, "as_stated": None,
    }


@pytest.mark.parametrize("requirements,field,expected", [
    ({"minimum_roof_area_m2": 800}, "min_m2", 800.0),
    ({"minimum_area_m2": "1200"}, "min_m2", 1200.0),
    ({"min_area_sqft": 4800}, "min_m2", 445.9),
    ({"max_area_acre": 2}, "max_m2", 8093.7),
    ({"target_area_cent": 10}, "target_m2", 404.7),
])
def test_legacy_area_keys_are_still_understood(requirements, field, expected):

    spec = spec_from_llm({"intent_type": "x", "requirements": requirements}, "q")

    assert getattr(spec.area, field) == expected


# ============================================================
# OTHER FIELDS
# ============================================================

def test_spec_defaults_for_garbage_input():

    for raw in (None, [], "text", 42):

        spec = spec_from_llm(raw, "my query")

        assert isinstance(spec, AnalysisSpec)
        assert spec.query == "my query"
        assert spec.intent_type == "unknown"
        assert spec.location is None
        assert spec.requirements == {}
        assert spec.criteria == []


@pytest.mark.parametrize("value,expected", [
    ("High", "high"), (" medium ", "medium"), ("low", "low"),
    ("very high", None), (0.9, None), (None, None),
])
def test_planner_confidence_is_normalised_to_a_label(value, expected):

    spec = spec_from_llm({"intent_type": "x", "confidence": value}, "q")

    assert spec.confidence == expected


@pytest.mark.parametrize("value,expected", [
    (2, 2.0), ("1.5", 1.5), (0, None), (-1, None), (True, None), ("far", None),
])
def test_radius_only_kept_when_positive(value, expected):

    spec = spec_from_llm({"intent_type": "x", "radius_km": value}, "q")

    assert spec.radius_km == expected


def test_list_fields_accept_strings_dicts_and_objects():

    spec = spec_from_llm({
        "intent_type": "x",
        "data_needed": "roads",
        "criteria": [{"id": "road_access"}, {"name": "competition"}, "size", None, 3],
        "constraints": {"must_be_vacant": True},
        "requirements": ["near highway"],
    }, "q")

    assert spec.data_needed == ["roads"]
    assert spec.criteria == ["road_access", "competition", "size", "3"]
    assert spec.constraints == ["must_be_vacant"]
    assert spec.requirements == {"notes": ["near highway"]}


def test_spec_never_carries_scores_or_measurements_from_the_model():
    """
    The model may put numbers anywhere in its JSON; the spec has no
    field for a score, and the model's numbers are not measurements.
    """

    spec = spec_from_llm({
        "intent_type": "land_acquisition",
        "score": 87,
        "suitability": "87%",
        "flood_risk": "low",
        "measurements": {"nearest_road_m": 12},
    }, "q")

    dumped = spec.model_dump()

    for key in ("score", "suitability", "flood_risk", "measurements"):
        assert key not in dumped
