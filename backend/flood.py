"""
Flood exposure evidence for candidate sites.

    measure_sites(sites, today)

returns, for each site, what was observed about water on it:

    water history      JRC Global Surface Water occurrence, 1984-2020
                       (Landsat-based; share of the site ever seen as
                       open water, and how often)
    observed flooding  Sentinel-1 radar over the last WET_SEASONS wet
                       seasons: standing water that is not also dark in
                       a dry-season reference (which excludes permanent
                       water and smooth surfaces such as tarmac or sand)

The wet season is chosen for the location from NASA POWER monthly
rainfall climatology (the wettest WINDOW_MONTHS consecutive months;
the driest give the dry reference), so it follows the local monsoon.

These are observations, not a flood-risk probability: floods between
satellite passes are missed, and radar under-detects water among
buildings. Each component is optional: a failed component is reported
and the others are still used.
"""

import json
import os
import warnings

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date

import numpy as np

from shapely.geometry import mapping


# ============================================================
# SETTINGS
# ============================================================

JRC_COLLECTION = "jrc-gsw"

S1_COLLECTION = "sentinel-1-rtc"

JRC_SOURCE_ID = "jrc_gsw_planetary_computer"

S1_SOURCE_ID = "sentinel_1_rtc_planetary_computer"

POWER_SOURCE_ID = "nasa_power_climatology"

POWER_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"

PIXEL_M = 10

# Consecutive months forming the wet season (and the dry reference).
WINDOW_MONTHS = 3

# Wet seasons looked at, most recent first.
WET_SEASONS = 3

# Radar images read per wet season, and for the dry reference.
IMAGES_PER_SEASON = 2

DRY_IMAGES = 2

# Radar reads are slow and variable (large remote files). Every chosen
# image must be read within this budget, or the radar component is
# left out: reading only some would make scores vary between runs.
RADAR_BUDGET_SECONDS = 40

RADAR_WORKERS = 4

# Smoothed VV backscatter below this (dB) is treated as open water.
WATER_DB = -18.0

# A site counts as flooded in an image when at least this share of it
# shows new standing water.
FLOODED_SHARE = 0.2

# Months used when the climatology service is unavailable: both South
# Indian monsoons, and the driest months there.
FALLBACK_WET = (6, 7, 8, 9, 10, 11, 12)

FALLBACK_DRY = (1, 2, 3)

MAX_GRID_PIXELS = 1000 * 1000

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "power")

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


# ============================================================
# PURE MATHS (tested with fixed arrays)
# ============================================================

def season_window(monthly, wettest=True, months=WINDOW_MONTHS):
    """
    Month numbers (1-12) of the wettest (or driest) run of `months`
    consecutive months, wrapping around the year, from 12 monthly
    rainfall values.
    """

    best = None

    for start in range(12):
        run = [(start + k) % 12 for k in range(months)]
        total = sum(monthly[i] for i in run)
        if best is None or (total > best[0] if wettest else total < best[0]):
            best = (total, run)

    return tuple(i + 1 for i in best[1])


def to_db(linear):
    """
    Radar backscatter in dB; NaN where there is no data.
    """

    linear = np.asarray(linear, dtype="float32")

    with np.errstate(divide="ignore", invalid="ignore"):
        out = 10 * np.log10(linear)

    out[~np.isfinite(out) | (linear <= 0)] = np.nan

    return out


def smooth(db):
    """
    3 x 3 mean ignoring NaN, to reduce radar speckle.
    """

    padded = np.pad(db, 1, constant_values=np.nan)

    stack = np.stack([
        padded[1 + dy: 1 + dy + db.shape[0], 1 + dx: 1 + dx + db.shape[1]]
        for dy in (-1, 0, 1) for dx in (-1, 0, 1)
    ])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows
        return np.nanmean(stack, axis=0)


def water_mask(db):

    with np.errstate(invalid="ignore"):
        return np.isfinite(db) & (smooth(db) < WATER_DB)


def new_water(wet_db, dry_db):
    """
    (water, valid) masks: pixels that are water in a wet-season image
    and not also dark in the dry-season reference, which would mean
    permanent water or smooth ground rather than flooding.
    """

    valid = np.isfinite(wet_db) & np.isfinite(dry_db)

    return water_mask(wet_db) & ~water_mask(dry_db) & valid, valid


