from datetime import datetime, timedelta, timezone

import pystac_client

from geodata import bbox_around


CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def _search_items(latitude, longitude, radius_km, days_back):
    """
    Sentinel-2 scenes under 20% cloud cover from the last
    days_back days that overlap the search area.
    """

    catalog = pystac_client.Client.open(CATALOG_URL)

    end = datetime.now(timezone.utc)

    start = end - timedelta(days=days_back)

    south, west, north, east = bbox_around(latitude, longitude, radius_km)

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=[west, south, east, north],
        datetime=(
            f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"
        ),
        query={"eo:cloud_cover": {"lt": 20}},
        max_items=10,
    )

    return list(search.items())


def _summary(item):

    return {
        "id": item.id,
        "date": item.datetime.isoformat() if item.datetime else None,
        "cloud_cover": item.properties.get("eo:cloud_cover"),
        "bbox": item.bbox,
    }


def search_satellite(
    latitude: float,
    longitude: float,
    radius_km: float = 5,
    days_back: int = 90,
):
    """
    Recent Sentinel-2 scenes around a location.
    """

    return [
        {**_summary(item), "assets": list(item.assets.keys())}
        for item in _search_items(latitude, longitude, radius_km, days_back)
    ]


def get_latest_satellite(
    latitude: float,
    longitude: float,
    radius_km: float = 5,
    days_back: int = 90,
):
    """
    The clearest (lowest cloud cover) recent scene, or None.
    """

    items = _search_items(latitude, longitude, radius_km, days_back)

    if not items:
        return None

    item = min(
        items,
        key=lambda i: i.properties.get("eo:cloud_cover", 100),
    )

    return {
        **_summary(item),
        "visual_url": item.assets["visual"].href,
    }
