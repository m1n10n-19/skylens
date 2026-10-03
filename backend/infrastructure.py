"""
Infrastructure around a place, from OpenStreetMap.

    collect(area, proj) -> [Feature]
    summary(features, area_shape) -> area report
    nearest(features, shape) -> per-site distances

Existing infrastructure (rail and metro stations, bus stations, trunk
roads and motorways, airports, power substations, high-tension power
lines) and projects tagged as under construction or proposed (roads,
rail and metro, stations, power, named developments).

Distances are measured from mapped geometry. Construction and proposal
tags are volunteer-mapped: completion dates, funding and approvals are
never known from the map, and the report says so. Nothing here claims
what a project will do to the area.
"""

import re

from dataclasses import dataclass

from shapely.geometry import LineString, Point

from geodata import _element_point, _polygon, _protected_shape, area_filters


# ============================================================
# CATEGORIES
# ============================================================

# kind -> (label, search radius in metres around the area)
KINDS = {
    "rail_station": ("Rail or metro station", 3000),
    "bus_station": ("Bus station", 2000),
    "major_road": ("Motorway or trunk road", 2000),
    "airport": ("Airport", 25000),
    "substation": ("Power substation", 3000),
    "power_line": ("High-tension power line", 500),
    "road_project": ("Road project", 5000),
    "rail_project": ("Rail or metro project", 5000),
    "power_project": ("Power project", 5000),
    "development": ("Large development", 5000),
}

PROJECT_KINDS = ("road_project", "rail_project", "power_project", "development")

# A power line this close to a site (or crossing it) is flagged.
POWER_LINE_WARNING_M = 15

# Road works without a name are listed only on these road classes;
# unnamed minor roads under construction are mostly resurfacing.
MAJOR_ROAD_CLASSES = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"}

# Names of parts of a site ("Tower 3", "Block B", "T3 (Under
# Construction)", "Utility") rather than of a development.
_PART_NAME = re.compile(
    r"^(tower|block|wing|phase|building|bldg|t|b)\s*[-#]?\s*[0-9a-z]{1,3}(\s*\(.*\))?$", re.I)


@dataclass
class Feature:

    kind: str

    # "existing" | "under_construction" | "proposed"
    status: str

    name: str | None

    # Metric geometry (local projection).
    shape: object

    detail: str | None = None

    @property
    def label(self):
        return KINDS[self.kind][0]


def classify(tags):
    """
    (kind, status, detail) for an OSM feature, or None.
    """

    railway, highway, power = tags.get("railway"), tags.get("highway"), tags.get("power")

    if railway in ("construction", "proposed"):
        detail = tags.get(railway) or tags.get("construction") or tags.get("proposed")
        status = "under_construction" if railway == "construction" else "proposed"
        return ("rail_project", status, detail)

    if highway in ("construction", "proposed"):
        detail = tags.get(highway)
        if not tags.get("name") and detail not in MAJOR_ROAD_CLASSES:
            return None
        status = "under_construction" if highway == "construction" else "proposed"
        return ("road_project", status, detail)

    if power in ("construction", "proposed"):
        return ("power_project", "under_construction" if power == "construction" else "proposed",
                tags.get(power))

    if railway in ("station", "halt"):
        # Stations being built are often tagged railway=station plus
        # construction=yes (or the line type).
        status = "under_construction" if tags.get("construction") else "existing"
        return ("rail_station", status, "metro" if tags.get("station") == "subway" else tags.get("station"))

    if tags.get("amenity") == "bus_station":
        return ("bus_station", "existing", None)

    if highway in ("motorway", "trunk"):
        return ("major_road", "existing", highway)

    if tags.get("aeroway") == "aerodrome":
        return ("airport", "existing", tags.get("aerodrome:type") or tags.get("aerodrome"))

    if power == "substation":
        return ("substation", "existing", tags.get("voltage"))

    if power == "line":
        return ("power_line", "existing", tags.get("voltage"))

    if tags.get("landuse") == "construction" or tags.get("building") == "construction":
        if _development_name(tags.get("name")):
            return ("development", "under_construction", tags.get("construction"))

    return None


def _development_name(name):
    """
    True for the name of a development; False for no name or the name
    of a part of one (a tower or block, often mapped inside a campus).
    """

    if not name:
        return False

    if _PART_NAME.match(name.strip()):
        return False

    # Single short words ("Utility", "HSD") name parts, not developments.
    return len(name.split()) > 1


# ============================================================
# QUERY
# ============================================================

def query(area):
    """
    One Overpass query for every category, each within its radius of
    the searched area.
    """

    def each(selector, kind):
        return [f"{selector}{f};" for f in area_filters(area, KINDS[kind][1])]

    lines = (
        each('way["highway"~"^(motorway|trunk)$"]', "major_road")
        + each('way["power"="line"]', "power_line")
        + each('way["highway"~"^(construction|proposed)$"]', "road_project")
        + each('way["railway"~"^(construction|proposed)$"]', "rail_project")
        + each('way["power"~"^(construction|proposed)$"]', "power_project")
    )

    places = (
        each('nwr["railway"~"^(station|halt)$"]', "rail_station")
        + each('nwr["amenity"="bus_station"]', "bus_station")
        + each('nwr["aeroway"="aerodrome"]', "airport")
        + each('nwr["power"="substation"]', "substation")
        + each('wr["landuse"="construction"]["name"]', "development")
        + each('wr["building"="construction"]["name"]', "development")
    )

    return (
        "[out:json][timeout:90];\n"
        "(\n  " + "\n  ".join(lines) + "\n);\nout tags geom;\n"
        "(\n  " + "\n  ".join(places) + "\n);\nout tags center;"
    )


