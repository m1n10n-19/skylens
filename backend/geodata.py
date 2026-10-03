"""
OpenStreetMap data layers beyond building footprints:

- open / vacant land polygons (candidates for site selection)
- commercial buildings (candidates for commercial sites)
- context layers used for scoring: roads, points of interest,
  EV chargers and parking

All geometry is projected into a local metric plane around the
search centre so distances and areas are in metres.
"""

import math

from dataclasses import dataclass, field

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping, shape as to_shape
from shapely.ops import linemerge, polygonize, transform, unary_union

from overpass import provenance as _provenance
from overpass import query_overpass as _query_overpass


# ============================================================
# LOCAL PROJECTION
# ============================================================

class LocalProjection:
    """
    Equirectangular projection around a centre point.
    Accurate to well under 1% within a few kilometres.
    """

    def __init__(self, latitude, longitude):

        self.lat0 = latitude

        self.lon0 = longitude

        self.m_per_deg_lat = 110574.0

        self.m_per_deg_lon = 111320.0 * math.cos(
            math.radians(latitude)
        )

    def to_xy(self, lon, lat):

        return (
            (lon - self.lon0) * self.m_per_deg_lon,
            (lat - self.lat0) * self.m_per_deg_lat,
        )

    def to_lonlat(self, x, y):

        return (
            self.lon0 + x / self.m_per_deg_lon,
            self.lat0 + y / self.m_per_deg_lat,
        )


def bbox_around(latitude, longitude, radius_km):
    """
    (south, west, north, east), same approximation as buildings.py.
    """

    lat_delta = radius_km / 111

    lon_delta = radius_km / (
        111 * max(abs(math.cos(math.radians(latitude))), 0.1)
    )

    return (
        latitude - lat_delta,
        longitude - lon_delta,
        latitude + lat_delta,
        longitude + lon_delta,
    )


def _polygon(geometry, proj):

    if not geometry or len(geometry) < 3:
        return None

    coords = [
        proj.to_xy(p["lon"], p["lat"])
        for p in geometry
    ]

    if coords[0] != coords[-1]:
        coords.append(coords[0])

    try:

        polygon = Polygon(coords)

        if not polygon.is_valid:
            polygon = polygon.buffer(0)

        if polygon.is_empty:
            return None

    except Exception:
        return None

    return polygon


def _element_point(element, proj):
    """
    Metric point for a node, or a way/relation with `out center`.
    """

    if "lat" in element and "lon" in element:
        return Point(proj.to_xy(element["lon"], element["lat"]))

    center = element.get("center")

    if center:
        return Point(proj.to_xy(center["lon"], center["lat"]))

    return None


# ============================================================
# CANDIDATES: LAND AND SITES
# ============================================================

# OSM tags that describe open or undeveloped land.
# (tag key, tag value) -> human-readable site type
OPEN_LAND_TAGS = {
    ("landuse", "vacant"): "vacant land",
    ("landuse", "brownfield"): "brownfield (cleared) land",
    ("landuse", "greenfield"): "greenfield land",
    ("landuse", "meadow"): "meadow / open grass",
    ("landuse", "grass"): "grass area",
    ("landuse", "farmland"): "farmland",
    ("natural", "scrub"): "scrub land",
    ("natural", "grassland"): "grassland",
}

COMMERCIAL_BUILDING_TYPES = (
    "commercial",
    "retail",
    "office",
    "warehouse",
    "industrial",
    "supermarket",
)


def _site_type(tags):

    for (key, value), label in OPEN_LAND_TAGS.items():
        if tags.get(key) == value:
            return label, "open_land"

    building = tags.get("building")

    if building:
        return f"{building} building", "building"

    return "unknown", "unknown"


