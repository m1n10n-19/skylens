"""
Change detection from satellite imagery (Sentinel-2 and Landsat).

    detect_changes(geometry, before_date, after_date, change_types)

compares two clear images of an area and returns the patches whose
spectral indices changed past fixed thresholds:

    vegetation_loss / vegetation_gain   NDVI
    built_or_bare_increase              NDBI (with low NDVI after)
    water_gain / water_loss             NDWI

Sensors: Sentinel-2 (10 m, 2017 onwards) by default. When the earlier
date is before 2017, both images come from Landsat (30 m, 1984
onwards): one sensor per comparison, so sensor differences are never
mistaken for change. Landsat 7 after May 2003 has permanent data gaps
and is used only when no other Landsat image is clear.

Every observation keeps both images' ids, satellites and dates, the
area clear of cloud in each, the before / after index values and the
method, so a report can say exactly what was compared. Nothing is
estimated: no clear image means ChangeDataUnavailable, never a guess.

Images are chosen by the share of the searched area that is clear of
cloud in their own per-pixel quality layer, not by the scene-wide
cloud percentage, which can hide local clouds. All tiles taken on the
same day are combined, so an area on a tile edge is fully covered.

collect_changes() adapts the result to the analysis pipeline: each
patch becomes a ranked candidate.
"""

import math
import time

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import numpy as np

from shapely.geometry import mapping, shape as to_shape
from shapely.ops import transform as reproject


# ============================================================
# SETTINGS
# ============================================================

CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Scene-wide cloud cover, used only to skip hopeless images before
# the area itself is checked.
MAX_SCENE_CLOUD = 60

# The later image is the most recent clear one this far back.
AFTER_LOOKBACK_DAYS = 90

# The earlier image is the clear one closest to its target date
# within this many days, so both images are from a similar season.
SEASON_WINDOW_DAYS = 45

# Share of the searched area that must be clear of cloud.
MIN_CLEAR_FRACTION = 0.6

# Distinct acquisition dates checked per image (per pass).
MAX_SCENES_CHECKED = 6

# Smallest patch reported, in pixels (500 m² at 10 m, 4,500 m² at
# 30 m); smaller ones are mostly noise.
MIN_PATCH_PIXELS = 5

# Largest grid read for an area (6 x 6 km at 10 m), and for the
# bounding box of a shortlist of parcels, which can be spread along
# a road.
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
        "construction or land clearing. Satellite imagery at this "
        "resolution cannot tell these apart."
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

