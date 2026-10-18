"""harvest_economics.py: harvest now or wait, in dollars, for every standing corn and soybean field.
Hand-built fixtures pin the dry-down, drying and shrink arithmetic, the field-loss rules, the no-window probability
and the recommendation band; the edge cases degrade to None plus a note; one read-only pass over data/mock checks
shapes and ranges (never exact numbers: the mock is rebuilt often)."""
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

import harvest_economics as econ
import weather_model as wm

ROOT = Path(__file__).resolve().parents[2]
D = date.fromisoformat
TODAY = D("2026-10-17")
EM_DASH = chr(0x2014)                      # farmer-facing text never carries one
CONFIG = {"as_of": "2026-10-17", "machine_rate_ha_per_day": 40.0,
          "thresholds": {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25, "rain_act_now_mm": 15.0,
                         "rain_lookahead_days": 3, "frost_tmin_c": 0.0}}


# ----------------------------------------------------------------------------------------------- fixtures
def feature(fid, crop, area_ha=20.0, zone="Z01"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-93.5, 42.0], [-93.49, 42.0],
            [-93.49, 42.01], [-93.5, 42.01], [-93.5, 42.0]]]},
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": area_ha, "crop": crop, "source": "test"}}


def rec(fid, stage="ready", start="2026-10-17", end="2026-10-20", zone="Z01", peak="2026-08-09"):
    """One status.json row. The fixture status carries peak_to_harvest_days 69, so the default peak 2026-08-09 puts
    a ready field's unclamped harvest estimate (peak + lag) on 2026-10-17, the same day as the clamped window start
    status writes for every ready field. Overdue fields get an earlier peak, the way the real file reads."""
    return {"field_id": fid, "zone_id": zone, "stage": stage, "peak_date": peak, "peak_ndvi": 0.9,
            "harvest_date": None, "predicted_harvest_start": start, "predicted_harvest_end": end,
            "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.7, "notes": ""}


def weather(tmean=20.0, fc_days=14, precip=None, prob=None, tmin=None, tmax=None, obs_start="2026-09-01"):
    """Observed record obs_start .. 2026-10-16 at a constant mean temperature (tmax = tmean + 8, tmin = tmean - 8),
    then fc_days of forecast from 2026-10-17. Index 0 of a forecast list is today (the harvest-now day); the wait
    week is indices 1..7. Lists given override the forecast arrays from today on and are padded to fc_days with
    dry, frost-free days."""
    n = (D("2026-10-16") - D(obs_start)).days + 1
    obs = {"dates": [(D(obs_start) + timedelta(days=i)).isoformat() for i in range(n)],
           "precip_mm": [0.0] * n, "tmax_c": [tmean + 8] * n, "tmin_c": [tmean - 8] * n}

    def pad(vals, fill):
        vals = list(vals) if vals is not None else []
        return (vals + [fill] * fc_days)[:fc_days]

    fc = {"dates": [(TODAY + timedelta(days=i)).isoformat() for i in range(fc_days)],
          "precip_mm": pad(precip, 0.0), "precip_prob": pad(prob, 0),
          "tmin_c": pad(tmin, tmean - 8), "tmax_c": pad(tmax, tmean + 8)}
    return {"observed": obs, "forecast": fc}


def dataset(features, recs, wx=None, **extra):
    data = {"fields": {"type": "FeatureCollection", "features": features},
            "zones": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"zone_id": "Z01", "name": "Zone A1", "centroid": [-93.5, 42.05]}},
                {"type": "Feature", "properties": {"zone_id": "Z02", "name": "Zone A2", "centroid": [-93.47, 42.05]}}]},
            "timeseries": {"source": "test", "dates": ["2026-10-01", "2026-10-11", "2026-10-16"],
                           "fields": {f["properties"]["field_id"]: {"ndvi": [0.5, 0.4, 0.3], "nbr": [0.3, 0.2, 0.1],
                                                                     "clear_frac": [0.9, 0.9, 0.9]} for f in features}},
            "status": {"as_of": "2026-10-17", "peak_to_harvest_days": 69.0, "fields": recs},
            "weather": wx if wx is not None else weather(),
            "fires": {"source": "test", "detections": []}, "insights": {"items": []},
            "plans": None, "route": None, "truth": None, "usda": None, "chcs": None}
    data.update(extra)
    return data


def yield_prior(**bu):
    return {"yield": {"key": "yield", "fields": {fid: {"production_bu": b} for fid, b in bu.items()}}}


def corn_ready():
    """Corn C1, 20 ha, status ready with harvest estimate 2026-10-17 (peak 2026-08-09 + 69 d): maturity = 2026-10-03,
    Tmean 20 C all season so the kernels dry 0.6 points a day. 10000 bushels from the yield module -> crop value
    42000 USD."""
    return dataset([feature("C1", "corn")], [rec("C1")]), yield_prior(C1=10000)


