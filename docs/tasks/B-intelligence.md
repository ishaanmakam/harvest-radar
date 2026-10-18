# Ishaan — decision layer, morning queue, voice + texts (`intelligence/`)

Paste into your agent: "Read AGENTS.md, the spec in docs/superpowers/specs/2026-10-17-field-inspector-design.md,
and docs/tasks/B-intelligence.md. Work through the list in order, committing after each step."

**Change of direction (Oct 17, 5 PM):** the team spec drops machine-dispatch routing as the hero output.
Stop work on the MILP dispatch. Keep `demand.py` / `routing.py` / the MILP on the branch (possible pitch extension),
but nothing below depends on them. `harvest_model.py` (stage, peak, harvest date, burn evidence) and `evaluate.py` stay
useful: they feed several checks.

## 1. Plans + route from the stand-in season (spec build step 1)
- Add schemas `contracts/plans.schema.json`, `route.schema.json`, `deliveries.schema.json` exactly per the spec's Data
  section, register them in `scripts/validate_contracts.py`. This is a contract addition: post it in `#bot-channel`.
- `intelligence/decide.py`: inputs `status.json`, `timeseries.json`, `weather.json` (observed + forecast), `fires.json`,
  `insights.json` (optional). Output one plan per farm (farm = field from `fields.geojson`) with all 8 checks in table order,
  each `{kind, status, detail, where, window}`, plus `flag`, `source`, `today`, `risks`, `as_of` (latest clear pass date).
  Rules come straight from the spec table, e.g. `harvest_before_rain` = `act_now` when the crop looks ready and any forecast
  day in the next 3 days is ≥ 15 mm; `fire` = `act_now` for a FIRMS hotspot in the field in the last 5 days.
  `flood`, `stress`, `visible_change` use Perceptron insights when present, else index cues; set `source` accordingly.
- `route.json`: stops ordered exactly as the spec's route order; `action` `visit` or `call`; short `why`.
- Fixture tests per the spec's Testing section: fire, flood, harvest-before-rain, burn scar, stress, new visible change,
  a quiet farm with 8 clear checks, a farm with two failures taking the worse flag.
- Wire into `run_all.py` so `--data data/mock` produces valid `plans.json` + `route.json`. Merge to `main` early so Srikar's
  map can read them.

## 2. Roster + shared tools
- Roster CSV outside git (path in `.env` as `ROSTER_PATH`), columns `roster_id, farm_id, name, phone, language, role`.
  One officer row and one farmer row per demo farm; teammates supply the numbers.
- `intelligence/tools.py`: `get_plan(farm_id)`, `get_route()`, `get_change(farm_id)`. Farmer sessions only for their own farm.
  Additive: `get_outlook(farm_id)` reads `analytics.json` (the optional analytics layer) with the same scope rules.

## 3. Morning queue (spec build step 2)
- `intelligence/queue.py --dry-run`: joins roster to plans/route, writes a `pending` delivery row per channel before each
  attempt, places nothing. Then real mode: Grok Voice call + Photon SMS per farmer; Grok Voice + Photon SMS of the route
  to the officer. Two retries each; voice and SMS have separate rows; `deliveries.json` is the restart checkpoint
  (never send the same channel twice per morning). Render the roster language at send time.
- **First, verify the providers** (half an hour, before writing queue code): outbound phone calls via the xAI console's
  Voice Agents (it offers agents reachable by phone number) and Photon 2.0 SMS/iMessage (ask the Photon booth for keys/docs).
  Post findings in `#bot-channel`; if outbound calling isn't possible tonight, tell the team immediately.

## 4. Inbound handlers
Caller ID / iMessage handle → roster row. Farmer voice: that farm only. Officer voice + iMessage: route and any farm.
Unknown numbers get "no farm registered" and nothing else.

Keep `python scripts/validate_contracts.py data/mock` passing at every commit.