_RULES_TEXT = (
    "NDVI = (NIR-Red)/(NIR+Red), NDBI = (SWIR1-NIR)/(SWIR1+NIR), "
    "NDWI = (Green-NIR)/(Green+NIR). Vegetation change: NDVI from >= 0.4 "
    "falling by >= 0.25, or rising by >= 0.25 to >= 0.4. Built-up or bare "
    "increase: NDBI rising by >= 0.10 with NDVI < 0.25 after. Water: NDWI "
    "from <= 0 to >= 0.15 (or the reverse) and changing by >= 0.2."
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

# Sentinel-2 scenes processed with baseline 04.00 or later (from
# January 2022) store reflectance with an added offset of 1000.
OFFSET_BASELINE = "04.00"

BOA_OFFSET = 1000

# Sentinel-2 scene classification (SCL) classes treated as clear
# ground: 4 vegetation, 5 not vegetated, 6 water, 11 snow / ice. No
# data, saturated, dark, cloud shadow, unclassified, cloud and cirrus
# pixels (0-3, 7-10) are excluded.
CLEAR_SCL = (4, 5, 6, 11)

# Landsat Collection 2 QA_PIXEL bits.
QA_FILL, QA_DILATED_CLOUD, QA_CIRRUS, QA_CLOUD, QA_SHADOW = 0, 1, 2, 3, 4
QA_CLEAR, QA_WATER = 6, 7

LANDSAT_SCALE = 0.0000275

LANDSAT_OFFSET = -0.2


def reflectance(dn, processing_baseline):
    """
    Sentinel-2 surface reflectance (0-1, float32) from L2A digital
    numbers. No-data pixels (0) become NaN.
    """

    dn = np.asarray(dn)

    offset = BOA_OFFSET if str(processing_baseline or "0") >= OFFSET_BASELINE else 0

    out = np.maximum((dn.astype("float32") - offset) / 10000.0, 0)

    out[dn == 0] = np.nan

    return out


def landsat_reflectance(dn):
    """
    Landsat Collection 2 Level-2 surface reflectance (0-1, float32).
    No-data pixels (0) become NaN.
    """

    dn = np.asarray(dn)

    out = np.maximum(dn.astype("float32") * LANDSAT_SCALE + LANDSAT_OFFSET, 0)

    out[dn == 0] = np.nan

    return out


def clear_mask(scl):
    """
    Sentinel-2: pixels classed as clear ground.
    """

    return np.isin(np.asarray(scl), CLEAR_SCL)


def _bit(qa, bit):

    return (np.asarray(qa).astype("uint16") >> bit) & 1 == 1


def landsat_clear_mask(qa):
    """
    Landsat: pixels flagged clear or water, and not fill, cloud,
    dilated cloud, cirrus or cloud shadow.
    """

    bad = _bit(qa, QA_FILL) | _bit(qa, QA_DILATED_CLOUD) | _bit(qa, QA_CIRRUS) \
        | _bit(qa, QA_CLOUD) | _bit(qa, QA_SHADOW)

    return (_bit(qa, QA_CLEAR) | _bit(qa, QA_WATER)) & ~bad


def _normalized_difference(a, b):

    with np.errstate(divide="ignore", invalid="ignore"):
        total = a + b
        result = (a - b) / total

    result[~np.isfinite(result) | (total <= 0)] = np.nan

    return result


def spectral_indices(bands):
    """
    {"NDVI", "NDBI", "NDWI"} from reflectance bands keyed by role:
    green, red, nir, swir.
    """

    return {
        "NDVI": _normalized_difference(bands["nir"], bands["red"]),
        "NDBI": _normalized_difference(bands["swir"], bands["nir"]),
        "NDWI": _normalized_difference(bands["green"], bands["nir"]),
    }


def classify(before, after, valid, change_types=CHANGE_TYPES):
    """
    Label image: 0 = no change, k = CHANGE_TYPES[k - 1].
    before / after are spectral_indices(); valid marks pixels clear
    in both images and inside the searched area.
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
# SENSORS
# ============================================================

@dataclass(frozen=True)
class Sensor:

    id: str

    name: str

    collection: str

    # data_registry source id.
    source_id: str

    pixel_m: int

    # band role (green, red, nir, swir) -> STAC asset key
    bands: dict

    # per-pixel quality asset
    mask_asset: str

    # mask values -> pixels with any data
    has_data: Callable

    # mask values -> pixels clear of cloud
    clear: Callable

    # (digital numbers, STAC item) -> reflectance
    to_reflectance: Callable

    # STAC item -> True if used only when nothing else is clear
    last_resort: Callable

    mask_description: str

    def method(self):

        return (
            f"{self.name} surface reflectance at {self.pixel_m} m, cloud-masked "
            f"with {self.mask_description}. {_RULES_TEXT} Patches of at least "
            f"{MIN_PATCH_PIXELS} connected pixels "
            f"({MIN_PATCH_PIXELS * self.pixel_m ** 2:,} m²)."
        )


SENTINEL_2 = Sensor(
    id="sentinel-2",
    name="Sentinel-2",
    collection="sentinel-2-l2a",
    source_id="sentinel_2_planetary_computer",
    pixel_m=10,
    bands={"green": "B03", "red": "B04", "nir": "B08", "swir": "B11"},
    mask_asset="SCL",
    has_data=lambda scl: np.asarray(scl) != 0,
    clear=clear_mask,
    to_reflectance=lambda dn, item: reflectance(dn, item.properties.get("s2:processing_baseline")),
    last_resort=lambda item: False,
    mask_description="the scene classification layer (20 m bands resampled to 10 m)",
)


# Landsat 7's scan-line corrector failed on 31 May 2003; later images
# have permanent diagonal gaps.
LANDSAT_7_SLC_OFF = date(2003, 5, 31)


def _landsat_7_slc_off(item):

    return (
        item.properties.get("platform") == "landsat-7"
        and item.datetime.date() > LANDSAT_7_SLC_OFF
    )


LANDSAT = Sensor(
    id="landsat",
    name="Landsat",
    collection="landsat-c2-l2",
    source_id="landsat_c2_l2_planetary_computer",
    pixel_m=30,
    bands={"green": "green", "red": "red", "nir": "nir08", "swir": "swir16"},
    mask_asset="qa_pixel",
    has_data=lambda qa: ~_bit(qa, QA_FILL) & (np.asarray(qa) != 0),
    clear=landsat_clear_mask,
    to_reflectance=lambda dn, item: landsat_reflectance(dn),
    last_resort=_landsat_7_slc_off,
    mask_description="the QA_PIXEL cloud, cirrus and cloud-shadow flags",
)

SENSORS = {sensor.id: sensor for sensor in (SENTINEL_2, LANDSAT)}

# Sentinel-2 Level-2A is used from this date; earlier periods use
# Landsat for both images.
SENTINEL_2_FROM = date(2017, 1, 1)


def choose_sensor(before_target):
    """
    Sentinel-2 unless the earlier image must come from before 2017.
    """

    if before_target - timedelta(days=SEASON_WINDOW_DAYS) < SENTINEL_2_FROM:
        return LANDSAT

    return SENTINEL_2


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

    pixel_m: int = 10


def make_grid(geometry_lonlat, pixel_m=10, max_pixels=MAX_GRID_PIXELS):
    """
    A UTM grid with pixel_m pixels covering the area, and the mask of
    pixels inside it.
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

    minx = math.floor(minx / pixel_m) * pixel_m
    miny = math.floor(miny / pixel_m) * pixel_m
    maxx = math.ceil(maxx / pixel_m) * pixel_m
    maxy = math.ceil(maxy / pixel_m) * pixel_m

    width = int((maxx - minx) / pixel_m)

    height = int((maxy - miny) / pixel_m)

    if width * height > max_pixels:
        raise ValueError(f"Area too large for change detection ({width} x {height} pixels)")

    transform = from_origin(minx, maxy, pixel_m, pixel_m)

    inside = features.rasterize(
        [transform_geom("EPSG:4326", crs, mapping(geometry_lonlat))],
        out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
    ).astype(bool)

    return Grid(crs, transform, width, height, inside, (west, south, east, north), pixel_m)


# ============================================================
# PROVIDER (Microsoft Planetary Computer)
# ============================================================

def _catalog():

    import planetary_computer
    import pystac_client

    return pystac_client.Client.open(CATALOG_URL, modifier=planetary_computer.sign_inplace)


def search_scenes(bbox_lonlat, start, end, collection=SENTINEL_2.collection):
    """
    Items of a collection overlapping the area between two dates,
    skipping images that are mostly cloud.
    """

    search = _catalog().search(
        collections=[collection],
        bbox=list(bbox_lonlat),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}},
        max_items=100,
    )

    return list(search.items())


