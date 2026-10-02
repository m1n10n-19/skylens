"""
SkyLens use-case registry.

Each use case is a "playbook": what kind of physical thing is
evaluated, which data layers are needed, which criteria matter,
how they are weighted, and what the output looks like.

DeepSeek picks the use case (via the AnalysisSpec). Everything in
this file is deterministic configuration that SkyLens executes.

To add a use case: add a UseCase entry to USE_CASES. If it needs a
new measurement, add an evaluator in criteria.py and reference it by
name from a Criterion.
"""

from dataclasses import dataclass
from typing import Optional

import data_registry


# ============================================================
# DATA LAYERS
# ============================================================

# Layers, their sources and metadata live in data_registry.py.
# This is the original {layer_id: {"label", "source", "available"}}
# view of it, kept for existing callers. It is built once at import;
# use data_layer_available() / data_registry for live availability.
#
# "available" means SkyLens has a working provider today.
# Unavailable layers are listed so that criteria depending on them
# are reported as missing evidence instead of being fabricated.

DATA_LAYERS = data_registry.legacy_data_layers()


# ============================================================
# MODELS
# ============================================================

@dataclass(frozen=True)
class Criterion:

    id: str

    label: str

    weight: float

    # Data layer that supplies the evidence.
    data_layer: str

    # Name of the evaluator in criteria.EVALUATORS.
    # None means SkyLens does not measure this yet.
    evaluator: Optional[str] = None

    # Shown when the criterion cannot be measured.
    missing_note: Optional[str] = None


@dataclass(frozen=True)
class UseCase:

    id: str

    title: str

    # Used in the DeepSeek planner prompt.
    description: str

    example_queries: tuple

    candidate_type: str

    # Singular noun used in summaries, e.g. "building".
    candidate_noun: str

    # How candidates are generated: "buildings", "land_parcels",
    # "sites" or None (not implemented).
    candidate_source: Optional[str]

    data_layers: tuple

    criteria: tuple

    constraints: tuple = ()

    # Things that matter for the decision but that SkyLens never
    # assesses (not weighted, always reported as missing).
    unassessed: tuple = ()

    # Name of the scoring function in scoring.SCORERS.
    scorer: str = "weighted_criteria"

    # Candidates smaller than this are dropped when the user gave
    # no size. When the user gave a target size, candidates below
    # target * target_tolerance are dropped instead.
    default_min_area_m2: float = 0

    target_tolerance: float = 0.5

    output_format: str = "ranked_candidates"

    # Phrase used in summaries ("ranked for <purpose>");
    # defaults to the lower-cased title.
    purpose: Optional[str] = None

    recommended_action: str = ""

    limitations: tuple = ()

    implemented: bool = True

    # Add open land found in imagery (landcover.py) to the OSM candidates.
    discover_open_land: bool = False

    aliases: tuple = ()


# ============================================================
# USE CASES
# ============================================================

SOLAR_PROSPECTING = UseCase(

    id="solar_prospecting",

    title="Solar prospecting",

    description=(
        "Find rooftops (buildings) that could host solar panels."
    ),

    example_queries=(
        "Find large roofs around Adyar Chennai suitable for solar",
    ),

    candidate_type="building",

    candidate_noun="building",

    candidate_source="buildings",

    data_layers=(
        "satellite_imagery",
        "building_footprints",
    ),

    # footprint_size : building_type = 0.7 : 0.3, which reproduces
    # the legacy score_solar_candidate() points exactly (size and
    # large-site bonus max 70, building type max 30).
    criteria=(

        Criterion(
            id="footprint_size",
            label="Building footprint size",
            weight=0.35,
            data_layer="building_footprints",
            evaluator="solar_footprint_size",
        ),

        Criterion(
            id="building_type",
            label="Building type",
            weight=0.15,
            data_layer="building_footprints",
            evaluator="solar_building_type",
        ),

        Criterion(
            id="solar_suitability",
            label="Solar irradiance / roof suitability",
            weight=0.20,
            data_layer="solar_irradiance",
            missing_note="No rooftop irradiance data connected.",
        ),

        Criterion(
            id="shading",
            label="Shading",
            weight=0.15,
            data_layer="shading",
            missing_note="No shading analysis connected.",
        ),

        Criterion(
            id="accessibility",
            label="Site accessibility",
            weight=0.15,
            data_layer="roads",
            missing_note=(
                "Not yet measured for solar prospects."
            ),
        ),
    ),

    constraints=(
        "Building footprint is used as a proxy for roof area.",
    ),

    unassessed=(
        "roof_condition",
        "ownership",
    ),

    default_min_area_m2=500,

    output_format="ranked_solar_prospects",

    recommended_action=(
        "Prioritize the highest-scoring "
        "sites for roof-level verification."
    ),

    limitations=(
        "Building footprint is not "
        "equivalent to usable roof area.",

        "Solar suitability has not "
        "been verified from "
        "high-resolution imagery.",

        "Ownership, roof condition, "
        "structural suitability and "
        "shading require additional "
        "verification.",
    ),

    aliases=(
        "solar",
        "rooftop_solar",
        "solar_site_selection",
    ),
)


