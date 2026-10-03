"""
Building footprints detected by machine learning (Microsoft Global ML
Building Footprints, via Microsoft Planetary Computer), for buildings
that OpenStreetMap does not have, which is common in Indian cities.

    buildings_in(bbox_lonlat)

returns the footprints (lon/lat polygons) overlapping a box.

The data comes as one large file per map tile (~75 km, e.g. 45 MB and
500,000 buildings for Chennai). Reading such a file needs about 110 MB
of memory, so it is done once per tile: the file is downloaded, split
into small cells on disk, and later questions read only the cells they
need. A lock lets only one tile be prepared at a time, which keeps the
app within a small instance's memory. The cache lives on local disk;
on hosts whose disk is wiped on restart, the first question in a
region afterwards pays the preparation again (about 30 s).
"""

import glob
import os
import threading

import numpy as np


COLLECTION = "ms-buildings"

SOURCE_ID = "ms_buildings_planetary_computer"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "ml_buildings")

# Size of the cells a tile is split into, in degrees (~5.5 km).
CELL_DEG = 0.05

BATCH_ROWS = 20000

# Buildings are filed under the cell of their centre; cells this far
# beyond the box are read too, so buildings straddling its edge count.
EDGE_MARGIN_DEG = 0.002

_PREPARE_LOCK = threading.Lock()


def _cell(lon, lat):

    return int(np.floor(lon / CELL_DEG)), int(np.floor(lat / CELL_DEG))


def _cells_for(bbox_lonlat):

    west, south, east, north = bbox_lonlat

    x0, y0 = _cell(west, south)

    x1, y1 = _cell(east, north)

    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def _tile_dir(tile_id):

    return os.path.join(CACHE_DIR, tile_id)


# ============================================================
# PROVIDER
# ============================================================

def search_tiles(bbox_lonlat):
    """
    The latest footprint file for each map tile overlapping the box.
    Items are named <Region>_<quadkey>_<date>; regional duplicates of
    the same quadkey (e.g. "India" and "Asia") are the same buildings.
    """

    from change_detection import _catalog

    latest = {}

    for item in _catalog().search(collections=[COLLECTION], bbox=list(bbox_lonlat)).items():

        parts = item.id.split("_")

        if len(parts) != 3:
            continue

        region, quadkey, day = parts

        best = latest.get(quadkey)

        # Prefer the newest release; for the same date, a country
        # region over a continent.
        if best is None or (day, region != "Asia") > (best[0], best[1] != "Asia"):
            latest[quadkey] = (day, region, item)

    return [item for _, _, item in latest.values()]


def download(item, path):
    """
    Download a tile's parquet file to path.
    """

    import adlfs

    asset = item.assets["data"]

    fs = adlfs.AzureBlobFileSystem(**asset.extra_fields["table:storage_options"])

    remote = fs.ls(asset.href.replace("abfs://", ""))

    fs.get(remote[0], path)


# ============================================================
# CACHE
# ============================================================

def prepare(tile_id, fetch):
    """
    Split a downloaded tile into per-cell files under its cache
    directory (once). fetch(path) downloads the tile's parquet file.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    folder = _tile_dir(tile_id)

    if os.path.exists(os.path.join(folder, "READY")):
        return folder

    with _PREPARE_LOCK:

        if os.path.exists(os.path.join(folder, "READY")):
            return folder

        os.makedirs(folder, exist_ok=True)

        source = os.path.join(folder, "tile.parquet")

        fetch(source)

        cells = {}

        for batch in pq.ParquetFile(source).iter_batches(batch_size=BATCH_ROWS, columns=["geometry"]):

            wkb = batch.column(0).to_numpy(zero_copy_only=False)

            centres = shapely.centroid(shapely.from_wkb(wkb))

            xs = np.floor(shapely.get_x(centres) / CELL_DEG).astype(int)

            ys = np.floor(shapely.get_y(centres) / CELL_DEG).astype(int)

            for key in set(zip(xs.tolist(), ys.tolist())):
                pick = (xs == key[0]) & (ys == key[1])
                cells.setdefault(key, []).extend(wkb[pick].tolist())

        for (x, y), geometries in cells.items():
            pq.write_table(pa.table({"geometry": pa.array(geometries, type=pa.binary())}),
                           os.path.join(folder, f"{x}_{y}.parquet"))

        os.remove(source)

        open(os.path.join(folder, "READY"), "w").close()

    return folder


def buildings_in(bbox_lonlat, search=None, fetcher=None):
    """
    (footprints, info): lon/lat shapely polygons overlapping the box,
    and which tiles they came from. fetcher(item) returns a callable
    that downloads the item's file to a path (default: download).
    """

    import pyarrow.parquet as pq
    import shapely

    search = search or search_tiles

    fetcher = fetcher or (lambda item: (lambda path: download(item, path)))

    tiles = search(bbox_lonlat)

    if not tiles:
        return [], {"source": SOURCE_ID, "tiles": []}

    west, south, east, north = bbox_lonlat

    found = []

    for tile in tiles:

        folder = prepare(tile.id, fetcher(tile))

        m = EDGE_MARGIN_DEG

        for x, y in _cells_for((west - m, south - m, east + m, north + m)):

            for path in glob.glob(os.path.join(folder, f"{x}_{y}.parquet")):

                geoms = shapely.from_wkb(pq.read_table(path).column("geometry").to_numpy(zero_copy_only=False))

                b = shapely.bounds(geoms)

                keep = (b[:, 2] >= west) & (b[:, 0] <= east) & (b[:, 3] >= south) & (b[:, 1] <= north)

                found.extend(geoms[keep].tolist())

    return found, {
        "source": SOURCE_ID,
        "tiles": [t.id for t in tiles],
        "imagery_period": "2014-2023 (Bing Maps imagery)",
    }
