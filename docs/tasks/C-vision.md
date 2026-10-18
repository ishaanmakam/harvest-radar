# Srikar — Perceptron reader (`vision/`); also owns the map (`docs/tasks/D-web.md`)

Paste into your agent: "Read AGENTS.md, the spec in docs/superpowers/specs/2026-10-17-field-inspector-design.md, and docs/tasks/C-vision.md. Start with the Perceptron validation test."

Per the spec, Perceptron feeds three of the eight checks: `flood` (water on the field in the latest clear pass), `stress` (part of a field declining that is not a harvest) and `visible_change` (a change dated since the previous clear pass, with a timestamp and place). Make `insights.json` carry those per zone or per field so the decision layer can use them; if polygons are weak, index math locates the change and Perceptron still describes and timestamps it. Agree any `insights.json` shape change with Ishaan in `#bot-channel` first.

You own the Perceptron sponsor track. Helpers already written (untested against the live API):
`vision/perceptron_client.py` (budget cap, spend log, annotation parser, 0–1000 coords → lon/lat),
`vision/validate_perceptron.py` (first-hour probes), `vision/analyze_timelapse.py` (per-zone time-lapse → insights.json).

Setup: `pip install -r vision/requirements.txt`, key in `.env` (`PERCEPTRON_API_KEY`). $50 total budget; client stops at `PERCEPTRON_BUDGET_USD` (default 40).

1. **Now:** `python vision/validate_perceptron.py` on a mock frame (tests auth + parsing). Fix anything in the client that breaks.
   Rerun on a real frame as soon as Person A has `data/out/frames/`. Verdict for the team: are Perceptron's field polygons usable?
2. `python vision/analyze_timelapse.py --data data/mock` → `insights.json`; then `--mode video` (clip annotations → dates). Compare quality, keep the better one.
3. If polygons are good: `vision/perceptron_fields.py` → `fields.geojson` with `source="perceptron"` (tile the AOI into crops so each request has few fields;
   convert coords with `norm_to_lonlat` on each crop's bounds; drop overlaps/tiny shapes).
4. **Burn-scar verification:** for fields the model calls burned, crop the frame around each field and ask Perceptron to confirm (box annotation).
   Output a confirmed flag per field (coordinate a schema addition with the team). This addresses the real gap: fire counts dropped but burned area didn't.
5. Per-zone narrative for the UI: one or two sentences each, cached in `insights.json`.
6. Track spend in `data/out/perceptron/spend.json`; leave ~$10 for demo-day reruns.

Pitch line you own: "Perceptron reads every satellite pass like a video of the season: when each field was cut, where burn scars appeared."
