# Where Overfield's data comes from

Generated 2026-10-18 from `web/public/data/sources.json`, the same list the site's Data page and the developer API (`/v1/sources`) serve. Every entry was checked against the code at the file and line it names.

Overfield cross-checks several sources because no single one is enough on its own. Imagery says what a field looks like today, weather says what is coming, fire feeds say what happened overnight, soil and terrain say why two neighbouring fields differ, and official statistics say whether the satellite reading can be trusted. Every check in a morning plan traces back to the sources below.

**Counts:** 33 sources; 24 need no key; 9 need a key or an account.

## Satellite imagery

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Sentinel-2 L2A surface reflectance](https://earth-search.aws.element84.com/v1) | ESA Copernicus, served from the AWS Open Data registry | Every pass over the farms: NDVI and NBR per field, the harvest curve, burn scars, flood and stress cues, the time-lapse frames. | every 2 to 5 days per field (clouds permitting) | 10 m | Copernicus Sentinel data: free, full and open | no | fetched by the pipeline and cached |
| [Earth Search STAC catalog](https://earth-search.aws.element84.com/v1) | Element 84 (AWS Open Data) | Finding every Sentinel-2 scene over the landscape by date and cloud cover. | continuous |  | Free STAC API over open data (Element 84 terms) | no | live from the pipeline |

- **Sentinel-2 L2A surface reflectance.** The one free satellite that sees every field at 10 m every few days; the whole season is read from it. (config/region.json:13, contracts/timeseries.schema.json:8, data/out/run.json, docs/PROJECT.md:105 and 62 more)
- **Earth Search STAC catalog.** A free catalog that lets the pipeline ask for scenes instead of crawling buckets. (config/region.json:13, contracts/timeseries.schema.json:8, data/out/run.json, docs/PROJECT.md:105 and 70 more)

## Base map

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Esri World Imagery](https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer) | Esri (ArcGIS Online) | The sub-metre aerial basemap under the farm map; what shows when you zoom past what Sentinel-2 can resolve. | updated by Esri over months to years | 0.3 to 1 m | Esri ArcGIS Online terms of use; attribution required | no | live in the browser |

- **Esri World Imagery.** Sentinel-2 is 10 m per pixel; past zoom 15 a farmer needs to see their own buildings and lanes. (docs/tasks/D-web.md:11, web/src/map.js:14, web/src/map.js:24, web/src/map.js:24-31 and 6 more)

## Weather

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Open-Meteo forecast](https://api.open-meteo.com/v1/forecast) | Open-Meteo | The 14-day daily forecast: rain, minimum and maximum temperature, that every plan reads. | hourly runs; the plan reads the morning run | about 10 km | CC BY 4.0 (Open-Meteo); free API for non-commercial use | no | live in the pipeline |
| [Open-Meteo previous runs](https://previous-runs-api.open-meteo.com/v1/forecast) | Open-Meteo | The forecast as it was issued on an earlier morning, so a replay judges the plan with what was known then, not hindsight. | per issued run | about 10 km | CC BY 4.0 (Open-Meteo); free API for non-commercial use | no | fetched for replays |
| [Open-Meteo multi-model ensemble](https://api.open-meteo.com/v1/forecast?models=...) | Open-Meteo | Eight weather models read side by side; the plan says how many agree on heavy rain or frost. | daily | 2 to 25 km by model | CC BY 4.0 (Open-Meteo); free API for non-commercial use | no | live in the pipeline |
| [ECMWF IFS 0.25 degree](https://open-meteo.com/en/docs/ecmwf-api) | ECMWF | Ensemble member. | 4 runs a day | 25 km | ECMWF open data, CC BY 4.0 | no | via Open-Meteo |
| [NOAA GFS and HRRR](https://open-meteo.com/en/docs/gfs-api) | NOAA NCEP | Ensemble member (HRRR for the first 48 h over the US). | 4 runs a day (HRRR hourly) | 3 km near term, 25 km global | US government work, public domain | no | via Open-Meteo |
| [DWD ICON](https://open-meteo.com/en/docs/dwd-api) | Deutscher Wetterdienst | Ensemble member. | 4 runs a day | 11 km global | DWD open data (German open data licence) | no | via Open-Meteo |
| [Canadian GEM](https://open-meteo.com/en/docs/gem-api) | Canadian Meteorological Centre | Ensemble member. | 4 runs a day | 15 km global | Environment and Climate Change Canada data licence | no | via Open-Meteo |
| [Meteo-France ARPEGE and AROME](https://open-meteo.com/en/docs/meteofrance-api) | Meteo-France | Ensemble member. | 4 runs a day | 25 km global | Meteo-France open data (Etalab) | no | via Open-Meteo |
| [UK Met Office global](https://open-meteo.com/en/docs/ukmo-api) | UK Met Office | Ensemble member. | 4 runs a day | 10 km global | UK Met Office data under CC BY-SA 4.0 | no | via Open-Meteo |
| [JMA GSM and MSM](https://open-meteo.com/en/docs/jma-api) | Japan Meteorological Agency | Ensemble member. | 4 runs a day | 20 km global | JMA open data | no | via Open-Meteo |
| [NOAA National Blend of Models](https://open-meteo.com/en/docs/gfs-api) | NOAA National Weather Service | Ensemble member (CONUS). | hourly | 2.5 km | US government work, public domain | no | via Open-Meteo |
| [NASA POWER daily](https://power.larc.nasa.gov/api/temporal/daily/point) | NASA Langley Research Center | Daily temperature and rain per county for every training year of the yield model. | daily, about a week behind | 0.5 degree | NASA open data, no restrictions | no | fetched once and cached |

- **Open-Meteo forecast.** Free, no key, global, with the same fields everywhere Overfield might run. (config/region.json:19, config/region.json:43, config/region.json:43-45, config/region.json:44 and 30 more)
- **Open-Meteo previous runs.** A warning only counts if it was possible on the day; the archive of issued forecasts proves it. (intelligence/common.py:59, intelligence/common.py:60, pipeline/replay_scan.py:1, pipeline/replay_scan.py:16 and 17 more)
- **Open-Meteo multi-model ensemble.** One model can be wrong about one storm; agreement across eight is what a farmer can act on. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:107 and 19 more)
- **ECMWF IFS 0.25 degree.** The global reference model. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:107 and 21 more)
- **NOAA GFS and HRRR.** The US model with the finest short-range detail. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:107 and 21 more)
- **DWD ICON.** An independent global model from a second agency. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:107 and 21 more)
- **Canadian GEM.** A third agency's global model. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:107 and 21 more)
- **Meteo-France ARPEGE and AROME.** A fourth agency's global model. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:108 and 9 more)
- **UK Met Office global.** A fifth agency's global model. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:108 and 9 more)
- **JMA GSM and MSM.** A sixth agency's global model. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:107 and 21 more)
- **NOAA National Blend of Models.** The forecasters' own blend for the US. (data/out/weather_ensemble.json, docs/tasks/E-yield-model.md:32-33, intelligence/LIVE_SETUP.md:105-113, intelligence/LIVE_SETUP.md:108 and 9 more)
- **NASA POWER daily.** The yield model needs the same weather history for 99 counties over 7 years; POWER gives it without a key. (data/out/yield_model.json, docs/tasks/A-pipeline.md:20, docs/tasks/E-yield-model.md:139, docs/tasks/E-yield-model.md:64 and 33 more)

## Soil

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [SoilGrids 2.0](https://rest.isric.org/soilgrids/v2.0/properties/query) | ISRIC World Soil Information | Organic carbon, clay, sand and pH at 0 to 30 cm for every field; the texture class in the yield notes. | static (2020 release) | 250 m | CC BY 4.0 | no | fetched once per field and cached |

- **SoilGrids 2.0.** Soil explains why two neighbouring fields yield differently under the same sky. (data/out/field_env.json, docs/tasks/E-yield-model.md:140, docs/tasks/E-yield-model.md:48-50, docs/tasks/E-yield-model.md:93 and 15 more)

## Terrain

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Open-Meteo elevation (Copernicus DEM GLO-90)](https://api.open-meteo.com/v1/elevation) | Open-Meteo, DEM from Copernicus | Elevation and a 100 m slope stencil per field; aspect in the yield notes. | static | 90 m | Copernicus DEM: free and open; API CC BY 4.0 | no | fetched once per field and cached |

- **Open-Meteo elevation (Copernicus DEM GLO-90).** Slope and aspect change how fast a field drains and dries. (data/out/field_env.json, docs/tasks/E-yield-model.md:140, docs/tasks/E-yield-model.md:49-50, docs/tasks/E-yield-model.md:93 and 16 more)

## Yields and crop statistics

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [USDA NASS QuickStats county yields](https://quickstats.nass.usda.gov/api) | USDA National Agricultural Statistics Service | Measured county corn and soybean yields for all 99 Iowa counties, 2008 to 2025: the training set of the county yield model (yield_train.py). | yearly | county | US government work, public domain; free API key | yes | fetched with NASS_API_KEY and cached; the committed yield_model.json was trained on it |
| [CropNet USDA crop dataset](https://huggingface.co/datasets/CropNet/CropNet) | CropNet (Hugging Face) | The same USDA county yield tables for 2016 to 2022, the fallback training set when no NASS key is set; the committed model no longer uses it. | static (2016 to 2022) | county | CC BY-NC 4.0, research use only | no | downloaded once and cached; fallback only |

- **USDA NASS QuickStats county yields.** The ground truth for yield in the US, public domain; eighteen seasons lift the held-out R² to 0.62 for corn and 0.71 for soybeans. (.env.example:30, docs/tasks/E-yield-model.md:137-138, docs/tasks/E-yield-model.md:148, docs/tasks/E-yield-model.md:63 and 17 more)
- **CropNet USDA crop dataset.** A ready-made, cleaned table of USDA county yields that lets the model train with no key at all. (data/out/yield_model.json, docs/tasks/E-yield-model.md:110, docs/tasks/E-yield-model.md:120, intelligence/tests/test_yield_calibration.py:13 and 12 more)

## Field boundaries and places

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [US Census Bureau county gazetteer 2023](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/) | US Census Bureau | County centroids that place each county's weather history. | yearly | county | US government work, public domain | no | downloaded once and cached |
| [Fields of The World](https://fieldsofthe.world/) | fieldsofthe.world (Source Cooperative) | Open global field boundaries, drawn on the yield map for comparison with the roster's farms. | yearly (2024 predictions) | 10 m | CC BY 4.0 | no | extracted once from the Iowa parquet (145 polygons in the demo box) and committed as data/out/ftw_fields_2024.geojson; a display overlay, not an input to any plan |

- **US Census Bureau county gazetteer 2023.** The yield model needs one point per county for NASA POWER. (data/out/yield_model.json, intelligence/tests/test_yield_train.py:21, intelligence/yield_train.py:160, intelligence/yield_train.py:160-168 and 7 more)
- **Fields of The World.** Shows how the farms would be found anywhere on Earth without a hand-drawn roster. (contracts/fields.schema.json:29, data/out/ftw_fields_2024.geojson, docs/tasks/E-yield-model.md:109, docs/tasks/E-yield-model.md:119 and 9 more)

## Fire

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [NASA FIRMS VIIRS active fires](https://firms.modaps.eosdis.nasa.gov/api/area/csv/) | NASA FIRMS (LANCE / EOSDIS) | Near-real-time hotspots that confirm a burn scar or raise the fire check. | every 3 hours | 375 m | NASA open data; free MAP_KEY | yes | live in the pipeline |

- **NASA FIRMS VIIRS active fires.** A dark patch in imagery is only a fire if something was burning; FIRMS says whether it was. (.env.example:4, contracts/fires.schema.json:4-8, data/out/fires.json, docs/PROJECT.md:108 and 38 more)

## Geocoding

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/search) | OpenStreetMap Foundation | Turns a farm address into a point at sign-up so the right field is matched. | continuous | address | OpenStreetMap data under ODbL; Nominatim usage policy (light use, identify yourself) | no | live at sign-up |

- **OpenStreetMap Nominatim.** Free, global, no key; one request per sign-up. (docs/superpowers/specs/2026-10-18-product-accounts-api-design.md, intelligence/LIVE_SETUP.md:92-96, intelligence/LIVE_SETUP.md:94, intelligence/inbound_server.py:132 and 21 more)

## Vision model

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Perceptron Mk1.5 vision model](https://docs.perceptron.inc) | Perceptron Inc. | Reads the true-colour frames for standing crop, change between passes, flood and stress cues (insights.json). | per pass | frame | Paid API (per-token pricing); key required | yes | offline, budget-capped |

- **Perceptron Mk1.5 vision model.** A second, independent reading of the imagery to cross-check the index curves. (.env.example:1, .env.example:1-3, .env.example:2, .env.example:3 and 92 more)

## Texts and messages

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Photon 2.0 SMS and iMessage](https://api.photon.codes) | Photon (photon.codes) | The morning text to every farmer and the officer, and the inbound questions. | every morning |  | Photon terms of service; key required | yes | live from the morning queue only |

- **Photon 2.0 SMS and iMessage.** Farmers read texts; no app to install. (.env.example:12, .env.example:13-16, .env.example:17, .env.example:17-18 and 80 more)

## Voice calls

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Twilio Programmable Voice](https://api.twilio.com) | Twilio | Places the morning call and bridges the voice session. | every morning |  | Twilio terms of service; key required | yes | live from the morning queue only |
| [Grok Voice realtime](https://api.x.ai) | xAI | The voice on the call: reads the plan and answers questions about that farm only. | every morning and on demand |  | xAI terms; key required | yes | live on calls only |

- **Twilio Programmable Voice.** A phone call reaches people who do not read texts, in their language. (.env.example:12, .env.example:13, .env.example:13-16, .env.example:16 and 63 more)
- **Grok Voice realtime.** A natural voice in the farmer's language with the plan as its only facts. (.env.example:10, .env.example:19, .env.example:19-20, .env.example:20 and 36 more)

## Email

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| Email (SMTP) | The sender's own mailbox provider | The optional third channel for the morning plan. | every morning |  | The mailbox provider's terms | yes | live from the morning queue only |

- **Email (SMTP).** Some people asked for email at sign-up. (.env.example:34, .env.example:34-38, .env.example:35, .env.example:37 and 21 more)

## Language model

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [Grok chat](https://api.x.ai/v1) | xAI | Translation of the plan words and the officer's questions over the finished files. | on demand |  | xAI terms; key required | yes | live on demand |

- **Grok chat.** Never invents a measurement: it only rewords facts from plans.json and route.json. (.env.example:1-3, .env.example:10, .env.example:10-11, .env.example:11 and 99 more)

## Validation against official numbers

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [USDA NASS Crop Progress](https://quickstats.nass.usda.gov/api) | USDA National Agricultural Statistics Service | Weekly percent harvested for central Iowa, used to check the satellite harvest curve against the official survey. | weekly in season | agricultural district | US government work, public domain; free API key | yes | code in place (validate_usda.py --refresh); the published run scored against the built-in sample rows, the real fetch needs NASS_API_KEY |

- **USDA NASS Crop Progress.** The satellite harvest model is only trusted because it can be scored against the official weekly numbers. (.env.example:30, config/region.json:32, data/mock/usda_crop_progress.json, data/mock/validation_usda.json and 36 more)

## Hosting and accounts

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [GitHub Pages](https://ishaanmakam.github.io/harvest-radar/) | GitHub | Hosts the site and the published plan files that the developer API serves. | on every merge to main |  | GitHub terms of service | no | live |

- **GitHub Pages.** Free static hosting straight from the repository. (.github/workflows/digest.yml:1-30, .github/workflows/pages.yml:1, .github/workflows/pages.yml:1-48, .github/workflows/pages.yml:27 and 12 more)

## The globe

| Source | Provider | What we use it for | Cadence | Resolution | Licence | Key | Live or cached |
|---|---|---|---|---|---|---|---|
| [CelesTrak orbital elements](https://celestrak.org/NORAD/elements/gp.php) | CelesTrak | Orbital elements for Sentinel-2A/B/C, Landsat 8/9, Terra, Aqua and the Starlink shell on the globe, propagated with SGP4. | daily snapshot (scripts/fetch_tles.py) |  | Free for any use with attribution (CelesTrak) | no | snapshot in the repo; propagated in the browser |
| [three.js Earth textures](https://github.com/mrdoob/three.js/tree/master/examples/textures/planets) | three.js (from NASA Blue Marble) | The day, night-lights, normal and specular maps on the globe. | static | 2048 px | MIT (three.js); NASA imagery public domain | no | bundled |

- **CelesTrak orbital elements.** The satellites on the globe are real: their positions come from the same elements trackers use. (scripts/fetch_tles.py, web/src/globe/satellites.js, web/public/sats/imaging.json, web/public/sats/starlink.json)
- **three.js Earth textures.** Public-domain NASA imagery packaged for the renderer. (web/README.md:63, web/package.json:14, web/src/assets/earth/, web/src/globe/globe.js:105-112 and 5 more)

## Not sources

Every entry was checked against the code at the listed file and line. Starlink is drawn on the globe for connectivity context; it is not a data source.

Not used, in case anyone assumes otherwise: Starlink (drawn on the globe for connectivity context only), Landsat, Terra and Aqua imagery (their orbits are drawn, their pixels are not read), Sentinel-1 radar, MODIS, commercial imagery or drones, weather stations or soil sensors, direct agency weather feeds (every model arrives through Open-Meteo), Google or Mapbox tiles, and farmer-entered measurements. Facts in a plan come from plans.json and route.json only.