EV_CHARGING = UseCase(

    id="ev_charging_site_selection",

    title="EV charging site selection",

    description=(
        "Find land or sites for an electric-vehicle charging "
        "station: accessible, near demand, not over-served."
    ),

    example_queries=(
        "Find 10 cent empty land parcels in Thoraipakkam Chennai "
        "for an EV charging station",
        "Where should I put my next EV charger?",
    ),

    candidate_type="land_parcel",

    candidate_noun="land parcel",

    candidate_source="land_parcels",

    data_layers=(
        "satellite_imagery",
        "land_parcels",
        "roads",
        "points_of_interest",
        "ev_chargers",
        "flood_risk",
        "historical_imagery",
        "terrain",
        "land_cover",
        "protected_areas",
        "land_in_use",
    ),

    criteria=(

        Criterion(
            id="parcel_size_fit",
            label="Parcel size fit",
            weight=0.20,
            data_layer="land_parcels",
            evaluator="size_fit",
        ),

        Criterion(
            id="road_access",
            label="Road access",
            weight=0.15,
            data_layer="roads",
            evaluator="road_access",
        ),

        Criterion(
            id="vacancy",
            label="Vacancy evidence",
            weight=0.10,
            data_layer="land_parcels",
            evaluator="vacancy_evidence",
        ),

        Criterion(
            id="major_road_proximity",
            label="Proximity to major roads",
            weight=0.05,
            data_layer="roads",
            evaluator="major_road_proximity",
        ),

        Criterion(
            id="demand_potential",
            label="Demand potential (dwell-time destinations)",
            weight=0.15,
            data_layer="points_of_interest",
            evaluator="demand_potential",
        ),

        Criterion(
            id="commercial_activity",
            label="Commercial activity",
            weight=0.05,
            data_layer="points_of_interest",
            evaluator="commercial_activity",
        ),

        Criterion(
            id="competition",
            label="Competition (existing chargers)",
            weight=0.15,
            data_layer="ev_chargers",
            evaluator="ev_competition",
        ),

        Criterion(
            id="flood_risk",
            label="Flood exposure (observed)",
            weight=0.15,
            data_layer="flood_risk",
            evaluator="flood_exposure",
        ),

        Criterion(
            id="recent_change",
            label="Recent change on site (Sentinel-2)",
            # Evidence only: change is reported, not scored, because
            # its meaning is ambiguous (clearing may mean "ready to build").
            weight=0.0,
            data_layer="historical_imagery",
            evaluator="recent_change",
        ),

        Criterion(
            id="terrain",
            label="Terrain (Copernicus DEM)",
            # Evidence only: Chennai-area terrain is flat and the
            # model's ~2 m noise is close to real differences.
            weight=0.0,
            data_layer="terrain",
            evaluator="terrain",
        ),

        Criterion(
            id="protected_status",
            label="Protected or in-use land (OpenStreetMap)",
            # Evidence only; sites entirely inside a protected area are
            # excluded before ranking.
            weight=0.0,
            data_layer="protected_areas",
            evaluator="protected_status",
        ),
    ),

    discover_open_land=True,

    constraints=(
        "Site should be vacant land.",
        "Vehicle access from a public road.",
    ),

    unassessed=(
        "ownership",
        "grid_connection_capacity",
        "zoning",
    ),

    default_min_area_m2=150,

    output_format="ranked_ev_charging_sites",

    purpose="an EV charging station",

    recommended_action=(
        "Verify vacancy and grid-connection capacity at the "
        "top-ranked parcels, then check ownership."
    ),

    limitations=(
        "Land polygons come from OpenStreetMap land-use tags; "
        "they are not legal/cadastral parcels.",

        "Vacancy is inferred from map tags and has not been "
        "verified on imagery.",

        "Existing chargers are taken from OpenStreetMap, which "
        "may not list every station.",

        "Demand is estimated from nearby mapped destinations, not "
        "from traffic counts or EV registrations.",

        "Flood risk, ownership, zoning and grid capacity "
        "have not been assessed.",
    ),

    aliases=(
        "ev_charging",
        "ev_charger_site_selection",
        "ev_charging_station_site_selection",
        "charging_station_site_selection",
    ),
)


