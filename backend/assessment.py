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
        "Flood history and zoning",
        "Has the site flooded, or is it in an official flood zone?",
        "records_check",
        "Satellite passes miss floods that drain quickly, and official flood-zone maps were not checked.",
    ),
    # Only listed when water was recorded on the site (see EXTRA).
    "water_body": (
        "Water body status",
        "Is the site a filled-in lake, tank or wetland, and is building on it permitted?",
        "records_check",
        "Open water was recorded on the site in past satellite images.",
    ),
    "protected_status": (
        "Protected status and current use",
        "Is the site in a protected forest, wetland, coastal regulation or eco-sensitive zone, "
        "or part of an institution, park or other land already in use?",
        "records_check",
        "OpenStreetMap records only some protected areas and land uses; official records were not checked.",
    ),
    "power_line": (
        "Power line clearance",
        "Does a power-line right-of-way or clearance zone restrict building on the site?",
        "records_check",
        "A mapped high-tension power line crosses or runs next to the site.",
    ),
    "permits": (
        "Permits",
        "Is the change covered by building or land-use permits?",
        "records_check",
        "Permits are not assessed by SkyLens.",
    ),
    "grid_connection_capacity": (
        "Grid connection capacity",
        "Is there enough grid capacity at this site for the planned load?",
        "records_check",
        "Grid capacity is not assessed by SkyLens.",
    ),

    # Only listed when the site changed (see CONDITIONAL).
    "recent_change": (
        "Recent change on site",
        "What is on the site now: new construction, clearing or water?",
        "field_visit",
        "Recent satellite imagery shows change on the site.",
    ),

    "terrain": (
        "Drainage",
        "Does water collect on this site in heavy rain, and has it flooded before?",
        "field_visit",
        "The site is lower than the ground around it.",
    ),

    # Imagery
    "cause_of_change": (
        "Cause of the change",
        "What actually changed on the ground: construction, clearing, farming or flooding?",
        "imagery_review",
        "Spectral change at satellite resolution cannot tell these causes apart.",
    ),
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


def _site_changed(scored):

    from criteria import ALERT_CHANGES
    from change_detection import PARCEL_ALERT_SHARE

    m = scored.get("measurements") or {}

    return any((m.get(f"{t}_share") or 0) >= PARCEL_ALERT_SHARE for t in ALERT_CHANGES)


def _low_lying(scored):

    from terrain import LOW_LYING_M

    relative = (scored.get("measurements") or {}).get("relative_elevation_m")

    return relative is not None and relative <= -LOW_LYING_M


# Verify items listed only when their condition holds for the site;
# "why" is then the criterion's evidence summary.
CONDITIONAL = {
    "recent_change": _site_changed,
    "terrain": _low_lying,
}


def _water_recorded(scored):

    from criteria import WATER_HISTORY_MINOR

    share = (scored.get("measurements") or {}).get("water_history_share")

    return share is not None and share >= WATER_HISTORY_MINOR


# Further verify items a measured criterion can raise:
# criterion id -> [(verify id, condition)]; "why" is the evidence.
def _power_line(scored):

    from infrastructure import POWER_LINE_WARNING_M

    distance = (scored.get("measurements") or {}).get("nearest_power_line_m")

    return distance is not None and distance <= POWER_LINE_WARNING_M


EXTRA = {
    "flood_risk": [("water_body", _water_recorded)],
    "infrastructure": [("power_line", _power_line)],
}


# Evidence statuses from strongest to weakest. A known criterion's
# basis is the weakest status among its evidence items that carry a
# unit (distances, areas, counts); descriptive tags such as a road's
# name are context and only decide the basis when nothing else does.
_BASIS_ORDER = ("measured", "inferred", "observed")


def _label(item_id):

    if item_id in VERIFY:
        return VERIFY[item_id][0]

    return item_id.replace("_", " ").capitalize()


def _basis(items):

    quantified = [
        item for item in items
        if (item.get("measurement") or {}).get("unit")
    ]

    statuses = {item["status"] for item in quantified or items}

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

        condition = CONDITIONAL.get(criterion_id)

        if criterion_id in VERIFY and (condition is None or (measured and condition(scored))):

            label, question, method_type, why = VERIFY[criterion_id]

            if condition is not None:
                why = entry.get("evidence")

            verify.append({
                "id": criterion_id,
                "label": label,
                "question": question,
                "why": why if measured else entry["note"],
                "method_type": method_type,
                "status": "verification_required",
            })

        for extra_id, extra_condition in EXTRA.get(criterion_id, ()):

            if measured and extra_condition(scored):

                label, question, method_type, _ = VERIFY[extra_id]

                verify.append({
                    "id": extra_id,
                    "label": label,
                    "question": question,
                    "why": entry.get("evidence"),
                    "method_type": method_type,
                    "status": "verification_required",
                })

    for item_id in use_case.unassessed:

        unknown.append({
            "id": item_id,
            "label": _label(item_id),
            "state": "not_assessed",
            "reason": "SkyLens has no data for this.",
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
