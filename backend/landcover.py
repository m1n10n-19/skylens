"""
Open land found in imagery, for land and site analyses.

    discover(area_geometry, context, existing, min_area_m2, max_area_m2)

OpenStreetMap only lists land someone has tagged. This finds further
open land in the searched area from ESA WorldCover (10 m, 2021):
tree cover, shrubland, grassland, cropland and bare ground, as
connected patches that

- are split by mapped roads (a candidate never spans a road),
- are at least MIN_WIDTH_PIXELS (20 m) wide: thinner strips are mostly
  road verges and edges, not plots,
- leave out land already covered by OSM candidates,
- leave out land that was vegetated in 2021 but looks built-up in the
  latest clear Sentinel-2 image (built on, or cleared, since 2021).

Land that was bare in 2021 cannot be checked this way (bare and
built-up look alike at 10 m), so it is kept and labelled. Every
candidate is labelled as found in imagery and not tagged on
OpenStreetMap; its land cover is an observation, not verified.
"""

import numpy as np

from shapely.geometry import mapping, shape as to_shape
from shapely.ops import transform as reproject


COLLECTION = "esa-worldcover"

SOURCE_ID = "esa_worldcover_planetary_computer"

PIXEL_M = 10

# WorldCover classes treated as open land.
OPEN_CLASSES = {
    10: "tree cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    60: "bare ground",
}

VEGETATED = (10, 20, 30, 40)

# Half widths cut out along mapped roads, in metres.
MAJOR_ROAD_HALF_WIDTH_M = 10

ROAD_HALF_WIDTH_M = 5

# Largest number of candidates added (largest first), to bound the
# per-candidate measurements that follow.
MAX_DISCOVERED = 150

MAX_GRID_PIXELS = 1000 * 1000

# Smallest patch added (3 x 3 pixels, 900 m²): smaller patches of a
# 2021 map with ~75% class accuracy are mostly edge noise.
MIN_PATCH_PIXELS = 9

# Strips narrower than this many pixels (road verges, medians, edges
# between road and buildings) are removed before forming patches.
MIN_WIDTH_PIXELS = 2


class LandCoverUnavailable(Exception):
    pass


# ============================================================
# PURE MATHS (tested with fixed arrays)
# ============================================================

def open_mask(classes, built_now=None, clear_now=None, removed=None):
    """
    Pixels of open land. classes: WorldCover codes (NaN = no data).
    built_now / clear_now: current Sentinel-2 check (optional).
    removed: pixels to leave out (roads, OSM candidates).
    """

    open_land = np.isin(classes, list(OPEN_CLASSES))

    if built_now is not None:
        # Vegetated in 2021 but built-up-looking now: built or cleared since.
        changed = np.isin(classes, VEGETATED) & clear_now & built_now
        open_land &= ~changed

    if removed is not None:
        open_land &= ~removed

    return open_land


def _shift(mask, dy, dx):

    out = np.zeros_like(mask)

    h, w = mask.shape

    out[max(dy, 0): h + min(dy, 0), max(dx, 0): w + min(dx, 0)] =         mask[max(-dy, 0): h + min(-dy, 0), max(-dx, 0): w + min(-dx, 0)]

    return out


def without_strips(mask, width=MIN_WIDTH_PIXELS):
    """
    Morphological opening with a width x width square: removes parts of
    the mask narrower than `width` pixels and keeps the rest unchanged.
    """

    offsets = [(dy, dx) for dy in range(width) for dx in range(width)]

    eroded = np.ones_like(mask)

    for dy, dx in offsets:
        eroded &= _shift(mask, -dy, -dx)

    opened = np.zeros_like(mask)

    for dy, dx in offsets:
        opened |= _shift(eroded, dy, dx)

    return opened & mask


def class_shares(classes, inside):
    """
    {class label: share} of a patch's pixels, largest first.
    """

    values = classes[inside]

    if values.size == 0:
        return {}

    codes, counts = np.unique(values[np.isfinite(values)], return_counts=True)

    shares = {
        OPEN_CLASSES[int(code)]: round(float(count) / values.size, 3)
        for code, count in zip(codes, counts)
        if int(code) in OPEN_CLASSES
    }

    return dict(sorted(shares.items(), key=lambda kv: -kv[1]))


# ============================================================
# PROVIDER
# ============================================================

def search_tiles(bbox_lonlat):
    """
    The latest WorldCover release covering the area.
    """

    from change_detection import _catalog

    items = list(_catalog().search(collections=[COLLECTION], bbox=list(bbox_lonlat)).items())

    if not items:
        return []

    latest = max(i.properties.get("start_datetime") or "" for i in items)

    return [i for i in items if (i.properties.get("start_datetime") or "") == latest]


def read_classes(href, grid):

    from flood import read

    return read(href, grid)


# ============================================================
# DISCOVER
# ============================================================