def candidate_selectors(source, land_only=False):
    """
    Overpass selectors for a candidate source:
    "land_parcels" = open / vacant land polygons (NOT cadastral),
    "sites" = open land plus existing commercial buildings (open land
    only when land_only: the question asked for land).
    """

    if source == "land_parcels":

        return [
            f'way["{key}"="{value}"]'
            for key, value in OPEN_LAND_TAGS
        ]

    if source == "sites":

        types = "|".join(COMMERCIAL_BUILDING_TYPES)

        return [
            f'way["{key}"="{value}"]'
            for key, value in OPEN_LAND_TAGS
            if value != "farmland"
        ] + ([] if land_only else [
            f'way["building"~"^({types})$"]'
        ])

    raise ValueError(f"Unknown candidate source: {source}")


def _candidate(element, shape, proj):

    tags = element.get("tags", {})

    site_type, site_kind = _site_type(tags)

    lon, lat = proj.to_lonlat(shape.centroid.x, shape.centroid.y)

    return {

        "osm_id": element["id"],

        "osm_type": element["type"],

        "latitude": round(lat, 6),

        "longitude": round(lon, 6),

        "area_m2": round(shape.area, 1),

        "site_type": site_type,

        "site_kind": site_kind,

        "building_type": tags.get("building"),

        "landuse": tags.get("landuse") or tags.get("natural"),

        "name": tags.get("name"),

        # Internal: metric geometry for scoring. Removed from
        # API output by the pipeline.
        "_shape": shape,
    }


# ============================================================
# CONTEXT LAYERS
# ============================================================

MAJOR_ROADS = {
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
}

VEHICLE_ROADS = MAJOR_ROADS | {
    "tertiary", "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
}

# Amenities that count as commercial activity.
COMMERCIAL_AMENITIES = {
    "restaurant", "fast_food", "cafe", "food_court", "bar", "pub",
    "ice_cream", "bank", "atm", "pharmacy", "clinic", "hospital",
    "doctors", "dentist", "fuel", "cinema", "marketplace",
    "college", "university", "school", "kindergarten",
    "car_wash", "car_rental", "bureau_de_change", "theatre",
    "community_centre", "place_of_worship",
}

# Places where people stay long enough to charge a car.
DWELL_AMENITIES = {
    "restaurant", "fast_food", "cafe", "food_court", "cinema",
    "hospital", "college", "university", "marketplace",
    "theatre", "fuel",
}

DWELL_SHOPS = {
    "mall", "supermarket", "department_store", "hypermarket",
}

DWELL_TOURISM = {
    "hotel", "motel", "attraction",
}

# Context is fetched around each candidate, in metres. The
# evaluators in criteria.py measure within the same distances.
ROAD_RADIUS_M = 500
MAJOR_ROAD_RADIUS_M = 1000
POI_RADIUS_M = 500
PARKING_RADIUS_M = 300
CHARGER_RADIUS_M = 2000


@dataclass
class Poi:

    point: Point

    tags: dict

    def value(self, key):

        return self.tags.get(key)


@dataclass
class Road:

    line: LineString

    highway: str

    name: str | None

    bounds: tuple


@dataclass
class ProtectedArea:
    """
    A mapped protected area, reserved forest or wetland (metric shape).
    kind: "protected_area" | "reserve_forest" | "wetland".
    """

    shape: object

    name: str | None

    kind: str

    title: str | None = None


@dataclass
class LandInUse:
    """
    A mapped area already in use (campus, school, park...), metric.
    """

    shape: object

    name: str | None

    use: str


@dataclass
class Context:
    """
    Everything the criterion evaluators can measure against.
    """

    proj: LocalProjection

    center: Point

    radius_km: float

    location_name: str | None = None

    roads: list = field(default_factory=list)

    pois: list = field(default_factory=list)

    chargers: list = field(default_factory=list)

    parking: list = field(default_factory=list)

    protected: list = field(default_factory=list)

    in_use: list = field(default_factory=list)

    # Mapped building footprints (metric polygons).
    buildings: list = field(default_factory=list)

    # layer id -> "loaded" for every layer that was fetched
    layer_status: dict = field(default_factory=dict)

    # layer id -> {"data_as_of", "retrieved_at"} (overpass.provenance)
    layer_provenance: dict = field(default_factory=dict)

    # Analysis-specific facts for the report, e.g. which imagery
    # dates a change detection compared.
    meta: dict = field(default_factory=dict)

    def has_layer(self, layer_id):

        return self.layer_status.get(layer_id) == "loaded"

    def shape_of(self, candidate):

        shape = candidate.get("_shape")

        if shape is not None:
            return shape

        return Point(
            self.proj.to_xy(candidate["longitude"], candidate["latitude"])
        )


