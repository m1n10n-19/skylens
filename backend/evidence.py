"""
Evidence records.

Every measurement behind a score becomes an Evidence item that says
what was found, how, from which source, and when that data is from,
so a report can separate what was measured from what was only
observed in tags, and from what was not measured at all.

Statuses:

    measured               computed by SkyLens from source geometry
                           (distances, areas, counts)
    observed               read directly from the source and not
                           verified (e.g. an OSM land-use tag)
    inferred               derived from several observations
    not_measured           no evidence: the layer is unavailable or
                           the value could not be measured
    verification_required  remote evidence cannot settle it

Evidence items are built from the "measurements" evaluators already
return (criteria.py), using MEASUREMENTS below for units and claims.
Evaluators never produce evidence text themselves.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel

import data_registry


Status = Literal[
    "measured",
    "observed",
    "inferred",
    "not_measured",
    "verification_required",
]


# ============================================================
# MODEL
# ============================================================

class Measurement(BaseModel):

    # None when nothing was found within the search distance.
    value: Any = None

    unit: Optional[str] = None


class Evidence(BaseModel):

    claim: str

    status: Status

    measurement: Optional[Measurement] = None

    # Data layer and source the evidence comes from.
    layer: str

    source: Optional[str] = None

    source_id: Optional[str] = None

    # When the phenomenon was observed (e.g. satellite acquisition).
    # None for map data, whose edit dates SkyLens does not track.
    observed_at: Optional[str] = None

    # When the source data was current (e.g. OSM snapshot time).
    data_as_of: Optional[str] = None

    # When SkyLens fetched the data.
    retrieved_at: Optional[str] = None

    method: Optional[str] = None

    note: Optional[str] = None


# ============================================================
# MEASUREMENT CATALOGUE
# ============================================================

_DISTANCE = "Shortest distance from the candidate outline, in a local metric projection"

_COUNT = "Count of mapped features within the stated distance of the candidate outline"

_TAG = "Read from the OpenStreetMap tag; not verified on imagery or the ground"

_INDEX = (
    "Mean over the patch of a normalized-difference index from Sentinel-2 "
    "surface reflectance (see the analysis method)"
)

_SCENE = "From the Sentinel-2 scene metadata"

_SHARE = (
    "Share of the site's cloud-free 10 m pixels whose spectral change "
    "between the two Sentinel-2 images passed the thresholds"
)

_DEM = (
    "Copernicus DEM GLO-30 surface model (includes trees and buildings; "
    "about ±2 m relative accuracy; acquired 2011-2015)"
)

_CLOUD = "Pixels classed as clear ground in the scene classification layer"

_ABSENT = (
    "None mapped within the search distance. Absence from the map "
    "does not prove absence on the ground."
)


# measurement key -> (claim, unit, status, method)
MEASUREMENTS = {

    # Size
    "footprint_area_m2": (
        "Building footprint area", "m2", "measured",
        "Area of the mapped building outline",
    ),
    "site_area_m2": (
        "Site area", "m2", "measured",
        "Area of the mapped polygon",
    ),
    "building_type_tag": (
        "Mapped building type", None, "observed", _TAG,
    ),

    # Access
    "nearest_road_m": (
        "Distance to the nearest mapped vehicle road (within 500 m)",
        "m", "measured", _DISTANCE,
    ),
    "nearest_road": (
        "Nearest mapped vehicle road", None, "observed", _TAG,
    ),
    "nearest_road_type": (
        "Class of the nearest mapped road", None, "observed", _TAG,
    ),
    "nearest_major_road_m": (
        "Distance to the nearest mapped primary, secondary or trunk road (within 1 km)",
        "m", "measured", _DISTANCE,
    ),
    "nearest_major_road": (
        "Nearest mapped major road", None, "observed", _TAG,
    ),

    # Activity
    "dwell_destinations_500m": (
        "Mapped dwell-time destinations within 500 m", "count", "measured", _COUNT,
    ),
    "businesses_500m": (
        "Mapped shops, offices and amenities within 500 m", "count", "measured", _COUNT,
    ),

    # Competition
    "chargers_2km": (
        "Mapped EV charging stations within 2 km", "count", "measured", _COUNT,
    ),
    "chargers_1km": (
        "Mapped EV charging stations within 1 km", "count", "measured", _COUNT,
    ),
    "nearest_charger_m": (
        "Distance to the nearest mapped EV charging station (within 2 km)",
        "m", "measured", _DISTANCE,
    ),
    "competitors_500m": (
        "Similar mapped businesses within 500 m", "count", "measured", _COUNT,
    ),

    # Parking
    "parking_areas_300m": (
        "Mapped parking areas within 300 m", "count", "measured", _COUNT,
    ),

    # Land
    "landuse_tag": (
        "Mapped land use", None, "observed", _TAG,
    ),
    "distance_to_centre_m": (
        "Distance from the centre of the searched place", "m", "measured", _DISTANCE,
    ),

    # Change (Sentinel-2)
    "changed_area_m2": (
        "Area of detected change", "m2", "measured",
        "10 m pixels whose spectral index change passed the thresholds, x 100 m²",
    ),
    "index_before": (
        "Mean spectral index in the earlier scene", "index", "measured", _INDEX,
    ),
    "index_after": (
        "Mean spectral index in the later scene", "index", "measured", _INDEX,
    ),
    "index_delta": (
        "Mean change in the spectral index", "index", "measured", _INDEX,
    ),
    "before_scene_date": (
        "Acquisition date of the earlier scene", None, "observed", _SCENE,
    ),
    "after_scene_date": (
        "Acquisition date of the later scene", None, "observed", _SCENE,
    ),
    "clear_fraction_before": (
        "Share of the searched area clear of cloud in the earlier scene", "fraction",
        "measured", _CLOUD,
    ),
    "clear_fraction_after": (
        "Share of the searched area clear of cloud in the later scene", "fraction",
        "measured", _CLOUD,
    ),
    # Recent change on a site (shares of its clear pixels)
    "changed_share": (
        "Share of the site that changed", "fraction", "measured", _SHARE,
    ),
    "built_or_bare_increase_share": (
        "Share of the site where built-up or bare surface increased", "fraction",
        "measured", _SHARE,
    ),
    "vegetation_loss_share": (
        "Share of the site that lost vegetation", "fraction", "measured", _SHARE,
    ),
    "vegetation_gain_share": (
        "Share of the site that gained vegetation", "fraction", "measured", _SHARE,
    ),
    "water_gain_share": (
        "Share of the site where water appeared", "fraction", "measured", _SHARE,
    ),
    "water_loss_share": (
        "Share of the site where water receded", "fraction", "measured", _SHARE,
    ),
    # Terrain (Copernicus DEM)
    "elevation_m": (
        "Surface elevation above sea level (median over the site)", "m", "measured", _DEM,
    ),
    "relative_elevation_m": (
        "Height of the site relative to the ground around it", "m", "measured", _DEM,
    ),
    "slope_pct": (
        "Mean slope across the site", "percent", "measured", _DEM,
    ),
    "season_gap_days": (
        "Difference in time of year between the two scenes", "days", "measured",
        "Days between the scenes' positions in the calendar year",
    ),
}


# ============================================================
# BUILDERS
# ============================================================

def _source_fields(layer_id, provenance):

    provenance = provenance or {}

    # A layer can be supplied by more than one source (historical
    # imagery: Sentinel-2 or Landsat); provenance names the one used.
    used = data_registry.SOURCES.get(provenance.get("source_id"))

    source = used or data_registry.resolve(layer_id)

    return {
        "layer": layer_id,
        "source": used.name if used else data_registry.source_label(layer_id),
        "source_id": source.id if source else None,
        "observed_at": provenance.get("observed_at"),
        "data_as_of": provenance.get("data_as_of"),
        "retrieved_at": provenance.get("retrieved_at"),
    }


def from_measurements(measurements, layer_id, provenance=None):
    """
    Evidence items for one criterion's measurements dict.
    Unknown keys are kept as "observed" with no unit rather than
    dropped, so nothing measured disappears from the report.
    """

    items = []

    for key, value in (measurements or {}).items():

        claim, unit, status, method = MEASUREMENTS.get(
            key, (key.replace("_", " ").capitalize(), None, "observed", None)
        )

        items.append(Evidence(
            claim=claim,
            status=status,
            measurement=Measurement(value=value, unit=unit),
            method=method,
            note=_ABSENT if value is None else None,
            **_source_fields(layer_id, provenance),
        ))

    return items


def not_measured(label, layer_id, note):
    """
    The single evidence item for a criterion with no evidence.
    """

    return Evidence(
        claim=label,
        status="not_measured",
        note=note,
        **_source_fields(layer_id, None),
    )


def dump(items):

    return [item.model_dump() for item in items]