def site_history(occurrence, site):
    """
    {"share", "mean_occurrence"} from JRC occurrence (0-100, NaN = no
    data) over a site mask, or None without data.
    """

    values = occurrence[site & np.isfinite(occurrence)]

    if values.size == 0:
        return None

    return {
        "share": round(float(np.mean(values > 0)), 3),
        "mean_occurrence": round(float(np.mean(values)), 1),
    }


def site_observations(images, site):
    """
    Flooded share of a site in each wet-season image:
    images is [(date, season_label, water_mask, valid_mask)].
    """

    observed = []

    for day, season, water, valid in images:

        seen = site & valid

        if not seen.any():
            continue

        share = float(water[seen].mean())

        observed.append({"date": day.isoformat(), "season": season, "share": round(share, 3)})

    flooded = [o for o in observed if o["share"] >= FLOODED_SHARE]

    return {
        "images": len(observed),
        "flooded_images": len(flooded),
        "flooded_seasons": len({o["season"] for o in flooded}),
        "max_share": max((o["share"] for o in observed), default=0.0),
        "flooded": flooded,
    }


# ============================================================
# PROVIDERS
# ============================================================

def climatology(lat, lon):
    """
    12 monthly mean rainfall values (mm/day) from NASA POWER, cached
    on disk per 0.5 degree cell.
    """

    import requests

    key = f"{round(lat * 2) / 2:.1f}_{round(lon * 2) / 2:.1f}"

    path = os.path.join(CACHE_DIR, key + ".json")

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass

    response = requests.get(POWER_URL, params={
        "parameters": "PRECTOTCORR", "community": "AG",
        "latitude": round(lat, 2), "longitude": round(lon, 2), "format": "JSON",
    }, timeout=20)

    response.raise_for_status()

    values = response.json()["properties"]["parameter"]["PRECTOTCORR"]

    monthly = [float(values[m]) for m in MONTHS]

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(monthly, f)
    except OSError:
        pass

    return monthly


def seasons_for(lat, lon, climate=None):
    """
    (wet months, dry months, how they were chosen).
    """

    try:
        monthly = (climate or climatology)(lat, lon)
    except Exception as e:
        print("CLIMATOLOGY FAILED:", repr(e))
        return FALLBACK_WET, FALLBACK_DRY, (
            "default months (June-December wet, January-March dry); the rainfall "
            "climatology service was unavailable"
        )

    wet = season_window(monthly, wettest=True)

    dry = season_window(monthly, wettest=False)

    return wet, dry, "the wettest and driest three months of NASA POWER rainfall climatology"


def _catalog():

    from change_detection import _catalog as catalog

    return catalog()


def search(collection, bbox_lonlat, start=None, end=None):

    kwargs = {"collections": [collection], "bbox": list(bbox_lonlat), "max_items": 200}

    if start:
        kwargs["datetime"] = f"{start.isoformat()}/{end.isoformat()}"

    return list(_catalog().search(**kwargs).items())


def read(href, grid):
    """
    One band on the grid as float32; NaN outside the file's footprint
    and where the file's own no-data value is set.

    Files already in the grid's projection (Sentinel-1 RTC tiles in the
    local UTM zone) are read directly by window, which is much faster
    than reprojecting; others go through a WarpedVRT.
    """

    import rasterio

    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import from_bounds

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MAX_RETRY="2",
        GDAL_HTTP_RETRY_DELAY="1",
    ):
        with rasterio.open(href) as src:

            nodata = src.nodata

            if src.crs and src.crs.to_string() == grid.crs and nodata is not None:

                t = grid.transform

                bounds = (t.c, t.f - grid.height * grid.pixel_m,
                          t.c + grid.width * grid.pixel_m, t.f)

                values = src.read(
                    1, window=from_bounds(*bounds, src.transform),
                    out_shape=(grid.height, grid.width), boundless=True,
                    fill_value=nodata, resampling=Resampling.nearest,
                ).astype("float32")

            else:

                with WarpedVRT(
                    src, crs=grid.crs, transform=grid.transform,
                    width=grid.width, height=grid.height,
                    resampling=Resampling.nearest, add_alpha=True,
                ) as vrt:
                    values = vrt.read(1).astype("float32")
                    values[vrt.read(vrt.count) == 0] = np.nan

    if nodata is not None:
        values[values == nodata] = np.nan

    return values