CONTEXT_LAYERS = (
    "roads",
    "points_of_interest",
    "ev_chargers",
    "parking",
    "protected_areas",
    "building_footprints",
    "land_in_use",
)


# Land already in use by an institution or as public open space: not
# available for most projects. Residential, commercial and industrial
# land-use areas are left out on purpose: in OpenStreetMap they usually
# cover whole neighbourhoods, empty plots included.
IN_USE_AMENITIES = ("university", "college", "school", "kindergarten", "hospital",
                    "place_of_worship", "grave_yard")

IN_USE_LANDUSE = ("education", "institutional", "religious", "cemetery", "military",
                  "recreation_ground")

IN_USE_LEISURE = ("park", "garden", "pitch", "sports_centre", "stadium", "golf_course",
                  "playground")


def in_use_kind(tags):
    """
    What a land-in-use area is used for (e.g. "university"), or None.
    """

    if tags.get("amenity") in IN_USE_AMENITIES:
        return tags["amenity"]

    if tags.get("landuse") in IN_USE_LANDUSE:
        return tags["landuse"]

    if tags.get("leisure") in IN_USE_LEISURE:
        return tags["leisure"]

    return None


def new_context(latitude, longitude, radius_km, location_name=None):

    proj = LocalProjection(latitude, longitude)

    return Context(
        proj=proj,
        center=Point(0, 0),
        radius_km=radius_km,
        location_name=location_name,
    )


def area_filters(area, margin_m):
    """
    Overpass poly filters for the search area grown by margin_m, so
    context near any candidate in the area is fetched, including
    candidates found later in imagery.
    """

    from search_area import _poly_filter

    proj = LocalProjection(area.latitude, area.longitude)

    if area.geometry:
        outline = to_shape(area.geometry)
    else:
        south, west, north, east = bbox_around(area.latitude, area.longitude, area.reach_km)
        outline = Polygon([(west, south), (east, south), (east, north), (west, north)])

    grown = transform(lambda x, y, z=None: proj.to_xy(x, y), outline).buffer(margin_m)

    parts = grown.geoms if isinstance(grown, MultiPolygon) else [grown]

    return [_poly_filter(part, proj)[1] for part in parts]