def corn_at_the_floor():
    """The same field, overdue: peak 2026-06-07 + 69 d = estimate 2026-08-15, maturity 2026-08-01, 77 days at
    0.6/day would be 32 - 46 -> floored at the storage standard, so no drying cost either way."""
    data, prior = corn_ready()
    data["status"]["fields"] = [rec("C1", peak="2026-06-07")]
    return data, prior


# ----------------------------------------------------------------------------------------------- dry-down
class TestMoisture:
    def test_corn_dries_from_32_at_maturity(self):
        # maturity 2026-10-03, 14 days to today at 0.6/day -> 32 - 8.4 = 23.6; 21 days to 2026-10-24 -> 19.4
        data, prior = corn_ready()
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["harvest_estimate"] == "2026-10-17" and f["estimate_source"] == "peak_lag"
        assert f["maturity"] == "2026-10-03"
        assert f["moisture_today_pct"] == pytest.approx(23.6, abs=0.05)
        assert f["moisture_in_7d_pct"] == pytest.approx(19.4, abs=0.05)
        # first day at or under 25%: 32 - 0.6 k <= 25 -> k = 12 -> 2026-10-15, by the curve itself
        assert f["combine_ready"] == "2026-10-15" and f["ready_source"] == "drydown"

    def test_corn_uses_weather_model_drydown_numbers(self):
        c = wm.CROPS["corn"]
        curve = econ.corn_moisture_curve(D("2026-10-01"), {}, 20.0, 2)        # k = 0, 1, 2 days after maturity
        assert list(curve) == pytest.approx([c["moisture_at_maturity"], c["moisture_at_maturity"] - 0.6,
                                             c["moisture_at_maturity"] - 1.2])
        cold = econ.corn_moisture_curve(D("2026-10-01"), {}, 1.0, 1)          # 0.03 x 1 C = 0.03 -> floor 0.10
        assert cold[1] == pytest.approx(c["moisture_at_maturity"] - c["drydown_min"])

    def test_corn_never_drops_below_the_field_floor(self):
        data, prior = corn_at_the_floor()
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["harvest_estimate"] == "2026-08-15" and f["maturity"] == "2026-08-01"
        assert f["moisture_today_pct"] == pytest.approx(econ.STORAGE_MOISTURE_PCT["corn"])
        assert f["moisture_in_7d_pct"] == pytest.approx(econ.STORAGE_MOISTURE_PCT["corn"])

    def test_corn_before_maturity_sits_at_the_maturity_moisture(self):
        # growing, window 2026-10-31 .. 2026-11-14 -> centre 2026-11-07 -> maturity 2026-10-24 (after today)
        data, prior = corn_ready()
        data["status"]["fields"] = [rec("C1", stage="growing", start="2026-10-31", end="2026-11-14")]
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["maturity"] == "2026-10-24" and f["estimate_source"] == "status"
        assert f["moisture_today_pct"] == pytest.approx(32.0) and f["moisture_in_7d_pct"] == pytest.approx(32.0)
        assert f["combine_ready"] == "2026-11-05"                 # 12 days at 0.6/day after maturity
        assert f["recommendation"] == "wait" and "2026-11-05" in f["reason"]

    def test_soybean_linear_to_13_a_week_after_maturity(self):
        # maturing, window 2026-10-20 .. 2026-11-03 -> centre 2026-10-27 -> maturity 2026-10-13; today is day 4:
        # 60 - 47 x 4/7 = 33.14; day 11 -> 13.0; ready 2026-10-20
        data = dataset([feature("S1", "soybean")], [rec("S1", stage="maturing", start="2026-10-20", end="2026-11-03")])
        f = econ.analyze(data, CONFIG, yield_prior(S1=10000))["fields"]["S1"]
        assert f["maturity"] == "2026-10-13" and f["combine_ready"] == "2026-10-20"
        assert f["moisture_today_pct"] == pytest.approx(60 - 47 * 4 / 7, abs=0.05)
        assert f["moisture_in_7d_pct"] == pytest.approx(13.0)

    def test_soybean_ready_field_is_at_storage_moisture(self):
        data = dataset([feature("S1", "soybean")], [rec("S1")])             # maturity 2026-10-03, 13% from 2026-10-10
        f = econ.analyze(data, CONFIG, yield_prior(S1=10000))["fields"]["S1"]
        assert f["moisture_today_pct"] == pytest.approx(13.0) and f["drying_usd_now"] == 0.0 and f["shrink_usd_now"] == 0.0


