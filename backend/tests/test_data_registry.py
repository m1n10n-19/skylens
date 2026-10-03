"""
Data registry: layers, sources and their metadata.

The registry replaced the literal use_cases.DATA_LAYERS dict; the
old shapes must be unchanged (golden file frozen before the change),
and new metadata is only ever added.
"""

import dataclasses
import json
import os
import re

import pytest

import data_registry

from analysis_spec import AnalysisSpec
from geodata import new_context
from scoring import score_weighted_criteria
from use_cases import DATA_LAYERS, EV_CHARGING, USE_CASES, data_layer_available, describe_use_case

from tests.conftest import LAT, LON


GOLDEN = json.load(open(
    os.path.join(os.path.dirname(__file__), "legacy", "use_cases_67fe066.json"),
    encoding="utf-8",
))


# Deliberate changes since the golden file was frozen. Anything not
# listed here must be unchanged.
CHANGED_LAYERS = {
    # Phase 2: Sentinel-2 change detection.
    "historical_imagery": {
        "label": "Historical imagery comparison",
        "source": "Microsoft Planetary Computer",
        "available": True,
    },
    # Phase 2: observed flood exposure (Sentinel-1, JRC water history).
    "flood_risk": {
        "label": "Flood exposure (observed water and flooding)",
        "source": "Sentinel-1 radar (Microsoft Planetary Computer)",
        "available": True,
    },
}

# Phase 2: construction_progress became the implemented
# land-and-construction change module.
CHANGED_USE_CASES = {"construction_progress"}

# Items inside use cases deliberately changed in Phase 2 (the flood
# criterion and layer are now measured as observed flood exposure).
CHANGED_ITEMS = {"flood_risk"}


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================

# Layers added since the golden file; they may only be appended.
NEW_LAYERS = {
    # Phase 2: Copernicus DEM.
    "terrain": {
        "label": "Elevation and terrain",
        "source": "Copernicus DEM (Microsoft Planetary Computer)",
        "available": True,
    },
    # Phase 2: open land found in imagery, and protected areas.
    "land_cover": {
        "label": "Open land found in imagery",
        "source": "ESA WorldCover (Microsoft Planetary Computer)",
        "available": True,
    },
    "protected_areas": {
        "label": "Protected areas, reserved forests and wetlands",
        "source": "OpenStreetMap (Overpass)",
        "available": True,
    },
    "land_in_use": {
        "label": "Land already in use (campuses, schools, parks...)",
        "source": "OpenStreetMap (Overpass)",
        "available": True,
    },
    "infrastructure": {
        "label": "Infrastructure and projects",
        "source": "OpenStreetMap (Overpass)",
        "available": True,
    },
}


def test_legacy_data_layers_are_unchanged():

    assert DATA_LAYERS == {**GOLDEN["data_layers"], **CHANGED_LAYERS, **NEW_LAYERS}
    assert list(DATA_LAYERS)[:len(GOLDEN["data_layers"])] == list(GOLDEN["data_layers"])


def test_describe_use_case_keeps_every_old_field():

    for old, use_case in zip(GOLDEN["use_cases"], USE_CASES.values()):

        new = describe_use_case(use_case)

        assert set(old) <= set(new)

        if use_case.id in CHANGED_USE_CASES:
            continue

        for key, value in old.items():

            if key not in ("data_layers", "criteria"):
                assert new[key] == value, (use_case.id, key)

        # Layers and criteria may only be appended (Phase 2 added the
        # evidence-only recent_change criterion and historical imagery).
        for list_key in ("data_layers", "criteria"):

            assert len(new[list_key]) >= len(old[list_key])

            for old_item, new_item in zip(old[list_key], new[list_key]):
                if old_item["id"] in CHANGED_ITEMS:
                    assert new_item["id"] == old_item["id"]
                    continue
                for key, value in old_item.items():
                    assert new_item[key] == value, (use_case.id, list_key, old_item["id"], key)

            for added in new[list_key][len(old[list_key]):]:
                if list_key == "criteria":
                    assert added["weight"] == 0, (use_case.id, added["id"])


def test_data_layer_available_matches_registry():

    for layer_id in DATA_LAYERS:
        assert data_layer_available(layer_id) == data_registry.is_available(layer_id)

    assert data_layer_available("no_such_layer") is False


# ============================================================
# REGISTRY CONSISTENCY
# ============================================================

def test_every_layer_source_exists():

    for layer in data_registry.LAYERS.values():
        if layer.source is not None:
            assert layer.source in data_registry.SOURCES, layer.id


def test_every_use_case_layer_is_registered():

    for use_case in USE_CASES.values():

        for layer_id in use_case.data_layers:
            assert layer_id in data_registry.LAYERS, (use_case.id, layer_id)

        for criterion in use_case.criteria:
            assert criterion.data_layer in data_registry.LAYERS, (use_case.id, criterion.id)


