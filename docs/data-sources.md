# SkyLens data sources

Status of the external data sources SkyLens uses or could use. The
authoritative list of what is connected is `backend/data_registry.py`:
every source there has its resolution, coverage, freshness, cost and
limitations, and analyses resolve sources through it.

Last reviewed: October 2026.

## In use

| Source | Used for | Access |
|---|---|---|
| OpenStreetMap via Overpass | Roads, buildings, POIs, land-use tags, EV chargers, parking | Public Overpass servers; cached 24 h (`overpass.py`) |
| Sentinel-2 L2A (Microsoft Planetary Computer) | Change detection from 2017, recent change on shortlisted sites, context imagery | STAC `sentinel-2-l2a`; 10 m; free, no key |
| Landsat Collection 2 Level-2 (Microsoft Planetary Computer) | Change detection for periods before 2017 (back to 1984) | STAC `landsat-c2-l2`; 30 m; free, no key. Landsat 7 after May 2003 is a last resort (permanent data gaps) |
| Sentinel-1 RTC radar (Microsoft Planetary Computer) | Standing water on candidate sites in the location's wet seasons (last 3 plus the current one), against a dry-season reference; part of the scored "Flood exposure (observed)" | STAC `sentinel-1-rtc`; 10 m; free, no key. 2 images per season; all-or-nothing within a 40 s budget so scores are reproducible. Misses floods that drain between passes; under-detects water among buildings; gaps in some months (none over Chennai in Dec 2023) |
| JRC Global Surface Water (Microsoft Planetary Computer) | Water history of candidate sites, 1984-2020; part of flood exposure, and a "filled-in water body?" check | STAC `jrc-gsw`; 30 m; free, no key. Ends 2020; misses most short floods |
| NASA POWER climatology | Choosing each location's wet and dry months (wettest and driest 3 consecutive months) | Free API, no key; cached per 0.5° cell. Falls back to June-December wet, January-March dry |
| ESA WorldCover (Microsoft Planetary Computer) | Finding open land (tree, shrub, grass, crop, bare) not tagged on OpenStreetMap, as extra candidates in EV, commercial and land analyses; split at roads; land vegetated in 2021 that looks built-up in the latest Sentinel-2 image is left out | STAC `esa-worldcover`; 10 m; 2021; free (CC BY 4.0). Bare ground and built-up look alike at 10 m; patches are not legal plots |
| Tavily web search (on request) | "What's reported about this area": exact quotes from news and government pages about infrastructure projects, flooding, land issues and one analysis-specific topic, with URL, title, dates and source type; status "reported", never scored | `TAVILY_API_KEY` (and `WEB_RESEARCH_PROVIDER=tavily`); 3 searches per click (1 credit each; 1,000 free credits a month); cached 24 h. Only the place name and topic are sent. Provider-estimated dates can be wrong, so dates in article URLs are preferred |
| OpenStreetMap buildings and land in use | Mapped buildings (open land covered >=20% is not ranked; >=5% lowers vacancy with a warning); campuses, schools, hospitals, places of worship, cemeteries, military land, parks and sports grounds (sites >=50% inside are not ranked) | Same Overpass request. Residential/commercial/industrial zones are not treated as in use (they contain empty plots) |
| Microsoft ML building footprints (Microsoft Planetary Computer) | Buildings missing from OpenStreetMap (common in India), in the same building checks | STAC `ms-buildings`; ODbL; needs `pyarrow` + `adlfs`. One ~45 MB file per ~75 km tile (Chennai: 500k buildings), split once into small cells on local disk (~+110 MB memory while splitting, one tile at a time); about 30 s for the first question in a region, then about 1 s. Imagery 2014-2023: newer buildings are missed. On hosts whose disk is wiped on restart the first question pays again |
| OpenStreetMap protected areas | Overlap with protected areas, reserved forests and wetlands; sites ≥95% inside a protected area are not ranked; a records check is always listed | Same Overpass request. Records only some protected areas: absence does not prove land is unprotected |
| Copernicus DEM GLO-30 (Microsoft Planetary Computer) | Elevation, height relative to surroundings and slope of shortlisted open-land sites; low-lying warning (evidence only, not scored) | STAC `cop-dem-glo-30`; 30 m; free, no key. Surface model (roofs and trees included), so buildings are not measured; ±2 m relative accuracy; data from 2011-2015. Not flood risk |

## Candidates

"On Planetary Computer" means the same free catalogue and client code
SkyLens already uses: no new account, key or dependency.

| Source | Value for SkyLens | Access | Notes |
|---|---|---|---|
| NASADEM (SRTM) | Low: older alternative to the Copernicus DEM now in use | On Planetary Computer (`nasadem`) | |
| Esri annual land cover | Medium: newer annual land-cover maps than WorldCover 2021 | On Planetary Computer (`io-lulc-annual-v02`) | Could replace the 2021 map |
| WDPA / Protected Planet | Official global protected areas | Free API, but the licence forbids commercial use without permission | Needs a licence before SkyLens can use it |
| Open Charge Map | High for EV: OSM charger coverage is incomplete | Free REST API, key required | CC BY-SA 4.0 |
| WorldPop | High for commercial sites: fills the "population" unknown | Free downloads and stats API; not on Planetary Computer | Residents, not footfall: label accordingly. Large rasters |
| NASA FIRMS / MODIS fire | Low now; disaster and agriculture later | FIRMS API (free key); MODIS fire on Planetary Computer (`modis-14A1-061`) | |
| Global Forest Watch | Low now; conservation later | API | Overlaps SkyLens change detection |
| GBIF | Low now; ecological sensitivity later | API; on Planetary Computer (`gbif`) | |
| NASA Earthdata | Portal, not a dataset | Earthdata login | Useful parts (SRTM, MODIS) are on Planetary Computer |
| TNGIS (Tamil Nadu) | Possibly high: admin and environmental layers, maybe land use | Unknown | Check API, licence and coverage first; Tamil Nadu only |
| India-WRIS | Medium: rivers, basins, hydrology | Mostly portal / WMS | Check licence and machine access first |
| data.gov.in | Low: mostly district-level statistics | API with key | Context, not site-level measurement |

## Suggested order

1. Open Charge Map (EV competition).
2. WorldPop (population around commercial sites).
3. Check access for TNGIS and India-WRIS before planning work on them.

## Adding a source

1. Add a `DataSource` (and, if new, a `DataLayer`) to `data_registry.py`
   with honest limitations. A layer with no source stays listed so its
   criteria are reported as not measured.
2. Add the evaluator in `criteria.py`; list every measurement key in
   `evidence.MEASUREMENTS` with its unit, claim and status.
3. Record which source supplied a result in the context's provenance
   (`source_id`, dates) so evidence names it.
4. Tests must run offline: fake the provider in `backend/tests/conftest.py`.