COMMERCIAL_SITE_SELECTION = UseCase(

    id="commercial_site_selection",

    title="Commercial site selection",

    description=(
        "Find a site for a business (shop, restaurant, food "
        "court, showroom, office, clinic, gym...)."
    ),

    example_queries=(
        "Find 4800 sq ft sites around Adyar Chennai suitable "
        "for a food court",
        "Where should I build a food court?",
    ),

    candidate_type="site",

    candidate_noun="site",

    candidate_source="sites",

    data_layers=(
        "satellite_imagery",
        "land_parcels",
        "building_footprints",
        "roads",
        "points_of_interest",
        "parking",
        "population",
        "historical_imagery",
        "terrain",
        "land_cover",
        "protected_areas",
        "land_in_use",
    ),

    criteria=(

        Criterion(
            id="site_size_fit",
            label="Site size fit",
            weight=0.20,
            data_layer="land_parcels",
            evaluator="size_fit",
        ),

        Criterion(
            id="road_access",
            label="Road access",
            weight=0.15,
            data_layer="roads",
            evaluator="road_access",
        ),

        Criterion(
            id="major_road_proximity",
            label="Proximity to major roads",
            weight=0.10,
            data_layer="roads",
            evaluator="major_road_proximity",
        ),

        Criterion(
            id="commercial_activity",
            label="Surrounding commercial activity",
            weight=0.20,
            data_layer="points_of_interest",
            evaluator="commercial_activity",
        ),

        Criterion(
            id="parking_potential",
            label="Parking potential",
            weight=0.10,
            data_layer="parking",
            evaluator="parking_potential",
        ),

        Criterion(
            id="competition",
            label="Competition (similar businesses nearby)",
            weight=0.10,
            data_layer="points_of_interest",
            evaluator="business_competition",
        ),

        Criterion(
            id="population",
            label="Surrounding population / footfall",
            weight=0.15,
            data_layer="population",
            missing_note="No population or footfall data connected.",
        ),

        Criterion(
            id="vacancy",
            label="Vacancy evidence",
            # Evidence only here: shows land cover, tags and mapped
            # building cover for open land without changing commercial
            # scores. (Not measured for building candidates.)
            weight=0.0,
            data_layer="land_parcels",
            evaluator="vacancy_evidence",
        ),

        Criterion(
            id="recent_change",
            label="Recent change on site (Sentinel-2)",
            # Evidence only: change is reported, not scored, because
            # its meaning is ambiguous (clearing may mean "ready to build").
            weight=0.0,
            data_layer="historical_imagery",
            evaluator="recent_change",
        ),

        Criterion(
            id="terrain",
            label="Terrain (Copernicus DEM)",
            # Evidence only: Chennai-area terrain is flat and the
            # model's ~2 m noise is close to real differences.
            weight=0.0,
            data_layer="terrain",
            evaluator="terrain",
        ),

        Criterion(
            id="protected_status",
            label="Protected or in-use land (OpenStreetMap)",
            # Evidence only; sites entirely inside a protected area are
            # excluded before ranking.
            weight=0.0,
            data_layer="protected_areas",
            evaluator="protected_status",
        ),
    ),

    discover_open_land=True,

    constraints=(
        "Site must fit the requested floor/land area.",
    ),

    unassessed=(
        "ownership",
        "rent_or_price",
        "zoning",
    ),

    default_min_area_m2=150,

    output_format="ranked_commercial_sites",

    recommended_action=(
        "Visit the top-ranked sites to confirm availability, "
        "frontage and parking before approaching owners."
    ),

    limitations=(
        "Sites are open-land polygons and commercial buildings "
        "from OpenStreetMap; availability for sale or lease is "
        "unknown.",

        "Site area is a map footprint, not usable floor area.",

        "Commercial activity is estimated from mapped shops and "
        "amenities, not from footfall data.",

        "Ownership, price/rent and zoning have not been assessed.",
    ),

    aliases=(
        "commercial_site",
        "retail_site_selection",
        "restaurant_site_selection",
        "food_court_site_selection",
        "business_site_selection",
    ),
)