def test_every_source_has_full_metadata():

    for source in data_registry.SOURCES.values():

        for field in dataclasses.fields(source):

            value = getattr(source, field.name)

            if field.name in ("limitations",):
                assert value, (source.id, field.name)
            elif isinstance(value, str):
                assert value.strip(), (source.id, field.name)

        assert source.cost in ("free", "metered", "paid")


def test_capabilities_are_snake_case_ids():

    for layer in data_registry.LAYERS.values():

        assert layer.capabilities, layer.id

        for capability in layer.capabilities:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", capability), (layer.id, capability)


def test_layers_without_provider_are_unavailable():

    for layer in data_registry.LAYERS.values():

        if layer.source is None:
            assert not data_registry.is_available(layer.id)
            assert data_registry.resolve(layer.id) is None
            assert data_registry.source_label(layer.id) is None


# ============================================================
# LOOKUP
# ============================================================

def test_resolve_returns_the_provider():

    assert data_registry.resolve("roads").id == "osm_overpass"
    assert data_registry.resolve("satellite_imagery").id == "sentinel_2_planetary_computer"
    assert data_registry.resolve("flood_risk").id == "sentinel_1_rtc_planetary_computer"
    assert data_registry.resolve("zoning") is None
    assert data_registry.resolve("no_such_layer") is None


def test_layers_for_capability_lists_available_first():

    assert [l.id for l in data_registry.layers_for("road_access")] == ["roads"]
    assert [l.id for l in data_registry.layers_for("flood_exposure")] == ["flood_risk"]
    assert data_registry.layers_for("telepathy") == []

    land_area = [l.id for l in data_registry.layers_for("land_area")]

    assert set(land_area) == {"building_footprints", "land_parcels"}


def test_source_label_uses_layer_note():

    assert data_registry.source_label("roads") == "OpenStreetMap (Overpass)"
    assert data_registry.source_label("land_parcels") == (
        "OpenStreetMap land-use tags (not cadastral parcels)"
    )


def test_layer_label_falls_back_to_id():

    assert data_registry.layer_label("roads") == "Road network"
    assert data_registry.layer_label("mystery") == "mystery"


# ============================================================
# PUBLIC DESCRIPTION
# ============================================================

def test_describe_available_layer_includes_source_metadata():

    roads = data_registry.describe_layer("roads")

    assert roads["available"] is True
    assert "road_access" in roads["capabilities"]
    assert roads["limitations"]

    source = roads["source"]

    for key in ("id", "name", "provider", "spatial_resolution", "temporal_resolution",
                "coverage", "freshness", "cost", "latency", "license", "limitations"):
        assert key in source


def test_describe_unavailable_layer_has_no_source():

    zoning = data_registry.describe_layer("zoning")

    assert zoning["available"] is False
    assert zoning["source"] is None
    assert zoning["capabilities"] == ["zoning"]


def test_use_cases_endpoint_exposes_registry_metadata(client):

    body = client.get("/use-cases").json()

    land = next(u for u in body["use_cases"] if u["id"] == "land_acquisition")

    layers = {l["id"]: l for l in land["data_layers"]}

    assert layers["roads"]["source"]["cost"] == "free"
    assert layers["zoning"]["source"] is None
    assert [s["id"] for s in layers["flood_risk"]["other_sources"]] == [
        "jrc_gsw_planetary_computer", "nasa_power_climatology",
    ]


# ============================================================
# PROVIDER OUTAGE / SWAP
# ============================================================

def test_disabling_a_provider_makes_its_layers_unmeasured(monkeypatch):
    """
    If a provider is switched off, every layer it supplies becomes
    unavailable and the criteria that need it are reported missing,
    not scored as zero.
    """

    monkeypatch.setitem(
        data_registry.SOURCES, "osm_overpass",
        dataclasses.replace(data_registry.OSM_OVERPASS, available=False),
    )

    assert not data_registry.is_available("roads")
    assert not data_layer_available("land_parcels")

    # Nothing was fetched, so no layer is loaded.
    context = new_context(LAT, LON, 1)

    result = score_weighted_criteria(
        {"latitude": LAT, "longitude": LON, "area_m2": 1000},
        AnalysisSpec(query="q", intent_type="ev_charging_site_selection"),
        EV_CHARGING, context,
    )

    assert result["score"] is None
    assert "road_access" in result["missing_data"]
    assert result["criteria"]["road_access"]["note"] == "No road network data connected."


def test_evidence_section_reads_sources_from_registry(client, deepseek, overpass, monkeypatch):

    from tests.conftest import land_elements, planner_reply

    monkeypatch.setitem(
        data_registry.SOURCES, "osm_overpass",
        dataclasses.replace(data_registry.OSM_OVERPASS, name="Test map provider"),
    )

    deepseek(planner_reply("land_acquisition"))
    overpass(land_elements())

    body = client.post("/analyze", json={"query": "land in Adyar"}).json()

    sources = {e["id"]: e["source"] for e in body["evidence"]}

    assert sources["roads"] == "Test map provider"
    # Layer-specific note still wins over the provider name.
    assert sources["land_parcels"] == "OpenStreetMap land-use tags (not cadastral parcels)"
    assert sources["zoning"] is None
