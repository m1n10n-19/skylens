"""
Generic scoring engine.

score = sum(weight * score) / sum(weight) over MEASURED criteria only.
Unknown evidence is excluded, never counted as zero, and coverage
says how much of the weighted evidence was measured.
"""

import pytest

import criteria
import scoring

from analysis_spec import AnalysisSpec
from geodata import new_context
from scoring import score_candidate, score_weighted_criteria
from use_cases import Criterion, USE_CASES, UseCase

from tests.conftest import LAT, LON


# ============================================================
# TEST USE CASE
# ============================================================

# Fixed-score evaluators registered for these tests only.
FIXED = {
    "t_80": lambda c, ctx, spec: {"score": 80, "evidence": "80", "reasons": ["eighty"],
                                  "measurements": {"m80": 80}},
    "t_40": lambda c, ctx, spec: {"score": 40, "evidence": "40", "reasons": ["forty"]},
    "t_none": lambda c, ctx, spec: None,
    "t_high": lambda c, ctx, spec: {"score": 250, "evidence": "high"},
    "t_low": lambda c, ctx, spec: {"score": -30, "evidence": "low"},
}


@pytest.fixture(autouse=True)
def fixed_evaluators(monkeypatch):

    for name, fn in FIXED.items():
        monkeypatch.setitem(criteria.EVALUATORS, name, fn)

    # scoring imported the same dict object.
    assert scoring.EVALUATORS is criteria.EVALUATORS


def _use_case(*specs, unassessed=()):
    """
    specs: (id, weight, data_layer, evaluator)
    """

    return UseCase(
        id="test_case",
        title="Test case",
        description="",
        example_queries=(),
        candidate_type="site",
        candidate_noun="site",
        candidate_source="land_parcels",
        data_layers=(),
        criteria=tuple(
            Criterion(id=i, label=i.title(), weight=w, data_layer=layer, evaluator=ev)
            for i, w, layer, ev in specs
        ),
        unassessed=unassessed,
    )


def _context(*layers):

    context = new_context(LAT, LON, 1)

    for layer in layers:
        context.layer_status[layer] = "loaded"

    return context


SPEC = AnalysisSpec(query="q", intent_type="test_case")

CANDIDATE = {"osm_id": 1, "osm_type": "way", "latitude": LAT, "longitude": LON,
             "area_m2": 1000, "_shape": None, "_internal": "x"}


# ============================================================
# MEASURED-ONLY SCORING
# ============================================================