# ----------------------------------------------------------------------------------------------- ready stage
class TestReadyStage:
    """status.json clamps a ready field's window start to as_of, so the start alone says nothing about how long
    the crop has stood. The module reads the estimate from the peak date plus the crop's lag instead, and takes the
    satellite's word that a ready crop can be combined today."""

    def test_ready_corn_dry_down_starts_from_the_unclamped_window(self):
        # peak 2026-07-20 + corn lag 76 d = 2026-10-04 (status window still 2026-10-17 .. 2026-10-20)
        # -> maturity 2026-09-20, 27 days at 0.6 -> 32 - 16.2 = 15.8%; curve-ready 32 - 0.6 k <= 25 -> k = 12 -> 2026-10-02
        data, prior = corn_ready()
        data["status"]["peak_to_harvest_days_by_crop"] = {"corn": 76.0, "soybean": 60.0}
        data["status"]["fields"] = [rec("C1", peak="2026-07-20")]
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["harvest_estimate"] == "2026-10-04" and f["estimate_source"] == "peak_lag"
        assert f["maturity"] == "2026-09-20"
        assert f["moisture_today_pct"] == pytest.approx(15.8, abs=0.05)
        assert f["combine_ready"] == "2026-10-02" and f["ready_source"] == "drydown"
        assert f["field_loss_pct_7d"] == pytest.approx(0.5)

    def test_two_ready_fields_with_different_peaks_price_differently(self):
        # the old reading gave every ready corn field the same 27% because the clamped start hid the peak date
        data = dataset([feature("C1", "corn"), feature("C2", "corn")],
                       [rec("C1", peak="2026-08-09"), rec("C2", peak="2026-07-20")])
        out = econ.analyze(data, CONFIG, yield_prior(C1=10000, C2=10000))["fields"]
        assert out["C1"]["moisture_today_pct"] > out["C2"]["moisture_today_pct"] + 5
        assert out["C1"]["cost_now_usd"] > out["C2"]["cost_now_usd"]

    def test_ready_corn_is_combine_ready_today_whatever_the_curve_says(self):
        # cold season, Tmean 5 C -> 0.15/day: 14 days after maturity the curve reads 32 - 2.1 = 29.9% and would not
        # reach 25% until day 47. Status says ready, so the field is combine-ready today: all 7 wait days stand
        # (0.5% = 210) and the premium is live (rain certain on every wait day -> 1.0 x 210); forecast nights
        # stay at 0 C so no freeze event joins in
        data, prior = corn_ready()
        data["weather"] = weather(tmean=5.0, precip=[0.0] + [16.0] * 7, prob=[0] + [100] * 7,
                                  tmin=[0.0] * 14, tmax=[10.0] * 14)
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["moisture_today_pct"] == pytest.approx(29.9, abs=0.05)
        assert f["combine_ready"] == "2026-10-17" and f["ready_source"] == "satellite"
        assert f["field_loss_pct_7d"] == pytest.approx(0.5) and f["field_loss_usd_7d"] == pytest.approx(210.0)
        assert f["p_no_window_7d"] == pytest.approx(1.0) and f["risk_premium_usd"] == pytest.approx(210.0)
        assert f["recommendation"] in ("harvest_now", "wait", "either") and "not combine-ready" not in f["reason"]

    def test_ready_soybean_is_combine_ready_today_too(self):
        # maturing-style window but status ready, peak 2026-08-05 + 69 = 2026-10-13 -> maturity 2026-09-29, beans
        # read 13% from 2026-10-06 by the curve (before today: the curve date stands)
        data = dataset([feature("S1", "soybean")], [rec("S1", peak="2026-08-05")])
        f = econ.analyze(data, CONFIG, yield_prior(S1=10000))["fields"]["S1"]
        assert f["combine_ready"] == "2026-10-06" and f["ready_source"] == "drydown"
        assert f["field_loss_pct_7d"] == pytest.approx(1.0)

    def test_ready_field_without_a_peak_falls_back_to_the_window_start(self):
        data, prior = corn_ready()
        data["status"]["fields"] = [rec("C1", peak=None)]
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["harvest_estimate"] == "2026-10-17" and f["estimate_source"] == "status"
        assert f["maturity"] == "2026-10-03"
        data["status"]["peak_to_harvest_days"] = None                      # a peak but no lag: the same fallback
        data["status"]["fields"] = [rec("C1")]
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["harvest_estimate"] == "2026-10-17" and f["estimate_source"] == "status"

    def test_wait_sentence_names_field_losses_only_when_there_are_some(self):
        kw = dict(crop="corn", rec="wait", m_now=23.6, m_7d=19.4, savings=3755.29, band=1260.0,
                  maturity=D("2026-10-03"), ready=D("2026-10-15"), today=TODAY, standing_days=7, events=[], p_no=0.0)
        assert "after field losses" in econ._reason(loss_usd=210.0, **kw)
        assert "after field losses" not in econ._reason(loss_usd=0.0, **kw)


