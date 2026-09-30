import math
import time

import requests
from shapely.geometry import Polygon


# Try these in order.
OVERPASS_URLS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

HEADERS = {
    "User-Agent": "SkyLens/0.1 local development"
}


def _query_overpass(query: str):
    """
    Try multiple Overpass servers.
    """

    last_error = None

    for url in OVERPASS_URLS:

        try:

            print(f"Trying Overpass: {url}")

            response = requests.post(
                url,
                data={"data": query},
                headers=HEADERS,
                timeout=30
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            print(
                f"Overpass failed: {url} -> {e}"
            )

            last_error = e

            time.sleep(1)

    raise RuntimeError(
        f"All Overpass servers failed. "
        f"Last error: {last_error}"
    )


def get_buildings(
    latitude: float,
    longitude: float,
    radius_km: float = 1,
    minimum_area_m2: float = 500
):

    # ---------------------------------------------
    # Safety limits for MVP
    # ---------------------------------------------

    radius_km = min(radius_km, 2)

    # ---------------------------------------------
    # Calculate bounding box
    # ---------------------------------------------

    lat_delta = radius_km / 111

    lon_delta = radius_km / (
        111 * max(
            abs(
                math.cos(
                    math.radians(latitude)
                )
            ),
            0.1
        )
    )

    south = latitude - lat_delta
    west = longitude - lon_delta
    north = latitude + lat_delta
    east = longitude + lon_delta

    # ---------------------------------------------
    # Query only building ways
    # ---------------------------------------------

    query = f"""
    [out:json][timeout:25];

    way["building"]
    ({south},{west},{north},{east});

    out geom;
    """

    data = _query_overpass(query)

    candidates = []

    # ---------------------------------------------
    # Process buildings
    # ---------------------------------------------

    for element in data.get("elements", []):

        geometry = element.get("geometry")

        if not geometry or len(geometry) < 3:
            continue

        coordinates = [
            (
                point["lon"],
                point["lat"]
            )
            for point in geometry
        ]

        if coordinates[0] != coordinates[-1]:
            coordinates.append(
                coordinates[0]
            )

        try:

            polygon = Polygon(coordinates)

            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            if polygon.is_empty:
                continue

        except Exception:
            continue

        # -----------------------------------------
        # Approximate geographic area
        # -----------------------------------------

        centroid_lat = polygon.centroid.y

        metres_per_degree_lat = 111320

        metres_per_degree_lon = (
            111320
            * math.cos(
                math.radians(
                    centroid_lat
                )
            )
        )

        area_m2 = (
            polygon.area
            * metres_per_degree_lat
            * metres_per_degree_lon
        )

        if area_m2 < minimum_area_m2:
            continue

        centroid = polygon.centroid

        tags = element.get(
            "tags",
            {}
        )

        candidates.append({
            "osm_id": element["id"],
            "osm_type": element["type"],

            "latitude": round(
                centroid.y,
                6
            ),

            "longitude": round(
                centroid.x,
                6
            ),

            "area_m2": round(
                area_m2,
                1
            ),

            "building_type": tags.get(
                "building",
                "unknown"
            ),

            "name": tags.get(
                "name"
            )
        })

    # ---------------------------------------------
    # Largest buildings first
    # ---------------------------------------------

    candidates.sort(
        key=lambda x: x["area_m2"],
        reverse=True
    )

    return candidates