def discover(area_geometry, context, existing, min_area_m2=0, max_area_m2=None,
             today=None, search=None, reader=None, current=None):
    """
    (candidates, info). area_geometry: lon/lat shape of the searched
    area; existing: metric shapes of OSM candidates; current: a
    callable(grid) -> (built_now, clear_now, scene summary), by
    default change_detection.built_like_now.

    info["problems"] lists checks that could not be made (e.g. no clear
    current image); discovery still runs without them.
    """

    from rasterio import features
    from rasterio.warp import transform_geom

    from change_detection import ChangeDataUnavailable, built_like_now, make_grid, rasterize

    search = search or search_tiles

    reader = reader or read_classes

    grid = make_grid(area_geometry, PIXEL_M, MAX_GRID_PIXELS)

    tiles = search(grid.bbox_lonlat)

    if not tiles:
        raise LandCoverUnavailable("No ESA WorldCover tile covers this area.")

    classes = np.full((grid.height, grid.width), np.nan, dtype="float32")

    for tile in tiles:
        values = reader(tile.assets["map"].href, grid)
        fill = np.isnan(classes) & np.isfinite(values) & (values > 0)
        classes[fill] = values[fill]

    classes[~grid.inside] = np.nan

    proj = context.proj

    def to_grid(metric_shape):
        lonlat = reproject(lambda x, y, z=None: proj.to_lonlat(x, y), metric_shape)
        return transform_geom("EPSG:4326", grid.crs, mapping(lonlat))

    def burn(metric_shapes):
        shapes = [to_grid(s) for s in metric_shapes if not s.is_empty]
        if not shapes:
            return np.zeros((grid.height, grid.width), dtype=bool)
        return rasterize(shapes, out_shape=(grid.height, grid.width),
                         transform=grid.transform, fill=0, dtype="uint8",
                         all_touched=True).astype(bool)

    from geodata import MAJOR_ROADS

    roads = [
        road.line.buffer(MAJOR_ROAD_HALF_WIDTH_M if road.highway in MAJOR_ROADS else ROAD_HALF_WIDTH_M)
        for road in context.roads
    ]

    removed = burn(roads) | burn(list(existing))

    problems = []

    info = {
        "source": SOURCE_ID,
        "tiles": [t.id for t in tiles],
        "land_cover_year": str(tiles[0].properties.get("start_datetime") or "")[:4] or None,
        "problems": problems,
    }

    built_now = clear_now = None

    try:
        built_now, clear_now, scene = (current or (lambda g: built_like_now(g, today)))(grid)
        info["checked_against"] = scene
    except ChangeDataUnavailable as e:
        problems.append(
            "Open land from the 2021 land-cover map could not be checked against a "
            f"current image: {e.reason}"
        )
    except Exception as e:
        problems.append(
            "Open land from the 2021 land-cover map could not be checked against a "
            f"current image ({type(e).__name__}: {e})."
        )

    mask = without_strips(open_mask(classes, built_now, clear_now, removed))

    # 4-connected, so patches touching only at a corner stay apart.
    patches = [
        (geometry, int(round(to_shape(geometry).area / PIXEL_M ** 2)))
        for geometry, value in features.shapes(
            mask.astype("uint8"), mask=mask, connectivity=4, transform=grid.transform,
        )
    ]

    smallest = max(min_area_m2, MIN_PATCH_PIXELS * PIXEL_M ** 2)

    patches = [
        (g, px) for g, px in patches
        if px * PIXEL_M ** 2 >= smallest and (not max_area_m2 or px * PIXEL_M ** 2 <= max_area_m2)
    ]

    patches.sort(key=lambda gp: -gp[1])

    info["patches_found"] = len(patches)

    patches = patches[:MAX_DISCOVERED]

    checked_on = (info.get("checked_against") or {}).get("date")

    candidates = []

    for i, (geometry, pixels) in enumerate(patches, start=1):

        inside = rasterize([geometry], out_shape=(grid.height, grid.width),
                           transform=grid.transform, fill=0, dtype="uint8").astype(bool)

        shares = class_shares(classes, inside & mask)

        if not shares:
            continue

        dominant = next(iter(shares))

        lonlat = to_shape(transform_geom(grid.crs, "EPSG:4326", geometry, precision=6))

        metric = reproject(lambda x, y, z=None: proj.to_xy(x, y), lonlat)

        centroid = lonlat.centroid

        candidates.append({
            "candidate_id": f"landcover-{i}",
            "latitude": round(centroid.y, 6),
            "longitude": round(centroid.x, 6),
            "area_m2": pixels * PIXEL_M ** 2,
            "site_type": f"untagged open land ({dominant})",
            "site_kind": "open_land",
            "building_type": None,
            "landuse": None,
            "landcover": dominant,
            "name": None,
            "discovered": {
                "source": SOURCE_ID,
                "land_cover_year": info["land_cover_year"],
                "classes": shares,
                "checked_on": checked_on,
                "note": (
                    "Found in the land-cover map, not tagged on OpenStreetMap"
                    + (f"; not built-up-looking in Sentinel-2 on {checked_on}" if checked_on else "")
                    + (". Bare ground cannot be told apart from built-up at 10 m."
                       if dominant == "bare ground" else ".")
                ),
            },
            # Evidence sources for this candidate's size and land cover.
            "_sources": {"land_parcels": {"source_id": SOURCE_ID}},
            # Weaker evidence than a mapped, tagged parcel.
            "_max_confidence": "medium",
            "_shape": metric,
        })

    return candidates, info
