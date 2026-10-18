# Overfield

A field inspector for small family farms: the satellite season does the visiting, and every farmer and the extension officer get a morning plan by call and text. Built at DubHacks 2026. Full context for humans and agents: [AGENTS.md](AGENTS.md). Live site: https://ishaanmakam.github.io/harvest-radar/

## Quickstart (5 min)
```bash
git clone https://github.com/ishaanmakam/harvest-radar && cd harvest-radar
cp .env.example .env        # add keys
pip install -r intelligence/requirements.txt
python scripts/make_mock_data.py
python intelligence/run_all.py --data data/mock
python scripts/validate_contracts.py data/mock
cd web && npm install && npm run dev
```

## What has been built since the first push

**The season pipeline** (`pipeline/`). Real Sentinel-2 L2A scenes over Story County, Iowa, found through the Earth Search STAC catalogue, read per field for NDVI and NBR, 38 clear passes through 17 Oct 2026. Field outlines from Fields of The World, 136 fields. Weather from NASA POWER and the Open-Meteo forecast, folded into an ensemble of eight forecast runs. Fires from NASA FIRMS VIIRS. Landsat added as a second imaging record. A world watch of 24 farm regions on six continents, each a 5.5 km box with its own season series derived from the Iowa record. Every output validates against a JSON schema in `contracts/`.

**The decision layer and the morning queue** (`intelligence/`). Eight checks per farm every morning (harvest ready, harvest before rain, establishment, burn scar, fire, flood, stress, visible change), a flag per farm, a route for the officer with act-now farms first, a yield estimate from the season curve and NASS county history, harvest economics, demand and routing blocks. The queue renders a plan per farmer and a route for the officer, sends by Twilio voice, Photon SMS and email, logs every delivery, and replays any past morning. Inbound handlers answer STOP, farm questions and officer questions. A voice gate checks every outgoing message against a written voice before it leaves. 912 tests.

**Accounts and a public API** (`intelligence/accounts.py`, `intelligence/accounts_api.py`). An own backend on SQLite: salted PBKDF2 passwords, bearer sessions, roster rows, API keys with a daily limit and terms of use, an admin view, a deliveries log, CSV export. Mounted on the inbound server and published through a tunnel. The site hides sign-in and sign-up for the demo; the routes remain.

**The web** (`web/`). Vite, plain JavaScript, MapLibre and three.js. An opening globe with real satellites (Sentinel-2, Landsat, Terra, Aqua from CelesTrak elements propagated with SGP4, Starlink as context), a season map with the farm list, the time-lapse of every clear pass, the officer route, the deliveries log, the yield and season views, a data-sources page listing all 42 sources with licence and cadence, and a replay of any morning. Hash routes for the developer and admin pages.

**Mars** (`pipeline/mars/`, `web/src/mars/`). Four checks on every 0.125 degree cell between 60 degrees south and 60 degrees north from MOLA heights, TES thermal inertia and albedo, SWIM 2025 ice consistency and the USGS geologic map: ice within reach, low ground, plume-safe ground and science reach. Ten sites at least 300 km apart; our top pick, M01, is in Arcadia Planitia. SpaceX's six 2019 HiRISE candidate sites graded on the same checks, with THEMIS thumbnails. Mars sits beside Earth on the opening globe and opens its own map.

**World regions on the globe.** The 24 regions are green dots anywhere on Earth. A click turns the globe to the region and opens its card: stage, canopy now and at peak, rain in the next 3 days, fires in the last 5 days, crops, clear passes. Each region opens its own map with a drawn field-pattern layer over its box, coloured from the region's canopy value and stage. Those layers are drawn, not satellite reads, and the card says so.

**The interface system.** Black canvas, D-DIN self-hosted under the SIL OFL, uppercase tracked labels, one outlined button, status as a word coloured only when it needs action, panels as black bays with a single rule, no radius, no shadow, no blur, a source strip on the landing page. A slop check (`scripts/slop-check.sh`) runs from `npm run slop`, from the pre-commit hook and from the Pages workflow, and fails the build on any of the patterns it names. Reference measurements, the token plan and before and after captures are in `docs/ui-audit/`.

**Deployment.** GitHub Pages builds and deploys `web/` on every push to main after the slop check. The inbound server and its tunnel run on a laptop; `scripts/publish_backend_url.py` tells the site where it is.

## Repository map

| Folder | What it holds |
|---|---|
| `pipeline/` | the Earth season pipeline, the world watch and the Mars pipeline |
| `intelligence/` | checks, plans, route, morning queue, voice gate, accounts, inbound server, tests |
| `vision/` | the Perceptron reader for change notes and counts |
| `web/` | the site |
| `contracts/` | JSON schemas for every data file |
| `data/out/` | the real season; `data/mock/` the stand-in; `data/global/` the world regions |
| `docs/` | the spec, the project background, the data sources, the UI audit, the agent voice |
| `scripts/` | mock data, validation, overlays, the slop check, the deploy helper |

Product spec: [docs/superpowers/specs/2026-10-17-field-inspector-design.md](docs/superpowers/specs/2026-10-17-field-inspector-design.md). Background and pitch: [docs/PROJECT.md](docs/PROJECT.md). Every data source with its licence: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Licence

The code and documentation are released under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Data files keep the terms of the sources they come from, listed with each source in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
