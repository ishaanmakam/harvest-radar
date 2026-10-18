"""When weather_ensemble.json is present the plan says how many of the latest weather models agree: on the act-now
rain, on a split day the single feed calls dry, and on a frost night. The deterministic rules (15 mm in 3 days on a
ready crop) stay on the pipeline's forecast; the ensemble only adds words."""
import json
from datetime import date
from pathlib import Path

import decide
from test_decide import DRY, QUIET_PASSES, check, rec, status_of

ROOT = Path(__file__).resolve().parents[2]
D = date.fromisoformat
TODAY = D("2026-10-17")
READY = dict(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")


def day(**kw):
    base = {"n_models": 8, "p_rain_5mm": 0.0, "p_rain_12mm": 0.0, "p_rain_15mm": 0.0, "p_rain_25mm": 0.0,
            "precip_median_mm": 0.0, "precip_max_mm": 0.0, "p_frost": 0.0, "p_hard_freeze": 0.0}
    base.update(kw)
    return base


class TestRainRowAgreement:
    def test_act_now_rain_says_how_many_models_agree(self):
        fc = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 0.0), (D("2026-10-19"), 16.0), (D("2026-10-20"), 0.0)]
        ens = {D("2026-10-19"): day(p_rain_15mm=0.75, p_rain_5mm=1.0, precip_median_mm=14.0, precip_max_mm=22.0)}
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY, ensemble=ens)
        c = check(plan, "harvest_before_rain")
        assert c["status"] == "act_now" and "6 of 8 weather models agree" in c["detail"]
        assert "6 of 8 weather models agree" in plan["today"]

    def test_a_split_day_the_single_feed_calls_dry_is_mentioned_but_stays_clear(self):
        ens = {D("2026-10-18"): day(p_rain_15mm=0.375, p_rain_5mm=0.5, precip_median_mm=4.0, precip_max_mm=19.0)}
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, ensemble=ens)
        c = check(plan, "harvest_before_rain")
        assert c["status"] == "clear" and status_of(plan)["harvest_before_rain"] == "clear"
        assert "3 of 8 weather models put 15 mm or more on 2026-10-18" in c["detail"]

    def test_quiet_agreement_adds_nothing(self):
        ens = {D("2026-10-18"): day(p_rain_15mm=0.125)}
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, ensemble=ens), "harvest_before_rain")
        assert "weather models" not in c["detail"]

    def test_frost_note_carries_the_agreement(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -1.0)]
        ens = {D("2026-10-18"): day(p_frost=0.625, p_hard_freeze=0.125)}
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY, ensemble=ens), "harvest_before_rain")
        assert "frost expected on 2026-10-18 (-1 °C; 5 of 8 models agree)" in c["detail"]

    def test_no_ensemble_means_the_old_words(self):
        fc = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 0.0), (D("2026-10-19"), 16.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY)
        assert "weather models" not in check(plan, "harvest_before_rain")["detail"]


class TestRunReadsTheFile:
    def test_run_picks_up_weather_ensemble_json(self, tmp_path):
        data = ROOT / "data" / "mock"
        for name in ("status.json", "timeseries.json", "fires.json", "fields.geojson", "insights.json", "weather.json"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        w = json.loads((data / "weather.json").read_text())
        heavy = next(i for i, p in enumerate(w["forecast"]["precip_mm"]) if p >= 15)
        days = [{"date": dt, **day()} for dt in w["forecast"]["dates"]]
        days[heavy].update(p_rain_15mm=0.875, p_rain_5mm=1.0, precip_median_mm=17.0, precip_max_mm=24.0)
        (tmp_path / "weather_ensemble.json").write_text(json.dumps({"source": "test", "dates": w["forecast"]["dates"], "models": ["a"] * 8, "days": days}))
        plans, _ = decide.run(tmp_path)
        act = [p for p in plans["plans"] if status_of(p)["harvest_before_rain"] == "act_now"]
        assert act and all("7 of 8 weather models agree" in check(p, "harvest_before_rain")["detail"] for p in act)
