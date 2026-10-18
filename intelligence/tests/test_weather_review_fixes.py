"""Fixes from the adversarial review of weather_model.py and its use in decide.py.

  weather_model: the degree-day budget is charged from the stage the satellite date really marks (green-up, not
  emergence); a start before the observed record is unknown, not truncated; soybeans are combine-ready two weeks
  after maturity; dry-down uses forecast temperatures and slows in the cold; a stale forecast is named, not blamed on
  rain; a dry forecast reads as "dry through", not as a window ending on the horizon; the day that ends a window is
  named (shower or rain); frost has two tiers and says whether it can still hurt the grain; forecast dates may be
  unsorted; the satellite future date is never echoed as "combine-ready".
  decide: a maturing farm is never "combine-ready today" (the satellite window centre is its estimate); frost alone
  never sets act_now; harvested or never-planted farms do not hear "not ready to cut"; heavy rain today or tomorrow
  names the next fit day; moderate rain is named in the clear row; a ready farm with no workable window gets no window
  in the today line; "by the degree days" only when that path ran.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import decide
import weather_model as wm
from test_decide import DRY, QUIET_PASSES, check, rec, status_of
from test_outlook_in_plans import outlook

ROOT = Path(__file__).resolve().parents[2]
D = date.fromisoformat
TODAY = D("2026-10-17")
READY = dict(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")


def days(start, n):
    return [start + timedelta(days=i) for i in range(n)]


def observed(start, n, tmax=25.0, tmin=12.0, precip=0.0):
    return {"dates": [x.isoformat() for x in days(start, n)], "tmax_c": [tmax] * n, "tmin_c": [tmin] * n, "precip_mm": [precip] * n}


def forecast(start, precip, tmin=None, tmax=None):
    n = len(precip)
    out = {"dates": [x.isoformat() for x in days(start, n)], "precip_mm": list(precip)}
    if tmin is not None:
        out["tmin_c"] = list(tmin)
    if tmax is not None:
        out["tmax_c"] = list(tmax)
    return out


# ---------------------------------------------------------------- weather_model
class TestDegreeDayStart:
    def test_green_up_budget_is_shorter_than_the_emergence_budget(self):
        obs = observed(D("2026-06-01"), 150, tmax=28.0, tmin=16.0)          # 12 GDD / day
        from_green = wm.maturity_date(obs, D("2026-06-08"), crop="corn", start_stage="green_up")
        from_emergence = wm.maturity_date(obs, D("2026-06-08"), crop="corn", start_stage="emergence")
        assert from_green < from_emergence
        assert D("2026-09-15") <= from_green <= D("2026-09-25")             # ~1240 C / 12 = ~103 days

    def test_a_start_before_the_observed_record_is_unknown_not_truncated(self):
        obs = observed(D("2026-06-01"), 150, tmax=28.0, tmin=16.0)
        assert wm.maturity_date(obs, D("2026-05-20"), crop="corn") is None

    def test_green_up_keyword_in_harvest_outlook(self):
        obs = observed(D("2026-06-01"), 138, tmax=28.0, tmin=16.0)          # through Oct 16
        fc = forecast(TODAY, [0.0] * 7)
        out = wm.harvest_outlook(obs, fc, TODAY, crop="corn", green_up=D("2026-06-08"))
        assert out["maturity"] is not None and out["ready_source"] == "degree_days"


class TestDryDown:
    def test_soybeans_are_combine_ready_two_weeks_after_maturity(self):
        obs = observed(D("2026-09-20"), 40, tmax=20.0, tmin=8.0)
        assert wm.combine_ready_date(obs, D("2026-09-25"), crop="soybean") == D("2026-10-09")

    def test_cold_weather_slows_corn_dry_down_to_the_floor(self):
        cold = observed(D("2026-10-01"), 60, tmax=4.0, tmin=-4.0)           # mean 0 C -> floor 0.10 pt/day
        assert wm.combine_ready_date(cold, D("2026-10-01"), crop="corn") is None      # 7 points at 0.10/day is 70 days: not before winter
        warm = observed(D("2026-10-01"), 60, tmax=26.0, tmin=14.0)
        assert wm.combine_ready_date(warm, D("2026-10-01"), crop="corn") is not None

    def test_forecast_temperatures_drive_dry_down_beyond_the_record(self):
        obs = observed(D("2026-09-01"), 46, tmax=26.0, tmin=14.0)           # through Oct 16, mean 20 C
        cold_fc = forecast(TODAY, [0.0] * 14, tmin=[-4.0] * 14, tmax=[2.0] * 14)
        without = wm.combine_ready_date(obs, D("2026-10-14"), crop="corn")
        with_fc = wm.combine_ready_date(obs, D("2026-10-14"), crop="corn", fc=cold_fc)
        assert without == D("2026-10-26")                                    # 7 points at 0.6/day
        assert with_fc is not None and with_fc > D("2026-11-15")

    def test_outlook_says_not_field_dry_when_dry_down_runs_out(self):
        cold = observed(D("2026-06-01"), 138, tmax=4.0, tmin=-4.0)
        cold["tmax_c"][:108] = [28.0] * 108                                   # a warm summer to Sep 16 (maturity), then a cold autumn
        cold["tmin_c"][:108] = [16.0] * 108
        fc = forecast(TODAY, [0.0] * 7, tmin=[-4.0] * 7, tmax=[2.0] * 7)
        out = wm.harvest_outlook(cold, fc, TODAY, crop="corn", green_up=D("2026-06-05"))
        assert out["maturity"] is not None and out["combine_ready"] is None
        assert "dry" in out["summary"].lower() and out["window"] is None


class TestForecastEdges:
    def test_stale_forecast_is_named_not_blamed_on_rain(self):
        obs = observed(D("2026-09-01"), 46)
        fc = forecast(D("2026-10-01"), [0.0] * 14)                           # ends Oct 14, today is Oct 17
        out = wm.harvest_outlook(obs, fc, TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert out["forecast_stale"] is True and out["window"] is None
        assert out["workable_days_in_forecast"] == 0
        assert "2026-10-14" in out["summary"] and "rain" not in out["summary"].lower()

    def test_unsorted_forecast_dates_give_the_same_answer(self):
        obs = observed(D("2026-09-01"), 46)
        fc = forecast(TODAY, [0, 0, 20.0, 0, 0, 0, 0], tmin=[5, 4, 3, 2, 6, 7, 8])
        rev = {k: list(reversed(v)) for k, v in fc.items()}
        a = wm.harvest_outlook(obs, fc, TODAY, crop="corn", ready_override=D("2026-10-15"))
        b = wm.harvest_outlook(obs, rev, TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert a == b and a["window"] == {"start": "2026-10-17", "end": "2026-10-18"}


class TestWindowWords:
    def test_dry_forecast_reads_as_dry_through_not_a_window(self):
        obs = observed(D("2026-09-01"), 46)
        out = wm.harvest_outlook(obs, forecast(TODAY, [0.0] * 7), TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert out["window"] == {"start": "2026-10-17", "end": "2026-10-23"} and out["window_to_horizon"] is True
        assert "Combine-ready since 2026-10-15" in out["summary"]
        assert "Dry through 2026-10-23" in out["summary"] and "window" not in out["summary"].lower()

    def test_a_shower_that_ends_the_window_is_named(self):
        obs = observed(D("2026-09-01"), 46)
        out = wm.harvest_outlook(obs, forecast(TODAY, [0, 0, 6.0, 0, 0, 0, 0]), TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert out["window"] == {"start": "2026-10-17", "end": "2026-10-18"}
        assert out["rain_days"] == ["2026-10-19"] and out["rain_mm"] == {"2026-10-19": 6.0}
        assert "today and tomorrow (2026-10-17 to 2026-10-18)" in out["summary"]
        assert "shower (6 mm) on 2026-10-19" in out["summary"] and "before" not in out["summary"]
        assert "next window from 2026-10-20" in out["summary"]

    def test_heavy_rain_keeps_the_before_wording(self):
        obs = observed(D("2026-09-01"), 46)
        out = wm.harvest_outlook(obs, forecast(TODAY, [0, 0, 20.0, 0, 0, 0, 0]), TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert "before 20 mm of rain on 2026-10-19" in out["summary"] and "next window from 2026-10-21" in out["summary"]

    def test_one_day_window_says_today_only(self):
        assert wm.window_phrase(TODAY, TODAY, TODAY) == "today only (2026-10-17)"
        assert wm.window_phrase(TODAY, TODAY + timedelta(days=1), TODAY) == "today and tomorrow (2026-10-17 to 2026-10-18)"
        assert wm.window_phrase(TODAY + timedelta(days=2), TODAY + timedelta(days=4), TODAY) == "2026-10-19 to 2026-10-21"

    def test_satellite_future_date_is_not_echoed_as_combine_ready(self):
        obs = observed(D("2026-09-01"), 46)
        out = wm.harvest_outlook(obs, forecast(TODAY, [0.0] * 7), TODAY, crop="corn", ready_override=D("2026-10-25"))
        assert out["ready_source"] == "satellite" and out["window"] is None
        assert "since" not in out["summary"] and "2026-10-25" in out["summary"] and "Dry through 2026-10-23" in out["summary"]

    def test_no_workable_day_names_the_rain(self):
        obs = observed(D("2026-09-01"), 46)
        out = wm.harvest_outlook(obs, forecast(TODAY, [8.0, 30.0, 0, 15.0, 0, 6.0, 8.0]), TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert out["window"] is None and "rain on 2026-10-17" in out["summary"].lower()


class TestFrostTiers:
    def test_light_frost_and_hard_freeze_are_both_listed(self):
        fc = forecast(TODAY, [0.0] * 4, tmin=[3.0, -1.0, -3.0, 5.0])
        assert wm.frost_nights(fc) == [D("2026-10-18"), D("2026-10-19")]
        assert wm.hard_freezes(fc) == [D("2026-10-19")]

    def test_mature_crop_hears_the_frost_will_not_hurt_the_grain(self):
        obs = observed(D("2026-09-01"), 46)
        fc = forecast(TODAY, [0.0] * 4, tmin=[3.0, -1.0, -3.0, 5.0])
        out = wm.harvest_outlook(obs, fc, TODAY, crop="corn", ready_override=D("2026-10-15"))
        assert out["hard_freeze"] == ["2026-10-19"]
        assert "hard freeze" in out["summary"] and "will not hurt" in out["summary"]

    def test_crop_still_filling_hears_it_may_lose_yield(self):
        obs = observed(D("2026-09-01"), 46)
        fc = forecast(TODAY, [0.0] * 4, tmin=[3.0, -1.0, 2.0, 5.0])
        out = wm.harvest_outlook(obs, fc, TODAY, crop="corn", ready_override=D("2026-11-10"))     # harvest 3+ weeks out: not mature yet
        assert "may lose some yield" in out["summary"] and "nothing to do today" in out["summary"]

    def test_satellite_reading_beats_degree_day_maturity_for_the_frost_note(self):
        obs = observed(D("2026-06-01"), 138, tmax=28.0, tmin=16.0)          # warm season: degree days say mature in September
        fc = forecast(TODAY, [0.0] * 4, tmin=[3.0, -1.0, 2.0, 5.0])
        out = wm.harvest_outlook(obs, fc, TODAY, crop="corn", green_up=D("2026-06-08"), ready_override=D("2026-11-06"))
        assert out["maturity"] is not None and out["ready_source"] == "satellite"
        assert "may lose some yield" in out["summary"] and "will not hurt" not in out["summary"]

    def test_maturing_crop_within_a_fortnight_of_harvest_is_mature_for_the_frost(self):
        obs = observed(D("2026-09-01"), 46)
        fc = forecast(TODAY, [0.0] * 4, tmin=[3.0, -1.0, 2.0, 5.0])
        out = wm.harvest_outlook(obs, fc, TODAY, crop="corn", ready_override=D("2026-10-26"))
        assert "will not hurt" in out["summary"]


class TestFitDay:
    def test_first_fit_day_after_rain_follows_the_same_rule(self):
        precip = {D("2026-10-17"): 20.0, D("2026-10-18"): 0.0, D("2026-10-19"): 0.0}
        assert wm.first_fit_day(precip, D("2026-10-17")) == D("2026-10-19")          # 20 mm blocks the next day too
        precip = {D("2026-10-18"): 30.0}
        assert wm.first_fit_day(precip, D("2026-10-18")) == D("2026-10-21")          # >= 25 mm: two days after
        soaked = {D("2026-10-17") + timedelta(days=i): 40.0 for i in range(20)}
        assert wm.first_fit_day(soaked, D("2026-10-17")) is None


# ---------------------------------------------------------------- decide
def mock_copy(tmp_path, weather_edit=None):
    data = ROOT / "data" / "mock"
    for name in ("status.json", "timeseries.json", "fires.json", "fields.geojson", "insights.json"):
        (tmp_path / name).write_bytes((data / name).read_bytes())
    w = json.loads((data / "weather.json").read_text())
    if weather_edit:
        weather_edit(w)
    (tmp_path / "weather.json").write_text(json.dumps(w))
    return tmp_path


class TestGreenUp:
    def test_first_pass_already_green_is_unknown(self):
        assert decide.green_up_date([(D("2026-06-03"), 0.5, 0.3), (D("2026-06-08"), 0.6, 0.3)]) is None
        assert decide.green_up_date([(D("2026-06-03"), 0.2, 0.1), (D("2026-06-08"), 0.45, 0.2)]) == D("2026-06-08")
        assert decide.green_up_date([]) is None


class TestMaturingIsNotReadyToday:
    def test_maturing_farms_carry_the_window_centre_not_today(self, tmp_path):
        plans, _ = decide.run(mock_copy(tmp_path))
        status = {r["field_id"]: r for r in json.loads((tmp_path / "status.json").read_text())["fields"]}
        maturing = [p for p in plans["plans"] if status[p["farm_id"]]["stage"] == "maturing"]
        assert maturing
        for p in maturing:
            r = status[p["farm_id"]]
            o = p["harvest_outlook"]
            a, b = D(r["predicted_harvest_start"]), D(r["predicted_harvest_end"])
            assert o["ready_source"] == "satellite"
            assert D(o["combine_ready"]) == a + (b - a) // 2 and D(o["combine_ready"]) > D(p["as_of"])
            assert "Combine-ready since" not in o["summary"]
            assert "by the weather" not in check(p, "harvest_ready")["detail"]

    def test_ready_farms_are_ready_since_today(self, tmp_path):
        plans, _ = decide.run(mock_copy(tmp_path))
        ready = [p for p in plans["plans"] if status_of(p)["harvest_ready"] == "watch"]
        assert ready and all(p["harvest_outlook"]["combine_ready"] == "2026-10-17" for p in ready)

    def test_stale_forecast_means_no_outlooks_and_a_named_gap(self, tmp_path):
        def stale(w):
            w["forecast"]["dates"] = [(D("2026-10-01") + timedelta(days=i)).isoformat() for i in range(14)]
        plans, _ = decide.run(mock_copy(tmp_path, stale))
        assert not any("harvest_outlook" in p for p in plans["plans"])
        ready = [p for p in plans["plans"] if status_of(p)["harvest_ready"] == "watch"]
        assert ready and all(status_of(p)["harvest_before_rain"] == "clear" for p in ready)
        assert "2026-10-14" in check(ready[0], "harvest_before_rain")["detail"]


class TestFrostIsInformation:
    def test_frost_alone_never_sets_act_now_on_a_ready_crop(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -1.0), (D("2026-10-19"), 0.0, 4.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY)
        assert status_of(plan)["harvest_before_rain"] == "clear" and plan["flag"] == "watch"
        c = check(plan, "harvest_before_rain")
        assert "frost" in c["detail"].lower() and "will not hurt" in c["detail"]
        assert plan["today"].startswith("Crop looks ready")

    def test_hard_freeze_is_called_one(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -3.0)]
        assert "hard freeze" in check(decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY), "harvest_before_rain")["detail"]

    def test_a_half_degree_frost_is_not_printed_as_minus_zero(self):
        fc = [(D("2026-10-17"), 0.0, -0.5), (D("2026-10-18"), 0.0, 4.0)]
        detail = check(decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY), "harvest_before_rain")["detail"]
        assert "(-0.5 °C)" in detail and "-0 °C" not in detail

    def test_frost_on_a_maturing_crop_is_information(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -1.0)]
        c = check(decide.farm_plan(rec(), QUIET_PASSES, fc, [], None, TODAY), "harvest_before_rain")
        assert c["status"] == "clear" and "may lose some yield" in c["detail"] and "nothing to do today" in c["detail"]

    def test_maturing_crop_close_to_harvest_is_not_warned_about_yield(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 0.0, -1.0)]
        o = outlook(window=None, combine_ready="2026-10-26", ready_source="satellite")
        c = check(decide.farm_plan(rec(), QUIET_PASSES, fc, [], None, TODAY, outlook=o), "harvest_before_rain")
        assert "frost expected on 2026-10-18" in c["detail"] and "nothing to do today" in c["detail"]
        assert "lose some yield" not in c["detail"]

    def test_rain_and_frost_on_a_maturing_crop_read_as_one_sentence(self):
        fc = [(D("2026-10-17"), 0.0, 6.0), (D("2026-10-18"), 20.0, 3.0), (D("2026-10-19"), 0.0, -1.0)]
        c = check(decide.farm_plan(rec(), QUIET_PASSES, fc, [], None, TODAY), "harvest_before_rain")
        assert c["detail"].count("nothing to do today") == 1 and " and frost expected on 2026-10-19" in c["detail"]

    def test_run_on_mock_routes_nobody_for_frost(self, tmp_path):
        def frost(w):
            w["forecast"]["precip_mm"] = [0.0] * len(w["forecast"]["dates"])
            w["forecast"]["tmin_c"] = [6.0, -2.0] + [8.0] * (len(w["forecast"]["dates"]) - 2)
        plans, route = decide.run(mock_copy(tmp_path, frost))
        assert not any(status_of(p)["harvest_before_rain"] == "act_now" for p in plans["plans"])
        assert not any(s["kind"] == "harvest_before_rain" for s in route["stops"])


class TestRainRowWords:
    def test_harvested_farm_hears_already_harvested(self):
        done = rec(stage="harvested", harvest_date="2026-10-10", predicted_harvest_start=None, predicted_harvest_end=None)
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        c = check(decide.farm_plan(done, QUIET_PASSES, wet, [], None, TODAY), "harvest_before_rain")
        assert c["status"] == "clear" and "already harvested" in c["detail"] and "not ready" not in c["detail"]

    def test_never_planted_farm_hears_no_standing_crop(self):
        bare = rec(stage="unknown", peak_ndvi=0.2, peak_date=None, predicted_harvest_start=None, predicted_harvest_end=None)
        c = check(decide.farm_plan(bare, [(D("2026-10-16"), 0.15, 0.1)], DRY, [], None, TODAY), "harvest_before_rain")
        assert "No standing crop" in c["detail"] and "not ready" not in c["detail"]

    def test_maturing_farm_with_rain_coming(self):
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        c = check(decide.farm_plan(rec(), QUIET_PASSES, wet, [], None, TODAY), "harvest_before_rain")
        assert c["status"] == "clear" and "20 mm of rain" in c["detail"] and "not ready yet" in c["detail"] and "nothing to do today" in c["detail"]

    def test_maturing_farm_quiet(self):
        c = check(decide.farm_plan(rec(), QUIET_PASSES, DRY, [], None, TODAY), "harvest_before_rain")
        assert c["detail"].startswith("No heavy rain") and "next 3 days" in c["detail"] and "not ready to cut" not in c["detail"]

    def test_heavy_rain_today_names_the_next_fit_day(self):
        fc = [(D("2026-10-17"), 20.0), (D("2026-10-18"), 0.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY)
        assert status_of(plan)["harvest_before_rain"] == "act_now"
        assert plan["today"].startswith("Rain today (20 mm)") and "2026-10-19" in plan["today"]
        assert "Harvest before 2026-10-17" not in plan["today"]

    def test_heavy_rain_tomorrow_says_harvest_today(self):
        fc = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 22.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY)
        assert plan["today"].startswith("Harvest today:") and "tomorrow" in plan["today"] and "2026-10-20" in plan["today"]

    def test_moderate_rain_inside_the_lookahead_is_named_in_the_clear_row(self):
        fc = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 13.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0), (D("2026-10-21"), 0.0)]
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, fc, [], None, TODAY)
        c = check(plan, "harvest_before_rain")
        assert c["status"] == "clear" and "13 mm" in c["detail"] and "2026-10-18" in c["detail"]
        assert "today only" in c["detail"] and "fit again from 2026-10-20" in c["detail"]


class TestReadyRowWords:
    def test_no_workable_window_gives_no_window_in_the_today_line(self):
        o = outlook(window=None, next_window=None, rain_days=["2026-10-17", "2026-10-18", "2026-10-19"],
                    summary="Combine-ready since 2026-10-15. No workable day in the forecast: rain on 2026-10-17, 2026-10-18, 2026-10-19.")
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, outlook=o)
        assert check(plan, "harvest_ready")["window"] == {}
        assert "no workable day" in plan["today"].lower() and "for 2026" not in plan["today"]

    def test_dry_forecast_today_line_says_dry_through(self):
        o = outlook(window={"start": "2026-10-17", "end": "2026-10-30"}, next_window=None, rain_days=[], rain_mm={},
                    window_to_horizon=True, summary="Combine-ready since 2026-10-15. Dry through 2026-10-30: start as soon as the combine is free.")
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, outlook=o)
        assert "dry through 2026-10-30" in plan["today"] and "as soon as the combine is free" in plan["today"]
        assert "for 2026-10-17 to 2026-10-30" not in plan["today"]
        assert "dry weather through 2026-10-30" in check(plan, "harvest_ready")["detail"]

    def test_short_window_today_line_says_today_and_tomorrow(self):
        o = outlook(rain_mm={"2026-10-19": 20.0})
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, DRY, [], None, TODAY, outlook=o)
        assert "today and tomorrow (2026-10-17 to 2026-10-18)" in plan["today"]
        d = check(plan, "harvest_ready")["detail"]
        assert "before 20 mm of rain on 2026-10-19" in d and "next 2026-10-21 to 2026-10-23" in d

    def test_degree_days_clause_only_when_that_path_ran(self):
        sat = outlook(window=None, combine_ready="2026-10-24", ready_source="satellite")
        gdd = outlook(window=None, combine_ready="2026-10-24", ready_source="degree_days")
        assert "by the" not in check(decide.farm_plan(rec(), QUIET_PASSES, DRY, [], None, TODAY, outlook=sat), "harvest_ready")["detail"]
        assert "by the degree days" in check(decide.farm_plan(rec(), QUIET_PASSES, DRY, [], None, TODAY, outlook=gdd), "harvest_ready")["detail"]
