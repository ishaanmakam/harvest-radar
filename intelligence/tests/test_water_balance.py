"""water_balance.py: FAO-56 reference evapotranspiration (Hargreaves) and a root-zone water bucket per field.
Fixtures are small hand-built dicts in the shapes of data/mock; the hand arithmetic sits in the comments."""
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

import water_balance as wb

ROOT = Path(__file__).resolve().parents[2]
D = date.fromisoformat


# ----------------------------------------------------------------------------------------------- fixtures

def square(lon, lat, size=0.005):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]]}


def feature(fid, zone, crop, lon=-93.5, lat=42.0):
    return {"type": "Feature", "geometry": square(lon, lat),
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": 25.0, "crop": crop, "source": "test"}}


def weather_block(start, n, tmax=30.0, tmin=20.0, precip=None):
    dates = [(start + timedelta(days=i)).isoformat() for i in range(n)]
    pr = list(precip) if precip is not None else [0.0] * n
    return {"dates": dates, "precip_mm": pr, "tmax_c": [tmax] * n, "tmin_c": [tmin] * n}


def config_for(season_start, as_of):
    return {"bbox": [-93.52, 41.98, -93.42, 42.07], "season_start": season_start.isoformat(), "as_of": as_of.isoformat(),
            "thresholds": {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25}}


def synthetic():
    """20-day July season, constant 30/20 C, one 30 mm rain on 2026-07-11, five fields in two zones.
    F1 corn, standing all season, NDVI 0.7 on three passes          -> Kc 1.25*0.7-0.10 = 0.775
    F2 soybean, same NDVI, harvested 2026-07-11                      -> Kc 0.775 for 10 days then 0.20 residue
    F3 other, no entry in timeseries                                 -> None
    F4 corn, every pass cloudy                                       -> None
    F5 corn, a single clear pass with NDVI 0.6                       -> Kc 0.65 all season
    F6 other (grass), NDVI 0.5 on three passes, no status record     -> Kc 0.525"""
    start, today = D("2026-07-01"), D("2026-07-21")
    precip = [0.0] * 20
    precip[10] = 30.0                                            # 2026-07-11
    obs = weather_block(start, 20, precip=precip)
    fc = weather_block(today, 14)
    fc["precip_prob"] = [0] * 14
    ts_dates = ["2026-07-01", "2026-07-08", "2026-07-15"]
    ts = {"source": "test", "dates": ts_dates, "fields": {
        "F1": {"ndvi": [0.7, 0.7, 0.7], "nbr": [0.4, 0.4, 0.4], "clear_frac": [0.9, 0.9, 0.9]},
        "F2": {"ndvi": [0.7, None, 0.7], "nbr": [0.4, None, 0.4], "clear_frac": [0.9, 0.1, 0.9]},
        "F4": {"ndvi": [None, None, None], "nbr": [None, None, None], "clear_frac": [0.1, 0.1, 0.1]},
        "F5": {"ndvi": [None, None, 0.6], "nbr": [None, None, 0.3], "clear_frac": [0.1, 0.1, 0.9]},
        "F6": {"ndvi": [0.5, 0.5, 0.5], "nbr": [0.3, 0.3, 0.3], "clear_frac": [0.9, 0.9, 0.9]},
    }}
    status = {"as_of": today.isoformat(), "peak_to_harvest_days": 60.0, "peak_to_harvest_days_by_crop": {}, "fields": [
        {"field_id": "F1", "zone_id": "Z01", "stage": "growing", "peak_date": "2026-07-15", "peak_ndvi": 0.7, "harvest_date": None,
         "predicted_harvest_start": None, "predicted_harvest_end": None, "sowing_deadline": None, "burn_date": None,
         "burn_evidence": "none", "confidence": 0.5, "notes": ""},
        {"field_id": "F2", "zone_id": "Z01", "stage": "harvested", "peak_date": "2026-07-01", "peak_ndvi": 0.7, "harvest_date": "2026-07-11",
         "predicted_harvest_start": None, "predicted_harvest_end": None, "sowing_deadline": None, "burn_date": None,
         "burn_evidence": "none", "confidence": 0.9, "notes": ""},
        {"field_id": "F4", "zone_id": "Z02", "stage": "unknown", "peak_date": None, "peak_ndvi": None, "harvest_date": None,
         "predicted_harvest_start": None, "predicted_harvest_end": None, "sowing_deadline": None, "burn_date": None,
         "burn_evidence": "none", "confidence": 0.0, "notes": ""},
        {"field_id": "F5", "zone_id": "Z02", "stage": "growing", "peak_date": None, "peak_ndvi": 0.6, "harvest_date": None,
         "predicted_harvest_start": None, "predicted_harvest_end": None, "sowing_deadline": None, "burn_date": None,
         "burn_evidence": "none", "confidence": 0.3, "notes": ""},
    ]}
    fields = {"type": "FeatureCollection", "features": [
        feature("F1", "Z01", "corn"), feature("F2", "Z01", "soybean"), feature("F3", "Z02", "other"),
        feature("F4", "Z02", "corn"), feature("F5", "Z02", "corn"), feature("F6", "Z02", "other")]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": square(-93.52, 41.98, 0.03), "properties": {"zone_id": "Z01", "name": "Zone A1", "centroid": [-93.5, 42.0]}},
        {"type": "Feature", "geometry": square(-93.49, 41.98, 0.03), "properties": {"zone_id": "Z02", "name": "Zone A2", "centroid": [-93.47, 42.0]}},
        {"type": "Feature", "geometry": square(-93.46, 41.98, 0.03), "properties": {"zone_id": "Z03", "name": "Zone A3", "centroid": [-93.44, 42.0]}}]}
    data = {"fields": fields, "zones": zones, "timeseries": ts, "status": status,
            "weather": {"observed": obs, "forecast": fc}, "fires": {"detections": []}, "insights": None,
            "plans": None, "route": None, "truth": None, "usda": None, "chcs": None}
    return data, config_for(start, today)


