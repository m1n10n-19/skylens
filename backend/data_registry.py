"""
SkyLens data registry.

Every data layer the analyses can ask for, and the source (provider)
that supplies it today, with the metadata needed to judge the
evidence: resolution, coverage, freshness, cost, latency and known
limitations.

A layer is what the analysis needs ("roads"); a source is who
provides it ("OpenStreetMap via Overpass"). Business logic asks for
a layer and resolves its source here, so a provider can be swapped
(Overpass -> PostGIS, Sentinel-2 -> commercial imagery) without
touching the analysis engine.

Layers with no source are listed on purpose: criteria that depend on
them are reported as not measured instead of being estimated.

Capabilities name what SkyLens actually measures with a layer today,
not what the underlying data could support in principle.
"""

from dataclasses import dataclass
from typing import Optional


# ============================================================
# MODELS
# ============================================================

@dataclass(frozen=True)
class DataSource:

    id: str

    # Shown to users as the evidence source.
    name: str

    provider: str

    spatial_resolution: str

    temporal_resolution: str

    coverage: str

    freshness: str

    # "free" | "metered" | "paid"
    cost: str

    latency: str

    license: Optional[str] = None

    limitations: tuple = ()

    available: bool = True


@dataclass(frozen=True)
class DataLayer:

    id: str

    label: str

    # DataSource id, or None when SkyLens has no provider yet.
    source: Optional[str] = None

    # What SkyLens measures with this layer.
    capabilities: tuple = ()

    # Overrides the source name where the layer is a specific use of
    # the source, e.g. land-use tags rather than parcels.
    source_note: Optional[str] = None

    # Further sources that can supply the layer; the one actually used
    # is recorded in each result's provenance.
    other_sources: tuple = ()

    limitations: tuple = ()


# ============================================================
# SOURCES
# ============================================================

OSM_OVERPASS = DataSource(

    id="osm_overpass",

    name="OpenStreetMap (Overpass)",

    provider="OpenStreetMap contributors, via public Overpass API servers",

    spatial_resolution="Vector features; positional accuracy depends on the mapper",

    temporal_resolution="Continuous community edits",

    coverage="Global; completeness varies strongly by area",

    freshness=(
        "Live OSM data, cached by SkyLens for up to 24 hours "
        "(OVERPASS_CACHE_HOURS)"
    ),

    cost="free",

    latency=(
        "Seconds to about a minute; public servers are rate-limited "
        "(time budget OVERPASS_BUDGET_SECONDS, default 100 s)"
    ),

    license="ODbL, © OpenStreetMap contributors",

    limitations=(
        "Absence of a mapped feature does not prove it is absent on the ground.",
        "Tags are volunteer-entered and not verified by SkyLens.",
    ),
)


SENTINEL_2_PC = DataSource(

    id="sentinel_2_planetary_computer",

    name="Microsoft Planetary Computer",

    provider="ESA Copernicus Sentinel-2 L2A, via Microsoft Planetary Computer STAC",

    spatial_resolution="10 m (visual bands)",

    temporal_resolution="About 5-day revisit",

    coverage="Global land",

    freshness="Clearest scene from the last 90 days with under 20% cloud cover",

    cost="free",

    latency="A few seconds (catalogue search)",

    license="Copernicus Sentinel data terms",

    limitations=(
        "10 m pixels cannot resolve individual rooftops or small plots.",
        "Clouds or haze can hide the area even below the 20% scene threshold.",
    ),
)


LANDSAT_PC = DataSource(

    id="landsat_c2_l2_planetary_computer",

    name="Microsoft Planetary Computer (Landsat)",

    provider="USGS Landsat Collection 2 Level-2 (Landsat 5, 7, 8, 9), via Microsoft Planetary Computer STAC",

    spatial_resolution="30 m",

    temporal_resolution="About 16-day revisit per satellite",

    coverage="Global land, 1984 onwards",

    freshness="Used for comparisons that reach back before 2017",

    cost="free",

    latency="A few seconds (catalogue search)",

    license="USGS public domain",

    limitations=(
        "30 m pixels: changes under about 4,500 m² are not detected.",
        "Landsat 7 images after May 2003 have permanent data gaps; used only when no other image is clear.",
        "Small calibration differences between Landsat 5, 7, 8 and 9 sensors.",
    ),
)


COPERNICUS_DEM_PC = DataSource(

    id="copernicus_dem_glo30_planetary_computer",

    name="Copernicus DEM (Microsoft Planetary Computer)",

    provider="Copernicus DEM GLO-30 (ESA / Airbus, TanDEM-X), via Microsoft Planetary Computer STAC",

    spatial_resolution="30 m",

    temporal_resolution="Single release; radar data acquired 2011-2015",

    coverage="Global land",

    freshness="Static: earthworks and landfill after 2015 do not appear",

    cost="free",

    latency="A few seconds per area",

    license="Copernicus DEM licence (free use with attribution)",

    limitations=(
        "Surface model: includes buildings and trees, so on built-up land it measures roofs.",
        "About ±2 m relative and ±4 m absolute vertical accuracy.",
        "Terrain, not flood risk: it shows where ground is low, not how often it floods.",
    ),
)


