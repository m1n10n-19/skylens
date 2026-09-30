from datetime import datetime, timedelta

import planetary_computer
import pystac_client


CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def search_satellite(
    latitude: float,
    longitude: float,
    radius_km: float = 5,
    days_back: int = 90,
):
    """
    Find recent Sentinel-2 imagery around a location.
    """

    catalog = pystac_client.Client.open(
        CATALOG_URL
    )

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    # Approximate bounding box.
    # Good enough for the MVP.
    lat_delta = radius_km / 111
    lon_delta = radius_km / (
        111 * max(abs(__import__("math").cos(
            __import__("math").radians(latitude)
        )), 0.1)
    )

    bbox = [
        longitude - lon_delta,
        latitude - lat_delta,
        longitude + lon_delta,
        latitude + lat_delta,
    ]

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{start_date.isoformat()}Z/{end_date.isoformat()}Z",
        query={
            "eo:cloud_cover": {
                "lt": 20
            }
        },
        max_items=10,
    )

    items = list(search.items())

    results = []

    for item in items:

        results.append({
            "id": item.id,
            "date": item.datetime.isoformat()
            if item.datetime else None,
            "cloud_cover": item.properties.get(
                "eo:cloud_cover"
            ),
            "bbox": item.bbox,
            "assets": list(item.assets.keys()),
        })

    return results

def get_latest_satellite(
    latitude: float,
    longitude: float,
    radius_km: float = 5,
    days_back: int = 90,
):
    catalog = pystac_client.Client.open(
        CATALOG_URL
    )

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    import math

    lat_delta = radius_km / 111
    lon_delta = radius_km / (
        111 * max(abs(math.cos(math.radians(latitude))), 0.1)
    )

    bbox = [
        longitude - lon_delta,
        latitude - lat_delta,
        longitude + lon_delta,
        latitude + lat_delta,
    ]

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{start_date.isoformat()}Z/{end_date.isoformat()}Z",
        query={
            "eo:cloud_cover": {
                "lt": 20
            }
        },
        max_items=10,
    )

    items = list(search.items())

    if not items:
        return None

    # Lowest cloud cover first
    items.sort(
        key=lambda item: item.properties.get(
            "eo:cloud_cover", 100
        )
    )

    item = items[0]

    return {
        "id": item.id,
        "date": item.datetime.isoformat()
        if item.datetime else None,
        "cloud_cover": item.properties.get(
            "eo:cloud_cover"
        ),
        "visual_url": item.assets["visual"].href,
        "bbox": item.bbox,
    }