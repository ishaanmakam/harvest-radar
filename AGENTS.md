# Harvest Radar — shared context for every AI agent

DubHacks 2026. Submissions due **Sun Oct 18, 10:30 AM PT**. Team of 3, each running an agent in parallel.

**Source of truth for the product: `docs/superpowers/specs/2026-10-17-field-inspector-design.md`.**
If anything here disagrees with the spec, the spec wins. Background, sources and pitch: `docs/PROJECT.md`.

## Product (summary of the spec)
An autonomous field inspector for small family farms. The satellite season does the visiting; an extension officer
and each farmer get a morning plan, and a call or text when something needs action. The demo landscape is Story County, Iowa (`config/region.json`); `data/mock` stays the Sangrur stand-in.

- Every farm gets a plan **every morning**, quiet days included, with all 8 checks and their status
  (`clear` / `watch` / `act_now`): `harvest_ready`, `harvest_before_rain`, `establishment`, `burn_scar`, `fire`,
  `flood`, `stress`, `visible_change`. The farm's `flag` (`fine` / `watch` / `act_now`) is the worst check.
- The officer gets a route: `act_now` farms first (fire, flood, harvest-before-rain, new burn scar), then `watch`
  farms (stress, visible change, harvest readiness, establishment, older burn scar). `fine` farms are not stops.
- Delivery: **Grok Voice** call + **Photon** SMS to every farmer in the roster; Grok Voice + Photon SMS of the route
  to the officer. Officer can ask questions by voice or Photon iMessage; farmers can call in about their own farm only.
- The map (Vite + MapLibre) shows the time-lapse, every farm's flag, its full plan on click, and delivery status.
- Out of scope: Grok Imagine, Photon voice, machine-dispatch routing as the hero output, charging farmers.

## Workstreams and folders
| Who | Owns | Folder | Brief |
|---|---|---|---|
| Niranjan | Season pipeline: fields, zones, timeseries, frames, weather (rain + forecast), fires → `data/out/` | `pipeline/` | `docs/tasks/A-pipeline.md` |
| Ishaan | Decision layer (`plans.json`, `route.json`), roster, morning queue (`deliveries.json`), Grok Voice + Photon, inbound handlers | `intelligence/` | `docs/tasks/B-intelligence.md` |
| Srikar | Perceptron reader (`insights.json`) and the web map | `vision/`, `web/` | `docs/tasks/C-vision.md`, `docs/tasks/D-web.md` |

## Data flow
```
pipeline/ (Niranjan)  -> data/out: fields.geojson zones.geojson timeseries.json weather.json fires.json frames/
vision/   (Srikar)    -> data/out: insights.json (change notes, counts, flood/stress cues; perceptron polygons optional)
intelligence/ (Ishaan)-> data/out: status.json -> plans.json + route.json ; morning queue -> deliveries.json
web/      (Srikar)    <- reads the files above only (no live Perceptron/Grok calls)
```
Schemas: `contracts/*.schema.json` (plans/route/deliveries schemas are added by Ishaan's workstream, build step 1).
Validate: `python scripts/validate_contracts.py data/out`. Mock data for every existing file is in `data/mock/`.

## Build order (from the spec)
1. Generate plans + route from the stand-in season (mock) and validate them.
2. Dry-run the morning queue, then send Grok Voice + Photon to the full roster.
3. Process the real Sentinel-2 season, let Perceptron read it, replace plan files only when validation passes.
4. Point the map at those files; confirm flags, time-lapse, delivery status.

## Rules for agents
1. **Stay in your folder** (table above). Don't edit another person's folder.
2. **Shared contracts, `config/` and the spec change only after the team agrees.** Raise it in `#bot-channel` first;
   when agreed, update schema + mock generator + validator in the same PR.
3. Never break the mock path: `python intelligence/run_all.py --data data/mock` and the map on mock data must keep working.
4. The map reads finished files only. Only the morning queue and inbound handlers make live calls/texts.
5. **Sends go only to numbers in the roster.** The roster (farm, name, phone, language, role) lives outside git, like API keys.
   Always dry-run the queue before a real send.
6. Never invent farm measurements: facts come from `plans.json` / `route.json`.
7. Secrets only in `.env` (gitignored). Copy `.env.example`.
8. Small commits on your branch (`niranjan-pipeline`, `b/intel`, `c/vision`), merge to `main` often via PR.
   Run the validator before pushing. Large rasters/caches go in `data/cache/` (gitignored).
9. **Coordinate in Slack `#bot-channel`.** Each person's Grok Bot posts updates as
   `DONE / FILES / CONTRACT IMPACT / NEEDS`. `python scripts/notify.py "<who>: <what>"` also posts there.
   Before starting new work, `git pull origin main` and check the channel so you build on what others announced.

## Perceptron API (verified from docs.perceptron.inc)
- `pip install "perceptron>=0.4.0"`, key in `PERCEPTRON_API_KEY`, model `perceptron-mk1.5` (or pin `perceptron-mk1`)
- annotations: `vision_config={"annotation_format": "polygon"|"box"|"point"|"clip"}`; returned as markup in text
- coordinates normalized 0–1000, origin top-left. Helpers: `vision/perceptron_client.py`
- $50 budget total. Client enforces `PERCEPTRON_BUDGET_USD` (default 40) and logs spend.

## Commands
```
pip install -r intelligence/requirements.txt      # vision/, pipeline/, bots/ have their own requirements.txt
python scripts/make_mock_data.py
python intelligence/run_all.py --data data/mock
python scripts/validate_contracts.py data/mock
python vision/validate_perceptron.py            # first-hour Perceptron probe (the spec's gate)
```
