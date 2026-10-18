# Harvest Radar map

```bash
cd web && npm install && npm run dev
```

Open http://localhost:5173.

The app reads `../data/out` when `out/plans.json` exists, otherwise `../data/mock`. It loads finished JSON files only and makes no live API calls.
