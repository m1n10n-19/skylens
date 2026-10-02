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

## Candidates

"On Planetary Computer" means the same free catalogue and client code
SkyLens already uses: no new account, key or dependency.

| Source | Value for SkyLens | Access | Notes |
|---|---|---|---|
| Copernicus DEM / NASADEM (SRTM) | High: elevation, slope, low-lying land | On Planetary Computer (`cop-dem-glo-30`, `nasadem`) | Terrain, not flood risk: label as "low-lying", never "flood risk" |
| Sentinel-1 radar | High: sees through clouds (monsoon gaps), flood and water mapping | On Planetary Computer (`sentinel-1-rtc`, `sentinel-1-grd`) | Different physics from optical; needs its own thresholds |
| JRC Global Surface Water | High: where water has occurred since 1984 (flood history) | On Planetary Computer (`jrc-gsw`) | |
| ESA WorldCover / Esri land cover | Medium: annual land-cover classes, cross-check for spectral change | On Planetary Computer (`esa-worldcover`, `io-lulc-annual-v02`) | 1-2 years behind |
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

1. Copernicus DEM (terrain and low-lying land).
2. Sentinel-1 and JRC Global Surface Water (flood and water evidence that works through clouds).
3. Open Charge Map (EV competition).
4. WorldPop (population around commercial sites).
5. Check access for TNGIS and India-WRIS before planning work on them.

## Adding a source

1. Add a `DataSource` (and, if new, a `DataLayer`) to `data_registry.py`
   with honest limitations. A layer with no source stays listed so its
   criteria are reported as not measured.
2. Add the evaluator in `criteria.py`; list every measurement key in
   `evidence.MEASUREMENTS` with its unit, claim and status.
3. Record which source supplied a result in the context's provenance
   (`source_id`, dates) so evidence names it.
4. Tests must run offline: fake the provider in `backend/tests/conftest.py`.