# ----------------------------------------------------------------------------------------------- dollars
class TestCosts:
    def test_wait_when_field_drying_beats_field_loss(self):
        # now: 8.6 points over 15 -> drying 10000 x 8.6 x 0.045 = 3870; shrink 10000 x 8.6/85 x 4.20 = 4249.41
        # in 7 d: 4.4 points -> drying 1980; shrink 2174.12; field loss 0.5% of 42000 = 210; no rain -> no premium
        # cost_now 8119.41, cost_wait 4364.12 -> waiting saves 3755.29 (> 3% band of 1260) -> wait
        data, prior = corn_ready()
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["bushels"] == 10000 and f["bushels_source"] == "yield_module"
        assert f["crop_value_usd"] == pytest.approx(42000.0)
        assert f["drying_usd_now"] == pytest.approx(3870.0, abs=0.5)
        assert f["shrink_usd_now"] == pytest.approx(4249.41, abs=0.5)
        assert f["drying_usd_7d"] == pytest.approx(1980.0, abs=0.5)
        assert f["shrink_usd_7d"] == pytest.approx(2174.12, abs=0.5)
        assert f["field_loss_pct_7d"] == pytest.approx(0.5) and f["field_loss_usd_7d"] == pytest.approx(210.0)
        assert f["p_no_window_7d"] == 0.0 and f["risk_premium_usd"] == 0.0
        assert f["cost_now_usd"] == pytest.approx(8119.41, abs=0.5)
        assert f["cost_wait_7d_usd"] == pytest.approx(4364.12, abs=0.5)
        assert f["recommendation"] == "wait" and f["savings_usd"] == pytest.approx(3755.29, abs=1.0)
        assert f["reason"] and EM_DASH not in f["reason"] and "after field losses" in f["reason"]

    def test_harvest_now_when_beans_face_wet_dry_spells(self):
        # beans at 13% both ways (no drying); 10000 bu x 10.40 = 104000; 3% band = 3120
        # three wet (6 mm) -> dry days in the wait week (indices 1, 3, 5): 1% + 3 x 2% = 7% = 7280; the rain days
        # are certain but light (< 12 mm, no carry) so days 2, 4, 6, 7 are open: a 1-day window is certain ->
        # premium 0 -> harvest now
        data = dataset([feature("S1", "soybean")], [rec("S1")],
                       weather(precip=[0.0, 6.0, 0.0, 6.0, 0.0, 6.0, 0.0, 0.0], prob=[0, 100, 0, 100, 0, 100, 0, 0]))
        f = econ.analyze(data, CONFIG, yield_prior(S1=10000))["fields"]["S1"]
        assert f["field_loss_pct_7d"] == pytest.approx(7.0) and f["field_loss_usd_7d"] == pytest.approx(7280.0)
        assert f["loss_events"] == ["2026-10-18", "2026-10-20", "2026-10-22"]
        assert f["p_no_window_7d"] == 0.0
        assert f["recommendation"] == "harvest_now" and f["savings_usd"] == pytest.approx(7280.0, abs=0.5)
        assert f["cost_now_usd"] == 0.0

    def test_either_inside_the_three_percent_band(self):
        # corn at the floor: no drying either way. Hard freeze on wait day 2 -> +1.5%, plus 0.5%/week = 840.
        # Rain certain at 16 mm on every wait day -> no window -> premium 1.0 x 0.5% x 42000 = 210.
        # Total 1050 < 1260 band.
        data, prior = corn_at_the_floor()
        data["weather"] = weather(precip=[16.0] * 8, prob=[100] * 8, tmin=[5, 5, -3, 5, 5, 5, 5, 5])
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["field_loss_pct_7d"] == pytest.approx(2.0) and f["loss_events"] == ["2026-10-19"]
        assert f["p_no_window_7d"] == pytest.approx(1.0) and f["risk_premium_usd"] == pytest.approx(210.0)
        assert f["cost_wait_7d_usd"] == pytest.approx(1050.0, abs=0.5)
        assert f["recommendation"] == "either" and f["savings_usd"] == pytest.approx(1050.0, abs=0.5)

    def test_a_tie_on_the_band_is_either(self):
        # beans at 13%, one wet-dry spell: 1% + 2% = 3% of the value, exactly the band -> either, whatever the
        # floating-point remainder says
        data = dataset([feature("S1", "soybean", area_ha=26.67)], [rec("S1")],
                       weather(precip=[0.0, 0.0, 16.0, 0.0, 0.0, 0.0, 0.0, 0.0], prob=[0, 0, 100, 0, 0, 0, 0, 0]))
        f = econ.analyze(data, CONFIG, None)["fields"]["S1"]
        assert f["field_loss_pct_7d"] == pytest.approx(3.0)
        assert f["cost_wait_7d_usd"] == pytest.approx(0.03 * f["crop_value_usd"], abs=0.01)
        assert f["recommendation"] == "either"

    def test_soak_counts_like_a_freeze_for_corn_and_only_once(self):
        data, prior = corn_at_the_floor()
        data["weather"] = weather(precip=[0.0, 30.0, 0.0, 0.0, 0.0, 26.0, 0.0, 0.0], prob=[0] * 8, tmin=[-4] * 8)
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["field_loss_pct_7d"] == pytest.approx(2.0)

    def test_field_loss_only_after_combine_ready(self):
        # maturing, estimate 2026-10-21 -> maturity 2026-10-07 -> curve-ready 2026-10-19, so of the wait week
        # 2026-10-18 .. 2026-10-24 only 2026-10-19 .. 2026-10-24 (6 days) count: 0.5 x 6/7
        data, prior = corn_ready()
        data["status"]["fields"] = [rec("C1", stage="maturing", start="2026-10-21", end="2026-10-21")]
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["combine_ready"] == "2026-10-19" and f["ready_source"] == "drydown"
        assert f["field_loss_pct_7d"] == pytest.approx(0.5 * 6 / 7, abs=1e-3)   # rounded to 3 digits

    def test_reference_yield_when_no_yield_module(self):
        data, _ = corn_ready()
        f = econ.analyze(data, CONFIG, None)["fields"]["C1"]
        assert f["bushels_source"] == "reference_yield"
        assert f["bushels"] == pytest.approx(20.0 * econ.AC_PER_HA * econ.REFERENCE_YIELD_BU_PER_AC["corn"], rel=1e-3)
        assert f["price_usd_per_bu"] == econ.PRICE_USD_PER_BU["corn"]

    def test_yield_module_nested_production_shape(self):
        # yield_model.py writes production: {bu, t} per field; the flat production_bu is the contract name
        data, _ = corn_ready()
        prior = {"yield": {"key": "yield", "fields": {"C1": {"production": {"bu": 10000, "t": 254.0}}}}}
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["bushels"] == 10000 and f["bushels_source"] == "yield_module"
        prior = {"yield": {"key": "yield", "fields": {"C1": {"production": None}}}}
        assert econ.analyze(data, CONFIG, prior)["fields"]["C1"]["bushels_source"] == "reference_yield"

    def test_workability_prior_replaces_the_rain_model(self):
        data, prior = corn_ready()
        prior["workability"] = {"key": "workability", "fields": {"C1": {"p_finish_within_7d": 0.8}}}
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["p_no_window_7d"] == pytest.approx(0.2) and f["risk_source"] == "workability"
        assert f["risk_premium_usd"] == pytest.approx(0.2 * 210.0)

    def test_days_needed_from_area_and_machine_rate(self):
        data, prior = corn_ready()
        data["fields"]["features"] = [feature("C1", "corn", area_ha=95.0)]      # 95 / 40 -> 3 days
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["days_needed"] == 3


