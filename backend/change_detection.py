"""
Change detection from Sentinel-2 imagery.

    detect_changes(geometry, before_date, after_date, change_types)

compares two clear Sentinel-2 L2A scenes of an area and returns the
patches whose spectral indices changed past fixed thresholds:

    vegetation_loss / vegetation_gain   NDVI
    built_or_bare_increase              NDBI (with low NDVI after)
    water_gain / water_loss             NDWI

Every observation keeps both scene ids and dates, the area clear of
cloud in each, the before / after index values and the method, so a
report can say exactly what was compared. Nothing is estimated: no
clear scene means ChangeDataUnavailable, never a guess.

Scenes are chosen by the share of the searched area that is clear of
cloud in the scene's own classification layer (SCL), not by the
scene-wide cloud percentage, which can hide local clouds.

collect_changes() adapts the result to the analysis pipeline: each
patch becomes a ranked candidate.
"""

import math
import time

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np

from shapely.geometry import mapping, shape as to_shape
from shapely.ops import transform as reproject


# ============================================================
# SETTINGS
# ============================================================

CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

COLLECTION = "sentinel-2-l2a"

SOURCE_ID = "sentinel_2_planetary_computer"

PIXEL_M = 10

PIXEL_AREA_M2 = PIXEL_M * PIXEL_M

BANDS = ("B03", "B04", "B08", "B11")

# Scene classification (SCL) classes treated as clear ground:
# 4 vegetation, 5 not vegetated, 6 water, 11 snow / ice. No data,
# saturated, dark, cloud shadow, unclassified, cloud and cirrus
# pixels (0-3, 7-10) are excluded.
CLEAR_SCL = (4, 5, 6, 11)

# Scenes processed with baseline 04.00 or later (from January 2022)
# store reflectance with an added offset of 1000.
OFFSET_BASELINE = "04.00"

BOA_OFFSET = 1000

# Scene-wide cloud cover, used only to skip hopeless scenes before
# the area itself is checked.
MAX_SCENE_CLOUD = 60

# The later scene is the most recent clear one this far back.
AFTER_LOOKBACK_DAYS = 90

# The earlier scene is the clear one closest to its target date
# within this many days, so both scenes are from a similar season.
SEASON_WINDOW_DAYS = 45

# Share of the searched area that must be clear of cloud.
MIN_CLEAR_FRACTION = 0.6

MAX_SCENES_CHECKED = 6

# Smallest patch reported (5 pixels = 500 m²); smaller ones are
# mostly noise at 10 m.
MIN_PATCH_PIXELS = 5

# Largest grid read for an area (6 x 6 km), and for the bounding box
# of a shortlist of parcels, which can be spread along a road.
MAX_GRID_PIXELS = 600 * 600

MAX_PARCEL_GRID_PIXELS = 1000 * 1000

# Parcels with fewer pixels than this are too small to measure.
MIN_PARCEL_PIXELS = 3

# A change covering at least this share of a parcel is flagged.
PARCEL_ALERT_SHARE = 0.2

TIME_BUDGET_SECONDS = 75


# Per-pixel rules. Each pixel gets at most one change type, in this
# order of precedence.
CHANGE_TYPES = (
    "water_gain",
    "water_loss",
    "built_or_bare_increase",
    "vegetation_loss",
    "vegetation_gain",
)

# min_change is the smallest index change a rule accepts and
# full_scale a very strong change; criteria.change_magnitude scores
# between the two.
RULES = {
    # Water must clearly cross zero, not hover around it.
    "water_gain": {"index": "NDWI", "before_max": 0.0, "after_min": 0.15, "delta_min": 0.2,
                   "min_change": 0.2, "full_scale": 1.0},
    "water_loss": {"index": "NDWI", "before_min": 0.15, "after_max": 0.0, "delta_max": -0.2,
                   "min_change": 0.2, "full_scale": 1.0},
    "built_or_bare_increase": {"index": "NDBI", "delta_min": 0.10, "after_ndvi_max": 0.25,
                               "min_change": 0.10, "full_scale": 0.5},
    "vegetation_loss": {"index": "NDVI", "before_min": 0.4, "delta_max": -0.25,
                        "min_change": 0.25, "full_scale": 0.8},
    "vegetation_gain": {"index": "NDVI", "after_min": 0.4, "delta_min": 0.25,
                        "min_change": 0.25, "full_scale": 0.8},
}