def read_all(jobs, reader, grid, budget=None, workers=None):
    """
    {href: dB array} for every href, read in parallel. Raises
    TimeoutError unless all are read within the budget (default
    RADAR_BUDGET_SECONDS, read at call time so it can be configured).
    """

    budget = RADAR_BUDGET_SECONDS if budget is None else budget

    workers = RADAR_WORKERS if workers is None else workers

    pool = ThreadPoolExecutor(max_workers=workers)

    futures = {pool.submit(lambda h: to_db(reader(h, grid)), href): href for href in jobs}

    done, pending = wait(futures, timeout=budget)

    pool.shutdown(wait=False, cancel_futures=True)

    if pending:
        raise TimeoutError(
            f"only {len(done)} of {len(futures)} radar images were read within "
            f"{budget} s"
        )

    return {futures[future]: future.result() for future in done}


# ============================================================
# MEASURE
# ============================================================

def season_ranges(months, today, count):
    """
    The last `count` complete runs of the given consecutive months,
    most recent first, as (label, start, end), preceded by the current
    run "(so far)" if one is under way. A run wrapping the year end
    (e.g. Nov-Jan) is labelled by the year it ends in.
    """

    first, last = months[0], months[-1]

    runs = []

    for year in range(today.year, today.year - count - 2, -1):

        start = date(year - 1 if last < first else year, first, 1)

        if start >= today:
            continue

        # First day after the last month.
        end = date(year + 1, 1, 1) if last == 12 else date(year, last + 1, 1)

        label = f"{MONTHS[first - 1].title()}-{MONTHS[last - 1].title()} {year}"

        if end > today:
            # The current season, still under way: an extra run.
            runs.append((label + " (so far)", start, today))
            continue

        runs.append((label, start, end))

        if sum(1 for r in runs if not r[0].endswith("(so far)")) == count:
            break

    return runs


def _spread(items, n):
    """
    Up to n items with distinct dates, spread evenly in time.
    """

    by_day = {}

    for item in sorted(items, key=lambda i: i.datetime):
        by_day.setdefault(item.datetime.date(), item)

    days = list(by_day.values())

    if len(days) <= n:
        return days

    step = (len(days) - 1) / (n - 1) if n > 1 else 0

    return [days[round(k * step)] for k in range(n)]


def measure_sites(sites, today=None, area_center=None,
                  search_items=None, reader=None, climate=None, jrc_reader=None):
    """
    Flood evidence for each site. sites: list of lon/lat shapes.
    Returns (results, info): results in input order, each

        {"water_history": {"share", "mean_occurrence"} | None,
         "observed": {"images", "flooded_images", "flooded_seasons",
                      "max_share", "flooded"} | None}

    info: months used, sources, and "problems" for components that
    could not be measured.
    """

    from rasterio.warp import transform_geom
    from shapely.ops import unary_union

    from change_detection import make_grid, rasterize

    search_items = search_items or search

    reader = reader or read

    jrc_reader = jrc_reader or reader

    today = today or date.today()

    grid = make_grid(unary_union(sites), PIXEL_M, MAX_GRID_PIXELS)

    west, south, east, north = grid.bbox_lonlat

    lat, lon = area_center or ((south + north) / 2, (west + east) / 2)

    masks = [
        rasterize(
            [transform_geom("EPSG:4326", grid.crs, mapping(site))],
            out_shape=(grid.height, grid.width), transform=grid.transform,
            fill=0, dtype="uint8", all_touched=True,
        ).astype(bool)
        for site in sites
    ]

    problems = []

    info = {"problems": problems}

    # The water history runs alongside the radar searches and reads.
    background = ThreadPoolExecutor(max_workers=1)

    history_job = background.submit(_water_history, grid, masks, search_items, jrc_reader)

    background.shutdown(wait=False)

    observed = _observed_flooding(grid, masks, lat, lon, today, search_items, reader,
                                  climate, info, problems)

    history, history_info, history_problem = history_job.result()

    if history_info:
        info["water_history"] = history_info

    if history_problem:
        problems.insert(0, history_problem)

    results = [
        {"water_history": h, "observed": o}
        for h, o in zip(history, observed)
    ]

    return results, info