LAND_ACQUISITION = UseCase(

    id="land_acquisition",

    title="Land acquisition",

    description=(
        "Find vacant land to buy for development or investment."
    ),

    example_queries=(
        "Find vacant land above 1 acre near OMR with good "
        "road access",
    ),

    candidate_type="land_parcel",

    candidate_noun="land parcel",

    candidate_source="land_parcels",

    data_layers=(
        "satellite_imagery",
        "land_parcels",
        "roads",
        "zoning",
        "flood_risk",
        "ownership",
        "historical_imagery",
        "terrain",
        "land_cover",
        "protected_areas",
        "land_in_use",
    ),

    criteria=(

        Criterion(
            id="parcel_size",
            label="Parcel size",
            weight=0.25,
            data_layer="land_parcels",
            evaluator="parcel_size",
        ),

        Criterion(
            id="vacancy",
            label="Vacancy evidence",
            weight=0.15,
            data_layer="land_parcels",
            evaluator="vacancy_evidence",
        ),

        Criterion(
            id="road_access",
            label="Road access",
            weight=0.20,
            data_layer="roads",
            evaluator="road_access",
        ),

        Criterion(
            id="location",
            label="Closeness to requested location",
            weight=0.10,
            data_layer="land_parcels",
            evaluator="location_proximity",
        ),

        Criterion(
            id="land_use_compatibility",
            label="Land-use compatibility (zoning)",
            weight=0.15,
            data_layer="zoning",
            missing_note="No zoning data connected.",
        ),

        Criterion(
            id="flood_risk",
            label="Flood exposure (observed)",
            weight=0.15,
            data_layer="flood_risk",
            evaluator="flood_exposure",
        ),

        Criterion(
            id="recent_change",
            label="Recent change on site (Sentinel-2)",
            # Evidence only: change is reported, not scored, because
            # its meaning is ambiguous (clearing may mean "ready to build").
            weight=0.0,
            data_layer="historical_imagery",
            evaluator="recent_change",
        ),

        Criterion(
            id="terrain",
            label="Terrain (Copernicus DEM)",
            # Evidence only: Chennai-area terrain is flat and the
            # model's ~2 m noise is close to real differences.
            weight=0.0,
            data_layer="terrain",
            evaluator="terrain",
        ),

        Criterion(
            id="protected_status",
            label="Protected or in-use land (OpenStreetMap)",
            # Evidence only; sites entirely inside a protected area are
            # excluded before ranking.
            weight=0.0,
            data_layer="protected_areas",
            evaluator="protected_status",
        ),
    ),

    discover_open_land=True,

    constraints=(
        "Land should be vacant.",
    ),

    unassessed=(
        "ownership",
        "legal_title",
        "price",
    ),

    default_min_area_m2=1000,

    output_format="ranked_land_parcels",

    recommended_action=(
        "Shortlist the top parcels for a title search and a "
        "site visit."
    ),

    limitations=(
        "Land polygons come from OpenStreetMap land-use tags; "
        "they are not legal/cadastral parcels.",

        "Vacancy is inferred from map tags and has not been "
        "verified on imagery.",

        "Ownership, title, price, zoning and flood risk have "
        "not been assessed.",
    ),

    aliases=(
        "land_search",
        "land_purchase",
        "land_prospecting",
        "vacant_land_search",
    ),
)