def _context_statements(layers, area):
    """
    Context statements covering the whole search area plus each
    layer's measuring distance.
    """

    statements = []

    def each(selector, margin_m):
        return [f"{selector}{f};" for f in area_filters(area, margin_m)]

    if "roads" in layers:

        vehicle = "|".join(sorted(VEHICLE_ROADS))

        major = "|".join(sorted(MAJOR_ROADS))

        roads = (
            each(f'way["highway"~"^({vehicle})$"]', ROAD_RADIUS_M)
            + each(f'way["highway"~"^({major})$"]', MAJOR_ROAD_RADIUS_M)
        )

        statements.append("(\n  " + "\n  ".join(roads) + "\n);\nout tags geom;")

    points = []

    if "points_of_interest" in layers:

        amenities = "|".join(sorted(COMMERCIAL_AMENITIES))

        for kind in ("node", "way"):
            points += each(f'{kind}["shop"]', POI_RADIUS_M)
            points += each(f'{kind}["office"]', POI_RADIUS_M)
            points += each(f'{kind}["tourism"~"^(hotel|motel|attraction)$"]', POI_RADIUS_M)
            points += each(f'{kind}["amenity"~"^({amenities})$"]', POI_RADIUS_M)

    if "parking" in layers:

        for kind in ("node", "way"):
            points += each(f'{kind}["amenity"="parking"]', PARKING_RADIUS_M)

    if "ev_chargers" in layers:

        for kind in ("node", "way"):
            points += each(f'{kind}["amenity"="charging_station"]', CHARGER_RADIUS_M)

    if points:

        statements.append(
            "(\n  " + "\n  ".join(points) + "\n);\nout tags center;"
        )

    if "building_footprints" in layers:

        statements.append(
            "(\n  " + "\n  ".join(each('way["building"]', 0)) + "\n);\nout tags geom;"
        )

    if "land_in_use" in layers:

        in_use = (
            each(f'wr["amenity"~"^({"|".join(IN_USE_AMENITIES)})$"]', 0)
            + each(f'wr["landuse"~"^({"|".join(IN_USE_LANDUSE)})$"]', 0)
            + each(f'wr["leisure"~"^({"|".join(IN_USE_LEISURE)})$"]', 0)
        )

        statements.append("(\n  " + "\n  ".join(in_use) + "\n);\nout tags geom;")

    if "protected_areas" in layers:

        protected = (
            each('wr["boundary"="protected_area"]', 0)
            + each('wr["leisure"="nature_reserve"]', 0)
            + each('wr["natural"="wetland"]', 0)
            + each('wr["landuse"="forest"]["name"~"[Rr]eserve"]', 0)
        )

        statements.append("(\n  " + "\n  ".join(protected) + "\n);\nout tags geom;")

    return statements


# ------------------------------------------------------------
# Protected areas
# ------------------------------------------------------------

def protected_kind(tags):
    """
    "protected_area", "reserve_forest", "wetland" or None.
    """

    if tags.get("boundary") == "protected_area" or tags.get("leisure") == "nature_reserve":
        return "protected_area"

    if "reserve" in (tags.get("name") or "").lower() and (
        tags.get("landuse") == "forest" or tags.get("natural") == "wood"
    ):
        return "reserve_forest"

    if tags.get("natural") == "wetland":
        return "wetland"

    return None


def _protected_shape(element, proj):
    """
    Metric polygon of a protected way or multipolygon relation, or None.
    """

    if element.get("type") == "way":
        return _polygon(element.get("geometry"), proj)

    outer, inner = [], []

    for member in element.get("members", []):

        geometry = member.get("geometry")

        if member.get("type") != "way" or not geometry or len(geometry) < 2:
            continue

        line = LineString([proj.to_xy(p["lon"], p["lat"]) for p in geometry])

        (inner if member.get("role") == "inner" else outer).append(line)

    if not outer:
        return None

    shape = unary_union(list(polygonize(linemerge(outer))))

    if inner:
        shape = shape.difference(unary_union(list(polygonize(linemerge(inner)))))

    return None if shape.is_empty else shape


