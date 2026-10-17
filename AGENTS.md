# Harvest Radar — shared context for every AI agent

DubHacks 2026 project. Submissions due **Sun Oct 18, 10:30 AM PT**. Team of 3, each running an agent in parallel.
Tracks: SpaceX (real space data, built with Cursor), Perceptron (Mk1 family, image/video understanding), SideShift, Community Kitchen.

## Problem
Punjab farmers have a 15–18 day window between rice harvest and wheat sowing. Crop-residue machines
(Happy/Super Seeders, balers) are rented from custom hiring centres (CHCs), are scarce, and arrive late,
so farmers who can't get one burn the stubble (major driver of North India's autumn air pollution).
Existing tools are reactive (booking apps like Unnat Kisan / i-Khet, barely used) or punitive
(satellite fire detection for fines). Nobody forecasts **where machine demand will hit next**.

## What we build
Satellite imagery (Sentinel-2 / NASA HLS) of a Punjab district → per-field harvest stage + predicted harvest
date → machine demand per village zone per day → a CHC dispatch plan that beats first-come-first-served.
Perceptron reads the satellite time-lapse (when/where fields were harvested or burned). Web map shows it all.

Demo AOI: Sangrur, Punjab — `config/region.json` (bbox, dates, thresholds, assumptions). CHCs are SIMULATED (`config/chcs.json`).

## Architecture (data contracts are the API between people)
```
pipeline/ (Person A)        intelligence/ (Person B)               web/ (everyone, from ~10 PM)
STAC/Earthdata/FIRMS  -->   harvest_model -> demand -> routing -->   MapLibre app reads data/{out|mock}/*.json
writes data/out/:           writes: status.json demand.json routing.json
 fields.geojson zones.geojson
 timeseries.json weather.json   vision/ (Person C): Perceptron reads frames/time-lapse
 fires.json frames/frames.json  writes: insights.json (+ optional perceptron fields)
```
Schemas: `contracts/*.schema.json`. Validate: `python scripts/validate_contracts.py data/out`.
Mock data for every file already exists in `data/mock/` (run `python scripts/make_mock_data.py && python intelligence/run_all.py --data data/mock` to regenerate).

## Rules for agents
1. **Stay in your folder.** A → `pipeline/`, B → `intelligence/`, C → `vision/`. `web/` is built together later. Don't edit another person's folder.
2. **Contracts are shared.** Changing a schema in `contracts/` = tell the team first, update mock generator + validator in the same PR.
3. Never break the mock path: `intelligence/run_all.py --data data/mock` and the web app on mock data must always work.
4. Outputs are static files. The web app never calls live APIs during the demo.
5. Secrets only in `.env` (gitignored). Copy `.env.example`.
6. Small commits on your branch (`a/pipeline`, `b/intel`, `c/vision`, later `web`), merge to `main` often via PR. Run the validator before pushing.
7. Large rasters/caches go in `data/cache/` (gitignored). Commit only `data/out/` JSON + JPEG frames (keep each < ~1 MB).
8. Be honest in UI copy: label simulated CHCs and assumptions from `config/region.json`.

## Perceptron API (verified from docs.perceptron.inc)
- `pip install "perceptron>=0.4.0"`, key in `PERCEPTRON_API_KEY`, model `perceptron-mk1.5` (or pin `perceptron-mk1`)
- annotations: `vision_config={"annotation_format": "polygon"|"box"|"point"|"clip"}`; returned as markup in text
- coordinates normalized 0–1000, origin top-left. Helpers: `vision/perceptron_client.py`
- $50 budget total. Client enforces `PERCEPTRON_BUDGET_USD` (default 40) and logs spend.

## Commands
```
pip install -r intelligence/requirements.txt      # vision/ and pipeline/ have their own requirements.txt
python scripts/make_mock_data.py
python intelligence/run_all.py --data data/mock
python scripts/validate_contracts.py data/mock
python vision/validate_perceptron.py            # first-hour Perceptron test
```
Background, rationale, sources and pitch: `docs/PROJECT.md`.
Task briefs: `docs/tasks/A-pipeline.md`, `B-intelligence.md`, `C-vision.md`, `D-web.md`.