# ----------------------------------------------------------------------------------------------- the wait week
class TestWaitWeek:
    """The farmer who waits is not combining today, so the weather risk and the standing days are judged over
    tomorrow .. today + 7. Today's forecast matters only through a heavy rain carrying into tomorrow."""

    def test_a_dry_today_does_not_make_the_wait_safe(self):
        # corn at the floor; today dry and certain, 16 mm certain on every wait day 2026-10-18 .. 2026-10-24:
        # no window for the waiting farmer -> p_no 1.0, premium 210; field loss 0.5% = 210 -> cost_wait 420
        data, prior = corn_at_the_floor()
        data["weather"] = weather(precip=[0.0] + [16.0] * 7, prob=[0] + [100] * 7)
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["p_no_window_7d"] == pytest.approx(1.0) and f["risk_premium_usd"] == pytest.approx(210.0)
        assert f["cost_wait_7d_usd"] == pytest.approx(420.0, abs=0.5)
        assert f["recommendation"] == "either"                              # 420 < 3% band of 1260

    def test_rain_today_does_not_count_against_the_wait(self):
        # 16 mm certain today, dry wait week: the carry blocks tomorrow only, six open days remain -> p_no 0.0;
        # today's rain is not a wait-week event either
        data, prior = corn_at_the_floor()
        data["weather"] = weather(precip=[30.0] + [0.0] * 7, prob=[100] + [0] * 7, tmin=[-3] + [12] * 7)
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["p_no_window_7d"] == pytest.approx(0.0) and f["risk_premium_usd"] == 0.0
        assert f["loss_events"] == [] and f["field_loss_pct_7d"] == pytest.approx(0.5)

    def test_wait_week_runs_from_tomorrow_to_day_seven(self):
        # a hard freeze on 2026-10-24 (index 7) is the last wait day and counts; one on 2026-10-25 (index 8) does not
        data, prior = corn_at_the_floor()
        data["weather"] = weather(tmin=[12] * 7 + [-3])
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["loss_events"] == ["2026-10-24"] and f["field_loss_pct_7d"] == pytest.approx(2.0)
        data["weather"] = weather(tmin=[12] * 8 + [-3])
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["loss_events"] == [] and f["field_loss_pct_7d"] == pytest.approx(0.5)

    def test_bean_wet_day_on_day_seven_needs_the_day_after(self):
        # 6 mm on 2026-10-24 (index 7) followed by a dry 2026-10-25 (index 8, outside the wait but in the forecast)
        # is a wet-dry cycle; with the forecast cut at index 7 the dry day is unknown and no cycle is counted
        data = dataset([feature("S1", "soybean")], [rec("S1")], weather(precip=[0.0] * 7 + [6.0], prob=[0] * 7 + [100]))
        f = econ.analyze(data, CONFIG, yield_prior(S1=10000))["fields"]["S1"]
        assert f["loss_events"] == ["2026-10-24"] and f["field_loss_pct_7d"] == pytest.approx(3.0)
        data["weather"] = weather(fc_days=8, precip=[0.0] * 7 + [6.0], prob=[0] * 7 + [100])
        f = econ.analyze(data, CONFIG, yield_prior(S1=10000))["fields"]["S1"]
        assert f["loss_events"] == [] and f["field_loss_pct_7d"] == pytest.approx(1.0)

    def test_forecast_covering_today_only_gives_no_risk_number(self):
        data, prior = corn_ready()
        data["weather"] = weather(fc_days=1, precip=[16.0], prob=[100])
        out = econ.analyze(data, CONFIG, prior)
        f = out["fields"]["C1"]
        assert f["p_no_window_7d"] is None and f["risk_premium_usd"] == 0.0
        assert any("wait week" in n for n in out["landscape"]["notes"])


