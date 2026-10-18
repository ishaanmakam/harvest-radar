"""When weather_ensemble.json is present the plan says which of the latest weather models also expect the act-now
rain, a split day the single feed calls dry, and a frost night, with exact counts and honest words when they disagree.
The deterministic rules (15 mm in 3 days on a ready crop) stay on the pipeline's forecast; the ensemble only adds words."""
import json
from datetime import date
from pathlib import Path

import decide
import forecast_models as fm
from test_decide import DRY, QUIET_PASSES, check, rec, status_of

ROOT = Path(__file__).resolve().parents[2]
D = date.fromisoformat
TODAY = D("2026-10-17")
READY = dict(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")


def day(n=8, k15=0, k5=None, kf=0, kh=0, n_tmin=None, **kw):
    base = {"n_models": n, "n_precip": n, "n_tmin": n if n_tmin is None else n_tmin,
            "k_rain_15mm": k15, "k_rain_5mm": k15 if k5 is None else k5, "k_frost": kf, "k_hard_freeze": kh,
            "p_rain_15mm": k15 / n if n else 0.0, "p_frost": kf / n if n else 0.0, "precip_median_mm": 0.0, "precip_max_mm": 0.0,
            "rain_act_now_mm": 15.0}
    base.update(kw)
    return base


RAIN19 = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 0.0), (D("2026-10-19"), 16.0), (D("2026-10-20"), 0.0)]


class TestRainRowAgreement:
    def test_majority_agreement_reads_as_also_expect(self):
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, RAIN19, [], None, TODAY, ensemble={D("2026-10-19"): day(k15=6)})
        c = check(plan, "harvest_before_rain")
        assert c["status"] == "act_now"
        assert "16 mm of rain forecast on 2026-10-19, which 6 of 8 weather models also expect;" in c["detail"]
        assert "which 6 of 8 weather models also expect" in plan["today"] and ") (" not in plan["today"]

    def test_no_model_agreeing_is_said_plainly_and_the_rule_still_fires(self):
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, RAIN19, [], None, TODAY, ensemble={D("2026-10-19"): day(k15=0)})
        c = check(plan, "harvest_before_rain")
        assert c["status"] == "act_now" and "0 of 8" not in c["detail"]
        assert "16 mm of rain forecast on 2026-10-19, though no other weather model expects that much" in c["detail"]
        assert plan["today"].startswith("Harvest before 2026-10-19")

    def test_a_minority_reads_as_only(self):
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, RAIN19, [], None, TODAY, ensemble={D("2026-10-19"): day(k15=3)}), "harvest_before_rain")
        assert ", though only 3 of 8 weather models expect that much" in c["detail"]

    def test_too_few_models_say_nothing(self):
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, RAIN19, [], None, TODAY, ensemble={D("2026-10-19"): day(n=2, k15=1)}), "harvest_before_rain")
        assert "weather model" not in c["detail"]

    def test_rain_today_and_tomorrow_carry_one_clean_clause(self):
        today_rain = [(D("2026-10-17"), 20.0), (D("2026-10-18"), 0.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, today_rain, [], None, TODAY, ensemble={D("2026-10-17"): day(k15=7)})
        assert plan["today"].startswith("Rain today (20 mm, which 7 of 8 weather models also expect):")
        tomorrow = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 22.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, tomorrow, [], None, TODAY, ensemble={D("2026-10-18"): day(k15=5)})
        assert "tomorrow (2026-10-18), which 5 of 8 weather models also expect;" in plan["today"] and ") (" not in plan["today"]

    def test_a_split_day_the_single_feed_calls_dry_is_mentioned_but_stays_clear(self):
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, ensemble={D("2026-10-18"): day(k15=3)})
        c = check(plan, "harvest_before_rain")
        assert c["status"] == "clear" and status_of(plan)["harvest_before_rain"] == "clear"
        assert "3 of 8 weather models put 15 mm or more on 2026-10-18" in c["detail"]

    def test_quiet_agreement_or_a_file_counted_at_another_line_adds_nothing(self):
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, ensemble={D("2026-10-18"): day(k15=1)}), "harvest_before_rain")
        assert "weather models" not in c["detail"]
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, ensemble={D("2026-10-18"): day(k15=4, rain_act_now_mm=10.0)}), "harvest_before_rain")
        assert "weather models" not in c["detail"]

    def test_no_ensemble_means_the_old_words(self):
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, RAIN19, [], None, TODAY)
        assert "weather model" not in check(plan, "harvest_before_rain")["detail"]


