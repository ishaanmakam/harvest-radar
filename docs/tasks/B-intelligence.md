# Person B — models + optimization (`intelligence/`)

Paste into your agent: "Read AGENTS.md and docs/tasks/B-intelligence.md, then work through the list in order."

Already working on mock data: `harvest_model.py` → `demand.py` → `routing.py` (run all: `python intelligence/run_all.py --data data/mock`).
Current routing is a greedy earliest-deadline-first heuristic. The hero pitch number is `routing.json.summary` (optimized vs baseline).

1. **Ground truth for evaluation.** Make `scripts/make_mock_data.py` also write `data/mock/truth.json` (true harvest date, burned, sown per field).
   Add `intelligence/evaluate.py`: harvest-date error (days), stage accuracy, burn precision/recall on mock. Tune the model until errors are small.
2. **Real optimization for dispatch.** Replace/augment greedy with a MILP (`scipy.optimize.milp`, HiGHS):
   variables = machines from CHC c to zone z on day t (integer) and ha served per job-day; constraints = machines per CHC per day,
   max service distance, a job can only be served between its ready day and sowing deadline; objective = minimize ha missing the window
   (tie-break: travel distance). Keep greedy as fallback if the solver is slow. Report both vs baseline.
3. **Weather urgency.** Use `weather.json` forecast: rain on a day blocks harvesting/sowing in that zone (or reduces machine rate). Fields ready before
   a rain event get priority. This makes the plan visibly react to the forecast.
4. **Scenarios for the UI.** Write `scenarios.json` (add a schema in contracts/ + tell the team): served % for machine counts x0.5..x2 and share_needing_chc 0.2..0.6,
   optimized vs baseline. Lets the demo answer "how many machines does this district actually need?"
5. **Real data.** When Person A merges `data/out/`, run on it, spot-check 20 fields against the frames, and tune thresholds in `config/region.json`.
6. Stretch: US validation — compare our harvest dates on a US region with USDA Crop Progress (needs Person A's pipeline on that bbox).

Keep every output passing `python scripts/validate_contracts.py data/mock` and keep `run_all.py` under ~30 s.
