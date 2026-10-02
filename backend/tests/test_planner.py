"""
Planner: DeepSeek JSON -> (AnalysisSpec, UseCase).
"""

import pytest

from planner import build_planner_prompt, plan_from_llm_json
from use_cases import USE_CASES, resolve_use_case


@pytest.mark.parametrize("intent_type,expected", [
    ("solar_prospecting", "solar_prospecting"),
    ("Solar Prospecting", "solar_prospecting"),
    ("solar", "solar_prospecting"),
    ("rooftop-solar", "solar_prospecting"),
    ("ev_charging_site_selection", "ev_charging_site_selection"),
    ("commercial_site_selection", "commercial_site_selection"),
    ("land_acquisition", "land_acquisition"),
    ("construction_progress", "construction_progress"),
])
def test_known_intents_resolve_to_a_use_case(intent_type, expected):

    assert resolve_use_case(intent_type).id == expected


@pytest.mark.parametrize("intent_type", [
    "flood_resilience_planning", "", None, 42, "unknown",
])
def test_unknown_intents_resolve_to_none(intent_type):

    assert resolve_use_case(intent_type) is None


def test_every_alias_resolves_to_its_own_use_case():

    for use_case in USE_CASES.values():
        for alias in use_case.aliases:
            assert resolve_use_case(alias) is use_case


def test_plan_fills_candidate_type_from_use_case():

    spec, use_case = plan_from_llm_json(
        {"intent_type": "ev_charging", "location": "Adyar"}, "q"
    )

    assert use_case is not None
    assert spec.use_case == use_case.id
    assert spec.candidate_type == use_case.candidate_type


def test_plan_keeps_the_models_candidate_type_when_given():

    spec, _ = plan_from_llm_json(
        {"intent_type": "solar", "candidate_type": "warehouse_roof"}, "q"
    )

    assert spec.candidate_type == "warehouse_roof"


def test_plan_for_unsupported_intent_keeps_the_intent():

    spec, use_case = plan_from_llm_json(
        {"intent_type": "railway_inspection", "location": "Chennai"}, "q"
    )

    assert use_case is None
    assert spec.use_case is None
    assert spec.intent_type == "railway_inspection"


def test_plan_tolerates_non_dict_reply():

    spec, use_case = plan_from_llm_json(["not", "a", "dict"], "q")

    assert use_case is None
    assert spec.intent_type == "unknown"


def test_planner_prompt_lists_every_module_and_the_query():

    prompt = build_planner_prompt("Find 10 cent plots in Adyar")

    assert "Find 10 cent plots in Adyar" in prompt

    for use_case_id in USE_CASES:
        assert use_case_id in prompt


def test_planner_prompt_forbids_invented_data_and_unit_conversion():

    prompt = build_planner_prompt("q")

    assert "Do not invent coordinates, measurements" in prompt
    assert "Do not convert" in prompt
