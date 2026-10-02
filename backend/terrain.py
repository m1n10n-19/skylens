"""
Terrain from the Copernicus DEM (GLO-30, Microsoft Planetary Computer).

    measure_sites(sites)

reads the elevation model once around a list of sites and returns,
for each: its elevation above sea level, its height relative to the
ground within RING_M around it, and its mean slope.

The Copernicus DEM is a surface model: it includes buildings and
trees. On open land it is close to the ground; on a building it
measures the roof, so building sites are reported as not measurable.
Its relative vertical accuracy is about 2 m, so smaller differences
from the surroundings are reported as no meaningful difference. The
radar data was acquired in 2011-2015: later earthworks do not appear.

Terrain is evidence about drainage, not a flood-risk measurement.
"""

import numpy as np

from shapely.geometry import mapping


COLLECTION = "cop-dem-glo-30"

SOURCE_ID = "copernicus_dem_glo30_planetary_computer"

PIXEL_M = 30

# The surroundings a site is compared with.
RING_M = 500

# Relative vertical accuracy: differences within this are noise.
NOISE_M = 2.0

# A site at least this far below its surroundings is low-lying.
LOW_LYING_M = 2.0

# Largest grid read (30 km x 30 km at 30 m), for a shortlist spread
# along a road.
MAX_GRID_PIXELS = 1000 * 1000

BUILDING_REASON = (
    "The elevation model is a surface model: on a building it measures "
    "the roof, not the ground."
)


class TerrainUnavailable(Exception):
    pass


# ============================================================
# PURE MATHS (tested with fixed arrays)
# ============================================================

def slope_percent(dem, pixel_m=PIXEL_M):
    """
    Slope in percent for every pixel (NaN where the DEM is missing).
    """

    dz_dy, dz_dx = np.gradient(dem.astype("float64"), pixel_m)

    return np.hypot(dz_dx, dz_dy) * 100


def site_terrain(dem, slope, site, ring):
    """
    {"elevation_m", "surroundings_m", "relative_elevation_m",
     "slope_pct"} for boolean masks of a site and its ring, or None
    when the DEM has no data for the site or its surroundings.
    """

    on_site = dem[site & np.isfinite(dem)]

    around = dem[ring & np.isfinite(dem)]

    if on_site.size == 0 or around.size == 0:
        return None

    elevation = float(np.median(on_site))

    surroundings = float(np.median(around))

    slopes = slope[site & np.isfinite(slope)]

    return {
        "elevation_m": round(elevation, 1),
        "surroundings_m": round(surroundings, 1),
        "relative_elevation_m": round(elevation - surroundings, 1),
        "slope_pct": round(float(np.mean(slopes)), 1) if slopes.size else None,
    }


# ============================================================
# PROVIDER
# ============================================================

def search_tiles(bbox_lonlat):

    from change_detection import _catalog

    return list(_catalog().search(collections=[COLLECTION], bbox=list(bbox_lonlat)).items())


def read_dem(href, grid):
    """
    Elevation in metres on the grid; NaN outside the tile's footprint
    (the file has no no-data value, so the footprint comes from an
    alpha band rather than mistaking 0 for sea level).
    """

    import rasterio

    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MAX_RETRY="2",
        GDAL_HTTP_RETRY_DELAY="1",
    ):
        with rasterio.open(href) as src, WarpedVRT(
            src, crs=grid.crs, transform=grid.transform,
            width=grid.width, height=grid.height,
            resampling=Resampling.bilinear, add_alpha=True,
        ) as vrt:
            values = vrt.read(1).astype("float32")
            alpha = vrt.read(2)

    values[alpha == 0] = np.nan

    return values


# ============================================================
# MEASURE
# ============================================================

def measure_sites(sites, search=None, reader=None):
    """
    Terrain for each site. sites: list of (lonlat_shape, ring_lonlat_shape,
    is_building). Returns (results, info): results in input order, each

        {"measurable": True, "elevation_m", "surroundings_m",
         "relative_elevation_m", "slope_pct"}

    or {"measurable": False, "reason"}; info describes the data used.
    Raises TerrainUnavailable when no DEM tile covers the area.
    """

    from rasterio import features
    from rasterio.warp import transform_geom
    from shapely.ops import unary_union

    from change_detection import make_grid

    search = search or search_tiles

    reader = reader or read_dem

    area = unary_union([ring for _, ring, _ in sites])

    grid = make_grid(area, PIXEL_M, MAX_GRID_PIXELS)

    tiles = search(grid.bbox_lonlat)

    if not tiles:
        raise TerrainUnavailable("No Copernicus DEM tile covers this area.")

    dem = np.full((grid.height, grid.width), np.nan, dtype="float32")

    for tile in tiles:
        values = reader(tile.assets["data"].href, grid)
        fill = np.isnan(dem) & np.isfinite(values)
        dem[fill] = values[fill]

    slope = slope_percent(dem)

    def mask(geometry, all_touched=False):
        return features.rasterize(
            [transform_geom("EPSG:4326", grid.crs, mapping(geometry))],
            out_shape=(grid.height, grid.width), transform=grid.transform,
            fill=0, dtype="uint8", all_touched=all_touched,
        ).astype(bool)

    results = []

    for site, ring, is_building in sites:

        if is_building:
            results.append({"measurable": False, "reason": BUILDING_REASON})
            continue

        # Small plots may not contain a pixel centre: use every pixel
        # they touch.
        site_mask = mask(site, all_touched=True)

        measured = site_terrain(dem, slope, site_mask, mask(ring) & ~site_mask)

        if measured is None:
            results.append({"measurable": False, "reason": "No elevation data covers this site."})
        else:
            results.append({"measurable": True, **measured})

    info = {
        "source": SOURCE_ID,
        "tiles": [tile.id for tile in tiles],
        "resolution_m": PIXEL_M,
        "ring_m": RING_M,
        "acquired": "2011-2015 (TanDEM-X)",
        "method": (
            f"Copernicus DEM GLO-30 surface model resampled to {PIXEL_M} m. "
            f"Elevation: median over the site. Relative elevation: site median "
            f"minus the median within {RING_M} m around it. Slope: mean over "
            f"the site. Differences within ±{NOISE_M:g} m are within the "
            f"model's relative accuracy."
        ),
    }

    return results, info