# ----------------------------------------------------------------------------------------------- no-window odds
class TestNoWindowProbability:
    def test_certain_rain_every_day_leaves_no_window(self):
        assert econ.p_no_workable_window([(1.0, 16.0)] * 7, 1) == pytest.approx(1.0)

    def test_dry_week_always_has_a_window(self):
        assert econ.p_no_workable_window([(0.0, 0.0)] * 7, 7) == pytest.approx(0.0)

    def test_one_uncertain_day_at_the_end(self):
        days = [(0.0, 0.0)] * 6 + [(0.5, 16.0)]
        assert econ.p_no_workable_window(days, 1) == pytest.approx(0.0)       # six certain days
        assert econ.p_no_workable_window(days, 7) == pytest.approx(0.5)       # all seven needed

    def test_two_independent_days(self):
        days = [(0.5, 16.0), (0.5, 16.0)] + [(0.0, 0.0)] * 5
        assert econ.p_no_workable_window(days, 7) == pytest.approx(0.75)      # 1 - 0.5 x 0.5

    def test_heavy_rain_blocks_the_next_day_too(self):
        days = [(1.0, 16.0)] + [(0.0, 0.0)] * 6                               # day 0 and day 1 blocked -> 5 open days
        assert econ.p_no_workable_window(days, 6) == pytest.approx(1.0)
        assert econ.p_no_workable_window(days, 5) == pytest.approx(0.0)

    def test_light_rain_does_not_block(self):
        assert econ.p_no_workable_window([(1.0, 3.0)] * 7, 7) == pytest.approx(0.0)

    def test_more_days_needed_than_the_week_has(self):
        assert econ.p_no_workable_window([(0.0, 0.0)] * 7, 8) == pytest.approx(1.0)

    def test_empty_forecast_is_unknown(self):
        assert econ.p_no_workable_window([], 1) is None
        assert econ.p_no_workable_window([], 1, lead=(1.0, 16.0)) is None

    def test_lead_day_carries_into_tomorrow_but_is_not_a_window_day(self):
        dry = [(0.0, 0.0)] * 7
        assert econ.p_no_workable_window(dry, 7, lead=(1.0, 16.0)) == pytest.approx(1.0)   # tomorrow blocked: 6 open
        assert econ.p_no_workable_window(dry, 6, lead=(1.0, 16.0)) == pytest.approx(0.0)
        assert econ.p_no_workable_window(dry, 7, lead=(0.5, 16.0)) == pytest.approx(0.5)
        assert econ.p_no_workable_window(dry, 7, lead=(1.0, 6.0)) == pytest.approx(0.0)    # a shower does not carry
        assert econ.p_no_workable_window([(1.0, 16.0)] * 7, 1, lead=(0.0, 0.0)) == pytest.approx(1.0)  # dry today opens nothing


