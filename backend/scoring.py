from criteria import (
    EVALUATORS,
    solar_building_type,
    solar_footprint_size,
)

from use_cases import DATA_LAYERS, data_layer_available


# =============================================
# LEGACY SOLAR SCORER
# =============================================

def score_solar_candidate(candidate):
    """
    Scorer behind GET /solar/prospects (kept for API compatibility).

    Same points as the solar_prospecting criteria in criteria.py
    (footprint size max 70, building type max 30); confidence comes
    from the score alone, as before. /analyze uses score_candidate().
    """

    size = solar_footprint_size(candidate, None, None)

    building = solar_building_type(candidate, None, None)

    score = round(size["score"] * 0.7 + building["score"] * 0.3)

    if score >= 70:
        confidence = "high"
    elif score >= 45:
        confidence = "medium"
    else:
        confidence = "low"

    # Original order: size tier, building type, large-site bonus.
    reasons = size["reasons"][:1] + building["reasons"] + size["reasons"][1:]

    return {
        **candidate,
        "solar_score": score,
        "confidence": confidence,
        "reasons": reasons,
    }


# =============================================
# GENERIC SCORING ENGINE
# =============================================

def _confidence(score, coverage):
    """
    Confidence reflects both how strong the match is and how much
    of the weighted evidence was actually measured.
    """

    if score is None:
        return "low"

    if score >= 70:
        level = "high"
    elif score >= 45:
        level = "medium"
    else:
        level = "low"

    if coverage < 0.4:
        return "low"

    if coverage < 0.6 and level == "high":
        return "medium"

    return level


def _missing_note(criterion):

    if criterion.evaluator is None:
        return criterion.missing_note or "Not measured yet."

    layer = DATA_LAYERS.get(criterion.data_layer, {})

    if not data_layer_available(criterion.data_layer):
        return (
            criterion.missing_note
            or f"No {layer.get('label', criterion.data_layer).lower()} "
               f"data connected."
        )

    return "Could not be measured for this candidate."


def score_weighted_criteria(candidate, spec, use_case, context):
    """
    score = sum(weight * criterion score) / sum(weight)
    over the criteria that have measured evidence.

    Criteria without evidence are excluded from the score and
    reported in missing_data; evidence_coverage is the share of
    total weight that was actually measured.
    """

    total_weight = sum(c.weight for c in use_case.criteria)

    measured_weight = 0.0

    weighted_sum = 0.0

    results = {}

    reasons = []

    missing = []

    for criterion in use_case.criteria:

        result = None

        if (
            criterion.evaluator is not None
            and context.has_layer(criterion.data_layer)
        ):
            result = EVALUATORS[criterion.evaluator](
                candidate, context, spec
            )

        if result is None:

            results[criterion.id] = {
                "label": criterion.label,
                "weight": criterion.weight,
                "available": False,
                "score": None,
                "note": _missing_note(criterion),
            }

            missing.append(criterion.id)

            continue

        score = max(0.0, min(100.0, float(result["score"])))

        results[criterion.id] = {
            "label": criterion.label,
            "weight": criterion.weight,
            "available": True,
            "score": round(score, 1),
            "evidence": result.get("evidence"),
        }

        weighted_sum += criterion.weight * score

        measured_weight += criterion.weight

        reasons.extend(result.get("reasons") or [])

    if measured_weight > 0:

        overall = round(weighted_sum / measured_weight)

        for entry in results.values():
            if entry["available"]:
                entry["effective_weight"] = round(
                    entry["weight"] / measured_weight, 3
                )

    else:

        overall = None

    coverage = (
        round(measured_weight / total_weight, 2)
        if total_weight else 0.0
    )

    return {
        "score": overall,
        "score_basis": "measured_evidence_only",
        "evidence_coverage": coverage,
        "confidence": _confidence(overall, coverage),
        "criteria": results,
        "missing_data": missing + list(use_case.unassessed),
        "reasons": reasons,
    }


SCORERS = {
    "weighted_criteria": score_weighted_criteria,
}


def score_candidate(candidate, spec, use_case, context):
    """
    Score one candidate with the use case's scoring function.
    Returns the public candidate fields plus the score breakdown.
    """

    scorer = SCORERS[use_case.scorer]

    breakdown = scorer(candidate, spec, use_case, context)

    public = {
        key: value
        for key, value in candidate.items()
        if not key.startswith("_")
    }

    scored = {**public, **breakdown}

    if use_case.id == "solar_prospecting":
        # Field name the existing frontend and clients read.
        scored["solar_score"] = breakdown["score"]

    return scored
