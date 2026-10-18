# Overfield map

```bash
cd web && npm install && npm run dev
```

Open http://localhost:5173.

Each file is read from `../data/out` when that file is real JSON, otherwise from `../data/mock`. The badge says which source the imagery came from and which source the farms and plans came from. The app loads finished JSON files only and makes no live API calls.

The map draws farm flags over a season time-lapse (play and a date slider). The side panel lists the officer route, top stops first, and the clicked farm's plan plus its voice and SMS delivery status.

The opening scene is a three.js Earth: a Sentinel-2 satellite orbits, featured farm regions are marked, and Enter the season flies into the live Story County landscape. Clicking another region opens that place on the imagery and says the live demo is still Iowa. Earth textures are bundled locally from the three.js examples (r169), so the intro does not fetch them at runtime.
