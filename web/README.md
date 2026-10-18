# Overfield web

The map and the pages around it: the orbit intro, every farm's flag and morning plan, the season time-lapse, the officer's route, the morning deliveries and the season analytics.

```bash
cd web && npm install && npm run dev
```

Open http://localhost:5173. `npm run build` writes `web/dist/`.

## What it reads

Vite serves `../data` as the public folder. Each file is tried in `data/out` first and falls back to `data/mock` on its own, so a half-finished real season still renders; the badge in the top bar says whether the imagery and the farms came from the real or the stand-in season. The app loads finished files only and makes no live API calls.

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
| `analytics.json` | optional | the Outlook block on a farm, km and ETAs on the route, the Season view |

Every optional file may be absent; the view then says so. `?base=/mock` in the URL pins every file to one folder with no fallback, which is the way to check a dataset with files missing (`?base=/cache/some-folder` works for anything under `data/`).

## Views

- **Fields**: polygons coloured by flag with a hover tooltip, zone outlines, a legend with live counts, filters by flag, crop, zone and check, and a farm search. Clicking a farm opens its plan: today line, Outlook, NDVI season, the eight checks, risks and delivery status. Arrow keys move through the current list, Escape closes.
- **Time-lapse**: the season's passes as an image overlay with a scrubber, play/pause, speed and opacity; fields stay on top, dimmed.
- **Route**: the officer's day, visits first then calls, with km, ETAs and totals when the analytics layer has run.
- **Deliveries**: today's queue by role, farm, channel and status.
- **Season**: harvest progress per crop with projected 50/75/90 percent dates, the USDA comparison, the zone table, combine demand and the narrative.

## Sign-up link

The "Get your farm's morning plan" button links to the inbound server's sign-up page. Set `VITE_SIGNUP_URL` in `web/.env` to point it somewhere else; the default is `http://localhost:8788/signup`.

## Intro

The opening scene is a three.js Earth: a Sentinel-2 satellite orbits, featured farm regions are marked, and Enter the season flies into the live Story County landscape. Clicking another region opens that place on the imagery and says the live demo is still Iowa. Earth textures are bundled locally from the three.js examples (r169), so the intro does not fetch them at runtime. With reduced motion on, the Earth holds still and the fly-in is instant.
