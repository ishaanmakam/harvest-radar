# Web app + pitch (`web/`) — whole team, starts by ~10 PM at the latest

Whoever finishes their part first scaffolds this; everyone joins after. Paste into your agent:
"Read AGENTS.md and docs/tasks/D-web.md, then scaffold the web app against data/mock."

Stack: Vite + vanilla JS (or React) + MapLibre GL. Serve `../data` as the public dir so `fetch('/mock/fields.geojson')` works;
load `/out/...` if `out/status.json` exists, else `/mock/...` (show a MOCK/REAL badge). No live API calls.

Screens (one page):
1. Map: Esri World Imagery basemap; satellite frame overlay from `frames/frames.json` (image source on `bounds`) with a date slider/play button;
   fields colored by `status.json` stage (growing/maturing/ready/harvested/burned/sown); fire points from `fires.json`;
   zone outlines; CHC markers + dispatch lines (CHC → zone, width = machines) from `routing.json.plan` for the selected day.
   Use HTML markers for labels (no glyph server needed).
2. Side panel: KPI row (fields monitored, % harvested, ha needing machines next 7 days, optimized vs baseline % served);
   zone list sorted by `demand.json` machine_days_next_7d with Perceptron summary from `insights.json`;
   "Tomorrow's dispatch" table; forecast rain strip from `weather.json`; scenario slider from `scenarios.json` if it exists.
3. Click a field: NDVI sparkline (timeseries.json) with peak/harvest markers, stage, dates, burn evidence.

Then: pitch deck + 2-minute demo script + backup screen recording by 7 AM.
Pitch spine: problem (15-day window, scarce machines, burning) → existing tools are reactive/punitive → we forecast demand from orbit → dispatch plan → hero metric.