LABELS = {
    "water_gain": "Water appeared",
    "water_loss": "Water receded",
    "built_or_bare_increase": "Built-up or bare surface increased",
    "vegetation_loss": "Vegetation loss",
    "vegetation_gain": "Vegetation gain",
}

INTERPRETATIONS = {
    "water_gain": (
        "Open water where there was none: consistent with flooding, a new "
        "water body or seasonal water."
    ),
    "water_loss": (
        "Open water no longer visible: consistent with drying, filling or "
        "reclamation."
    ),
    "built_or_bare_increase": (
        "The surface became more built-up or bare: consistent with "
        "construction or land clearing. 10 m imagery cannot tell these apart."
    ),
    "vegetation_loss": (
        "Vegetation cover decreased: consistent with clearing, construction, "
        "harvest or drought."
    ),
    "vegetation_gain": (
        "Vegetation cover increased: consistent with planting, crop growth "
        "or regrowth."
    ),
}

METHOD = (
    "Sentinel-2 L2A surface reflectance at 10 m (20 m bands resampled), "
    "cloud-masked with the scene classification layer. NDVI = (B08-B04)/(B08+B04), "
    "NDBI = (B11-B08)/(B11+B08), NDWI = (B03-B08)/(B03+B08). Vegetation change: "
    "NDVI from >= 0.4 falling by >= 0.25, or rising by >= 0.25 to >= 0.4. "
    "Built-up or bare increase: NDBI rising by >= 0.10 with NDVI < 0.25 after. "
    "Water: NDWI from <= 0 to >= 0.15 (or the reverse) and changing by >= 0.2. "
    "Patches of at least 5 connected pixels (500 m²)."
)


class ChangeDataUnavailable(Exception):
    """
    No usable imagery for the requested period: reason says why.
    """

    def __init__(self, reason, tried=None):
        super().__init__(reason)
        self.reason = reason
        self.tried = tried or {}


# ============================================================
# PIXEL MATHS (pure; tested with fixed arrays)
# ============================================================

def reflectance(dn, processing_baseline):
    """
    Surface reflectance (0-1, float32) from L2A digital numbers.
    No-data pixels (0) become NaN.
    """

    dn = np.asarray(dn)

    offset = BOA_OFFSET if str(processing_baseline or "0") >= OFFSET_BASELINE else 0

    out = np.maximum((dn.astype("float32") - offset) / 10000.0, 0)

    out[dn == 0] = np.nan

    return out


def _normalized_difference(a, b):

    with np.errstate(divide="ignore", invalid="ignore"):
        total = a + b
        result = (a - b) / total

    result[~np.isfinite(result) | (total <= 0)] = np.nan

    return result


def spectral_indices(bands):
    """
    {"NDVI", "NDBI", "NDWI"} from reflectance bands B03, B04, B08, B11.
    """

    return {
        "NDVI": _normalized_difference(bands["B08"], bands["B04"]),
        "NDBI": _normalized_difference(bands["B11"], bands["B08"]),
        "NDWI": _normalized_difference(bands["B03"], bands["B08"]),
    }


def clear_mask(scl):

    return np.isin(np.asarray(scl), CLEAR_SCL)


def classify(before, after, valid, change_types=CHANGE_TYPES):
    """
    Label image: 0 = no change, k = CHANGE_TYPES[k - 1].
    before / after are spectral_indices(); valid marks pixels clear
    in both scenes and inside the searched area.
    """

    labels = np.zeros(valid.shape, dtype="uint8")

    finite = valid & np.isfinite(before["NDVI"]) & np.isfinite(after["NDVI"])

    for k, change_type in enumerate(CHANGE_TYPES, start=1):

        if change_type not in change_types:
            continue

        rule = RULES[change_type]

        b = before[rule["index"]]

        a = after[rule["index"]]

        with np.errstate(invalid="ignore"):

            hit = finite & np.isfinite(a) & np.isfinite(b)

            if "before_min" in rule:
                hit &= b >= rule["before_min"]
            if "before_max" in rule:
                hit &= b <= rule["before_max"]
            if "after_min" in rule:
                hit &= a >= rule["after_min"]
            if "after_max" in rule:
                hit &= a <= rule["after_max"]
            if "delta_min" in rule:
                hit &= (a - b) >= rule["delta_min"]
            if "delta_max" in rule:
                hit &= (a - b) <= rule["delta_max"]
            if "after_ndvi_max" in rule:
                hit &= after["NDVI"] < rule["after_ndvi_max"]

        # Earlier types take precedence.
        labels[hit & (labels == 0)] = k

    return labels