class TestFrostAgreement:
    FC = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -1.0)]

    def test_frost_note_keeps_the_temperature_together_and_the_agreement_last(self):
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, self.FC, [], None, TODAY, ensemble={D("2026-10-18"): day(kf=5)}), "harvest_before_rain")
        assert "frost expected on 2026-10-18 (-1 °C), which 5 of 8 weather models also expect: the crop is mature" in c["detail"]

    def test_hard_freeze_label_stays_on_the_temperature_and_counts_hard_freezes(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -3.0)]
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY, ensemble={D("2026-10-18"): day(kf=6, kh=2)}), "harvest_before_rain")
        assert "(-3 °C, a hard freeze), though only 2 of 8 weather models expect it:" in c["detail"]

    def test_no_model_seeing_frost_is_said_plainly(self):
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, self.FC, [], None, TODAY, ensemble={D("2026-10-18"): day(kf=0)}), "harvest_before_rain")
        assert "frost expected on 2026-10-18 (-1 °C), though no other weather model expects frost:" in c["detail"]

    def test_counts_come_from_the_temperature_models_not_the_rain_models(self):
        ens = {D("2026-10-18"): day(n=8, kf=3, n_tmin=3)}                     # three models report a temperature, all freezing
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, self.FC, [], None, TODAY, ensemble=ens), "harvest_before_rain")
        assert "which 3 of 3 weather models also expect" in c["detail"]

    def test_maturing_crop_row_carries_the_clause_too(self):
        c = check(decide.farm_plan(rec(), QUIET_PASSES, self.FC, [], None, TODAY, ensemble={D("2026-10-18"): day(kf=5)}), "harvest_before_rain")
        assert "frost expected on 2026-10-18 (-1 °C), which 5 of 8 weather models also expect: the crop is not ready yet" in c["detail"]


class TestRunReadsTheFile:
    def copy(self, tmp_path):
        data = ROOT / "data" / "mock"
        for name in ("status.json", "timeseries.json", "fires.json", "fields.geojson", "insights.json", "weather.json"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        return json.loads((data / "weather.json").read_text())

    def test_run_picks_up_weather_ensemble_json(self, tmp_path):
        w = self.copy(tmp_path)
        heavy = next(i for i, p in enumerate(w["forecast"]["precip_mm"]) if p >= 15)
        days = [{"date": dt, **day()} for dt in w["forecast"]["dates"]]
        days[heavy].update(day(k15=7, precip_median_mm=17.0, precip_max_mm=24.0))
        (tmp_path / "weather_ensemble.json").write_text(json.dumps({"source": "test", "fetched_at": "2026-10-17T06:00Z",
                                                                    "dates": w["forecast"]["dates"], "models": ["a"] * 8, "days": days}))
        plans, _ = decide.run(tmp_path)
        act = [p for p in plans["plans"] if status_of(p)["harvest_before_rain"] == "act_now"]
        assert act and all("which 7 of 8 weather models also expect" in check(p, "harvest_before_rain")["detail"] for p in act)

    def test_a_stale_or_malformed_file_is_ignored_and_the_plans_still_come_out(self, tmp_path):
        w = self.copy(tmp_path)
        days = [{"date": dt, **day(k15=8)} for dt in w["forecast"]["dates"]]
        (tmp_path / "weather_ensemble.json").write_text(json.dumps({"fetched_at": "2026-10-10T06:00Z", "dates": w["forecast"]["dates"], "models": ["a"] * 8, "days": days}))
        plans, _ = decide.run(tmp_path)
        assert not any("weather model" in check(p, "harvest_before_rain")["detail"] for p in plans["plans"])
        for bad in ("[]", "null", '{"days": [5, null, "x"]}', "not json"):
            (tmp_path / "weather_ensemble.json").write_text(bad)
            plans, _ = decide.run(tmp_path)
            assert len(plans["plans"]) == 245
        assert fm.load_ensemble(tmp_path) == {}