def test_weighted_mean_over_measured_criteria():

    uc = _use_case(("a", 0.6, "roads", "t_80"), ("b", 0.4, "roads", "t_40"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    assert result["score"] == round((0.6 * 80 + 0.4 * 40) / 1.0)
    assert result["evidence_coverage"] == 1.0
    assert result["missing_data"] == []
    assert result["score_basis"] == "measured_evidence_only"


def test_unavailable_layer_is_excluded_not_zero():

    uc = _use_case(("a", 0.5, "roads", "t_80"), ("flood", 0.5, "flood_risk", "t_40"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    # Only "a" measured: score is 80, not (80 + 0) / 2.
    assert result["score"] == 80
    assert result["evidence_coverage"] == 0.5
    assert result["missing_data"] == ["flood"]

    flood = result["criteria"]["flood"]

    assert flood["available"] is False
    assert flood["score"] is None
    assert flood["note"]


def test_evaluator_returning_none_is_excluded_not_zero():

    uc = _use_case(("a", 0.25, "roads", "t_80"), ("b", 0.75, "roads", "t_none"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    assert result["score"] == 80
    assert result["evidence_coverage"] == 0.25
    assert result["missing_data"] == ["b"]
    assert result["criteria"]["b"]["note"] == "Could not be measured for this candidate."


def test_criterion_without_evaluator_is_never_measured():

    uc = _use_case(("a", 0.5, "roads", "t_80"), ("b", 0.5, "roads", None))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    assert result["score"] == 80
    assert "b" in result["missing_data"]


def test_nothing_measured_gives_no_score():

    uc = _use_case(("a", 1.0, "flood_risk", "t_80"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context())

    assert result["score"] is None
    assert result["evidence_coverage"] == 0.0
    assert result["confidence"] == "low"


def test_scores_are_clamped_to_0_100():

    uc = _use_case(("hi", 0.5, "roads", "t_high"), ("lo", 0.5, "roads", "t_low"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    assert result["criteria"]["hi"]["score"] == 100.0
    assert result["criteria"]["lo"]["score"] == 0.0
    assert result["score"] == 50


def test_effective_weights_sum_to_one_over_measured():

    uc = _use_case(("a", 0.3, "roads", "t_80"), ("b", 0.2, "roads", "t_40"),
                   ("c", 0.5, "flood_risk", "t_40"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    weights = [c["effective_weight"] for c in result["criteria"].values() if c["available"]]

    assert sum(weights) == pytest.approx(1.0, abs=0.002)
    assert "effective_weight" not in result["criteria"]["c"]


def test_unassessed_items_are_always_reported_missing():

    uc = _use_case(("a", 1.0, "roads", "t_80"), unassessed=("ownership", "zoning"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    assert result["evidence_coverage"] == 1.0
    assert result["missing_data"] == ["ownership", "zoning"]


def test_reasons_and_measurements_come_from_measured_criteria_only():

    uc = _use_case(("a", 0.5, "roads", "t_80"), ("b", 0.5, "flood_risk", "t_40"))

    result = score_weighted_criteria(CANDIDATE, SPEC, uc, _context("roads"))

    assert result["reasons"] == ["eighty"]
    assert result["measurements"] == {"m80": 80}


def test_scoring_is_deterministic():

    uc = _use_case(("a", 0.3, "roads", "t_80"), ("b", 0.7, "roads", "t_40"))

    results = [
        score_weighted_criteria(dict(CANDIDATE), SPEC, uc, _context("roads"))
        for _ in range(5)
    ]

    assert all(r == results[0] for r in results)


# ============================================================
# CONFIDENCE (labels, bounded by coverage)
# ============================================================

@pytest.mark.parametrize("score,coverage,expected", [
    (None, 1.0, "low"),
    (90, 1.0, "high"),
    (90, 0.6, "high"),
    (90, 0.59, "medium"),   # high score, but under 60% measured
    (90, 0.4, "medium"),
    (90, 0.39, "low"),      # under 40% measured is always low
    (50, 0.5, "medium"),
    (50, 0.3, "low"),
    (44, 1.0, "low"),
    (70, 1.0, "high"),
    (45, 1.0, "medium"),
])
def test_confidence_never_exceeds_what_coverage_supports(score, coverage, expected):

    assert scoring._confidence(score, coverage) == expected


# ============================================================
# PUBLIC OUTPUT
# ============================================================

def test_score_candidate_strips_internal_fields():

    uc = _use_case(("a", 1.0, "roads", "t_80"))

    scored = score_candidate(dict(CANDIDATE), SPEC, uc, _context("roads"))

    assert not any(key.startswith("_") for key in scored)
    assert scored["osm_id"] == 1
    assert "solar_score" not in scored


def test_all_registered_criteria_reference_known_evaluators_and_layers():

    from use_cases import DATA_LAYERS

    for use_case in USE_CASES.values():

        assert use_case.scorer in scoring.SCORERS

        for criterion in use_case.criteria:

            assert criterion.data_layer in DATA_LAYERS, (use_case.id, criterion.id)

            if criterion.evaluator is not None:
                assert criterion.evaluator in criteria.EVALUATORS, (use_case.id, criterion.id)


def test_unavailable_layers_never_have_an_evaluator_that_runs():
    """
    A criterion on an unavailable layer must always be missing, even
    if a future evaluator is attached, because the layer never loads.
    """

    from use_cases import data_layer_available

    for use_case in USE_CASES.values():

        for criterion in use_case.criteria:

            if not data_layer_available(criterion.data_layer):

                result = score_weighted_criteria(
                    {"latitude": LAT, "longitude": LON, "area_m2": 1000},
                    SPEC, use_case, _context(),
                )

                assert criterion.id in result["missing_data"]