def _water_history(grid, masks, search_items, jrc_reader):
    """
    (per-site history, info, problem) from JRC occurrence.
    """

    try:

        tiles = search_items(JRC_COLLECTION, grid.bbox_lonlat)

        if not tiles:
            raise LookupError("no tile covers this area")

        occurrence = np.full((grid.height, grid.width), np.nan, dtype="float32")

        for tile in tiles:
            values = jrc_reader(tile.assets["occurrence"].href, grid)
            values[values > 100] = np.nan
            fill = np.isnan(occurrence) & np.isfinite(values)
            occurrence[fill] = values[fill]

        return (
            [site_history(occurrence, mask) for mask in masks],
            {"source": JRC_SOURCE_ID, "period": "1984-2020"},
            None,
        )

    except Exception as e:
        print("WATER HISTORY FAILED:", repr(e))
        return [None] * len(masks), None, f"Water history (JRC) could not be read ({type(e).__name__}: {e})."


def _observed_flooding(grid, masks, lat, lon, today, search_items, reader, climate, info, problems):
    """
    Per-site radar observations (None each if the component failed).
    """

    wet, dry, chosen = seasons_for(lat, lon, climate)

    info["seasons"] = {
        "wet_months": [MONTHS[m - 1].title() for m in wet],
        "dry_months": [MONTHS[m - 1].title() for m in dry],
        "chosen_by": chosen,
    }

    try:

        dry_runs = season_ranges(dry, today, 1)

        wet_runs = season_ranges(wet, today, WET_SEASONS)

        runs = dry_runs + wet_runs

        # One search over the whole period, split into seasons here.
        found = search_items(S1_COLLECTION, grid.bbox_lonlat,
                             min(start for _, start, _ in runs), today)

        def within(start, end):
            return [i for i in found if start <= i.datetime.date() < end]

        dry_items = []

        for _, start, end in dry_runs:
            dry_items += _spread(within(start, end), DRY_IMAGES)

        if not dry_items:
            raise LookupError("no dry-season radar image")

        wet_jobs = []

        for label, start, end in wet_runs:
            for item in _spread(within(start, end), IMAGES_PER_SEASON):
                wet_jobs.append((label, item))

        if not wet_jobs:
            raise LookupError("no wet-season radar image")

        # Dry reference first, so it is read before the budget runs out.
        dry_hrefs = [item.assets["vv"].href for item in dry_items]

        arrays = read_all(dry_hrefs + [item.assets["vv"].href for _, item in wet_jobs], reader, grid)

        dry_read = [arrays[h] for h in dry_hrefs]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels
            dry_db = np.nanmedian(np.stack(dry_read), axis=0)

        images = []

        for label, item in wet_jobs:
            water, valid = new_water(arrays[item.assets["vv"].href], dry_db)
            images.append((item.datetime.date(), label, water, valid))

        info["observed_flooding"] = {
            "source": S1_SOURCE_ID,
            "wet_images": [{"date": day.isoformat(), "season": label} for day, label, _, _ in images],
            "dry_images": [item.datetime.date().isoformat() for item in dry_items],
            "method": (
                f"Sentinel-1 RTC VV backscatter at {PIXEL_M} m, 3 x 3 mean; open water "
                f"below {WATER_DB:g} dB, {IMAGES_PER_SEASON} images per wet season. "
                f"Standing water counted only where the dry-season "
                f"reference ({len(dry_read)} images) is not also dark, which excludes "
                f"permanent water and smooth ground. A site is flooded in an image when "
                f"at least {round(FLOODED_SHARE * 100)}% of it shows standing water."
            ),
        }

        return [site_observations(images, mask) for mask in masks]

    except Exception as e:
        print("OBSERVED FLOODING FAILED:", repr(e))
        problems.append(f"Radar flood observations (Sentinel-1) could not be read ({type(e).__name__}: {e}).")
        return [None] * len(masks)
