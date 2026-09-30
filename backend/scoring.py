def score_solar_candidate(candidate):

    area = candidate.get(
        "area_m2",
        0
    )

    building_type = (
        candidate.get(
            "building_type"
        )
        or "unknown"
    ).lower()

    score = 0

    reasons = []

    # =============================================
    # SIZE
    # =============================================

    if area >= 10000:

        score += 50

        reasons.append(
            "Exceptional building footprint"
        )

    elif area >= 5000:

        score += 40

        reasons.append(
            "Very large building footprint"
        )

    elif area >= 2000:

        score += 30

        reasons.append(
            "Large building footprint"
        )

    elif area >= 1000:

        score += 20

        reasons.append(
            "Medium-large building footprint"
        )

    elif area >= 500:

        score += 10

        reasons.append(
            "Building exceeds 500 m² threshold"
        )

    # =============================================
    # BUILDING TYPE
    # =============================================

    high_value_types = {
        "commercial",
        "industrial",
        "warehouse",
        "retail",
        "office"
    }

    institutional_types = {
        "school",
        "university",
        "hospital",
        "college",
        "institutional"
    }

    residential_types = {
        "apartments",
        "residential",
        "house",
        "detached"
    }

    if building_type in high_value_types:

        score += 30

        reasons.append(
            f"Potentially relevant {building_type} building"
        )

    elif building_type in institutional_types:

        score += 20

        reasons.append(
            f"Large institutional {building_type} building"
        )

    elif building_type in residential_types:

        score += 5

        reasons.append(
            "Residential building requires additional qualification"
        )

    else:

        score += 5

        reasons.append(
            "Building type requires verification"
        )

    # =============================================
    # LARGE-SITE BONUS
    # =============================================

    if area >= 10000:

        score += 20

        reasons.append(
            "Exceptional footprint size"
        )

    elif area >= 5000:

        score += 10

        reasons.append(
            "Strong footprint size"
        )

    # =============================================
    # CAP SCORE
    # =============================================

    score = min(
        score,
        100
    )

    # =============================================
    # CONFIDENCE
    # =============================================

    if score >= 70:

        confidence = "high"

    elif score >= 45:

        confidence = "medium"

    else:

        confidence = "low"

    # =============================================
    # FINAL RESULT
    # =============================================

    return {
        **candidate,

        "solar_score": score,

        "confidence": confidence,

        "reasons": reasons
    }