def patches(labels, transform, before, after, min_pixels=MIN_PATCH_PIXELS):
    """
    Connected patches of one change type (8-connected), at least
    min_pixels each, with their index statistics:

        [{"change_type", "geometry" (GeoJSON in the grid CRS),
          "pixels", "index", "before_mean", "after_mean", "delta_mean"}]
    """

    from rasterio import features

    found = []

    for geometry, value in features.shapes(
        labels.astype("int16"), mask=labels > 0, connectivity=8, transform=transform,
    ):

        pixels = int(round(to_shape(geometry).area / (abs(transform.a) * abs(transform.e))))

        if pixels < min_pixels:
            continue

        found.append({"change_type": CHANGE_TYPES[int(value) - 1], "geometry": geometry, "pixels": pixels})

    if not found:
        return []

    ids = features.rasterize(
        ((p["geometry"], i) for i, p in enumerate(found, start=1)),
        out_shape=labels.shape, transform=transform, fill=0, dtype="int32",
    )

    for i, patch in enumerate(found, start=1):

        index = RULES[patch["change_type"]]["index"]

        inside = (ids == i) & (labels > 0)

        b = float(np.nanmean(before[index][inside]))

        a = float(np.nanmean(after[index][inside]))

        patch.update({
            "index": index,
            "before_mean": round(b, 3),
            "after_mean": round(a, 3),
            "delta_mean": round(a - b, 3),
        })

    return found


def season_gap_days(a, b):
    """
    Days between two dates' positions in the year (0-182).
    """

    gap = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)

    return min(gap, 365 - gap)


# ============================================================
# GRID
# ============================================================

@dataclass
class Grid:

    crs: str

    transform: object

    width: int

    height: int

    # Pixels inside the searched area.
    inside: object

    bbox_lonlat: tuple


def make_grid(geometry_lonlat, max_pixels=MAX_GRID_PIXELS):
    """
    A 10 m UTM grid covering the area, and the mask of pixels inside it.
    """

    from rasterio import features
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds, transform_geom

    west, south, east, north = geometry_lonlat.bounds

    lon = (west + east) / 2

    lat = (south + north) / 2

    zone = int((lon + 180) // 6) + 1

    crs = f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"

    minx, miny, maxx, maxy = transform_bounds("EPSG:4326", crs, west, south, east, north)

    minx = math.floor(minx / PIXEL_M) * PIXEL_M
    miny = math.floor(miny / PIXEL_M) * PIXEL_M
    maxx = math.ceil(maxx / PIXEL_M) * PIXEL_M
    maxy = math.ceil(maxy / PIXEL_M) * PIXEL_M

    width = int((maxx - minx) / PIXEL_M)

    height = int((maxy - miny) / PIXEL_M)

    if width * height > max_pixels:
        raise ValueError(f"Area too large for change detection ({width} x {height} pixels)")

    transform = from_origin(minx, maxy, PIXEL_M, PIXEL_M)

    inside = features.rasterize(
        [transform_geom("EPSG:4326", crs, mapping(geometry_lonlat))],
        out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
    ).astype(bool)

    return Grid(crs, transform, width, height, inside, (west, south, east, north))


# ============================================================
# PROVIDER (Microsoft Planetary Computer)
# ============================================================

def _catalog():

    import planetary_computer
    import pystac_client

    return pystac_client.Client.open(CATALOG_URL, modifier=planetary_computer.sign_inplace)


def search_scenes(bbox_lonlat, start, end):
    """
    Sentinel-2 L2A items overlapping the area between two dates,
    skipping scenes that are mostly cloud.
    """

    search = _catalog().search(
        collections=[COLLECTION],
        bbox=list(bbox_lonlat),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}},
        max_items=60,
    )

    return list(search.items())


