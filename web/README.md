# Overfield web

The map and the pages around it, in two aspects. **Crop health** is the daily inspector: every farm's flag and morning plan, the season time-lapse, the officer's route and the morning deliveries (the text and voice loop to farmers). **Crop yield** is the second reading of the same season: the county yield model, calibrated bushels an acre per farm, soil and terrain, and the season analytics.

```bash
cd web && npm install && npm run dev
```

Open http://localhost:5173. `npm run build` writes `web/dist/`; `npm run preview` serves it.

## Hosting

The build uses a relative base, so `web/dist/` runs from any folder or host, including a GitHub Pages project path. `.github/workflows/pages.yml` builds `web/` on every push to `main` and deploys `web/dist/` to GitHub Pages; the data folder ships with the site because it is the Vite public folder. Pages has to be enabled once in the repository settings (Settings, Pages, Source: GitHub Actions).

The site is installable: `web/public/manifest.webmanifest` and the icons under `web/public/icons/` are served in dev and copied into `dist/` by the plugin in `vite.config.js`, which also links them from the page. There is no service worker, on purpose: the plan files change every morning and a cache could show yesterday's plan.

## What it reads

Vite serves `../data` as the public folder. The app loads finished files only and makes no live API calls.

The four farm files `fields.geojson`, `plans.json`, `route.json` and `deliveries.json` are one group: they come from `data/out` only when both `fields.geojson` and `plans.json` are real JSON there, otherwise all four come from `data/mock`. Real fields are never joined to mock plans, because the farm ids overlap but are different farms, and a missing `route.json` or `deliveries.json` inside a real group means "none yet". When `data/out/fields.geojson` exists before the real plans do, those fields draw as a thin white outline layer under the mock farms, toggled from the badge in the top bar.

Every other file is tried in `data/out` first and falls back to `data/mock` on its own, so a half-finished real season still renders; the badge says whether the imagery and the farm group came from the real or the stand-in season.

| File | Needed | Used for |
|---|---|---|
| `fields.geojson`, `plans.json` | yes | polygons, flags, the eight checks, the today line, the harvest outlook |
| `zones.geojson` | optional | zone outlines, labels and the zone filter |
| `route.json` | optional | the Route view and the numbered visit markers |
| `deliveries.json` | optional | the Deliveries view and the delivery badges on a farm |
| `frames/frames.json` and the images beside it | optional | the time-lapse and the latest pass under the fields |
| `timeseries.json` | optional | the NDVI season sparkline on a farm |
| `status.json` | optional | stage, peak and harvest date on the sparkline, percent harvested |
| `weather.json` | optional | reserved; the weather line comes from `plans.json` |
| `analytics.json` | optional | the Outlook block on a farm (including calibrated bu/acre, soil and terrain), km and ETAs on the route, the Season view, the yield map and totals |
| `run.json` | optional, farm base only | the Live / Replay badge with the run date |
| `field_env.json` | optional, farm base only | soil texture, organic carbon, clay, pH, slope, elevation and aspect on a farm; landscape medians on the Yield view |
| `yield_model.json` | optional | the county estimate per crop with its band, held-out error, R² and hindcast |
| `ftw_fields_2024.geojson` | optional | the Fields of The World outline layer, toggled on the Yield view |

Every optional file may be absent; the view then says so. `?base=/mock` in the URL pins every file to one folder with no fallback, which is the way to check a dataset with files missing (`?base=/cache/some-folder` works for anything under `data/`).

## Views

Crop health:

- **Fields**: a quick-action strip (act now, watch, stops on the route) that jumps to the Route view, polygons coloured by flag with a hover tooltip, zone outlines, a legend with live counts, filters by flag, crop, zone and check, and a farm search. Clicking a farm opens its plan: today line, Outlook, NDVI season, the eight checks, risks and delivery status. Arrow keys move through the current list, Escape closes.
- **Time-lapse**: the season's passes as an image overlay with a scrubber, play/pause, speed and opacity; fields stay on top, dimmed.
- **Route**: the officer's day, visits first then calls, with km, ETAs and totals when the analytics layer has run.
- **Deliveries**: the day's counts by status and channel, the queue by role, farm, channel and status, and the "how a farmer gets this" card with the sign-up link.

Crop yield:

- **Yield**: the county estimate per crop with its 90 percent band, held-out error, R² and the number of county-seasons and years, in plain words; the hindcast chart per crop; the farms coloured by calibrated bushels an acre with a per-crop legend; landscape totals (reference mean, calibrated mean, county model); the Fields of The World outline toggle; soil and terrain medians. Without `yield_model.json` the view says so and the map keeps the flags.
- **Season**: harvest progress per crop with projected 50/75/90 percent dates, the USDA comparison, the zone table, combine demand and the narrative.

## Sign-up link

The "Get your farm's morning plan" button links to the inbound server's sign-up page. Set `VITE_SIGNUP_URL` in `web/.env` to point it somewhere else; the default is `http://localhost:8788/signup`.

## Intro

The opening scene is a three.js Earth with real satellites over it. Featured farm regions are marked, and Enter the season flies into the live Story County landscape. Clicking another region opens that place on the imagery and says the live demo is still Iowa. Earth textures are bundled locally from the three.js examples (r169), so the intro does not fetch them at runtime. With reduced motion on, the Earth holds still, the satellite clock runs at real time and the fly-in is instant.

Rendering (`src/globe/`): a procedural star field (points, no texture, with a Milky Way band), a fresnel atmosphere in two shells (soft blue limb, warmer and stronger along the terminator), the sun placed from the UTC time of the satellite clock so day, night and city lights are where they really are, specular oceans, and a slow spin that pauses on any drag, wheel or touch and eases back in six seconds later. No post-processing passes, so an integrated GPU keeps 60 fps.

Satellites (`src/globe/satellites.js`): positions come from SGP4 (`satellite.js`) on a clock that starts at wall-clock time and runs at 1×, 60× (the default) or 600×. Sentinel-2A, 2B and 2C are the green points, the satellites that visit every farm; Landsat 8 and 9, Terra and Aqua are the white ones; each carries a label and its ground track for the next 90 minutes, and an arc reaches down to the farms when a Sentinel-2 comes within 25°. Starlink is the faint shell of points, drawn for connectivity context only; it is not a data source. The legend gives a next-pass estimate: the first time in the next five days that a Sentinel-2 sub-satellite point comes within 150 km of Ames, Iowa.

The orbital elements are a CelesTrak GP snapshot in `public/sats/imaging.json` and `public/sats/starlink.json` (Starlink thinned by even sampling to 800 objects). Refresh them with:

```bash
python scripts/fetch_tles.py
```

The script is standard library only, writes `fetched_at` into both files, and the legend shows that date. SGP4 drifts by kilometres a day, so a snapshot older than a few weeks puts the points in the wrong place; refresh before a demo.

## Zoom past 15

The Sentinel-2 frame is drawn with nearest resampling, so its pixels stay square and read as the 10 m they are. In the Fields, Route and Yield views the frame fades out between zoom 14.5 and 15.5 and the Esri World Imagery basemap shows under the field outlines; the Time-lapse view keeps the frame at every zoom and says so under the scrubber.