def collect_candidates_and_context(source, area, min_area_m2, max_area_m2=None,
                                   layers=(), location_name=None, land_only=False):
    """
    ONE Overpass request returning the candidate polygons inside the
    search area (search_area.SearchArea) and the context layers
    (roads, POIs, parking, chargers) around them. One request uses
    one rate-limit slot instead of two.

    Returns (candidates, context). Raises OverpassError if the map
    data provider is unavailable.
    """

    context = new_context(area.latitude, area.longitude, area.reach_km, location_name)

    layers = [layer for layer in layers if layer in CONTEXT_LAYERS]

    selectors = "\n  ".join(
        f"{selector}{area_filter};"
        for selector in candidate_selectors(source, land_only)
        for area_filter in area.overpass_filters
    )

    query = (
        "[out:json][timeout:90];\n"
        f"(\n  {selectors}\n)->.cands;\n"
        ".cands out tags geom;\n"
        + "\n".join(_context_statements(layers, area))
    )

    data = _query_overpass(query)

    proj = context.proj

    candidates = {}

    roads = {}

    points = {}

    protected = {}

    in_use = {}

    buildings = {}

    for element in data.get("elements", []):

        key = (element.get("type"), element.get("id"))

        tags = element.get("tags", {})

        geometry = element.get("geometry")

        kind = protected_kind(tags) if "protected_areas" in layers else None

        if kind:

            if key not in protected:

                shape = _protected_shape(element, proj)

                if shape is not None:
                    protected[key] = ProtectedArea(
                        shape, tags.get("name"), kind,
                        tags.get("protection_title") or tags.get("designation"),
                    )

            continue

        use = in_use_kind(tags) if "land_in_use" in layers else None

        if use:

            if key not in in_use:

                shape = _protected_shape(element, proj)

                if shape is not None:
                    in_use[key] = LandInUse(shape, tags.get("name"), use)

            continue

        if geometry and tags.get("building") and "building_footprints" in layers:

            if key not in buildings:

                shape = _polygon(geometry, proj)

                if shape is not None:
                    buildings[key] = shape

            # Only commercial buildings in a "sites" analysis are candidates.
            if not (source == "sites" and not land_only
                    and tags["building"] in COMMERCIAL_BUILDING_TYPES):
                continue

        # Roads and candidates come with full geometry; POIs,
        # parking and chargers come as points (node or centre).

        if geometry and tags.get("highway"):

            if key in roads or len(geometry) < 2:
                continue

            line = LineString([
                proj.to_xy(p["lon"], p["lat"]) for p in geometry
            ])

            roads[key] = Road(line, tags["highway"], tags.get("name"), line.bounds)

        elif geometry:

            if key in candidates:
                continue

            shape = _polygon(geometry, proj)

            if shape is None or shape.area < min_area_m2:
                continue

            if max_area_m2 and shape.area > max_area_m2:
                continue

            candidates[key] = _candidate(element, shape, proj)

        else:

            if key in points:
                continue

            point = _element_point(element, proj)

            if point is not None:
                points[key] = Poi(point, tags)

    context.roads = list(roads.values())

    context.protected = list(protected.values())

    context.in_use = list(in_use.values())

    context.buildings = list(buildings.values())

    for poi in points.values():

        amenity = poi.value("amenity")

        if amenity == "charging_station":
            context.chargers.append(poi)
        elif amenity == "parking":
            context.parking.append(poi)
        else:
            context.pois.append(poi)

    context.layer_status["land_parcels"] = "loaded"

    if source == "sites":
        context.layer_status["building_footprints"] = "loaded"

    for layer in layers:
        context.layer_status[layer] = "loaded"

    # Candidates and context come from the same response.
    for layer in context.layer_status:
        context.layer_provenance[layer] = _provenance(data)

    ordered = sorted(
        candidates.values(),
        key=lambda c: c["area_m2"],
        reverse=True,
    )

    return ordered, context


# ============================================================
# MEASUREMENT HELPERS
# ============================================================

def building_cover(context, shape):
    """
    Share of a shape covered by mapped building footprints (0-1).
    """

    from shapely import STRtree

    if not context.buildings or shape.area == 0:
        return 0.0

    tree = context.meta.get("_building_index")

    if tree is None:
        tree = context.meta["_building_index"] = STRtree(context.buildings)

    hits = [context.buildings[i] for i in tree.query(shape)]

    if not hits:
        return 0.0

    return min(1.0, unary_union([shape.intersection(b) for b in hits]).area / shape.area)


def nearest_road(context, shape, max_distance_m, major_only=False):
    """
    (distance_m, Road) for the closest road within max_distance_m,
    or (None, None).
    """

    minx, miny, maxx, maxy = shape.bounds

    best = (None, None)

    for road in context.roads:

        if major_only and road.highway not in MAJOR_ROADS:
            continue

        rminx, rminy, rmaxx, rmaxy = road.bounds

        # Cheap bounding-box rejection before exact distance.
        if (
            rminx > maxx + max_distance_m
            or rmaxx < minx - max_distance_m
            or rminy > maxy + max_distance_m
            or rmaxy < miny - max_distance_m
        ):
            continue

        d = shape.distance(road.line)

        if d <= max_distance_m and (best[0] is None or d < best[0]):
            best = (d, road)

    return best