def read_band(href, grid):
    """
    One band resampled onto the grid (nearest neighbour).
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
            width=grid.width, height=grid.height, resampling=Resampling.nearest,
        ) as vrt:
            return vrt.read(1)


@dataclass
class Scene:

    id: str

    acquired: datetime

    cloud_cover: float

    processing_baseline: str

    clear_fraction: float

    clear: object

    item: object

    def summary(self):

        return {
            "id": self.id,
            "date": self.acquired.date().isoformat(),
            "acquired_at": self.acquired.isoformat(),
            "scene_cloud_cover": round(self.cloud_cover, 1),
            "clear_fraction": round(self.clear_fraction, 3),
            "processing_baseline": self.processing_baseline,
        }


def _scene_from(item, grid, reader):

    scl = reader(item.assets["SCL"].href, grid)

    clear = clear_mask(scl) & grid.inside

    inside = int(grid.inside.sum())

    return Scene(
        id=item.id,
        acquired=item.datetime,
        cloud_cover=float(item.properties.get("eo:cloud_cover") or 0),
        processing_baseline=str(item.properties.get("s2:processing_baseline") or ""),
        clear_fraction=(int(clear.sum()) / inside) if inside else 0.0,
        clear=clear,
        item=item,
    )


def pick_scene(items, grid, prefer, reader, deadline=None):
    """
    The first item (in `prefer` order) with at least
    MIN_CLEAR_FRACTION of the area clear, checking at most
    MAX_SCENES_CHECKED distinct dates. Returns (scene | None, checked).
    """

    seen = set()

    checked = []

    for item in sorted(items, key=prefer):

        day = item.datetime.date()

        if day in seen:
            continue

        seen.add(day)

        if len(checked) >= MAX_SCENES_CHECKED:
            break

        if deadline and time.monotonic() > deadline:
            break

        scene = _scene_from(item, grid, reader)

        checked.append({"id": scene.id, "date": day.isoformat(),
                        "clear_fraction": round(scene.clear_fraction, 3)})

        if scene.clear_fraction >= MIN_CLEAR_FRACTION:
            return scene, checked

    return None, checked


def _read_scene_bands(scene, grid, reader):

    with ThreadPoolExecutor(max_workers=len(BANDS)) as pool:
        raw = dict(zip(BANDS, pool.map(lambda b: reader(scene.item.assets[b].href, grid), BANDS)))

    return {
        band: reflectance(values, scene.processing_baseline)
        for band, values in raw.items()
    }


# ============================================================
# DETECT
# ============================================================

@dataclass
class ChangeResult:

    observations: list

    before: dict

    after: dict

    season_gap_days: int

    # Share of the area clear in both scenes.
    clear_fraction_both: float

    area_km2: float

    method: str = METHOD


def _to_lonlat(geometry, crs):

    from rasterio.warp import transform_geom

    return transform_geom(crs, "EPSG:4326", geometry, precision=6)


@dataclass
class Comparison:

    before: Scene

    after: Scene

    before_idx: dict

    after_idx: dict

    # Pixels clear in both scenes and inside the grid's area.
    valid: object

    labels: object


def compare(grid, before_date=None, after_date=None, change_types=CHANGE_TYPES,
            today=None, search=None, reader=None):
    """
    Choose the two scenes for the grid and classify every pixel.

    after_date: latest date for the later scene (default today); the
    most recent clear scene in the AFTER_LOOKBACK_DAYS before it is used.
    before_date: target date for the earlier scene (default one year
    before the later scene); the clear scene closest to it within
    SEASON_WINDOW_DAYS is used.

    Raises ChangeDataUnavailable when no clear scene exists.
    """

    search = search or search_scenes

    reader = reader or read_band

    deadline = time.monotonic() + TIME_BUDGET_SECONDS

    today = today or date.today()

    after_date = min(after_date or today, today)

    # Later scene: most recent clear one.
    after_items = search(grid.bbox_lonlat, after_date - timedelta(days=AFTER_LOOKBACK_DAYS), after_date)

    after, after_checked = pick_scene(
        after_items, grid, prefer=lambda i: -i.datetime.timestamp(), reader=reader, deadline=deadline,
    )

    if after is None:
        raise ChangeDataUnavailable(
            f"No Sentinel-2 scene between {after_date - timedelta(days=AFTER_LOOKBACK_DAYS)} "
            f"and {after_date} had at least {round(MIN_CLEAR_FRACTION * 100)}% of the area "
            f"clear of cloud.",
            {"after": after_checked},
        )

    # Earlier scene: closest clear one to the target date.
    target = before_date or (after.acquired.date() - timedelta(days=365))

    start = target - timedelta(days=SEASON_WINDOW_DAYS)

    end = min(target + timedelta(days=SEASON_WINDOW_DAYS), after.acquired.date() - timedelta(days=30))

    if end <= start:
        raise ChangeDataUnavailable(
            "The earlier date is too close to the latest clear scene to compare.",
            {"after": after_checked},
        )

    before_items = search(grid.bbox_lonlat, start, end)

    before, before_checked = pick_scene(
        before_items, grid,
        prefer=lambda i: abs((i.datetime.date() - target).days), reader=reader, deadline=deadline,
    )

    if before is None:
        raise ChangeDataUnavailable(
            f"No Sentinel-2 scene between {start} and {end} had at least "
            f"{round(MIN_CLEAR_FRACTION * 100)}% of the area clear of cloud.",
            {"after": after_checked, "before": before_checked},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        before_bands, after_bands = pool.map(lambda s: _read_scene_bands(s, grid, reader), (before, after))

    before_idx = spectral_indices(before_bands)

    after_idx = spectral_indices(after_bands)

    valid = before.clear & after.clear

    return Comparison(
        before, after, before_idx, after_idx, valid,
        classify(before_idx, after_idx, valid, change_types),
    )


def detect_changes(geometry, before_date=None, after_date=None,
                   change_types=CHANGE_TYPES, today=None,
                   search=None, reader=None):
    """
    ChangeResult for a shapely lon/lat geometry: every changed patch
    in the area. See compare() for how the dates are used.
    """

    grid = make_grid(geometry)

    c = compare(grid, before_date, after_date, change_types, today, search, reader)

    found = patches(c.labels, grid.transform, c.before_idx, c.after_idx)

    inside = int(grid.inside.sum())

    observations = []

    for patch in found:

        geometry_ll = _to_lonlat(patch["geometry"], grid.crs)

        centroid = to_shape(geometry_ll).centroid

        observations.append({
            "change_type": patch["change_type"],
            "label": LABELS[patch["change_type"]],
            "interpretation": INTERPRETATIONS[patch["change_type"]],
            "geometry": geometry_ll,
            "latitude": round(centroid.y, 6),
            "longitude": round(centroid.x, 6),
            "pixels": patch["pixels"],
            "area_m2": patch["pixels"] * PIXEL_AREA_M2,
            "index": patch["index"],
            "before_mean": patch["before_mean"],
            "after_mean": patch["after_mean"],
            "delta_mean": patch["delta_mean"],
            "min_change": RULES[patch["change_type"]]["min_change"],
            "full_scale": RULES[patch["change_type"]]["full_scale"],
        })

    return ChangeResult(
        observations=observations,
        before=c.before.summary(),
        after=c.after.summary(),
        season_gap_days=season_gap_days(c.before.acquired.date(), c.after.acquired.date()),
        clear_fraction_both=round(int(c.valid.sum()) / inside, 3) if inside else 0.0,
        area_km2=round(inside * PIXEL_AREA_M2 / 1e6, 2),
    )


def measure_parcels(geometries, before_date=None, after_date=None, today=None,
                    search=None, reader=None):
    """
    Share of each parcel's pixels that changed, by change type, for a
    list of shapely lon/lat geometries (one imagery read for all).

    Returns (parcels, scenes): parcels in input order, each either

        {"measurable": True, "pixels", "clear_pixels",
         "shares": {change_type: fraction}, "changed_share"}

    or {"measurable": False, "reason"}; scenes describes the two
    images compared. Raises ChangeDataUnavailable without clear imagery.
    """

    from rasterio import features
    from rasterio.warp import transform_geom
    from shapely.ops import unary_union

    grid = make_grid(unary_union(geometries), max_pixels=MAX_PARCEL_GRID_PIXELS)

    c = compare(grid, before_date, after_date, CHANGE_TYPES, today, search, reader)

    parcels = []

    for geometry in geometries:

        inside = features.rasterize(
            [transform_geom("EPSG:4326", grid.crs, mapping(geometry))],
            out_shape=(grid.height, grid.width), transform=grid.transform,
            fill=0, dtype="uint8",
        ).astype(bool)

        pixels = int(inside.sum())

        clear = inside & c.valid

        clear_pixels = int(clear.sum())

        if pixels < MIN_PARCEL_PIXELS:
            plural = "" if pixels == 1 else "s"
            parcels.append({
                "measurable": False,
                "reason": (
                    f"Too small to measure at 10 m ({pixels} pixel{plural}; "
                    f"at least {MIN_PARCEL_PIXELS} needed)."
                ),
            })
            continue

        if clear_pixels < max(MIN_PARCEL_PIXELS, MIN_CLEAR_FRACTION * pixels):
            parcels.append({
                "measurable": False,
                "reason": "Clouds covered too much of the parcel in one of the images.",
            })
            continue

        shares = {
            change_type: round(int((clear & (c.labels == k)).sum()) / clear_pixels, 3)
            for k, change_type in enumerate(CHANGE_TYPES, start=1)
        }

        parcels.append({
            "measurable": True,
            "pixels": pixels,
            "clear_pixels": clear_pixels,
            "shares": shares,
            "changed_share": round(int((clear & (c.labels > 0)).sum()) / clear_pixels, 3),
        })

    scenes = {
        "before": c.before.summary(),
        "after": c.after.summary(),
        "season_gap_days": season_gap_days(c.before.acquired.date(), c.after.acquired.date()),
        "method": METHOD,
        "source": SOURCE_ID,
        "resolution_m": PIXEL_M,
    }

    return parcels, scenes


# ============================================================
# PIPELINE ADAPTER
# ============================================================

def collect_changes(area, spec, location_name=None, min_area_m2=0, detect=None):
    """
    (candidates, context) for the analysis pipeline: one candidate
    per change patch. area is a search_area.SearchArea.
    """

    from geodata import new_context

    detect = detect or detect_changes

    time_range = spec.time_range

    result = detect(
        to_shape(area.geometry),
        before_date=date.fromisoformat(time_range.start) if time_range.start else None,
        after_date=date.fromisoformat(time_range.end) if time_range.end else None,
    )

    context = new_context(area.latitude, area.longitude, area.reach_km, location_name)

    context.layer_status["historical_imagery"] = "loaded"

    context.layer_provenance["historical_imagery"] = {
        "observed_at": result.after["acquired_at"],
        "data_as_of": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    context.meta["change_detection"] = {
        "before": result.before,
        "after": result.after,
        "season_gap_days": result.season_gap_days,
        "clear_fraction_both": result.clear_fraction_both,
        "area_km2": result.area_km2,
        "method": result.method,
        "source": SOURCE_ID,
        "resolution_m": PIXEL_M,
    }

    proj = context.proj

    candidates = []

    for i, obs in enumerate(result.observations, start=1):

        if obs["area_m2"] < min_area_m2:
            continue

        lonlat = to_shape(obs["geometry"])

        metric = reproject(lambda x, y, z=None: proj.to_xy(x, y), lonlat)

        candidates.append({
            "candidate_id": f"change-{i}",
            "latitude": obs["latitude"],
            "longitude": obs["longitude"],
            "area_m2": obs["area_m2"],
            "site_type": obs["label"],
            "site_kind": "change",
            "change_type": obs["change_type"],
            "interpretation": obs["interpretation"],
            "name": None,
            "change": {
                "index": obs["index"],
                "before_mean": obs["before_mean"],
                "after_mean": obs["after_mean"],
                "delta_mean": obs["delta_mean"],
                "min_change": obs["min_change"],
                "full_scale": obs["full_scale"],
                "pixels": obs["pixels"],
                "before": result.before,
                "after": result.after,
                "season_gap_days": result.season_gap_days,
            },
            "_shape": metric,
        })

    candidates.sort(key=lambda c: c["area_m2"], reverse=True)

    return candidates, context
