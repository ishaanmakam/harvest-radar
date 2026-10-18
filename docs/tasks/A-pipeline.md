# Person A — satellite data pipeline (`pipeline/`)

Paste into your agent: "Read AGENTS.md and docs/tasks/A-pipeline.md, then build it step by step, validating against contracts/ after each output."

Goal: produce REAL versions of the mock files in `data/mock/` into `data/out/`, for the AOI in `config/region.json`.

Spec rules for the pipeline: the demo landscape is Story County, Iowa, with at least 4 clear frames. `weather.json` must include the **forecast** (the harvest-before-rain check needs any day ≥ 15 mm in the next 3 days), and `fires.json` must cover at least the **last 5 days**.

Build these scripts (Python; deps in `pipeline/requirements.txt`: pystac-client odc-stac rasterio shapely pyproj scikit-image numpy pillow imageio imageio-ffmpeg requests):
1. `s01_search.py` — STAC search Sentinel-2 L2A over bbox, season_start→as_of, cloud < 70%.
   Fastest source: Earth Search `https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a` (no auth).
   Print `item.assets.keys()` once to confirm band names (red, green, blue, nir, swir22, scl). Optional: NASA HLS via Earthdata (needs EARTHDATA login).
2. `s02_cube.py` — `odc.stac.load(..., crs="EPSG:4326", resolution=0.0001, groupby="solar_day")`. Apply scale/offset from asset `raster:bands` metadata.
   Valid pixels: SCL in {2,4,5,6,7} (keep 2 = dark, burn scars are dark). Compute NDVI=(nir-red)/(nir+red), NBR=(nir-swir22)/(nir+swir22). Cache to `data/cache/cube.npz`.
3. `s03_fields.py` — field polygons (fallback; Person B may replace with Perceptron polygons): segment a stack of peak + October NDVI dates
   with skimage felzenszwalb, keep segments 0.3–15 ha with peak NDVI ≥ 0.5, vectorize (rasterio.features.shapes), simplify, area via pyproj Geod.
   Zones = 3x3 grid over bbox (same ids/names as mock: Z01..Z09). Write `fields.geojson`, `zones.geojson`.
4. `s04_timeseries.py` — per field per date mean NDVI/NBR over valid pixels; null if clear fraction < 0.6. Write `timeseries.json`.
5. `s05_frames.py` — true-colour JPEG per date with clear fraction ≥ 0.5 (2–98% stretch), north-up, aligned to bbox. Write `frames/frames.json` (+ optional timelapse.mp4).
6. `s06_weather.py` — NASA POWER daily (PRECTOTCORR, T2M_MAX, T2M_MIN, community=AG) + Open-Meteo 14-day forecast (precipitation_sum, precipitation_probability_max). Write `weather.json`.
7. `s07_fires.py` — NASA FIRMS area API (needs free FIRMS_MAP_KEY): VIIRS_SNPP_NRT, VIIRS_NOAA20_NRT, VIIRS_NOAA21_NRT in ≤10-day chunks from Sep 15. Point-in-polygon → field_id. Write `fires.json`.

Milestones: by 8 PM one clean cube cached + timeseries.json valid. By midnight all files valid with `--strict`.
Then hand off: `python intelligence/run_all.py --data data/out`.
Stretch: run the same pipeline on a US region and compare harvest dates to USDA Crop Progress (validation slide).