def walk_types(x, path="root"):
    """Every leaf must be a plain Python scalar: no numpy, no NaN."""
    if isinstance(x, dict):
        for k, v in x.items():
            assert isinstance(k, str), f"{path}: key {k!r} is not str"
            walk_types(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            walk_types(v, f"{path}[{i}]")
    else:
        assert type(x) in (float, int, str, bool, type(None)), f"{path}: {type(x)}"
        if isinstance(x, float):
            assert math.isfinite(x), f"{path}: {x}"


# ----------------------------------------------------------------------------------------------- FAO-56 math

class TestRadiationAndET0:
    def test_ra_matches_fao56_example_8(self):
        # FAO-56 example 8: 20 S, 3 September (J = 246): dr 0.985, delta 0.120, ws 1.527 -> Ra 32.2 MJ m-2 d-1
        assert wb.extraterrestrial_radiation(-20.0, 246) == pytest.approx(32.2, abs=0.1)

    def test_ra_vectorised_over_days(self):
        ra = wb.extraterrestrial_radiation(42.0, np.array([1, 172]))
        assert ra.shape == (2,)
        assert ra[1] > 3 * ra[0]                       # midsummer sun at 42 N is far more than midwinter

    def test_et0_matches_fao56_example_20(self):
        # FAO-56 example 20: Lyon, July, Tmax 26.6, Tmin 14.8, Ra 40.6 -> 0.0023*0.408*40.6*(20.7+17.8)*sqrt(11.8) = 5.04 mm/day
        assert wb.et0_hargreaves(40.6, 26.6, 14.8) == pytest.approx(5.0, abs=0.1)

    def test_et0_zero_when_no_temperature_range(self):
        assert wb.et0_hargreaves(40.0, 20.0, 20.0) == 0.0
        assert wb.et0_hargreaves(40.0, 18.0, 20.0) == 0.0      # inverted extremes clamp to zero, never NaN


class TestKc:
    def test_kc_linear_with_clips(self):
        # Kc = clip(1.25 * NDVI - 0.10, 0.15, 1.20)
        assert wb.kc_from_ndvi(0.2) == pytest.approx(0.15)     # 0.15 exactly at the floor
        assert wb.kc_from_ndvi(0.8) == pytest.approx(0.90)     # 1.0 - 0.1
        assert wb.kc_from_ndvi(0.0) == pytest.approx(0.15)     # clipped up
        assert wb.kc_from_ndvi(1.1) == pytest.approx(1.20)     # clipped down
        out = wb.kc_from_ndvi(np.array([0.2, 0.8]))
        assert out.tolist() == pytest.approx([0.15, 0.90])


class TestBucket:
    def test_rain_refills_and_excess_drains(self):
        # TAW 150, start full (depletion 0), ET0 4 mm/day, Kc 1, 20 mm on day 3:
        # end-of-day depletion 4, 8, 12, then 12 - 20 + 4 = -4 -> 0 with 4 mm drained, then 4.  ETc total 5 * 4 = 20.
        et0 = np.array([4.0] * 5)
        precip = np.array([0.0, 0.0, 0.0, 20.0, 0.0])
        kc = np.ones((1, 5))
        standing = np.ones((1, 5), dtype=bool)
        r = wb.run_bucket(et0, precip, kc, standing, taw=150.0, raw_fraction=0.5, start_depletion=0.0)
        assert r["depletion"][0].tolist() == pytest.approx([4.0, 8.0, 12.0, 0.0, 4.0])
        assert r["drained"][0].tolist() == pytest.approx([0.0, 0.0, 0.0, 4.0, 0.0])
        assert r["etc"][0].sum() == pytest.approx(20.0)
        assert int(r["stress"][0].sum()) == 0

    def test_stress_days_and_ks_past_raw(self):
        # start depletion 70, RAW 75, ET0 10, Kc 1:
        # day 0: Ks = min(1, (150-70)/75) = 1      -> ETc 10     -> depletion 80      (> 75: stress)
        # day 1: Ks = (150-80)/75 = 0.9333          -> ETc 9.333  -> depletion 89.333  (stress)
        et0 = np.array([10.0, 10.0])
        precip = np.zeros(2)
        kc = np.ones((1, 2))
        standing = np.ones((1, 2), dtype=bool)
        r = wb.run_bucket(et0, precip, kc, standing, taw=150.0, raw_fraction=0.5, start_depletion=70.0)
        assert r["depletion"][0].tolist() == pytest.approx([80.0, 89.3333], abs=1e-3)
        assert r["etc"][0].sum() == pytest.approx(19.3333, abs=1e-3)
        assert int(r["stress"][0].sum()) == 2

    def test_stress_only_counts_while_the_crop_stands(self):
        et0 = np.array([10.0, 10.0])
        kc = np.ones((1, 2))
        standing = np.array([[True, False]])
        r = wb.run_bucket(et0, np.zeros(2), kc, standing, taw=150.0, raw_fraction=0.5, start_depletion=70.0)
        assert int(r["stress"][0].sum()) == 1

    def test_bucket_never_empties_past_taw(self):
        # start 149 of 150: Ks = 1/75 -> 1.333 mm wanted, only 1 mm left -> ETc 1.0, depletion stays at 150
        r = wb.run_bucket(np.array([100.0]), np.zeros(1), np.ones((1, 1)), np.ones((1, 1), dtype=bool),
                          taw=150.0, raw_fraction=0.5, start_depletion=149.0)
        assert r["depletion"][0, 0] == pytest.approx(150.0)
        assert r["etc"][0, 0] == pytest.approx(1.0)

    def test_two_fields_run_side_by_side(self):
        et0 = np.array([5.0, 5.0])
        kc = np.array([[1.0, 1.0], [0.2, 0.2]])
        standing = np.ones((2, 2), dtype=bool)
        r = wb.run_bucket(et0, np.zeros(2), kc, standing, taw=150.0, raw_fraction=0.5, start_depletion=0.0)
        assert r["depletion"][:, -1].tolist() == pytest.approx([10.0, 2.0])


# ----------------------------------------------------------------------------------------------- analyze()

@pytest.fixture(scope="module")
def result():
    data, config = synthetic()
    return wb.analyze(data, config)


class TestAnalyzeSynthetic:
    def test_shape_and_key(self, result):
        assert result["key"] == "water"
        for k in ("fields", "landscape", "zones", "assumptions", "method"):
            assert k in result
        assert set(result["fields"]) == {"F1", "F2", "F3", "F4", "F5", "F6"}
        assert isinstance(result["method"], str) and len(result["method"]) > 50
        assert all(isinstance(v, str) for v in result["assumptions"].values())

    def test_json_clean(self, result):
        json.dumps(result)
        walk_types(result)

    def test_missing_and_cloudy_fields_are_none_with_a_note(self, result):
        assert result["fields"]["F3"] is None                      # not in timeseries
        assert result["fields"]["F4"] is None                      # every pass cloudy
        assert any("F3" in n or "2 field" in n or "no clear pass" in n for n in result["landscape"]["notes"])

    def test_one_pass_and_crop_other_still_get_a_balance(self, result):
        assert isinstance(result["fields"]["F5"], dict)
        assert isinstance(result["fields"]["F6"], dict)
        assert result["fields"]["F6"]["kc_today"] == pytest.approx(0.525, abs=0.01)   # 1.25 * 0.5 - 0.1

    def test_season_totals(self, result):
        f1 = result["fields"]["F1"]
        assert f1["season_precip_mm"] == pytest.approx(30.0)
        # Ra at 42 N in the first three weeks of July is about 41 MJ m-2 d-1:
        # ET0 = 0.0023 * 0.408 * 41 * (25 + 17.8) * sqrt(10) = 5.2 mm/day -> about 104 mm over 20 days
        assert 90.0 <= f1["season_et0_mm"] <= 115.0
        # standing all season at Kc 0.775 and never past RAW: ETc = 0.775 * ET0
        assert f1["season_etc_mm"] == pytest.approx(0.775 * f1["season_et0_mm"], abs=0.5)

    def test_depletion_and_status(self, result):
        f1, f2, f5 = (result["fields"][k] for k in ("F1", "F2", "F5"))
        # F1: 0.775 * ~104 - 30 = ~50.7 mm gone -> adequate (between 30 and 75)
        assert 44.0 <= f1["depletion_today_mm"] <= 57.0
        assert f1["water_status"] == "adequate"
        assert f1["available_fraction_today"] == pytest.approx(1 - f1["depletion_today_mm"] / 150.0, abs=0.01)
        # F2: residue Kc 0.20 after the 2026-07-11 harvest: 0.775 * 52 + 0.20 * 52 - 30 = ~20.7 -> wet (< 30)
        assert f2["depletion_today_mm"] < f1["depletion_today_mm"]
        assert f2["water_status"] == "wet"
        assert f2["kc_today"] == pytest.approx(0.20)
        assert f2["standing"] is False
        # F5: Kc 0.65 -> 0.65 * 104 - 30 = ~37.6 -> adequate
        assert f5["water_status"] == "adequate"
        assert f1["stress_days"] == 0
        assert f1["stress_days_since_peak"] == 0
        assert f5["stress_days_since_peak"] is None              # no peak date on record

    def test_rain_memory_and_surface(self, result):
        f1 = result["fields"]["F1"]
        assert f1["last_rain_date"] == "2026-07-11"
        assert f1["days_since_rain"] == 10
        # nine days of ~5.2 mm ET0 after the rain = ~47 mm > 30 mm of rain -> the surface has dried
        assert f1["surface_dry"] is True

    def test_landscape(self, result):
        L = result["landscape"]
        assert L["as_of"] == "2026-07-21" and L["season_start"] == "2026-07-01"
        assert L["season"]["precip_mm"] == pytest.approx(30.0)
        assert 90.0 <= L["season"]["et0_mm"] <= 115.0
        assert L["season"]["days"] == 20
        # F2 (residue after harvest, ~20.7) and F6 (Kc 0.525: 0.525 * 104 - 30 = ~24.8) are under 30 mm -> wet; F1 and F5 adequate
        assert L["water_status_counts"] == {"wet": 2, "adequate": 2, "dry": 0}
        assert L["fields_analysed"] == 4 and L["fields_skipped"] == 2
        assert L["last_rain_date"] == "2026-07-11" and L["days_since_rain"] == 10 and L["surface_dry"] is True
        # 14 forecast days at ~5.2 mm/day
        assert L["forecast"]["days"] == 14
        assert 60.0 <= L["forecast"]["et0_mm"] <= 85.0
        assert L["forecast"]["precip_mm"] == 0.0
        assert isinstance(L["narrative"], str) and "2026-07-11" in L["narrative"]
        assert "—" not in L["narrative"]

    def test_zones(self, result):
        Z = result["zones"]
        assert set(Z) == {"Z01", "Z02", "Z03"}
        assert Z["Z03"] is None                                   # no field there
        assert Z["Z01"]["n_fields"] == 2
        f1, f2 = result["fields"]["F1"], result["fields"]["F2"]
        assert Z["Z01"]["mean_depletion_mm"] == pytest.approx((f1["depletion_today_mm"] + f2["depletion_today_mm"]) / 2, abs=0.1)
        assert Z["Z01"]["mean_stress_days"] == 0.0
        assert Z["Z02"]["n_fields"] == 2                          # F5 and F6; F3 and F4 skipped

    def test_per_field_dicts_stay_small(self, result):
        for v in result["fields"].values():
            if v is not None:
                assert len(json.dumps(v)) < 600


class TestDrySeason:
    def test_long_dry_spell_counts_stress_days(self):
        # 60 days from 2026-07-01, 5 mm on day 0 only, NDVI 0.8 (Kc 0.9), ET0 ~5 mm/day:
        # ETc ~4.6/day crosses RAW 75 after ~16 days -> about 40-45 stress days, status dry, bucket never past 150
        start, today = D("2026-07-01"), D("2026-08-30")
        precip = [0.0] * 60
        precip[0] = 5.0
        data, _ = synthetic()
        data["weather"] = {"observed": weather_block(start, 60, precip=precip), "forecast": {}}
        data["timeseries"] = {"dates": ["2026-07-01", "2026-08-01"], "fields": {"F1": {"ndvi": [0.8, 0.8], "nbr": [0.4, 0.4], "clear_frac": [1, 1]}}}
        data["fields"] = {"type": "FeatureCollection", "features": [feature("F1", "Z01", "corn")]}
        data["status"] = None
        r = wb.analyze(data, config_for(start, today))
        f1 = r["fields"]["F1"]
        assert f1["water_status"] == "dry"
        assert 35 <= f1["stress_days"] <= 50
        assert 75.0 < f1["depletion_today_mm"] <= 150.0
        assert 0.0 <= f1["available_fraction_today"] < 0.5
        assert f1["last_rain_date"] == "2026-07-01" and f1["days_since_rain"] == 60
        assert f1["surface_dry"] is True
        assert r["landscape"]["forecast"] is None
        assert r["landscape"]["water_status_counts"] == {"wet": 0, "adequate": 0, "dry": 1}


class TestDegradedInputs:
    def test_no_weather_at_all(self):
        data, config = synthetic()
        data["weather"] = None
        r = wb.analyze(data, config)
        assert r["key"] == "water"
        assert set(r["fields"]) == {"F1", "F2", "F3", "F4", "F5", "F6"}
        assert all(v is None for v in r["fields"].values())
        assert any("weather" in n.lower() for n in r["landscape"]["notes"])
        json.dumps(r)

    def test_forecast_without_temperatures(self):
        data, config = synthetic()
        data["weather"]["forecast"] = {"dates": data["weather"]["forecast"]["dates"], "precip_mm": [0.0] * 14}
        r = wb.analyze(data, config)
        assert isinstance(r["fields"]["F1"], dict)
        assert r["landscape"]["forecast"] is None or r["landscape"]["forecast"]["et0_mm"] is None

    def test_observed_gap_is_interpolated(self):
        data, config = synthetic()
        data["weather"]["observed"]["tmax_c"][5] = None            # one day without a maximum
        data["weather"]["observed"]["precip_mm"][6] = None          # and one without rain data (reads as 0, with a note)
        r = wb.analyze(data, config)
        f1 = r["fields"]["F1"]
        assert 90.0 <= f1["season_et0_mm"] <= 115.0
        assert f1["season_precip_mm"] == pytest.approx(30.0)
        assert any("1 of 20" in n and "rain" in n.lower() for n in r["landscape"]["notes"])
        walk_types(r)

    def test_status_missing(self):
        data, config = synthetic()
        data["status"] = None
        r = wb.analyze(data, config)
        assert isinstance(r["fields"]["F2"], dict)
        assert r["fields"]["F2"]["standing"] is True               # no harvest date known
        assert r["fields"]["F1"]["stress_days_since_peak"] is None

    def test_fields_missing_falls_back_to_timeseries_ids(self):
        data, config = synthetic()
        data["fields"] = None
        data["zones"] = None
        r = wb.analyze(data, config)
        assert set(r["fields"]) == {"F1", "F2", "F4", "F5", "F6"}
        assert isinstance(r["fields"]["F1"], dict)

    def test_as_of_missing_uses_status_as_of(self):
        data, config = synthetic()
        config = {k: v for k, v in config.items() if k != "as_of"}
        r = wb.analyze(data, config)
        assert r["landscape"]["as_of"] == "2026-07-21"
        assert isinstance(r["fields"]["F1"], dict)

    def test_garbage_never_raises(self):
        r = wb.analyze({}, {})
        assert r["key"] == "water"
        assert r["fields"] == {}
        assert r["landscape"]["notes"]
        json.dumps(r)
        r2 = wb.analyze({"fields": {"features": "nope"}, "weather": {"observed": {"dates": "x"}}}, {"as_of": "2026-10-17"})
        assert r2["key"] == "water"
        json.dumps(r2)

    def test_prior_is_accepted_and_ignored(self):
        data, config = synthetic()
        r = wb.analyze(data, config, prior={"something": {"fields": {}}})
        assert isinstance(r["fields"]["F1"], dict)


class TestRainRecordGaps:
    """The real pipeline writes None for NASA POWER fill values, so a recorded day can carry no rain figure.
    A few such days are read as 0 mm and counted in a note; a missing or mostly blank rain column means the bucket
    cannot be filled, so no field is balanced and nothing claims 0 mm of rain."""

    def test_null_precip_on_recorded_days_is_counted_in_a_note(self):
        # three recorded days without a rain figure, none of them the 30 mm day: the bucket still runs with those
        # days read as 0 mm, the season total stays 30 mm, last rain stays 2026-07-11, and a note names the count
        data, config = synthetic()
        for i in (2, 6, 15):
            data["weather"]["observed"]["precip_mm"][i] = None
        r = wb.analyze(data, config)
        f1 = r["fields"]["F1"]
        assert isinstance(f1, dict)
        assert f1["season_precip_mm"] == pytest.approx(30.0)
        assert f1["last_rain_date"] == "2026-07-11"
        assert any("3 of 20" in n and "rain" in n.lower() for n in r["landscape"]["notes"])

    def test_missing_precip_column_gives_no_balance(self):
        data, config = synthetic()
        del data["weather"]["observed"]["precip_mm"]
        r = wb.analyze(data, config)
        assert set(r["fields"]) == {"F1", "F2", "F3", "F4", "F5", "F6"}
        assert all(v is None for v in r["fields"].values())
        L = r["landscape"]
        assert L["season"]["precip_mm"] is None
        assert 90.0 <= L["season"]["et0_mm"] <= 115.0                      # ET0 is still known
        assert L["last_rain_date"] is None and L["days_since_rain"] is None and L["surface_dry"] is None
        assert L["water_status_counts"] == {"wet": 0, "adequate": 0, "dry": 0}
        assert L["fields_analysed"] == 0
        assert "0 mm of rain" not in L["narrative"] and "fields are dry" not in L["narrative"]
        assert "rain" in L["narrative"].lower()
        assert any("precip_mm" in n for n in L["notes"])
        assert L["forecast"]["et0_mm"] > 0                                   # the forecast does not need observed rain
        json.dumps(r)
        walk_types(r)

    def test_all_null_precip_column_gives_no_balance(self):
        data, config = synthetic()
        data["weather"]["observed"]["precip_mm"] = [None] * 20
        r = wb.analyze(data, config)
        assert all(v is None for v in r["fields"].values())
        assert r["landscape"]["season"]["precip_mm"] is None
        assert r["landscape"]["last_rain_date"] is None
        assert any("20 of 20" in n for n in r["landscape"]["notes"])

    def test_mostly_null_precip_gives_no_balance(self):
        data, config = synthetic()
        pr = data["weather"]["observed"]["precip_mm"]
        for i in range(15):
            pr[i] = None                                                     # 15 of 20 recorded days blank, the 30 mm day among them
        r = wb.analyze(data, config)
        assert all(v is None for v in r["fields"].values())
        assert r["landscape"]["season"]["precip_mm"] is None
        assert r["landscape"]["surface_dry"] is None
        assert any("15 of 20" in n for n in r["landscape"]["notes"])

    def test_forecast_without_precip_column(self):
        data, config = synthetic()
        del data["weather"]["forecast"]["precip_mm"]
        r = wb.analyze(data, config)
        fc = r["landscape"]["forecast"]
        assert fc["et0_mm"] > 0
        assert fc["precip_mm"] is None and fc["net_mm"] is None
        n = r["landscape"]["narrative"]
        assert "forecast rain" not in n and "ET0" in n                       # the ET0 clause stays, the rain clause goes
        assert any("forecast" in note and "rain" in note for note in r["landscape"]["notes"])
        json.dumps(r)
        walk_types(r)

    def test_forecast_with_some_precip_rows(self):
        # 12 mm on the first forecast day, the last seven rows blank: the sum covers the known rows and a note says so
        data, config = synthetic()
        pr = data["weather"]["forecast"]["precip_mm"]
        pr[0] = 12.0
        for i in range(7, 14):
            pr[i] = None
        r = wb.analyze(data, config)
        fc = r["landscape"]["forecast"]
        assert fc["precip_mm"] == pytest.approx(12.0)
        assert fc["precip_days"] == 7
        assert fc["net_mm"] == pytest.approx(12.0 - fc["et0_mm"], abs=0.11)
        assert any("7 of 14" in n for n in r["landscape"]["notes"])


class TestWording:
    def test_method_and_narrative_claim_an_estimate_not_a_measurement(self, result):
        m = result["method"]
        assert "estimates" in m
        assert "cue, not a trafficability check" in m
        assert "knows how much water" not in m and "carry a machine" not in m
        n = result["landscape"]["narrative"]
        assert "the surface has dried since" not in n
        assert "enough ET since" in n                                        # surface_dry is True in the synthetic season
        for text in (m, n, wb.__doc__):
            assert "—" not in text


# ----------------------------------------------------------------------------------------------- data/mock (read-only)

class TestMock:
    def test_mock_season(self):
        mock = ROOT / "data" / "mock"
        if not (mock / "timeseries.json").exists():
            pytest.skip("data/mock is missing: run scripts/make_mock_data.py")

        def load(n, default=None):
            p = mock / n
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

        data = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
                "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
                "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
                "truth": load("truth.json"), "usda": load("usda_crop_progress.json"),
                "chcs": json.loads((ROOT / "config" / "chcs.json").read_text(encoding="utf-8"))}
        config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
        r = wb.analyze(data, config)
        json.dumps(r)
        walk_types(r)
        assert r["key"] == "water"
        n_fields = len(data["fields"]["features"])
        assert len(r["fields"]) == n_fields
        done = [v for v in r["fields"].values() if v is not None]
        assert len(done) >= 0.8 * n_fields
        L = r["landscape"]
        n_days = L["season"]["days"]
        assert 100 <= n_days <= 200
        assert 0.0 <= L["season"]["precip_mm"] <= 1500.0
        assert 250.0 <= L["season"]["et0_mm"] <= 900.0           # Iowa June to October reference ET
        assert sum(L["water_status_counts"].values()) == len(done)
        for v in done:
            assert 0.0 <= v["depletion_today_mm"] <= 150.0
            assert 0.0 <= v["available_fraction_today"] <= 1.0
            assert 0 <= v["stress_days"] <= n_days
            assert v["water_status"] in ("wet", "adequate", "dry")
            assert 0.15 <= v["kc_today"] <= 1.20
            assert v["season_etc_mm"] <= v["season_et0_mm"] * 1.2 + 1
            assert len(json.dumps(v)) < 1000
        assert set(r["zones"]) == {f["properties"]["zone_id"] for f in data["zones"]["features"]}
        assert L["forecast"] is None or L["forecast"]["et0_mm"] >= 0.0
        assert isinstance(L["narrative"], str) and L["narrative"]
