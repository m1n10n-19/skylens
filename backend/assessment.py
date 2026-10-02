"""
Per-candidate assessment: what is known, what is unknown, and what
needs verification that remote data cannot provide.

Built deterministically from the scored criteria and their evidence
items (scoring.py, evidence.py). It interprets measurements; it never
creates them, and the LLM is not involved.

The verify list says WHAT to check, the question that check would
resolve, and a broad method type. It does not prescribe sensors or
providers, and SkyLens does not carry out the checks itself.
"""

# Method types, cheapest first. Verify items are ordered this way so
# cheaper checks come before more expensive ones.
METHOD_TYPES = (
    "records_check",
    "imagery_review",
    "field_visit",
    "site_survey",
)


# id (criterion id or unassessed item) -> (label, question, method_type, why)
#
# "why" explains the remote limitation when the criterion WAS measured;
# for criteria that were not measured the criterion's own note is used.
# Only items that remote evidence cannot settle are listed.
VERIFY = {

    # Legal and records
    "ownership": (
        "Ownership",
        "Who owns the property, and is it available?",
        "records_check",
        "Ownership is not assessed by SkyLens.",
    ),
    "legal_title": (
        "Legal title",
        "Is the title clear of disputes and encumbrances?",
        "records_check",
        "Legal title is not assessed by SkyLens.",
    ),
    "zoning": (
        "Zoning",
        "Does zoning permit the intended use?",
        "records_check",
        "Zoning is not assessed by SkyLens.",
    ),
    "land_use_compatibility": (
        "Land-use compatibility (zoning)",
        "Does zoning permit the intended use?",
        "records_check",
        "No zoning data is connected.",
    ),
    "parcel_size": (
        "Parcel boundaries",
        "Do the legal plot boundaries match the mapped polygon?",
        "records_check",
        "Mapped land-use polygons are not cadastral parcels.",
    ),
    "parcel_size_fit": (
        "Parcel boundaries",
        "Do the legal plot boundaries match the mapped polygon?",
        "records_check",
        "Mapped land-use polygons are not cadastral parcels.",
    ),
    "site_size_fit": (
        "Site boundaries",
        "Do the legal site boundaries match the mapped outline?",
        "records_check",
        "Mapped outlines are not cadastral parcels.",
    ),
    "flood_risk": (
        "Flood risk",
        "Has the site flooded, or is it in an official flood zone?",
        "records_check",
        "No flood risk data is connected.",
    ),
    "grid_connection_capacity": (
        "Grid connection capacity",
        "Is there enough grid capacity at this site for the planned load?",
        "records_check",
        "Grid capacity is not assessed by SkyLens.",
    ),

    # Imagery
    "footprint_size": (
        "Usable roof area",
        "How much of the roof is usable for panels?",
        "imagery_review",
        "Building footprint area is not usable roof area.",
    ),

    # On the ground
    "vacancy": (
        "Physical vacancy",
        "Is the land physically vacant, with no recent construction or encroachment?",
        "field_visit",
        "Vacancy comes from an OpenStreetMap tag, not verified on imagery or the ground.",
    ),
    "road_access": (
        "Vehicle access",
        "Is there physical vehicle access from the mapped road into the site?",
        "field_visit",
        "Distance to a mapped road is not legal access or frontage.",
    ),
    "building_type": (
        "Building use",
        "Is the building actually used the way the map says?",
        "field_visit",
        "Building type comes from an OpenStreetMap tag, often just 'yes'.",
    ),
    "accessibility": (
        "Site accessibility",
        "Can installers and equipment reach the building and roof?",
        "field_visit",
        "Site accessibility is not measured for solar prospects.",
    ),
    "population": (
        "Footfall",
        "How many people pass or visit the site at relevant times?",
        "field_visit",
        "No population or footfall data is connected.",
    ),

    # Specialist survey
    "roof_condition": (
        "Roof condition",
        "Is the roof structurally sound for panels?",
        "site_survey",
        "Roof condition is not assessed by SkyLens.",
    ),
    "shading": (
        "Shading",
        "Is the roof shaded by nearby buildings or trees?",
        "site_survey",
        "No shading analysis is connected.",
    ),
    "solar_suitability": (
        "Roof solar suitability",
        "Is the roof's orientation, tilt and irradiance suitable for panels?",
        "site_survey",
        "No rooftop irradiance data is connected.",
    ),
}


# Evidence statuses from strongest to weakest; a known criterion's
# basis is the weakest status among its evidence items.
_BASIS_ORDER = ("measured", "inferred", "observed")


def _label(item_id):

    if item_id in VERIFY:
        return VERIFY[item_id][0]

    return item_id.replace("_", " ").capitalize()


def _basis(items):

    statuses = {item["status"] for item in items}

    for status in reversed(_BASIS_ORDER):
        if status in statuses:
            return status

    return "measured"


def assess(scored, use_case):
    """
    {"known": [...], "unknown": [...], "verify": [...]} for one
    scored candidate (output of scoring.score_candidate).
    """

    known = []

    unknown = []

    verify = []

    for criterion_id, entry in scored["criteria"].items():

        measured = entry["state"] == "measured"

        if measured:

            known.append({
                "id": criterion_id,
                "label": entry["label"],
                "summary": entry.get("evidence"),
                "basis": _basis(entry["evidence_items"]),
            })

        else:

            unknown.append({
                "id": criterion_id,
                "label": entry["label"],
                "state": entry["state"],
                "reason": entry["note"],
            })

        if criterion_id in VERIFY:

            label, question, method_type, why = VERIFY[criterion_id]

            verify.append({
                "id": criterion_id,
                "label": label,
                "question": question,
                "why": why if measured else entry["note"],
                "method_type": method_type,
                "status": "verification_required",
            })

    for item_id in use_case.unassessed:

        unknown.append({
            "id": item_id,
            "label": _label(item_id),
            "state": "not_assessed",
            "reason": "Not assessed by SkyLens.",
        })

        if item_id in VERIFY:

            label, question, method_type, why = VERIFY[item_id]

            verify.append({
                "id": item_id,
                "label": label,
                "question": question,
                "why": why,
                "method_type": method_type,
                "status": "verification_required",
            })

    # Cheapest checks first; stable within a method type.
    verify.sort(key=lambda item: METHOD_TYPES.index(item["method_type"]))

    return {
        "known": known,
        "unknown": unknown,
        "verify": verify,
    }
