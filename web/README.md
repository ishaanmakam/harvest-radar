# Harvest Radar map

```bash
cd web && npm install && npm run dev
```

Open http://localhost:5173.

The app reads `../data/out` when `out/plans.json` exists, otherwise `../data/mock`. A MOCK or REAL badge shows which one loaded. It loads finished JSON files only and makes no live API calls.

The map draws farm flags over a season time-lapse (play and a date slider). The side panel lists the officer route, top stops first, and the clicked farm's plan plus its voice and SMS delivery status.

The opening scene is a three.js Earth: a Sentinel-2 satellite orbits, a marker pulses on Story County, Iowa, and Enter the season flies into the map. Earth textures are bundled locally from the three.js examples (r169), so the intro does not fetch them at runtime.
