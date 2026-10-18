"""The weather outlook flows into the plans: every standing farm carries a harvest_outlook (additive field),
a ready farm's harvest_ready row shows the best combine window and today's line says it, and the morning words
read it out."""
import json
from datetime import date
from pathlib import Path

import decide
import queue_morning as q
from test_decide import DRY, QUIET_PASSES, check, rec, status_of

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 17)
D = date.fromisoformat


def outlook(**kw):
    base = {"crop": "corn", "maturity": "2026-09-12", "combine_ready": "2026-10-15",
            "window": {"start": "2026-10-17", "end": "2026-10-18"}, "next_window": {"start": "2026-10-21", "end": "2026-10-23"},
            "rain_days": ["2026-10-19"], "rain_mm": {"2026-10-19": 20.0}, "frost_nights": [], "hard_freeze": [],
            "workable_days_in_forecast": 10, "window_to_horizon": False, "forecast_stale": False, "ready_source": "satellite",
            "summary": "Combine-ready since 2026-10-15. Best window 2026-10-17 to 2026-10-18, before 20 mm of rain on 2026-10-19; next window 2026-10-21 to 2026-10-23."}
    base.update(kw)
    return base


class TestFarmPlanWithOutlook:
    def test_ready_farm_shows_the_combine_window(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        plan = decide.farm_plan(ready, QUIET_PASSES, DRY, [], None, TODAY, outlook=outlook())
        c = check(plan, "harvest_ready")
        assert c["status"] == "watch"
        assert c["window"] == {"start": "2026-10-17", "end": "2026-10-18"}        # the weather window, not the satellite's
        assert "window today and tomorrow (2026-10-17 to 2026-10-18)" in c["detail"]
        assert "2026-10-17 to 2026-10-18" in plan["today"] and "combine" in plan["today"].lower()
        assert plan["harvest_outlook"]["combine_ready"] == "2026-10-15"

    def test_maturing_farm_keeps_clear_but_carries_the_outlook(self):
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, [], None, TODAY, outlook=outlook(window=None, combine_ready="2026-10-25",
                                                                                          ready_source="degree_days", summary="Combine-ready about 2026-10-25 (still drying down); no window inside the forecast."))
        assert status_of(plan)["harvest_ready"] == "clear"
        assert "2026-10-25" in check(plan, "harvest_ready")["detail"]
        assert plan["harvest_outlook"]["window"] is None

    def test_no_outlook_means_no_field_and_old_behaviour(self):
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, [], None, TODAY)
        assert "harvest_outlook" not in plan

    def test_harvested_farm_has_no_outlook_even_if_given(self):
        done = rec(stage="harvested", harvest_date="2026-10-10", predicted_harvest_start=None, predicted_harvest_end=None)
        plan = decide.farm_plan(done, QUIET_PASSES, DRY, [], None, TODAY, outlook=outlook())
        assert "harvest_outlook" not in plan


class TestRunBuildsOutlooks:
    def test_run_on_mock_gives_every_standing_farm_an_outlook(self):
        plans, _ = decide.run(ROOT / "data" / "mock")
        standing = [p for p in plans["plans"] if status_of(p)["harvest_ready"] == "watch"
                    or any(c["kind"] == "harvest_ready" and "expected" in c["detail"] for c in p["checks"])]
        assert standing
        assert all("harvest_outlook" in p for p in standing)
        o = next(p["harvest_outlook"] for p in standing)
        assert set(o) >= {"crop", "combine_ready", "window", "rain_days", "frost_nights", "summary"}
        assert o["crop"] in ("corn", "soybean")
        plan_schema = json.loads((ROOT / "contracts" / "plans.schema.json").read_text())
        from jsonschema import Draft202012Validator
        assert not list(Draft202012Validator(plan_schema).iter_errors(plans))

    def test_words_read_the_window(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        plan = decide.farm_plan(ready, QUIET_PASSES, DRY, [], None, TODAY, outlook=outlook())
        text = q.render_plan(plan, "en")
        assert "Best window 2026-10-17 to 2026-10-18" in text
