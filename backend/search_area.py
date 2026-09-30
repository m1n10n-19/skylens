"""
Choosing the area to search.

The place named in the question is geocoded with Nominatim, then:

- a road (ECR, OMR, GST Road...) -> a corridor: a strip either side of
  the road, following it up to CORRIDOR_REACH_KM from where the geocoder
  matched it. Roads are followed by their route number (e.g. SH49) or
  by the name the user used;
- a small area (a suburb, a campus) -> its whole boundary;
- a larger area (a city zone) -> a box around its centre, reported as
  partial coverage;
- anything larger than MAX_PLACE_KM2 (a city, a state, a country) ->
  refused with a request to name a smaller area;
- a point (a landmark, an address) or a stated distance -> a box.

Every search is limited to about MAX_SEARCH_KM2, which keeps the live
OpenStreetMap (Overpass) queries within what the public servers handle.
"""

import re
import time

from dataclasses import dataclass
from typing import Optional

import requests

from fastapi import HTTPException
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union

from geodata import LocalProjection, bbox_around, to_geojson
from overpass import query_overpass


# ============================================================
# LIMITS
# ============================================================

DEFAULT_RADIUS_KM = 1

MAX_RADIUS_KM = 2

# Largest area searched in one analysis (a 4 x 4 km box).
MAX_SEARCH_KM2 = 16

# Places larger than this (by bounding box) are refused.
MAX_PLACE_KM2 = 150

CORRIDOR_REACH_KM = 10

CORRIDOR_HALF_WIDTH_M = 300

MAX_CORRIDOR_HALF_WIDTH_M = 1000

ROAD_TYPES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential"

NOMINATIM_LOOKUP = "https://nominatim.openstreetmap.org/lookup"

HEADERS = {"User-Agent": "skylens-reality-intelligence"}


# ============================================================
# MODEL
# ============================================================

class AreaTooLarge(Exception):

    def __init__(self, name, area_km2):
        super().__init__(f"{name} covers about {area_km2:,.0f} km²")
        self.name = name
        self.area_km2 = area_km2


@dataclass
class SearchArea:

    # "radius" | "place" | "corridor"
    kind: str

    name: str

    latitude: float

    longitude: float

    # Rough distance from the centre to the edge of the area, in km.
    reach_km: float

    area_km2: float

    # Plain-language description, e.g. "all of Thuraipakkam (7.2 km²)".
    description: str

    # Overpass area filters, e.g. "(s,w,n,e)" or '(poly:"...")'. Each
    # candidate selector is run once per filter (a corridor with gaps
    # has several parts).
    overpass_filters: tuple = ()

    # GeoJSON outline of the searched area, for the maps.
    geometry: Optional[dict] = None

    # Size of the named place when only part of it was searched.
    place_area_km2: Optional[float] = None

    road: Optional[dict] = None

    # Full geocoder address of the named place.
    address: str = ""

    def public(self):

        return {
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "area_km2": round(self.area_km2, 1),
            "radius_km": self.reach_km if self.kind == "radius" else None,
            "place_area_km2": (
                round(self.place_area_km2, 1)
                if self.place_area_km2 else None
            ),
            "road": self.road,
            "geometry": self.geometry,
        }


# ============================================================
# HELPERS
# ============================================================

def _km(value):

    return f"{value:.1f}".rstrip("0").rstrip(".")


def _bbox_area_km2(raw):

    south, north, west, east = (float(v) for v in raw["boundingbox"])

    proj = LocalProjection((south + north) / 2, (west + east) / 2)

    (x1, y1), (x2, y2) = proj.to_xy(west, south), proj.to_xy(east, north)

    return abs(x2 - x1) * abs(y2 - y1) / 1e6


def _place_name(raw):

    names = raw.get("namedetails") or {}

    return (
        names.get("name:en") or names.get("name")
        or raw.get("display_name", "").split(",")[0]
    ).strip()


def _locality(raw):

    address = raw.get("address") or {}

    for key in ("suburb", "neighbourhood", "quarter", "village", "town", "city_district", "city"):
        if address.get(key):
            return address[key]

    return raw.get("display_name", "").split(",")[0]


def _names(value):

    return [n.strip() for n in (value or "").split(";") if n.strip()]


def _ql_regex(text):
    """
    Text as a literal inside an Overpass regex inside a QL string.
    """

    ere = re.sub(r'([.^$*+?()\[\]{}|\\])', r"\\\1", text)

    return ere.replace("\\", "\\\\").replace('"', '\\"')