def read_band(href, grid):
    """
    One band resampled onto the grid (nearest neighbour). Outside the
    image's footprint the band's no-data value is returned.
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
    """
    One acquisition date, combined from every tile that covers the
    area. source[y, x] is the index of the item each pixel comes from
    (-1 where no tile has data).
    """

    sensor: Sensor

    items: list

    acquired: datetime

    clear_fraction: float

    clear: object

    source: object

    def summary(self):

        first = self.items[0]

        return {
            "id": "+".join(item.id for item in self.items),
            "items": [item.id for item in self.items],
            "date": self.acquired.date().isoformat(),
            "acquired_at": self.acquired.isoformat(),
            "sensor": self.sensor.name,
            "platform": first.properties.get("platform"),
            "resolution_m": self.sensor.pixel_m,
            "scene_cloud_cover": round(max(
                float(item.properties.get("eo:cloud_cover") or 0) for item in self.items
            ), 1),
            "clear_fraction": round(self.clear_fraction, 3),
            "processing_baseline": first.properties.get("s2:processing_baseline"),
            "last_resort": self.sensor.last_resort(first),
        }


def _scene_from(items, sensor, grid, reader):
    """
    Combine the quality masks of a date's tiles: each pixel comes from
    the first tile that has data there.
    """

    source = np.full((grid.height, grid.width), -1, dtype="int16")

    clear = np.zeros((grid.height, grid.width), dtype=bool)

    for i, item in enumerate(items):

        mask = reader(item.assets[sensor.mask_asset].href, grid)

        take = sensor.has_data(mask) & (source == -1)

        source[take] = i

        clear |= take & sensor.clear(mask)

    clear &= grid.inside

    inside = int(grid.inside.sum())

    return Scene(
        sensor=sensor,
        items=items,
        acquired=items[0].datetime,
        clear_fraction=(int(clear.sum()) / inside) if inside else 0.0,
        clear=clear,
        source=source,
    )


def _date_groups(items, sensor):
    """
    Items grouped by acquisition date and satellite: one group per
    pass, combined into one Scene.
    """

    groups = {}

    for item in items:
        key = (item.datetime.date(), item.properties.get("platform"))
        groups.setdefault(key, []).append(item)

    return list(groups.values())


def pick_scene(items, grid, prefer, reader, sensor=SENTINEL_2, deadline=None):
    """
    The first date (in `prefer` order of its first item) with at least
    MIN_CLEAR_FRACTION of the area clear. Last-resort images (Landsat 7
    after 2003) are only tried when no other date qualifies. Returns
    (scene | None, checked).
    """

    groups = sorted(_date_groups(items, sensor), key=lambda g: prefer(g[0]))

    checked = []

    for last_resort in (False, True):

        tried = 0

        for group in groups:

            if sensor.last_resort(group[0]) != last_resort:
                continue

            if tried >= MAX_SCENES_CHECKED:
                break

            if deadline and time.monotonic() > deadline:
                break

            tried += 1

            scene = _scene_from(group, sensor, grid, reader)

            checked.append({
                "id": scene.items[0].id,
                "date": scene.acquired.date().isoformat(),
                "tiles": len(group),
                "clear_fraction": round(scene.clear_fraction, 3),
            })

            if scene.clear_fraction >= MIN_CLEAR_FRACTION:
                return scene, checked

    return None, checked


def _read_scene_bands(scene, grid, reader):
    """
    Reflectance per band role, each pixel from its source tile.
    """

    sensor = scene.sensor

    used = [i for i in range(len(scene.items)) if (scene.source == i).any()]

    jobs = [(role, i) for role in sensor.bands for i in used]

    def read(job):
        role, i = job
        item = scene.items[i]
        return sensor.to_reflectance(reader(item.assets[sensor.bands[role]].href, grid), item)

    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        values = dict(zip(jobs, pool.map(read, jobs)))

    out = {}

    for role in sensor.bands:

        band = np.full((grid.height, grid.width), np.nan, dtype="float32")

        for i in used:
            take = scene.source == i
            band[take] = values[(role, i)][take]

        out[role] = band

    return out


# ============================================================
# COMPARE
# ============================================================

@dataclass
class Comparison:

    sensor: Sensor

    before: Scene

    after: Scene

    before_idx: dict

    after_idx: dict

    # Pixels clear in both images and inside the grid's area.
    valid: object

    labels: object

    grid: Grid

    def scenes(self):

        return {
            "before": self.before.summary(),
            "after": self.after.summary(),
            "season_gap_days": season_gap_days(self.before.acquired.date(), self.after.acquired.date()),
            "method": self.sensor.method(),
            "sensor": self.sensor.name,
            "source": self.sensor.source_id,
            "resolution_m": self.sensor.pixel_m,
        }


def same_season(year, as_of):
    """
    The date in `year` at the same time of year as `as_of`.
    """

    try:
        return as_of.replace(year=year)
    except ValueError:  # 29 February
        return as_of.replace(year=year, day=28)


def compare(geometry, before_date=None, after_date=None, change_types=CHANGE_TYPES,
            today=None, search=None, reader=None, max_pixels=MAX_GRID_PIXELS,
            match_season=False):
    """
    Choose the sensor and the two images for a lon/lat geometry and
    classify every pixel.

    after_date: latest date for the later image (default today); the
    most recent clear image in the AFTER_LOOKBACK_DAYS before it is used.
    before_date: target date for the earlier image (default one year
    before after_date); the clear image closest to it within
    SEASON_WINDOW_DAYS is used. match_season: before_date only fixes
    the year (the user said e.g. "since 2010"), so the target moves to
    the same time of year as the later image, avoiding seasonal
    differences that look like change.

    Raises ChangeDataUnavailable when no clear image exists.
    """

    search = search or search_scenes

    reader = reader or read_band

    deadline = time.monotonic() + TIME_BUDGET_SECONDS

    today = today or date.today()

    after_date = min(after_date or today, today)

    sensor = choose_sensor(before_date or (after_date - timedelta(days=365)))

    grid = make_grid(geometry, sensor.pixel_m, max_pixels)

    find = lambda start, end: search(grid.bbox_lonlat, start, end, collection=sensor.collection)

    clear_pct = round(MIN_CLEAR_FRACTION * 100)

    # Later image: most recent clear one.
    after_start = after_date - timedelta(days=AFTER_LOOKBACK_DAYS)

    after, after_checked = pick_scene(
        find(after_start, after_date), grid, prefer=lambda i: -i.datetime.timestamp(),
        reader=reader, sensor=sensor, deadline=deadline,
    )

    if after is None:
        raise ChangeDataUnavailable(
            f"No {sensor.name} image between {after_start} and {after_date} had at "
            f"least {clear_pct}% of the area clear of cloud.",
            {"sensor": sensor.name, "after": after_checked},
        )

    # Earlier image: closest clear one to the target date.
    if before_date and match_season:
        target = same_season(before_date.year, after.acquired.date())
    else:
        target = before_date or (after.acquired.date() - timedelta(days=365))

    start = target - timedelta(days=SEASON_WINDOW_DAYS)

    end = min(target + timedelta(days=SEASON_WINDOW_DAYS), after.acquired.date() - timedelta(days=30))

    if end <= start:
        raise ChangeDataUnavailable(
            "The earlier date is too close to the latest clear image to compare.",
            {"sensor": sensor.name, "after": after_checked},
        )

    before, before_checked = pick_scene(
        find(start, end), grid, prefer=lambda i: abs((i.datetime.date() - target).days),
        reader=reader, sensor=sensor, deadline=deadline,
    )

    if before is None:
        raise ChangeDataUnavailable(
            f"No {sensor.name} image between {start} and {end} had at least "
            f"{clear_pct}% of the area clear of cloud.",
            {"sensor": sensor.name, "after": after_checked, "before": before_checked},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        before_bands, after_bands = pool.map(lambda s: _read_scene_bands(s, grid, reader), (before, after))

    before_idx = spectral_indices(before_bands)

    after_idx = spectral_indices(after_bands)

    valid = before.clear & after.clear

    return Comparison(
        sensor, before, after, before_idx, after_idx, valid,
        classify(before_idx, after_idx, valid, change_types), grid,
    )


# ============================================================
# DETECT
# ============================================================

@dataclass
class ChangeResult:

    observations: list

    before: dict

    after: dict

    season_gap_days: int

    # Share of the area clear in both images.
    clear_fraction_both: float

    area_km2: float

    method: str

    sensor: str

    source: str

    resolution_m: int


def _to_lonlat(geometry, crs):

    from rasterio.warp import transform_geom

    return transform_geom(crs, "EPSG:4326", geometry, precision=6)


def detect_changes(geometry, before_date=None, after_date=None,
                   change_types=CHANGE_TYPES, today=None,
                   search=None, reader=None, match_season=False):
    """
    ChangeResult for a shapely lon/lat geometry: every changed patch
    in the area. See compare() for how the sensor and dates are chosen.
    """

    c = compare(geometry, before_date, after_date, change_types, today, search, reader,
                match_season=match_season)

    grid = c.grid

    pixel_area = grid.pixel_m ** 2

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
            "area_m2": patch["pixels"] * pixel_area,
            "index": patch["index"],
            "before_mean": patch["before_mean"],
            "after_mean": patch["after_mean"],
            "delta_mean": patch["delta_mean"],
            "min_change": RULES[patch["change_type"]]["min_change"],
            "full_scale": RULES[patch["change_type"]]["full_scale"],
        })

    scenes = c.scenes()

    return ChangeResult(
        observations=observations,
        before=scenes["before"],
        after=scenes["after"],
        season_gap_days=scenes["season_gap_days"],
        clear_fraction_both=round(int(c.valid.sum()) / inside, 3) if inside else 0.0,
        area_km2=round(inside * pixel_area / 1e6, 2),
        method=scenes["method"],
        sensor=c.sensor.name,
        source=c.sensor.source_id,
        resolution_m=c.sensor.pixel_m,
    )


def measure_parcels(geometries, before_date=None, after_date=None, today=None,
                    search=None, reader=None, match_season=False):
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

    c = compare(unary_union(geometries), before_date, after_date, CHANGE_TYPES,
                today, search, reader, max_pixels=MAX_PARCEL_GRID_PIXELS,
                match_season=match_season)

    grid = c.grid

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
                    f"Too small to measure at {grid.pixel_m} m ({pixels} pixel{plural}; "
                    f"at least {MIN_PARCEL_PIXELS} needed)."
                ),
            })
            continue

        if clear_pixels < max(MIN_PARCEL_PIXELS, MIN_CLEAR_FRACTION * pixels):
            parcels.append({
                "measurable": False,
                "reason": "Clouds or image gaps covered too much of the parcel in one of the images.",
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

    return parcels, c.scenes()


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
        match_season=time_range.start_precision == "year",
    )

    context = new_context(area.latitude, area.longitude, area.reach_km, location_name)

    context.layer_status["historical_imagery"] = "loaded"

    context.layer_provenance["historical_imagery"] = {
        "source_id": result.source,
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
        "sensor": result.sensor,
        "source": result.source,
        "resolution_m": result.resolution_m,
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
                "resolution_m": result.resolution_m,
                "before": result.before,
                "after": result.after,
                "season_gap_days": result.season_gap_days,
            },
            "_shape": metric,
        })

    candidates.sort(key=lambda c: c["area_m2"], reverse=True)

    return candidates, context