def parse(data, proj):
    """
    Features from an Overpass response (metric shapes).
    """

    features = {}

    for element in data.get("elements", []):

        key = (element.get("type"), element.get("id"))

        if key in features:
            continue

        tags = element.get("tags", {})

        found = classify(tags)

        if found is None:
            continue

        kind, status, detail = found

        geometry = element.get("geometry")

        if geometry and len(geometry) >= 2 and kind in ("major_road", "power_line", "road_project",
                                                         "rail_project", "power_project"):
            shape = LineString([proj.to_xy(p["lon"], p["lat"]) for p in geometry])
        elif element.get("type") == "relation" and element.get("members"):
            shape = _protected_shape(element, proj)
        elif geometry and len(geometry) >= 3:
            shape = _polygon(geometry, proj)
        else:
            shape = _element_point(element, proj)

        if shape is None or shape.is_empty:
            continue

        features[key] = Feature(kind, status, tags.get("name") or tags.get("ref"), shape, detail)

    return list(features.values())


def collect(area, proj, run_query=None):
    """
    Features around a search area (search_area.SearchArea).
    """

    import geodata

    # Through geodata's Overpass hook (looked up at call time), so tests
    # that fake Overpass fake this request too.
    return parse((run_query or geodata._query_overpass)(query(area)), proj)


# ============================================================
# MEASURE
# ============================================================

def nearest(features, shape, kind, status="existing"):
    """
    (distance in metres, feature) for the nearest feature of a kind
    within its radius, or (None, None).
    """

    radius = KINDS[kind][1]

    best = (None, None)

    for feature in features:

        if feature.kind != kind or feature.status != status:
            continue

        distance = shape.distance(feature.shape)

        if distance <= radius and (best[0] is None or distance < best[0]):
            best = (distance, feature)

    return best


def project_key(name):
    """
    One key for both directions of a mapped project:
    "Line 5: Madhavaram → Shozhinganallur (u/c)" and the reverse.
    """

    if not name:
        return None

    def plain(text):
        # "Teynampet - Saidapet" and "Teynampet-Saidapet" are one name.
        return re.sub(r"[^0-9a-z]+", " ", text.lower()).strip()

    text = re.sub(r"\(u/c\)|\(under construction\)", "", name, flags=re.I).strip()

    if "\u2192" in text or "->" in text:
        prefix, _, route = text.rpartition(":")
        # "Yellow Line (Underground 2)" is a section of "Yellow Line".
        prefix = re.sub(r"\(.*?\)", "", prefix)
        ends = sorted(plain(p) for p in re.split("\u2192|->", route))
        return (plain(prefix), tuple(ends))

    return plain(text)


def projects_near(features, shape, radius_m=5000):
    """
    [(distance, feature)] for projects under construction or proposed
    within radius_m, nearest first. Projects with several mapped
    segments (one road, many ways) are listed once, at their nearest.
    """

    found = {}

    for feature in features:

        # Projects: anything under construction or proposed (including
        # stations being built), not only the project kinds.
        if feature.status == "existing":
            continue

        distance = shape.distance(feature.shape)

        if distance > radius_m:
            continue

        key = (feature.kind, feature.status, project_key(feature.name) or id(feature))

        if key not in found or distance < found[key][0]:
            found[key] = (distance, feature)

    return sorted(found.values(), key=lambda df: df[0])


def summary(features, area_shape, proj):
    """
    Area report: existing infrastructure (nearest of each kind, plus
    counts) and projects, with distances from the searched area (0 when
    inside it) and GeoJSON for the map.
    """

    from geodata import to_geojson

    def item(distance, feature):
        return {
            "kind": feature.kind,
            "label": feature.label,
            "status": feature.status,
            "name": feature.name,
            "detail": feature.detail,
            "distance_m": round(distance),
            "geometry": to_geojson(feature.shape, proj),
        }

    existing = []

    for kind in KINDS:

        if kind in PROJECT_KINDS or kind == "power_line":
            continue

        of_kind = [f for f in features if f.kind == kind and f.status == "existing"]

        if not of_kind:
            continue

        distances = sorted(((area_shape.distance(f.shape), f) for f in of_kind), key=lambda df: df[0])

        existing.append({
            **item(*distances[0]),
            "count_within_radius": len(of_kind),
            "radius_m": KINDS[kind][1],
            "nearest": [item(d, f) for d, f in distances[:5]],
        })

    projects = [item(d, f) for d, f in projects_near(features, area_shape, 5000)]

    return {
        "existing": existing,
        "under_construction": [p for p in projects if p["status"] == "under_construction"],
        "proposed": [p for p in projects if p["status"] == "proposed"],
        "power_lines_in_area": sum(1 for f in features if f.kind == "power_line"
                                   and f.shape.intersects(area_shape)),
        "unknowns": [
            "Completion dates, funding and approvals of projects (not recorded on the map).",
            "Projects announced but not yet mapped; use 'Check the web' for reported announcements.",
            "Whether a project will change land use, traffic or prices nearby.",
        ],
        "source": "OpenStreetMap (Overpass); construction and proposal tags are volunteer-mapped",
    }
