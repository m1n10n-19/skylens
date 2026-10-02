import math

from shapely.geometry import Polygon

from geodata import bbox_around

from overpass import provenance, query_overpass


def get_buildings(*args, **kwargs):
    """
    OSM building footprints around a location, largest first.
    See get_buildings_with_provenance() for the arguments.
    """

    return get_buildings_with_provenance(*args, **kwargs)[0]


def get_buildings_with_provenance(
    latitude: float,
    longitude: float,
    radius_km: float = 1,
    minimum_area_m2: float = 500,
    include_geometry: bool = False,
    area_filters=None
):
    """
    (candidates, provenance): OSM building footprints around a
    location, largest first, and when the map data is from
    (overpass.provenance).

    include_geometry adds an internal "_polygon" (lon/lat shapely
    polygon) used by the analysis pipeline to draw footprints.

    area_filters (Overpass filters from search_area.SearchArea, e.g. a
    road corridor) replace the box around the location.
    """

    if area_filters:

        body = "\n    ".join(f'way["building"]{f};' for f in area_filters)

        query = f"""
    [out:json][timeout:60];

    (
    {body}
    );

    out geom;
    """

    else:

        # MVP safety limit
        radius_km = min(radius_km, 2)

        south, west, north, east = bbox_around(latitude, longitude, radius_km)

        query = f"""
    [out:json][timeout:25];

    way["building"]
    ({south},{west},{north},{east});

    out geom;
    """

    data = query_overpass(query)

    candidates = []

    for element in data.get("elements", []):

        geometry = element.get("geometry")

        if not geometry or len(geometry) < 3:
            continue

        coordinates = [
            (point["lon"], point["lat"])
            for point in geometry
        ]

        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])

        try:

            polygon = Polygon(coordinates)

            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            if polygon.is_empty:
                continue

        except Exception:
            continue

        # Approximate area: degrees² scaled to m² at the centroid.

        centroid = polygon.centroid

        metres_per_degree_lat = 111320

        metres_per_degree_lon = 111320 * math.cos(
            math.radians(centroid.y)
        )

        area_m2 = (
            polygon.area
            * metres_per_degree_lat
            * metres_per_degree_lon
        )

        if area_m2 < minimum_area_m2:
            continue

        tags = element.get("tags", {})

        candidate = {
            "osm_id": element["id"],
            "osm_type": element["type"],
            "latitude": round(centroid.y, 6),
            "longitude": round(centroid.x, 6),
            "area_m2": round(area_m2, 1),
            "building_type": tags.get("building", "unknown"),
            "name": tags.get("name"),
        }

        if include_geometry:
            candidate["_polygon"] = polygon

        candidates.append(candidate)

    candidates.sort(
        key=lambda x: x["area_m2"],
        reverse=True
    )

    return candidates, provenance(data)