SENTINEL_1_PC = DataSource(

    id="sentinel_1_rtc_planetary_computer",

    name="Sentinel-1 radar (Microsoft Planetary Computer)",

    provider="ESA Copernicus Sentinel-1 RTC (radiometrically terrain corrected), via Microsoft Planetary Computer STAC",

    spatial_resolution="10 m",

    temporal_resolution="About 12 days (one satellite since 2022)",

    coverage="Global land; gaps in some months",

    freshness="Wet seasons of the last three years and the current one",

    cost="free",

    latency="Seconds per image; large remote files",

    license="Copernicus Sentinel data terms",

    limitations=(
        "Floods that drain between satellite passes are missed.",
        "Water among buildings is under-detected (radar double bounce).",
        "Some months have no images (e.g. December 2023 over Chennai).",
    ),
)


JRC_GSW_PC = DataSource(

    id="jrc_gsw_planetary_computer",

    name="JRC Global Surface Water (Microsoft Planetary Computer)",

    provider="EC Joint Research Centre Global Surface Water v1.3, via Microsoft Planetary Computer STAC",

    spatial_resolution="30 m",

    temporal_resolution="Summary of 1984-2020",

    coverage="Global",

    freshness="Ends in 2020",

    cost="free",

    license="Copernicus programme, free use with attribution",

    latency="A few seconds",

    limitations=(
        "Built from optical Landsat images: most short floods are not captured.",
        "Ends in 2020.",
    ),
)


NASA_POWER = DataSource(

    id="nasa_power_climatology",

    name="NASA POWER rainfall climatology",

    provider="NASA Prediction Of Worldwide Energy Resources, monthly climatology API",

    spatial_resolution="About 0.5 degree",

    temporal_resolution="Long-term monthly means",

    coverage="Global",

    freshness="Climatology (long-term averages)",

    cost="free",

    latency="About 2 seconds; cached per location",

    license="NASA open data",

    limitations=(
        "Used only to choose each location's wet and dry months.",
    ),
)


SOURCES = {

    source.id: source

    for source in (
        OSM_OVERPASS,
        SENTINEL_2_PC,
        LANDSAT_PC,
        COPERNICUS_DEM_PC,
        SENTINEL_1_PC,
        JRC_GSW_PC,
        NASA_POWER,
    )
}


# ============================================================
# LAYERS
# ============================================================