# ----------------------------------------------------------------------------------------------- degrade
class TestEdges:
    def test_skips_are_none_with_a_note(self):
        data = dataset([feature("C1", "corn"), feature("O1", "other"), feature("H1", "soybean"), feature("U1", "corn"),
                        feature("M1", "corn")],
                       [rec("C1"), rec("O1", stage="unknown", start=None, end=None),
                        rec("H1", stage="harvested", start=None, end=None), rec("U1", stage="unknown", start=None, end=None)])
        out = econ.analyze(data, CONFIG, None)
        assert out["key"] == "economics"
        assert out["fields"]["C1"] is not None
        assert out["fields"]["O1"] is None and out["fields"]["H1"] is None and out["fields"]["U1"] is None
        assert out["fields"]["M1"] is None                                     # not in status.json
        text = " ".join(out["landscape"]["notes"]).lower()
        assert "harvested" in text and "other" in text and "status" in text
        assert out["landscape"]["fields_evaluated"] == 1

    def test_no_estimate_is_none_with_a_note(self):
        data = dataset([feature("C1", "corn")], [rec("C1", stage="maturing", start=None, end=None)])
        out = econ.analyze(data, CONFIG, None)
        assert out["fields"]["C1"] is None
        assert any("estimate" in n for n in out["landscape"]["notes"])

    def test_phenology_prior_fills_a_missing_estimate(self):
        base = econ.analyze(*corn_ready())["fields"]["C1"]
        data, prior = corn_ready()
        data["status"]["fields"] = [rec("C1", stage="maturing", start=None, end=None)]
        prior["phenology"] = {"key": "phenology", "fields": {"C1": {"ensemble_harvest_date": "2026-10-17"}}}
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["estimate_source"] == "phenology" and f["maturity"] == base["maturity"]
        assert f["moisture_today_pct"] == pytest.approx(base["moisture_today_pct"])

    def test_missing_weather_still_prices_the_field(self):
        data, prior = corn_ready()
        for wx in ({}, None, {"observed": {}, "forecast": None}):
            data["weather"] = wx
            out = econ.analyze(data, CONFIG, prior)
            f = out["fields"]["C1"]
            assert f is not None and f["recommendation"] in ("harvest_now", "wait", "either")
            # 14 days at the default 15 C -> 0.45/day -> 32 - 6.3 = 25.7; the curve would be ready on day 16,
            # but status says ready, so the satellite's reading stands
            assert f["moisture_today_pct"] == pytest.approx(32 - 14 * 0.03 * econ.DEFAULT_TMEAN_C, abs=0.05)
            assert f["combine_ready"] == "2026-10-17" and f["ready_source"] == "satellite"
            assert f["p_no_window_7d"] is None and f["risk_premium_usd"] == 0.0
            assert any("forecast" in n.lower() or "weather" in n.lower() for n in out["landscape"]["notes"])

    def test_forecast_without_probabilities_or_temperatures(self):
        data, prior = corn_ready()
        fc = data["weather"]["forecast"]
        del fc["precip_prob"], fc["tmin_c"], fc["tmax_c"]
        fc["precip_mm"] = [16.0] + [0.0] * 13                                  # certain by amount: today carries into tomorrow
        f = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        assert f["p_no_window_7d"] == pytest.approx(0.0) and f["risk_source"] == "forecast"
        assert f["moisture_in_7d_pct"] == pytest.approx(19.4, abs=0.05)         # recent observed mean carries the week

    def test_timeseries_shape_does_not_matter(self):
        data, prior = corn_ready()
        base = econ.analyze(data, CONFIG, prior)["fields"]["C1"]
        for ts in (None, {"dates": [], "fields": {}}, {"dates": ["2026-10-16"], "fields": {"C1": {"ndvi": [0.3]}}},
                   {"dates": ["2026-10-01", "2026-10-16"], "fields": {"C1": {"ndvi": [None, None], "nbr": [None, None]}}}):
            data["timeseries"] = ts
            assert econ.analyze(data, CONFIG, prior)["fields"]["C1"] == base

    def test_optional_inputs_absent(self):
        data, prior = corn_ready()
        for k in ("fires", "insights", "plans", "route", "truth", "usda", "chcs", "zones"):
            data[k] = None
        out = econ.analyze(data, CONFIG, prior)
        assert out["fields"]["C1"]["recommendation"] == "wait"
        assert out["zones"]["Z01"]["evaluated"] == 1

    def test_garbage_never_raises(self):
        out = econ.analyze({}, {}, None)
        assert out["key"] == "economics" and out["fields"] == {} and out["landscape"]["notes"]
        out = econ.analyze({"fields": {"features": [{"properties": None}, {}]}, "status": {"fields": "nope"}}, CONFIG)
        assert out["fields"] == {} or all(v is None for v in out["fields"].values())
        data, prior = corn_ready()
        data["status"]["peak_to_harvest_days"] = "soon"
        data["status"]["fields"] = [rec("C1", peak="not a date")]
        assert econ.analyze(data, CONFIG, prior)["fields"]["C1"]["estimate_source"] == "status"

    def test_output_is_json_serialisable(self):
        data = dataset([feature("C1", "corn"), feature("S1", "soybean", zone="Z02")], [rec("C1"), rec("S1", zone="Z02")])
        out = econ.analyze(data, CONFIG, yield_prior(C1=10000, S1=5000))
        text = json.dumps(out, allow_nan=False)
        assert "NaN" not in text and "Infinity" not in text
        for k in ("key", "fields", "landscape", "zones", "assumptions", "method"):
            assert k in out
        assert out["landscape"]["counts"] == {"harvest_now": 0, "wait": 1, "either": 1}
        assert out["zones"]["Z02"] == {"harvest_now": 0, "wait": 0, "either": 1, "evaluated": 1}
        assert out["landscape"]["crop_value_usd_total"] == pytest.approx(42000 + 52000)
        assert out["landscape"]["drying_usd_after_recommendation_total"] < out["landscape"]["drying_usd_now_total"]
        assert all(isinstance(v, str) for v in out["assumptions"].values()) and out["method"]