# ============================================================
# AREA KINDS
# ============================================================

def _box(lat, lon, radius_km, name, around=None, note="", place_area_km2=None):

    south, west, north, east = bbox_around(lat, lon, radius_km)

    ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]

    side = _km(2 * radius_km)

    return SearchArea(
        kind="radius",
        name=name,
        latitude=lat,
        longitude=lon,
        reach_km=radius_km,
        area_km2=(2 * radius_km) ** 2,
        description=f"a {side} × {side} km area around {around or name}{note}",
        overpass_filters=(f"({south},{west},{north},{east})",),
        geometry={"type": "Polygon", "coordinates": [[[round(x, 6), round(y, 6)] for x, y in ring]]},
        place_area_km2=place_area_km2,
    )


def _place_polygon(raw):
    """
    Boundary of an OSM area (lon/lat shapely polygon), or None.
    """

    osm_id = f"{raw['osm_type'][0].upper()}{raw['osm_id']}"

    # Nominatim allows one request per second.
    time.sleep(1)

    try:
        response = requests.get(
            NOMINATIM_LOOKUP,
            params={
                "osm_ids": osm_id,
                "format": "json",
                "polygon_geojson": 1,
                "polygon_threshold": 0.0002,
            },
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        geometry = shape(response.json()[0]["geojson"])
    except Exception as e:
        print("PLACE BOUNDARY LOOKUP FAILED:", repr(e))
        return None

    if geometry.geom_type == "MultiPolygon":
        geometry = max(geometry.geoms, key=lambda g: g.area)

    return geometry if geometry.geom_type == "Polygon" else None


def _poly_filter(metric_polygon, proj, max_vertices=150):
    """
    (simplified outline, Overpass poly filter) for a metric polygon.
    """

    tolerance = 15
    outline = metric_polygon.simplify(tolerance)
    while len(outline.exterior.coords) > max_vertices:
        tolerance *= 2
        outline = metric_polygon.simplify(tolerance)

    points = " ".join(
        "{1:.6f} {0:.6f}".format(*proj.to_lonlat(x, y))
        for x, y in outline.exterior.coords
    )

    return outline, f'(poly:"{points}")'


def _place(polygon, name, lat, lon):

    proj = LocalProjection(lat, lon)

    metric = transform(proj.to_xy, polygon)

    area_km2 = metric.area / 1e6

    outline, poly = _poly_filter(metric, proj)

    minx, miny, maxx, maxy = metric.bounds

    reach_km = round(max(abs(minx), abs(maxx), abs(miny), abs(maxy)) / 1000, 1)

    return SearchArea(
        kind="place",
        name=name,
        latitude=lat,
        longitude=lon,
        reach_km=reach_km,
        area_km2=area_km2,
        description=f"all of {name} ({_km(area_km2)} km²)",
        overpass_filters=(poly,),
        geometry=to_geojson(outline, proj),
    )


def _corridor(raw, lat, lon, location_text, stated_radius_km):
    """
    Strip either side of the road the geocoder matched, or None if
    the road can't be traced.
    """

    names = raw.get("namedetails") or {}

    all_names = []
    for key in ("name", "name:en", "alt_name", "official_name", "old_name"):
        all_names += _names(names.get(key))

    # The road as the user named it, e.g. "East Coast Road". Segments
    # often carry local names, so match it inside any of the names.
    phrase = location_text.split(",")[0].strip()
    user_named_it = bool(phrase) and any(phrase.lower() in n.lower() for n in all_names)

    refs = _names(names.get("ref"))

    selectors = [f'["ref"~"(^|;) *{_ql_regex(r)} *(;|$)"]' for r in refs]

    if user_named_it:
        for key in ("name", "alt_name", "official_name"):
            selectors.append(f'["{key}"~"{_ql_regex(phrase)}",i]')
    elif not refs and names.get("name"):
        selectors.append(f'["name"~"^{_ql_regex(names["name"])}$",i]')

    if not selectors:
        return None

    half_width = CORRIDOR_HALF_WIDTH_M
    if stated_radius_km:
        half_width = int(min(stated_radius_km * 1000, MAX_CORRIDOR_HALF_WIDTH_M))

    reach_m = CORRIDOR_REACH_KM * 1000

    query = "[out:json][timeout:60];\n(\n" + "".join(
        f'  way["highway"~"^({ROAD_TYPES})(_link)?$"]{sel}(around:{reach_m},{lat},{lon});\n'
        for sel in selectors
    ) + ");\nout tags geom;"

    data = query_overpass(query)

    proj = LocalProjection(lat, lon)

    road = unary_union([
        transform(proj.to_xy, shape({
            "type": "LineString",
            "coordinates": [[p["lon"], p["lat"]] for p in e["geometry"]],
        }))
        for e in data.get("elements", [])
        if len(e.get("geometry") or []) >= 2
    ])

    if road.is_empty:
        return None

    # Follow the road up to reach_m from the matched point; shorten the
    # reach until the corridor fits the search limit.
    for _ in range(12):
        clipped = road.intersection(Point(0, 0).buffer(reach_m))
        corridor = clipped.buffer(half_width)
        if corridor.area / 1e6 <= MAX_SEARCH_KM2 or reach_m <= 1000:
            break
        reach_m *= 0.85

    parts = list(corridor.geoms) if corridor.geom_type == "MultiPolygon" else [corridor]

    # Tiny slivers (road fragments at the edge of the reach) add nothing.
    parts = [p for p in parts if p.area > 0.02 * corridor.area] or parts

    outlines, filters = zip(*(_poly_filter(p, proj, 250 // len(parts) + 20) for p in parts))

    area_km2 = sum(p.area for p in parts) / 1e6

    # Corridor length, not road length: dual carriageways and slip
    # roads would otherwise be counted twice.
    length_km = area_km2 / (2 * half_width / 1000)

    road_name = phrase if user_named_it else (names.get("name:en") or names.get("name") or refs[0])

    ref = ";".join(refs) or None

    anchor = _locality(raw)

    label = f"{road_name} ({ref})" if ref and ref.lower() not in road_name.lower() else road_name

    reach_km = round(reach_m / 1000, 1)

    return SearchArea(
        kind="corridor",
        name=road_name,
        latitude=lat,
        longitude=lon,
        reach_km=reach_km,
        area_km2=area_km2,
        description=(
            f"a {half_width} m strip either side of {label}: about "
            f"{_km(length_km)} km of road within {_km(reach_km)} km of {anchor}"
        ),
        overpass_filters=filters,
        geometry=to_geojson(unary_union(outlines), proj),
        road={
            "name": road_name,
            "ref": ref,
            "anchor": anchor,
            "length_km": round(length_km, 1),
            "half_width_m": half_width,
            "reach_km": reach_km,
        },
    )


# ============================================================
# RESOLVE
# ============================================================

def resolve(geolocator, location_text, stated_radius_km=None):
    """
    SearchArea for the place in the question. Raises HTTPException
    when the place can't be found, AreaTooLarge when it is too big.
    """

    try:
        result = geolocator.geocode(location_text, namedetails=True, addressdetails=True)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={
                "stage": "geocoding",
                "location_text": location_text,
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )

    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "stage": "geocoding",
                "location_text": location_text,
                "error": f"Location not found: {location_text}",
            },
        )

    area = _choose(result.raw, result.latitude, result.longitude, location_text, stated_radius_km)

    area.address = result.address

    return area


def _choose(raw, lat, lon, location_text, stated_radius_km):

    name = _place_name(raw)

    if raw.get("class") == "highway":
        corridor = _corridor(raw, lat, lon, location_text, stated_radius_km)
        if corridor:
            return corridor

    if stated_radius_km:
        return _box(lat, lon, min(float(stated_radius_km), MAX_RADIUS_KM), name)

    bbox_km2 = _bbox_area_km2(raw)

    if bbox_km2 > MAX_PLACE_KM2:
        raise AreaTooLarge(name, bbox_km2)

    default_km2 = (2 * DEFAULT_RADIUS_KM) ** 2

    if raw.get("osm_type") in ("relation", "way") and bbox_km2 > default_km2:

        polygon = _place_polygon(raw)

        if polygon is not None:

            place = _place(polygon, name, lat, lon)

            if place.area_km2 <= MAX_SEARCH_KM2:
                return place

            return _box(
                lat, lon, DEFAULT_RADIUS_KM, name,
                around=f"the centre of {name}",
                note=f" ({name} covers about {place.area_km2:,.0f} km², so only part of it was searched)",
                place_area_km2=place.area_km2,
            )

    return _box(lat, lon, DEFAULT_RADIUS_KM, name)