def count_within(points, shape, radius_m):
    """
    Items from a list of Poi within radius_m of the shape.
    """

    minx, miny, maxx, maxy = shape.bounds

    minx -= radius_m
    miny -= radius_m
    maxx += radius_m
    maxy += radius_m

    return [
        poi for poi in points
        if minx <= poi.point.x <= maxx
        and miny <= poi.point.y <= maxy
        and shape.distance(poi.point) <= radius_m
    ]


def is_dwell_poi(poi):

    return (
        poi.value("amenity") in DWELL_AMENITIES
        or poi.value("shop") in DWELL_SHOPS
        or poi.value("tourism") in DWELL_TOURISM
        or poi.value("office") is not None
    )


def is_commercial_poi(poi):

    return (
        poi.value("shop") is not None
        or poi.value("office") is not None
        or poi.value("amenity") in COMMERCIAL_AMENITIES
        or poi.value("tourism") in DWELL_TOURISM
    )


# ============================================================
# OUTPUT FOR MAPS
# ============================================================

def _rounded(value):

    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]

    if isinstance(value, float):
        return round(value, 6)

    return value


def to_geojson(shape, proj=None):
    """
    GeoJSON geometry (lon/lat) for a shape. Pass proj for shapes in
    the local metric plane; lon/lat shapes are used as they are.
    Geometry is simplified to ~0.5 m.
    """

    if proj is not None:
        shape = transform(proj.to_lonlat, shape.simplify(0.5))
    else:
        shape = shape.simplify(0.000005)

    geojson = mapping(shape)

    return {
        "type": geojson["type"],
        "coordinates": _rounded(geojson["coordinates"]),
    }


def candidate_geojson(candidate, context):

    if candidate.get("_shape") is not None:
        return to_geojson(candidate["_shape"], context.proj)

    if candidate.get("_polygon") is not None:
        return to_geojson(candidate["_polygon"])

    return None


def _poi_category(poi):

    for key in ("amenity", "shop", "tourism"):
        if poi.value(key):
            return poi.value(key)

    return "office" if poi.value("office") else "place"


def _point_json(context, poi, shape=None):

    lon, lat = context.proj.to_lonlat(poi.point.x, poi.point.y)

    item = {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "name": poi.value("name"),
        "category": _poi_category(poi),
    }

    if shape is not None:
        item["distance_m"] = round(shape.distance(poi.point))

    return item


def nearby_features(context, candidate, max_pois=80):
    """
    Mapped features around one candidate, for the site view:
    the nearest road, POIs, parking and EV chargers, within the
    same distances the criteria use. None if no context was loaded.
    """

    if not context.roads and not (
        context.pois or context.parking or context.chargers
    ):
        return None

    shape = context.shape_of(candidate)

    road = None

    distance, nearest = nearest_road(context, shape, ROAD_RADIUS_M)

    if nearest is not None:

        # Only the part of the road near the site.
        clipped = nearest.line.intersection(shape.buffer(ROAD_RADIUS_M + 100))

        road = {
            "name": nearest.name,
            "type": nearest.highway,
            "distance_m": round(distance),
            "geometry": (
                to_geojson(clipped, context.proj)
                if not clipped.is_empty else None
            ),
        }

    pois = count_within(context.pois, shape, POI_RADIUS_M)[:max_pois]

    return {
        "nearest_road": road,
        "pois": [_point_json(context, p) for p in pois],
        "parking": [
            _point_json(context, p)
            for p in count_within(context.parking, shape, PARKING_RADIUS_M)
        ],
        "chargers": [
            _point_json(context, p, shape)
            for p in count_within(context.chargers, shape, CHARGER_RADIUS_M)
        ],
    }
