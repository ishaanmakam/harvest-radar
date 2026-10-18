"""workability.py: the chance of getting the combine in, from the forecast's rain probabilities.
  Monte Carlo over the 14 forecast days with weather_model's rain rule -> workable-day probabilities for the
  landscape, and per standing field the chance of finishing before the next rain and within 7 days."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import weather_model as wm
import workability

D = date.fromisoformat
TODAY = "2026-10-17"
ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"


# ----------------------------------------------------------------------------------------------------- fixtures
def cfg(**over):
    base = {"as_of": TODAY, "machine_rate_ha_per_day": 40.0, "thresholds": {"rain_act_now_mm": 15.0}}
    base.update(over)
    return base


def forecast(precip, prob=None, start=TODAY):
    n = len(precip)
    out = {"dates": [(D(start) + timedelta(days=i)).isoformat() for i in range(n)], "precip_mm": list(precip)}
    if prob is not None:
        out["precip_prob"] = list(prob)
    return out


def observed(precip_by_date=None):
    items = sorted((precip_by_date or {}).items())
    return {"dates": [k for k, _ in items], "precip_mm": [v for _, v in items]}


def weather(precip, prob=None, obs=None, start=TODAY):
    return {"observed": obs or observed(), "forecast": forecast(precip, prob, start)}


def dry(n=14):
    return weather([0.0] * n, [0] * n)


def feature(fid, area, zone="Z01", crop="corn"):
    ring = [[-93.5, 42.0], [-93.49, 42.0], [-93.49, 42.01], [-93.5, 42.01], [-93.5, 42.0]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": area, "crop": crop, "source": "test"}}


def fields(*specs):
    return {"type": "FeatureCollection", "features": [feature(*s) for s in specs]}


def status(*rows):
    """rows: (field_id, stage[, zone_id[, predicted_harvest_start]]). A ready field is ready today; a maturing field
    without a date is combine-ready about 2026-10-24 (day 7 of the forecast)."""
    out = []
    for row in rows:
        fid, stage = row[0], row[1]
        zone = row[2] if len(row) > 2 else "Z01"
        ready = row[3] if len(row) > 3 else (TODAY if stage == "ready" else "2026-10-24")
        out.append({"field_id": fid, "zone_id": zone, "stage": stage, "peak_date": "2026-08-01", "peak_ndvi": 0.8,
                    "harvest_date": None, "predicted_harvest_start": ready,
                    "predicted_harvest_end": None, "sowing_deadline": None, "burn_date": None,
                    "burn_evidence": "none", "confidence": 0.8, "notes": ""})
    return {"as_of": TODAY, "peak_to_harvest_days": 58, "peak_to_harvest_days_by_crop": {}, "fields": out}


def zones(*ids):
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None, "properties": {"zone_id": z, "name": z, "centroid": [-93.47, 42.02]}}
                         for z in ids]}


def data(w=None, f=None, st=None, z=None):
    return {"fields": f, "zones": z, "timeseries": None, "status": st, "weather": w, "fires": {"detections": []},
            "insights": None, "plans": None, "route": None, "truth": None, "usda": None, "chcs": None}


ONE_SMALL = (fields(("F1", 10.0)), status(("F1", "ready")))     # 10 ha at 40 ha/day -> 0.5 machine-days


def run(w, *fs, z=None, config=None):
    """run(weather) uses the one small field; run(weather, fields, status) passes both through, None included."""
    f, st = fs if fs else ONE_SMALL
    return workability.analyze(data(w, f, st, z), config or cfg())


# ----------------------------------------------------------------------------------------------- dry fortnight
class TestDryFortnight:
    def test_every_day_workable(self):
        land = run(dry())["landscape"]
        assert land["horizon_days"] == 14
        assert land["p_workable_by_day"] == [1.0] * 14
        assert land["expected_workable_days_7"] == 7.0
        assert land["expected_workable_days_14"] == 14.0
        assert land["best_case_workable_days"] == 14 and land["worst_case_workable_days"] == 14
        for k in (1, 2, 3):
            assert land[f"p_window_{k}_days_within_5"] == 1.0

    def test_days_needed_in_half_day_steps(self):
        # ceil(2 * area / 40) / 2: 10 ha -> 0.5, 20 -> 0.5, 30 -> 1.0, 60 -> 1.5, 24.12 -> 1.0, 2 ha -> 0.5 (floor)
        f = fields(("A", 10.0), ("B", 20.0), ("C", 30.0), ("E", 60.0), ("G", 24.12), ("H", 2.0))
        st = status(*((fid, "ready") for fid in "ABCEGH"))
        out = run(dry(), f, st)["fields"]
        assert {k: v["days_needed"] for k, v in out.items()} == {"A": 0.5, "B": 0.5, "C": 1.0, "E": 1.5, "G": 1.0, "H": 0.5}
        for v in out.values():
            assert v["p_finish_before_rain"] == 1.0 and v["p_finish_within_7d"] == 1.0
        assert out["A"]["words"].startswith("100% chance to finish the 0.5 machine-days before the next rain")
        assert "1 machine-day " in out["C"]["words"]

    def test_result_envelope(self):
        res = run(dry())
        assert res["key"] == "workability"
        assert set(res) == {"key", "fields", "landscape", "zones", "assumptions", "method"}
        assert isinstance(res["method"], str) and len(res["method"]) > 50
        assert all(isinstance(v, str) for v in res["assumptions"].values())
        assert "shower_fallback_mm" in res["assumptions"]


# ---------------------------------------------------------------------------------------------- certain rain
class TestCertainRain:
    def test_16mm_blocks_that_day_and_the_next(self):
        # day 2 rains 16 mm for sure: >= 5 blocks day 2, >= 12 blocks day 3, < 25 so day 4 is free
        w = weather([0, 0, 16.0] + [0] * 11, [0, 0, 100] + [0] * 11)
        f = fields(("S", 60.0), ("L", 100.0))                 # 1.5 and 2.5 machine-days
        res = run(w, f, status(("S", "ready"), ("L", "maturing", "Z01", TODAY)))   # L: maturing but ready today
        land = res["landscape"]
        assert land["p_workable_by_day"] == [1.0, 1.0, 0.0, 0.0] + [1.0] * 10
        assert land["expected_workable_days_7"] == 5.0
        assert land["expected_workable_days_14"] == 12.0
        assert land["best_case_workable_days"] == 12 and land["worst_case_workable_days"] == 12
        assert res["fields"]["S"]["p_finish_before_rain"] == 1.0        # first run is 2 days >= 1.5
        assert res["fields"]["L"]["p_finish_before_rain"] == 0.0        # 2 days < 2.5
        assert res["fields"]["L"]["p_finish_within_7d"] == 1.0          # run of 10 starts on day 4
        assert res["fields"]["L"]["stage"] == "maturing"
        assert res["fields"]["L"]["counted_from"] == TODAY
        assert res["fields"]["L"]["forecast_covers_days"] == 14

    def test_soaking_blocks_two_more_days(self):
        w = weather([0, 0, 30.0] + [0] * 11, [0, 0, 100] + [0] * 11)
        p = run(w)["landscape"]["p_workable_by_day"]
        assert p[2] == 0.0 and p[3] == 0.0 and p[4] == 0.0 and p[5] == 1.0

    def test_window_probabilities_within_five_days(self):
        # dry days 0 and 1, then 30 mm on days 2..6: blocked through day 8 (day 6 soaks days 7 and 8), free from day 9.
        # A 1- or 2-day window starts within the first 5 days; the first 3-day window starts on day 9.
        w = weather([0, 0] + [30.0] * 5 + [0] * 7, [0, 0] + [100] * 5 + [0] * 7)
        land = run(w)["landscape"]
        assert land["p_workable_by_day"][:10] == [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        assert land["p_window_1_days_within_5"] == 1.0
        assert land["p_window_2_days_within_5"] == 1.0
        assert land["p_window_3_days_within_5"] == 0.0
        assert land["expected_workable_days_7"] == 2.0 and land["expected_workable_days_14"] == 7.0

    def test_rain_today_means_no_chance_before_the_rain(self):
        w = weather([10.0] + [0] * 13, [100] + [0] * 13)
        res = run(w)
        fld = res["fields"]["F1"]
        assert fld["p_finish_before_rain"] == 0.0
        assert fld["p_finish_within_7d"] == 1.0                           # 10 mm < 12: day 1 is free
        assert fld["words"].startswith("0% chance to finish the 0.5 machine-days before the next rain")
        assert res["landscape"]["p_workable_by_day"][0] == 0.0


# ------------------------------------------------------------------------------------------------ probabilities
class TestProbabilities:
    def test_half_chance_shower_blocks_today_only(self):
        # 50% of 3 mm: when it rains the amount is the 6 mm fallback, which blocks day 0 and nothing after
        w = weather([3.0] + [0] * 13, [50] + [0] * 13)
        res = run(w)
        land = res["landscape"]
        assert land["p_workable_by_day"][0] == pytest.approx(0.5, abs=0.03)
        assert land["p_workable_by_day"][1] == 1.0
        assert land["expected_workable_days_14"] == pytest.approx(13.5, abs=0.1)
        assert land["expected_workable_days_7"] == pytest.approx(6.5, abs=0.1)
        assert land["best_case_workable_days"] == 14 and land["worst_case_workable_days"] == 13
        fld = res["fields"]["F1"]
        assert fld["p_finish_before_rain"] == pytest.approx(0.5, abs=0.03)
        assert fld["p_finish_within_7d"] == 1.0

    def test_half_chance_soaking_carries_into_tomorrow(self):
        w = weather([20.0] + [0] * 13, [50] + [0] * 13)
        p = run(w)["landscape"]["p_workable_by_day"]
        assert p[0] == pytest.approx(0.5, abs=0.03)
        assert p[1] == p[0]                                               # the same draws block both days
        assert p[2] == 1.0

    def test_independent_days_multiply(self):
        # two 50% showers: P(day 0 and day 1 both dry) = 0.25 -> a 1.5-day job (60 ha, two whole days) finishes before
        # rain 25% of the time; a 1-day job (30 ha) needs day 0 only -> 50%
        w = weather([3.0, 3.0] + [0] * 12, [50, 50] + [0] * 12)
        res = run(w, fields(("C", 30.0), ("E", 60.0)), status(("C", "ready"), ("E", "ready")))
        assert res["fields"]["E"]["days_needed"] == 1.5
        assert res["fields"]["E"]["p_finish_before_rain"] == pytest.approx(0.25, abs=0.03)
        assert res["fields"]["C"]["p_finish_before_rain"] == pytest.approx(0.5, abs=0.03)
        assert res["landscape"]["p_window_2_days_within_5"] == 1.0        # days 2..13 are dry for sure

    def test_missing_probability_falls_back_to_the_amount(self):
        assert run(weather([6.0] + [0] * 13))["landscape"]["p_workable_by_day"][0] == 0.0     # >= 5 mm: certain
        assert run(weather([4.0] + [0] * 13))["landscape"]["p_workable_by_day"][0] == 1.0     # < 5 mm: dry
        per_day = weather([6.0, 6.0] + [0] * 12, [None, 0] + [0] * 12)
        p = run(per_day)["landscape"]["p_workable_by_day"]
        assert p[0] == 0.0 and p[1] == 1.0                                # None -> amount rule; 0% -> dry

    def test_probability_is_clamped(self):
        w = weather([3.0, 3.0] + [0] * 12, [150, -5] + [0] * 12)
        p = run(w)["landscape"]["p_workable_by_day"]
        assert p[0] == 0.0 and p[1] == 1.0

    def test_deterministic_across_calls(self):
        w = weather([3.0, 8.0, 20.0] + [0] * 11, [40, 60, 30] + [0] * 11)
        assert run(w) == run(w)

    def test_thresholds_come_from_weather_model(self):
        assert workability.wm.SHOWER_MM == wm.SHOWER_MM
        assert workability.SHOWER_FALLBACK_MM > wm.SHOWER_MM
        assert workability.SHOWER_FALLBACK_MM < wm.RAIN_BLOCK_MM


# ------------------------------------------------------------------------------------- observed rain carries in
class TestLeadIn:
    def test_yesterdays_soaking_blocks_today(self):
        obs = observed({"2026-10-15": 0.0, "2026-10-16": 20.0})
        p = run(weather([0] * 14, [0] * 14, obs))["landscape"]["p_workable_by_day"]
        assert p[0] == 0.0 and p[1] == 1.0

    def test_downpour_two_days_ago_blocks_today(self):
        obs = observed({"2026-10-15": 30.0, "2026-10-16": 0.0})
        p = run(weather([0] * 14, [0] * 14, obs))["landscape"]["p_workable_by_day"]
        assert p[0] == 0.0 and p[1] == 1.0

    def test_downpour_yesterday_blocks_two_days(self):
        obs = observed({"2026-10-15": 0.0, "2026-10-16": 30.0})
        p = run(weather([0] * 14, [0] * 14, obs))["landscape"]["p_workable_by_day"]
        assert p[0] == 0.0 and p[1] == 0.0 and p[2] == 1.0

    def test_yesterdays_shower_does_not_carry(self):
        obs = observed({"2026-10-16": 8.0})
        res = run(weather([0] * 14, [0] * 14, obs))
        assert res["landscape"]["p_workable_by_day"][0] == 1.0
        assert res["landscape"]["wet_carry_over_days"] == 0
        assert res["fields"]["F1"]["counted_from"] == TODAY
        assert not res["fields"]["F1"]["words"].startswith("Too wet")

    # -- carry-over rain is not a coming rain: the sentence must say "too wet", not "0% before the next rain"
    def test_yesterdays_soaking_words_say_too_wet_not_next_rain(self):
        obs = observed({"2026-10-15": 0.0, "2026-10-16": 20.0})
        res = run(weather([0] * 14, [0] * 14, obs))
        fld, land = res["fields"]["F1"], res["landscape"]
        assert land["p_workable_by_day"][:2] == [0.0, 1.0]
        assert land["wet_carry_over_days"] == 1
        assert land["lead_in_mm"] == {"2026-10-15": 0.0, "2026-10-16": 20.0}
        assert any("2026-10-16" in n and "20 mm" in n for n in land["notes"])
        assert fld["counted_from"] == "2026-10-18"                        # the first day after the carry-over
        assert fld["p_finish_before_rain"] == 1.0                         # dry from then on
        assert fld["p_finish_within_7d"] == 1.0
        assert fld["words"] == ("Too wet today after 20 mm yesterday; from 2026-10-18 100% chance to finish the "
                                "0.5 machine-days before the next rain, 100% within 7 days.")

    def test_downpour_yesterday_two_wet_days(self):
        obs = observed({"2026-10-15": 0.0, "2026-10-16": 30.0})
        res = run(weather([0] * 14, [0] * 14, obs))
        assert res["landscape"]["wet_carry_over_days"] == 2
        fld = res["fields"]["F1"]
        assert fld["counted_from"] == "2026-10-19"
        assert fld["p_finish_before_rain"] == 1.0
        assert fld["words"].startswith("Too wet today and tomorrow after 30 mm yesterday; from 2026-10-19 100% chance")

    def test_downpour_two_days_ago_named(self):
        obs = observed({"2026-10-15": 30.0, "2026-10-16": 0.0})
        res = run(weather([0] * 14, [0] * 14, obs))
        assert res["landscape"]["wet_carry_over_days"] == 1
        assert res["fields"]["F1"]["words"].startswith("Too wet today after 30 mm two days ago; from 2026-10-18")

    def test_carry_over_then_forecast_rain_keeps_the_zero(self):
        # yesterday 20 mm blocks today; 16 mm certain tomorrow blocks days 1 and 2: from day 1 the first run is 0 long,
        # so 0% before the next rain is right (there is a next rain), and a run of 11 from day 3 gives 100% within 7 days
        obs = observed({"2026-10-16": 20.0})
        res = run(weather([0, 16.0] + [0] * 12, [0, 100] + [0] * 12, obs))
        fld = res["fields"]["F1"]
        assert fld["counted_from"] == "2026-10-18"
        assert fld["p_finish_before_rain"] == 0.0 and fld["p_finish_within_7d"] == 1.0
        assert fld["words"].startswith("Too wet today after 20 mm yesterday; from 2026-10-18 0% chance")
        assert "100% within 7 days" in fld["words"]

    def test_carry_over_longer_than_the_forecast(self):
        obs = observed({"2026-10-16": 20.0})
        res = run(weather([0.0], [0], obs))                                 # one visible day, and it is wet
        fld = res["fields"]["F1"]
        assert fld["counted_from"] is None
        assert fld["p_finish_before_rain"] is None and fld["p_finish_within_7d"] is None
        assert fld["words"].startswith("Too wet today after 20 mm yesterday;") and "too short" in fld["words"]
        json.dumps(res)


# ------------------------------------------------------------------------ short forecast: no claim beyond the horizon
class TestShortForecast:
    def test_three_day_forecast_censors_the_week_figure(self):
        # 3 dry days: a 0.5 or 2.5 day job is seen to finish before any rain (days 0..2 visible), but "within 7 days"
        # cannot be judged, and a 4-day job cannot be judged at all (not 0%)
        f = fields(("A", 10.0), ("L", 100.0), ("X", 160.0))             # 0.5, 2.5, 4 machine-days
        st = status(("A", "ready"), ("L", "ready"), ("X", "ready"))
        res = run(weather([0.0] * 3, [0] * 3), f, st)
        a, l, x = res["fields"]["A"], res["fields"]["L"], res["fields"]["X"]
        assert a["forecast_covers_days"] == l["forecast_covers_days"] == x["forecast_covers_days"] == 3
        assert a["p_finish_before_rain"] == 1.0 and a["p_finish_within_7d"] is None
        assert l["p_finish_before_rain"] == 1.0 and l["p_finish_within_7d"] is None
        assert x["p_finish_before_rain"] is None and x["p_finish_within_7d"] is None
        assert a["words"] == ("100% chance to finish the 0.5 machine-days before the next rain; "
                              "the forecast covers only 3 days.")
        assert "within 7 days" not in l["words"] and "covers only 3 days" in l["words"]
        assert x["words"] == "Forecast covers only 3 days, too short to judge the 4 machine-days."

    def test_three_day_forecast_with_rain_chances(self):
        # P(days 0..2 dry) = 0.5 * 0.7 = 0.35 for a 2.5-day job: the figure before the rain is exact, the week is not
        res = run(weather([3.0, 0.0, 3.0], [50, 0, 30]), fields(("L", 100.0)), status(("L", "ready")))
        l = res["fields"]["L"]
        assert l["p_finish_before_rain"] == pytest.approx(0.35, abs=0.03)
        assert l["p_finish_within_7d"] is None

    def test_seven_day_forecast_is_exact_only_when_the_job_fits(self):
        # a run starting on day 6 (the last start "within 7 days") must be visible to its end: 1 day fits, 2 do not
        f = fields(("A", 10.0), ("C", 30.0), ("E", 60.0))                 # 0.5, 1.0, 1.5 machine-days
        st = status(("A", "ready"), ("C", "ready"), ("E", "ready"))
        out = run(weather([0.0] * 7, [0] * 7), f, st)["fields"]
        assert out["A"]["p_finish_within_7d"] == 1.0 and out["C"]["p_finish_within_7d"] == 1.0
        assert out["E"]["p_finish_within_7d"] is None
        assert out["E"]["p_finish_before_rain"] == 1.0
        assert "100% within 7 days" in out["A"]["words"]

    def test_full_forecast_carries_the_flag_and_both_figures(self):
        out = run(dry(), fields(("L", 100.0)), status(("L", "ready")))["fields"]["L"]
        assert out["forecast_covers_days"] == 14 and out["counted_from"] == TODAY
        assert out["p_finish_before_rain"] == 1.0 and out["p_finish_within_7d"] == 1.0
        assert out["words"] == "100% chance to finish the 2.5 machine-days before the next rain, 100% within 7 days."

    def test_zone_means_skip_censored_fields(self):
        f = fields(("A", 10.0, "Z01"), ("E", 60.0, "Z01"))
        res = run(weather([0.0], [0]), f, status(("A", "ready"), ("E", "ready")), z=zones("Z01"))
        z = res["zones"]["Z01"]
        assert z["standing_fields"] == 2 and z["machine_days_needed"] == 2.0
        assert z["mean_p_finish_before_rain"] == 1.0                      # A only; E has no figure
        assert z["mean_p_finish_within_7d"] is None                       # neither has one
        assert z["share_likely_before_rain"] == 1.0
        assert not any("failed" in n for n in res["landscape"]["notes"])


# ----------------------------------------------------------------- maturing fields count from the combine-ready date
class TestMaturing:
    def test_maturing_counts_from_the_ready_date(self):
        # ready about day 7 (2026-10-24); the 16 mm on day 2 is before that. From day 7 the run is 7 dry days, so the
        # 1 machine-day finishes for sure, and a run starting on days 7..13 of length 1 is fully visible: 100% within 7
        w = weather([0, 0, 16.0] + [0] * 11, [0, 0, 100] + [0] * 11)
        res = run(w, fields(("L", 30.0)), status(("L", "maturing", "Z01", "2026-10-24")))
        l = res["fields"]["L"]
        assert l["stage"] == "maturing" and l["combine_ready_about"] == "2026-10-24"
        assert l["counted_from"] == "2026-10-24"
        assert l["p_finish_before_rain"] == 1.0 and l["p_finish_within_7d"] == 1.0
        assert l["words"] == ("Combine-ready about 2026-10-24; from then 100% chance to finish the 1 machine-day "
                              "before the next rain, 100% within 7 days.")

    def test_maturing_ready_on_a_rainy_day(self):
        # ready on day 2, the day of the 16 mm: the first run from then is 0 long; free again from day 4
        w = weather([0, 0, 16.0] + [0] * 11, [0, 0, 100] + [0] * 11)
        res = run(w, fields(("L", 30.0)), status(("L", "maturing", "Z01", "2026-10-19")))
        l = res["fields"]["L"]
        assert l["counted_from"] == "2026-10-19"
        assert l["p_finish_before_rain"] == 0.0 and l["p_finish_within_7d"] == 1.0
        assert l["words"].startswith("Combine-ready about 2026-10-19; from then 0% chance")

    def test_maturing_ready_today_reads_like_ready(self):
        w = weather([3.0] + [0] * 13, [50] + [0] * 13)
        f = fields(("R", 10.0), ("M", 10.0))
        res = run(w, f, status(("R", "ready"), ("M", "maturing", "Z01", TODAY)))
        r, m = res["fields"]["R"], res["fields"]["M"]
        assert m["counted_from"] == TODAY
        assert m["p_finish_before_rain"] == r["p_finish_before_rain"] and m["words"] == r["words"]
        assert not m["words"].startswith("Combine-ready")

    def test_maturing_ready_date_in_the_past_counts_from_today(self):
        res = run(dry(), fields(("M", 10.0)), status(("M", "maturing", "Z01", "2026-10-10")))
        assert res["fields"]["M"]["counted_from"] == TODAY
        assert res["fields"]["M"]["words"].startswith("100% chance")

    def test_maturing_without_a_ready_date_counts_from_today(self):
        res = run(dry(), fields(("M", 10.0)), status(("M", "maturing", "Z01", None)))
        m = res["fields"]["M"]
        assert m["combine_ready_about"] is None and m["counted_from"] == TODAY
        assert m["p_finish_before_rain"] == 1.0

    def test_maturing_ready_after_the_forecast(self):
        res = run(dry(), fields(("M", 10.0)), status(("M", "maturing", "Z01", "2026-11-05")))
        m = res["fields"]["M"]
        assert m["counted_from"] is None
        assert m["p_finish_before_rain"] is None and m["p_finish_within_7d"] is None
        assert m["words"] == "Combine-ready about 2026-11-05, after the 14-day forecast ends: no figure yet."
        json.dumps(res)

    def test_maturing_late_in_the_forecast_censors_the_week(self):
        # ready on day 10: a 1-day run from day 10 is visible (100% before rain); runs starting on days 10..16 are not
        res = run(dry(), fields(("M", 30.0)), status(("M", "maturing", "Z01", "2026-10-27")))
        m = res["fields"]["M"]
        assert m["p_finish_before_rain"] == 1.0 and m["p_finish_within_7d"] is None
        assert m["words"] == ("Combine-ready about 2026-10-27; from then 100% chance to finish the 1 machine-day "
                              "before the next rain; the forecast covers only 14 days.")

    def test_wet_start_and_later_ready_date_name_the_later_one(self):
        obs = observed({"2026-10-16": 20.0})                                # carry-over blocks day 0 only
        res = run(weather([0] * 14, [0] * 14, obs), fields(("M", 10.0)), status(("M", "maturing", "Z01", "2026-10-20")))
        m = res["fields"]["M"]
        assert m["counted_from"] == "2026-10-20"
        assert m["words"].startswith("Combine-ready about 2026-10-20; from then")


# ------------------------------------------------------------------------------------------------------- edges
class TestEdges:
    def _empty(self, res, word):
        land = res["landscape"]
        assert res["fields"] == {} and res["zones"] == {}
        assert land["expected_workable_days_14"] is None and land["p_workable_by_day"] is None
        assert land["horizon_days"] == 0
        assert land["wet_carry_over_days"] is None and land["lead_in_mm"] is None
        assert any(word in n for n in land["notes"])

    def test_no_weather(self):
        self._empty(run(None), "forecast")

    def test_weather_without_forecast(self):
        self._empty(run({"observed": observed()}), "forecast")

    def test_empty_forecast(self):
        self._empty(run({"observed": observed(), "forecast": {"dates": [], "precip_mm": []}}), "forecast")

    def test_stale_forecast(self):
        self._empty(run(weather([0] * 14, [0] * 14, start="2026-10-01")), "before")

    def test_one_day_forecast(self):
        res = run(weather([0.0], [0]), fields(("A", 10.0), ("E", 60.0)), status(("A", "ready"), ("E", "ready")))
        land = res["landscape"]
        assert land["horizon_days"] == 1 and land["p_workable_by_day"] == [1.0]
        assert land["expected_workable_days_7"] == 1.0 and land["expected_workable_days_14"] == 1.0
        assert land["p_window_1_days_within_5"] == 1.0 and land["p_window_2_days_within_5"] == 0.0
        assert any("1 of 14" in n for n in land["notes"])
        a, e = res["fields"]["A"], res["fields"]["E"]
        assert a["p_finish_before_rain"] == 1.0                           # half a day fits in the one visible day
        assert a["p_finish_within_7d"] is None                            # a week is not visible
        assert e["p_finish_before_rain"] is None                          # 1.5 days needs two, only one is visible: unknown, not 0
        assert e["p_finish_within_7d"] is None
        assert a["forecast_covers_days"] == 1 and e["forecast_covers_days"] == 1
        assert "within 7 days" not in a["words"] and "within 7 days" not in e["words"]
        assert "0%" not in e["words"] and "too short" in e["words"]

    def test_forecast_starting_tomorrow_still_counts_from_as_of(self):
        res = run(weather([16.0] + [0] * 13, [100] + [0] * 13, start="2026-10-18"))
        land = res["landscape"]
        assert land["horizon_start"] == TODAY and land["horizon_days"] == 14
        assert land["p_workable_by_day"][:3] == [1.0, 0.0, 0.0]          # day 0 unknown -> dry, day 1 the 16 mm
        assert any("no forecast" in n for n in land["notes"])

    def test_forecast_longer_than_horizon_is_cut(self):
        land = run(weather([0] * 20, [0] * 20))["landscape"]
        assert land["horizon_days"] == 14 and len(land["p_workable_by_day"]) == 14
        assert land["horizon_end"] == "2026-10-30"

    def test_field_missing_from_geojson(self):
        res = run(dry(), fields(("F1", 10.0)), status(("F1", "ready"), ("F9", "ready")))
        assert res["fields"]["F9"] is None
        assert res["fields"]["F1"]["p_finish_before_rain"] == 1.0
        assert any("F9" in n or "no area" in n for n in res["landscape"]["notes"])

    def test_area_missing_or_bad(self):
        res = run(dry(), fields(("N", None), ("B", "wide")), status(("N", "ready"), ("B", "ready")))
        assert res["fields"]["N"] is None and res["fields"]["B"] is None

    def test_only_standing_stages_and_any_crop(self):
        f = fields(("R", 10.0, "Z01", "corn"), ("M", 10.0, "Z01", "soybean"), ("O", 10.0, "Z01", "other"),
                   ("H", 10.0), ("G", 10.0), ("U", 10.0))
        st = status(("R", "ready"), ("M", "maturing"), ("O", "ready"), ("H", "harvested"), ("G", "growing"), ("U", "unknown"))
        out = run(dry(), f, st)["fields"]
        assert set(out) == {"R", "M", "O"}
        assert out["O"]["crop"] == "other"
        assert out["M"]["combine_ready_about"] == "2026-10-24"

    def test_no_status(self):
        res = run(dry(), fields(("F1", 10.0)), None)
        assert res["fields"] == {}
        assert res["landscape"]["expected_workable_days_14"] == 14.0
        assert any("status" in n for n in res["landscape"]["notes"])

    def test_no_fields_file(self):
        res = run(dry(), None, status(("F1", "ready")))
        assert res["fields"] == {"F1": None}

    def test_machine_rate_default(self):
        res = run(dry(), fields(("C", 30.0)), status(("C", "ready")), config={"as_of": TODAY})
        assert res["fields"]["C"]["days_needed"] == 1.0

    def test_as_of_falls_back_to_status(self):
        res = run(dry(), fields(("F1", 10.0)), status(("F1", "ready")), config={"machine_rate_ha_per_day": 40.0})
        assert res["landscape"]["horizon_start"] == TODAY

    def test_zones_summarise_standing_fields(self):
        f = fields(("A", 10.0, "Z01"), ("B", 60.0, "Z01"), ("C", 30.0, "Z02"))
        st = status(("A", "ready", "Z01"), ("B", "ready", "Z01"), ("C", "maturing", "Z02"))
        w = weather([0, 0, 16.0] + [0] * 11, [0, 0, 100] + [0] * 11)    # first run is 2 days
        zs = run(w, f, st, z=zones("Z01", "Z02", "Z03"))["zones"]
        assert set(zs) == {"Z01", "Z02", "Z03"}
        assert zs["Z01"]["standing_fields"] == 2 and zs["Z01"]["machine_days_needed"] == 2.0
        assert zs["Z01"]["mean_p_finish_before_rain"] == 1.0            # 0.5 and 1.5 both fit in 2 days
        assert zs["Z02"]["standing_fields"] == 1
        assert zs["Z03"]["standing_fields"] == 0 and zs["Z03"]["mean_p_finish_before_rain"] is None

    def test_plain_json_types(self):
        res = run(weather([3.0, 8.0, 20.0] + [0] * 11, [40, 60, 30] + [0] * 11))
        plain = (float, int, str, bool, type(None))

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    assert type(k) is str
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            else:
                assert type(x) in plain, (x, type(x))
        walk(res)
        json.dumps(res)

    def test_garbage_input_does_not_raise(self):
        res = workability.analyze({"weather": {"forecast": {"dates": ["bad", None], "precip_mm": "x"}},
                                   "status": {"fields": [{"field_id": "Q", "stage": "ready"}]},
                                   "fields": {"features": [{"properties": None}]}}, {"as_of": "2026-10-17"})
        assert res["key"] == "workability"
        assert isinstance(res["landscape"]["notes"], list)


# --------------------------------------------------------------------------------------------- data/mock, read-only
@pytest.mark.skipif(not (MOCK / "weather.json").exists(), reason="data/mock is missing on this box")
def test_mock_data_shapes_and_ranges():
    def load(n, default=None):
        p = MOCK / n
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    d = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
         "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
         "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
         "truth": load("truth.json"), "usda": load("usda_crop_progress.json"), "chcs": None}
    config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
    res = workability.analyze(d, config)
    land = res["landscape"]
    n = land["horizon_days"]
    assert 1 <= n <= 14 and len(land["p_workable_by_day"]) == n
    assert all(0.0 <= p <= 1.0 for p in land["p_workable_by_day"])
    assert 0.0 <= land["expected_workable_days_7"] <= 7.0
    assert 0.0 <= land["expected_workable_days_14"] <= n
    assert 0 <= land["worst_case_workable_days"] <= land["best_case_workable_days"] <= n
    assert 1.0 >= land["p_window_1_days_within_5"] >= land["p_window_2_days_within_5"] >= land["p_window_3_days_within_5"] >= 0.0
    standing = {f["field_id"] for f in d["status"]["fields"] if f.get("stage") in ("ready", "maturing")}
    assert set(res["fields"]) == standing and standing
    for fid, v in res["fields"].items():
        assert v is not None, fid
        assert v["days_needed"] >= 0.5 and v["days_needed"] * 2 == int(v["days_needed"] * 2)
        assert v["forecast_covers_days"] == n
        pb, pw = v["p_finish_before_rain"], v["p_finish_within_7d"]
        # None only when the forecast cannot show the whole job from the day the count starts
        if pb is not None:
            assert 0.0 <= pb <= 1.0
        if pw is not None:
            assert 0.0 <= pw <= 1.0 and pb is not None and pw >= pb - 1e-9
        if v["counted_from"] is not None:
            assert land["horizon_start"] <= v["counted_from"] <= land["horizon_end"]
        assert ("chance" in v["words"] or "forecast" in v["words"]) and len(v["words"]) < 160
        assert len(json.dumps(v)) < 1024
    zone_ids = {z["properties"]["zone_id"] for z in d["zones"]["features"]}
    assert zone_ids <= set(res["zones"])
    assert sum(z["standing_fields"] for z in res["zones"].values()) == len(standing)
    json.dumps(res)
