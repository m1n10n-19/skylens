"""
Machine-learning building footprints: tile cache split into cells,
selection by box, and their effect on land analyses.
"""

import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

from shapely.geometry import box

import ml_buildings

# The real function; the shared fixtures replace the module attribute.
from ml_buildings import search_tiles as real_search_tiles

from tests.conftest import LAT, LON, land_elements, offset, planner_reply


class Tile:

    def __init__(self, tile_id):
        self.id = tile_id


def _write_tile(path, footprints):

    pq.write_table(pa.table({"geometry": pa.array(shapely.to_wkb(footprints).tolist(), type=pa.binary())}), path)


@pytest.fixture
def cache(tmp_path, monkeypatch):

    monkeypatch.setattr(ml_buildings, "CACHE_DIR", str(tmp_path))

    return tmp_path


FOOTPRINTS = [
    box(80.2001, 12.9001, 80.2002, 12.9002),     # inside the query box
    box(80.2499, 12.9499, 80.2501, 12.9501),     # straddles the box edge
    box(80.3001, 13.0001, 80.3002, 13.0002),     # far away, another cell
]

QUERY = (80.200, 12.900, 80.250, 12.950)


def _fetcher(downloads):

    def fetcher(item):
        def fetch(path):
            downloads.append(item.id)
            _write_tile(path, FOOTPRINTS)
        return fetch

    return fetcher


def test_tile_is_split_into_cells_once(cache):

    downloads = []

    for _ in range(2):
        found, info = ml_buildings.buildings_in(QUERY, search=lambda b: [Tile("India_123_2023")],
                                                fetcher=_fetcher(downloads))

    assert downloads == ["India_123_2023"]
    assert os.path.exists(os.path.join(str(cache), "India_123_2023", "READY"))
    assert not os.path.exists(os.path.join(str(cache), "India_123_2023", "tile.parquet"))
    assert info["tiles"] == ["India_123_2023"]


def test_only_buildings_in_the_box_are_returned(cache):

    found, _ = ml_buildings.buildings_in(QUERY, search=lambda b: [Tile("India_123_2023")],
                                         fetcher=_fetcher([]))

    assert len(found) == 2
    assert {round(f.centroid.x, 4) for f in found} == {80.2002, 80.25}


def test_no_tiles_means_no_footprints(cache):

    assert ml_buildings.buildings_in(QUERY, search=lambda b: [])[0] == []


def test_latest_release_and_country_preferred(monkeypatch):

    import change_detection

    class Item:
        def __init__(self, item_id):
            self.id = item_id

    class Catalog:
        def search(self, **kwargs):
            items = [Item(i) for i in ("Asia_1_2023-04-25", "India_1_2023-04-25",
                                       "India_1_2022-07-06", "India_2022-07-06", "India_2_2022-07-06")]
            return type("S", (), {"items": lambda self: items})()

    monkeypatch.setattr(change_detection, "_catalog", lambda: Catalog())

    assert sorted(i.id for i in real_search_tiles(QUERY)) == ["India_1_2023-04-25", "India_2_2022-07-06"]


# ============================================================
# IN THE ANALYSIS
# ============================================================

def test_unmapped_building_makes_a_vacant_parcel_unranked(client, deepseek, overpass, imagery,
                                                          dem, flood_evidence, open_land):
    """
    Parcel 2001 (0-60 m east, 10-70 m north) is tagged vacant on OSM,
    with no mapped building; a machine-learning footprint covers most
    of it.
    """

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence()

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    open_land()

    before = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    assert any(p["osm_id"] == 2001 for p in before["top_prospects"])

    open_land(footprints=[(5, 15, 45)])

    after = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    assert not any(p.get("osm_id") == 2001 for p in after["top_prospects"])
    assert after["analysis"]["excluded_as_built"] == 1
    assert after["completeness"]["status"] == "complete"


def test_footprint_failure_is_partial_and_named(client, deepseek, overpass, imagery, dem,
                                                flood_evidence, open_land, monkeypatch):

    from tests.conftest import clear_year

    imagery(clear_year())
    dem()
    flood_evidence()
    open_land()

    def down(*args, **kwargs):
        raise TimeoutError("blob storage down")

    monkeypatch.setattr(ml_buildings, "buildings_in", down)

    deepseek(planner_reply("ev_charging_site_selection"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "EV plots in Adyar"}).json()

    assert body["status"] == "success"
    assert body["completeness"]["status"] == "partial"
    assert any(r.startswith("Machine-learning building footprints could not be read")
               for r in body["completeness"]["reasons"])