CONSTRUCTION_PROGRESS = UseCase(

    id="construction_progress",

    title="Land and construction change",

    description=(
        "Find where land changed in an area between two dates: "
        "construction or clearing, vegetation loss or gain, water "
        "appearing or receding. Compares satellite imagery: "
        "Sentinel-2 (10 m) from 2017, Landsat (30 m) for periods "
        "back to 1984. Default period: the last 12 months."
    ),

    example_queries=(
        "What has changed around Thoraipakkam in the last year?",
        "Where has new construction or land clearing appeared "
        "along OMR since 2023?",
    ),

    candidate_type="change_area",

    candidate_noun="changed area",

    candidate_source="changes",

    data_layers=(
        "satellite_imagery",
        "historical_imagery",
    ),

    # Ranks changes by significance: how strong, how large, and how
    # comparable the two scenes are.
    criteria=(

        Criterion(
            id="change_magnitude",
            label="Change strength",
            weight=0.45,
            data_layer="historical_imagery",
            evaluator="change_magnitude",
        ),

        Criterion(
            id="changed_area",
            label="Changed area",
            weight=0.35,
            data_layer="historical_imagery",
            evaluator="changed_area",
        ),

        Criterion(
            id="imagery_quality",
            label="Imagery quality (cloud cover, date gap)",
            weight=0.2,
            data_layer="historical_imagery",
            evaluator="imagery_quality",
        ),
    ),

    unassessed=(
        "cause_of_change",
        "permits",
    ),

    # 5 pixels at 10 m (Landsat's own 5-pixel minimum is larger).
    default_min_area_m2=500,

    output_format="change_report",

    purpose="significance of change",

    recommended_action=(
        "Review the largest, strongest changes on recent "
        "high-resolution imagery, then confirm the cause on the ground."
    ),

    limitations=(
        "Changes smaller than about 500 m² (Sentinel-2, 10 m) or "
        "4,500 m² (Landsat, 30 m, used before 2017) are not detected.",

        "Spectral change shows that a surface changed, not why: "
        "construction, clearing, farming and flooding can look alike.",

        "Only two dates are compared; changes that started and "
        "reverted between them are missed.",
    ),

    aliases=(
        "construction_monitoring",
        "change_detection",
        "construction_change_detection",
    ),
)


USE_CASES = {

    use_case.id: use_case

    for use_case in (
        SOLAR_PROSPECTING,
        EV_CHARGING,
        COMMERCIAL_SITE_SELECTION,
        LAND_ACQUISITION,
        CONSTRUCTION_PROGRESS,
    )
}


_ALIASES = {

    alias: use_case.id

    for use_case in USE_CASES.values()

    for alias in use_case.aliases
}


# ============================================================
# LOOKUP
# ============================================================

def resolve_use_case(intent_type):
    """
    Map DeepSeek's intent_type onto a registered use case.
    Returns None when SkyLens has no playbook for it.
    """

    if not isinstance(intent_type, str):
        return None

    key = (
        intent_type
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    key = _ALIASES.get(key, key)

    return USE_CASES.get(key)


def data_layer_available(layer_id):

    return data_registry.is_available(layer_id)


def describe_use_case(use_case):
    """
    Public, JSON-friendly summary of a use case.
    """

    return {

        "id": use_case.id,

        "title": use_case.title,

        "purpose": use_case.purpose or use_case.title.lower(),

        "description": use_case.description,

        "implemented": use_case.implemented,

        "candidate_type": use_case.candidate_type,

        # id, label and available, plus the layer's capabilities,
        # limitations and source metadata from the data registry.
        "data_layers": [
            data_registry.describe_layer(layer)
            for layer in use_case.data_layers
        ],

        "criteria": [
            {
                "id": c.id,
                "label": c.label,
                "weight": c.weight,
                "measured": (
                    c.evaluator is not None
                    and data_layer_available(c.data_layer)
                ),
            }
            for c in use_case.criteria
        ],

        "constraints": list(use_case.constraints),

        "output_format": use_case.output_format,
    }


def planner_catalog():
    """
    Text block listing the use cases, injected into the DeepSeek
    planner prompt so it stays in sync with this registry.
    """

    lines = []

    for use_case in USE_CASES.values():

        criteria = ", ".join(
            c.id for c in use_case.criteria
        )

        examples = " | ".join(
            f'"{q}"' for q in use_case.example_queries
        )

        lines.append(
            f"- {use_case.id}: {use_case.description}\n"
            f"  candidate_type: {use_case.candidate_type}\n"
            f"  typical criteria: {criteria}\n"
            f"  examples: {examples}"
        )

    return "\n".join(lines)