# ----------------------------------------------------------------------------------------------- mock season
class TestMockSeason:
    def test_mock_shapes_and_ranges(self):
        d = ROOT / "data" / "mock"
        if not (d / "status.json").exists():
            pytest.skip("mock data missing under data/mock")

        def load(n, default=None):
            p = d / n
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

        data = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
                "status": load("status.json"), "weather": load("weather.json", {}),
                "fires": load("fires.json", {"detections": []}), "insights": load("insights.json", {}),
                "plans": load("plans.json"), "route": load("route.json"), "truth": load("truth.json"),
                "usda": load("usda_crop_progress.json"), "chcs": None}
        config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
        out = econ.analyze(data, config, None)
        json.dumps(out, allow_nan=False)
        assert out["key"] == "economics"
        assert set(out["fields"]) == {f["properties"]["field_id"] for f in data["fields"]["features"]}
        done = {k: v for k, v in out["fields"].items() if v is not None}
        assert done and out["landscape"]["fields_evaluated"] == len(done)
        assert sum(out["landscape"]["counts"].values()) == len(done)
        as_of = out["landscape"]["as_of"]
        for fid, f in done.items():
            assert f["crop"] in ("corn", "soybean") and f["stage"] in ("growing", "maturing", "ready")
            assert 10.0 <= f["moisture_today_pct"] <= 65.0 and 10.0 <= f["moisture_in_7d_pct"] <= 65.0
            assert f["moisture_in_7d_pct"] <= f["moisture_today_pct"] + 1e-6
            assert f["recommendation"] in ("harvest_now", "wait", "either")
            assert f["cost_now_usd"] >= 0 and f["cost_wait_7d_usd"] >= 0 and f["savings_usd"] >= 0
            assert 0 < f["crop_value_usd"] < 5e6 and f["bushels"] > 0
            assert f["p_no_window_7d"] is None or 0.0 <= f["p_no_window_7d"] <= 1.0
            assert f["ready_source"] in ("drydown", "satellite")
            if f["stage"] == "ready":                      # the satellite's ready crop is combine-ready today
                assert f["combine_ready"] <= as_of
                assert f["field_loss_pct_7d"] >= econ.FIELD_LOSS_PCT_PER_WEEK[f["crop"]] - 1e-9
                assert "not combine-ready" not in f["reason"]
            assert isinstance(f["reason"], str) and f["reason"] and EM_DASH not in f["reason"]
            assert len(json.dumps(f)) < 1024
        # ready corn fields with different peak dates must not all read the same moisture
        status = {r["field_id"]: r for r in data["status"]["fields"]}
        ready_corn = [fid for fid, f in done.items() if f["crop"] == "corn" and f["stage"] == "ready"]
        if len({status[fid]["peak_date"] for fid in ready_corn}) > 1:
            assert len({done[fid]["moisture_today_pct"] for fid in ready_corn}) > 1
        zone_ids = {z["properties"]["zone_id"] for z in data["zones"]["features"]}
        assert set(out["zones"]) <= zone_ids | {None}
        assert sum(z["harvest_now"] for z in out["zones"].values()) == out["landscape"]["counts"]["harvest_now"]
        assert out["landscape"]["crop_value_usd_total"] > 0
        assert math.isfinite(out["landscape"]["drying_usd_now_total"])
