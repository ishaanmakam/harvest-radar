# Srikar — the web map (`web/`)

Paste into your agent: "Read AGENTS.md, the spec in docs/superpowers/specs/2026-10-17-field-inspector-design.md,
and docs/tasks/D-web.md, then scaffold the map against data/mock."

Owner: Srikar (confirmed by Ishaan, Oct 17). Stack: Vite + vanilla JS (or React) + MapLibre GL.
Serve `../data` as the public dir so `fetch('/mock/fields.geojson')` works; load `/out/...` when `out/plans.json` exists,
else `/mock/...` (show a MOCK/REAL badge). The map reads finished files only: no live Perceptron or Grok calls.

What it shows (spec "Map" + "Testing"):
1. Esri World Imagery basemap; the season time-lapse from `frames/frames.json` (image source on `bounds`) with a date slider/play.
2. Field boundaries coloured by each farm's `flag` from `plans.json` (`fine` / `watch` / `act_now`).
3. Click a farm: its full plan, all 8 checks with status, `detail`, `where`, `window`, plus `today` and `risks`.
4. The officer route from `route.json`: numbered stops in order, with `action` and `why`.
5. Delivery status per farm from `deliveries.json` (`pending` / `sent` / `failed`, per channel).
6. Optional: NDVI sparkline from `timeseries.json` on the farm panel; FIRMS points from `fires.json`.

Until Ishaan's `plans.json` / `route.json` land on main, build against a hand-written sample that follows the spec's Data section.
Then: demo script + backup screen recording by 7 AM.