_LAYERS = (

    DataLayer(
        id="satellite_imagery",
        label="Satellite imagery (Sentinel-2, 10 m)",
        source="sentinel_2_planetary_computer",
        capabilities=("imagery_context",),
        limitations=(
            "Used as visual context only; not used in scoring.",
        ),
    ),

    DataLayer(
        id="building_footprints",
        label="Building footprints",
        source="osm_overpass",
        capabilities=("building_detection", "land_area"),
        limitations=(
            "Footprint area is not usable roof area.",
            "Building type comes from OSM tags, often just 'yes'.",
        ),
    ),

    DataLayer(
        id="land_parcels",
        label="Open / vacant land polygons",
        source="osm_overpass",
        source_note="OpenStreetMap land-use tags (not cadastral parcels)",
        capabilities=("land_area", "land_use"),
        limitations=(
            "Polygons are mapped land use, not legal parcel boundaries.",
            "Vacancy is a tag, not verified on imagery or the ground.",
        ),
    ),

    DataLayer(
        id="roads",
        label="Road network",
        source="osm_overpass",
        capabilities=("road_access", "infrastructure_proximity"),
        limitations=(
            "Distance to a road is not legal access or frontage.",
        ),
    ),

    DataLayer(
        id="points_of_interest",
        label="Shops, offices and amenities",
        source="osm_overpass",
        capabilities=("poi_density", "business_competition"),
        limitations=(
            "Mapped places are a proxy for activity, not measured footfall.",
        ),
    ),

    DataLayer(
        id="ev_chargers",
        label="Existing EV charging stations",
        source="osm_overpass",
        capabilities=("business_competition",),
        limitations=(
            "OSM coverage of charging stations may be incomplete.",
        ),
    ),

    DataLayer(
        id="parking",
        label="Mapped parking areas",
        source="osm_overpass",
        capabilities=("parking",),
    ),

    # --------------------------------------------------------
    # No provider yet
    # --------------------------------------------------------

    DataLayer(
        id="flood_risk",
        label="Flood exposure (observed water and flooding)",
        source="sentinel_1_rtc_planetary_computer",
        other_sources=("jrc_gsw_planetary_computer", "nasa_power_climatology"),
        capabilities=("flood_exposure",),
        limitations=(
            "Observed exposure, not a flood probability or an official flood-zone map.",
            "Floods between satellite passes, and before 1984, are missed.",
        ),
    ),

    DataLayer(
        id="population",
        label="Population / footfall",
        capabilities=("population",),
    ),

    DataLayer(
        id="zoning",
        label="Zoning / permitted land use",
        capabilities=("zoning",),
    ),

    DataLayer(
        id="ownership",
        label="Ownership / title records",
        capabilities=("ownership",),
    ),

    DataLayer(
        id="solar_irradiance",
        label="Rooftop solar irradiance",
        capabilities=("solar_irradiance",),
    ),

    DataLayer(
        id="shading",
        label="Roof shading analysis",
        capabilities=("shading",),
    ),

    DataLayer(
        id="historical_imagery",
        label="Historical imagery comparison",
        source="sentinel_2_planetary_computer",
        other_sources=("landsat_c2_l2_planetary_computer",),
        capabilities=(
            "historical_change",
            "vegetation_change",
            "construction_change",
            "water_change",
        ),
        limitations=(
            "Two-date spectral comparison: Sentinel-2 at 10 m (2017 onwards) or, for "
            "earlier periods, Landsat at 30 m (1984 onwards).",
            "Changes under about 500 m² (Sentinel-2) or 4,500 m² (Landsat) are not detected.",
            "Shows that a surface changed, not why: construction, clearing, farming and flooding can look alike.",
        ),
    ),

    DataLayer(
        id="site_registry",
        label="Customer's registered sites",
        capabilities=("customer_sites",),
    ),

    DataLayer(
        id="terrain",
        label="Elevation and terrain",
        source="copernicus_dem_glo30_planetary_computer",
        capabilities=("terrain", "elevation", "slope", "low_lying_land"),
        limitations=(
            "Not measurable on buildings: the surface model measures roofs.",
            "Differences under about 2 m are within the model's accuracy.",
            "Low-lying is not flood risk; drainage and flood history need verification.",
        ),
    ),
)


LAYERS = {layer.id: layer for layer in _LAYERS}


# ============================================================
# LOOKUP
# ============================================================

def get_layer(layer_id):

    return LAYERS.get(layer_id)


def resolve(layer_id):
    """
    The DataSource that currently supplies a layer, or None when
    SkyLens has no working provider for it.
    """

    layer = LAYERS.get(layer_id)

    if layer is None or layer.source is None:
        return None

    source = SOURCES[layer.source]

    return source if source.available else None


def is_available(layer_id):

    return resolve(layer_id) is not None


def layers_for(capability):
    """
    Layers that provide a capability, available ones first.
    """

    matches = [
        layer for layer in _LAYERS
        if capability in layer.capabilities
    ]

    return sorted(matches, key=lambda layer: not is_available(layer.id))


def layer_label(layer_id):

    layer = LAYERS.get(layer_id)

    return layer.label if layer else layer_id


def source_label(layer_id):
    """
    Evidence source shown to users, or None without a provider.
    """

    layer = LAYERS.get(layer_id)

    if layer is None or layer.source is None:
        return None

    return layer.source_note or SOURCES[layer.source].name


# ============================================================
# OUTPUT
# ============================================================

def describe_source(source):

    return {
        "id": source.id,
        "name": source.name,
        "provider": source.provider,
        "spatial_resolution": source.spatial_resolution,
        "temporal_resolution": source.temporal_resolution,
        "coverage": source.coverage,
        "freshness": source.freshness,
        "cost": source.cost,
        "latency": source.latency,
        "license": source.license,
        "limitations": list(source.limitations),
    }


def describe_layer(layer_id):
    """
    Public, JSON-friendly description of a layer and its source.
    """

    layer = LAYERS.get(layer_id)

    if layer is None:
        return {
            "id": layer_id,
            "label": layer_id,
            "available": False,
            "capabilities": [],
            "limitations": [],
            "source": None,
        }

    source = resolve(layer_id)

    return {
        "id": layer.id,
        "label": layer.label,
        "available": source is not None,
        "capabilities": list(layer.capabilities),
        "limitations": list(layer.limitations),
        "source": describe_source(source) if source else None,
        "other_sources": [
            describe_source(SOURCES[s]) for s in layer.other_sources
            if SOURCES[s].available
        ],
    }


def legacy_data_layers():
    """
    The original use_cases.DATA_LAYERS shape:
    {layer_id: {"label", "source", "available"}}.
    """

    return {
        layer.id: {
            "label": layer.label,
            "source": source_label(layer.id),
            "available": is_available(layer.id),
        }
        for layer in _LAYERS
    }
