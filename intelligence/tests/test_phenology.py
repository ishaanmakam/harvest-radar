"""phenology.py: the shape of each field's season (green-up, plateau, senescence) and a second harvest date read off
the fitted decline, compared with the lag model's window. Synthetic fixtures pin the math; one read-only test runs
on data/mock for shapes and ranges."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import phenology

D = date.fromisoformat
AS_OF = "2026-10-17"
START = D("2026-06-03")
DATES = [(START + timedelta(days=5 * i)).isoformat() for i in range(28)]     # 2026-06-03 .. 2026-10-16, like the mock
CONFIG = {"as_of": AS_OF, "season_start": "2026-06-01",
          "thresholds": {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25, "peak_plateau_drop": 0.04,
                         "default_peak_to_harvest_days": 58}}

# Standing corn field, hand-built so the numbers can be checked by hand:
#   green-up: 0.30 on 06-08 -> 0.55 on 06-13 crosses 0.40 at 0.4 of the gap = 2 days -> 2026-06-10
#   plateau: 0.85 on passes 5..10, 0.82 on pass 11 (>= 0.85 - 0.04) -> 06-28 .. 07-28 = 30 days, centre 07-13
#   onset: first smoothed value <= 0.90 * 0.85 = 0.765 is 0.75 on pass 14 (2026-08-12)
#   decline: 0.75 falling 0.025 per 5-day pass = -0.005 per day; 0.25 is reached 100 days after 08-12 -> 2026-11-20
STANDING = [0.20, 0.30, 0.55, 0.70, 0.80, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.82, 0.80, 0.78,
            0.75, 0.725, 0.70, 0.675, 0.65, 0.625, 0.60, 0.575, 0.55, 0.525, 0.50, 0.475, 0.45, 0.425]
# Harvested corn field: same start, plateau held at 0.80, onset 0.75 on pass 20 (2026-09-11), then 0.65, 0.55, 0.45
# (-0.02 per day), harvested 2026-09-28, residue 0.15 afterwards.
HARVESTED = [0.20, 0.30, 0.55, 0.70, 0.80, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.82, 0.80, 0.80,
             0.80, 0.80, 0.80, 0.80, 0.80, 0.80, 0.75, 0.65, 0.55, 0.45, 0.15, 0.15, 0.15, 0.15]


def ramp(k):
    """A standing field whose green-up ramp starts k passes later (green-up = DATES[k + 1] + 2 days)."""
    vals = [0.20] * k + STANDING
    return vals[:28]


def feature(fid, crop="corn", zone="Z01"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-93.5, 42.0], [-93.49, 42.0], [-93.49, 42.01],
                                                                                [-93.5, 42.01], [-93.5, 42.0]]]},
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": 20.0, "crop": crop, "source": "test"}}


def rec(fid, stage, **kw):
    base = {"field_id": fid, "zone_id": "Z01", "stage": stage, "peak_date": "2026-07-13", "peak_ndvi": 0.85,
            "harvest_date": None, "predicted_harvest_start": None, "predicted_harvest_end": None,
            "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.5, "notes": ""}
    base.update(kw)
    return base


def make_data(series, feats, recs=None, truth=None, lag_by_crop=None, dates=DATES):
    ts = {"source": "test", "dates": list(dates),
          "fields": {fid: {"ndvi": list(v), "nbr": [0.3 if x is not None else None for x in v],
                           "clear_frac": [1.0 if x is not None else 0.0 for x in v]} for fid, v in series.items()}}
    status = None
    if recs is not None:
        status = {"as_of": AS_OF, "peak_to_harvest_days": 69.0,
                  "peak_to_harvest_days_by_crop": lag_by_crop if lag_by_crop is not None else {"corn": 76.0, "soybean": 60.0},
                  "fields": recs}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"zone_id": "Z01", "name": "north west", "centroid": [-93.5, 42.05]}},
        {"type": "Feature", "geometry": None, "properties": {"zone_id": "Z02", "name": "north", "centroid": [-93.47, 42.05]}}]}
    return {"fields": {"type": "FeatureCollection", "features": feats}, "zones": zones, "timeseries": ts, "status": status,
            "weather": {}, "fires": {"detections": []}, "insights": None, "plans": None, "route": None,
            "truth": truth, "usda": None, "chcs": None}


def one(series, stage="maturing", **kw):
    data = make_data({"C1": series}, [feature("C1")], [rec("C1", stage, **kw)])
    return phenology.analyze(data, CONFIG)["fields"]["C1"]


class TestSeasonShape:
    def test_green_up_is_interpolated_between_the_passes_around_0_40(self):
        f = one(STANDING)
        assert f["green_up_date"] == "2026-06-10"

    def test_peak_and_plateau(self):
        f = one(STANDING)
        assert f["peak_date"] == "2026-07-13" and f["peak_ndvi"] == pytest.approx(0.85)
        assert f["peak_source"] == "status"
        assert f["plateau_days"] == 30
        assert f["days_green_up_to_peak"] == 33                      # 06-10 -> 07-13
        assert f["season_length_days"] == 129                        # 06-10 -> as_of 10-17

    def test_peak_from_the_smoothed_series_when_status_is_missing(self):
        data = make_data({"C1": STANDING}, [feature("C1")], recs=None)
        f = phenology.analyze(data, CONFIG)["fields"]["C1"]
        assert f["peak_source"] == "series"
        assert f["peak_ndvi"] == pytest.approx(0.85)
        assert f["peak_date"] == "2026-07-13"                        # time centre of the 06-28 .. 07-28 plateau
        assert f["plateau_days"] == 30

    def test_senescence_onset_rate_and_curve_harvest_date(self):
        f = one(STANDING)
        assert f["senescence_onset"] == "2026-08-12"
        assert f["senescence_rate"] == pytest.approx(-0.005, abs=1e-4)
        assert f["fit_points"] == 14
        assert f["curve_harvest_date"] == "2026-11-20"
        assert f["curve_status"] == "ok"
        assert f["observed_harvest_date"] is None

    def test_harvested_field_reports_the_observed_date_and_fits_only_standing_passes(self):
        f = one(HARVESTED, stage="harvested", harvest_date="2026-09-28")
        assert f["senescence_onset"] == "2026-09-11"
        assert f["fit_points"] == 4                                  # 09-11, 09-16, 09-21, 09-26; residue passes excluded
        assert f["senescence_rate"] == pytest.approx(-0.02, abs=1e-4)
        assert f["curve_harvest_date"] is None and f["curve_status"] == "harvested"
        assert f["observed_harvest_date"] == "2026-09-28"
        assert f["season_length_days"] == 110                        # 06-10 -> 09-28
        assert f["agreement"] is None and f["ensemble_harvest_date"] is None

    def test_flat_decline_is_not_extrapolated(self):
        vals = STANDING[:14] + [round(0.75 - 0.005 * i, 4) for i in range(14)]       # -0.001 per day
        f = one(vals)
        assert f["senescence_rate"] == pytest.approx(-0.001, abs=1e-4)
        assert f["curve_harvest_date"] is None and f["curve_status"] == "flat"

    def test_crossing_too_far_past_the_last_pass_is_no_forecast(self):
        vals = STANDING[:14] + [round(0.75 - 0.0125 * i, 4) for i in range(14)]      # -0.0025 per day: 0.25 in 200 days
        f = one(vals)
        assert f["curve_harvest_date"] is None and f["curve_status"] == "too_far"

    def test_too_few_decline_points(self):
        vals = STANDING[:26] + [0.75, 0.70]                          # plateau-ish, then two passes of decline
        vals[12:26] = [0.85] * 14
        f = one(vals)
        assert f["senescence_onset"] == "2026-10-11"
        assert f["senescence_rate"] is None and f["fit_points"] == 2
        assert f["curve_harvest_date"] is None and f["curve_status"] == "few_points"

    def test_no_onset_while_still_on_the_plateau(self):
        vals = STANDING[:12] + [0.85] * 16
        f = one(vals, stage="growing")
        assert f["senescence_onset"] is None and f["curve_status"] == "no_onset"


class TestAgreementWithTheLagModel:
    def test_later_than_the_window(self):
        f = one(STANDING, predicted_harvest_start="2026-10-20", predicted_harvest_end="2026-11-03")   # centre 10-27
        assert f["agreement"] == "later"
        assert f["delta_days"] == 24                                 # 11-20 minus 10-27
        assert f["lag_centre"] == "2026-10-27"
        assert f["ensemble_harvest_date"] == "2026-11-08"            # halfway between 10-27 and 11-20

    def test_inside_the_window_agrees(self):
        f = one(STANDING, predicted_harvest_start="2026-11-15", predicted_harvest_end="2026-11-25")
        assert f["agreement"] == "agree" and f["delta_days"] == 0
        assert f["ensemble_harvest_date"] == "2026-11-20"

    def test_within_three_days_of_the_window_agrees(self):
        f = one(STANDING, predicted_harvest_start="2026-11-11", predicted_harvest_end="2026-11-17")   # centre 11-14
        assert f["agreement"] == "agree" and f["delta_days"] == 6
        assert f["ensemble_harvest_date"] == "2026-11-17"

    def test_earlier_than_the_window(self):
        f = one(STANDING, predicted_harvest_start="2026-12-01", predicted_harvest_end="2026-12-11")   # centre 12-06
        assert f["agreement"] == "earlier" and f["delta_days"] == -16

    def test_ready_stage_uses_as_of_as_the_window_start(self):
        f = one(STANDING, stage="ready", predicted_harvest_start="2026-10-15", predicted_harvest_end="2026-10-21")
        assert f["lag_centre"] == "2026-10-19"                       # (10-17 + 10-21) / 2, not (10-15 + 10-21) / 2
        assert f["agreement"] == "later" and f["delta_days"] == 32

    def test_no_window_means_no_agreement(self):
        f = one(STANDING, stage="growing")
        assert f["agreement"] is None and f["delta_days"] is None and f["ensemble_harvest_date"] is None


class TestCohortsAndLandscape:
    def test_green_up_tertiles_within_crop(self):
        series = {f"C{k}": ramp(k) for k in range(6)}                # green-ups 06-10, 06-15, 06-20, 06-25, 06-30, 07-05
        series["S1"] = STANDING
        feats = [feature(f"C{k}", "corn") for k in range(6)] + [feature("S1", "soybean", "Z02")]
        out = phenology.analyze(make_data(series, feats), CONFIG)
        cohorts = {fid: out["fields"][fid]["cohort"] for fid in series}
        assert cohorts == {"C0": "early", "C1": "early", "C2": "mid", "C3": "mid", "C4": "late", "C5": "late", "S1": None}
        corn = out["landscape"]["crops"]["corn"]
        assert corn["cohorts"] == {"early": 2, "mid": 2, "late": 2, "none": 0}
        assert corn["median_green_up"] == "2026-06-22"               # lower middle of 06-20 and 06-25
        assert corn["median_senescence_rate"] == pytest.approx(-0.005, abs=1e-4)
        assert out["landscape"]["crops"]["soybean"]["cohorts"]["none"] == 1
        assert out["zones"]["Z01"]["n"] == 6 and out["zones"]["Z02"]["n"] == 1
        assert out["zones"]["Z02"]["name"] == "north"

    def test_agreement_counts_per_crop(self):
        series = {"A": STANDING, "B": STANDING, "H": HARVESTED}
        recs = [rec("A", "maturing", predicted_harvest_start="2026-11-15", predicted_harvest_end="2026-11-25"),
                rec("B", "maturing", predicted_harvest_start="2026-10-20", predicted_harvest_end="2026-11-03"),
                rec("H", "harvested", harvest_date="2026-09-28")]
        out = phenology.analyze(make_data(series, [feature(x) for x in series], recs), CONFIG)
        corn = out["landscape"]["crops"]["corn"]
        assert corn["agreement"] == {"agree": 1, "earlier": 0, "later": 1, "rate": 0.5}
        assert out["landscape"]["counts"]["fields"] == 3
        assert out["landscape"]["counts"]["harvested"] == 1


class TestEvaluationAgainstTruth:
    def test_curve_and_lag_errors_for_a_harvested_field(self):
        # Truth harvest 2026-09-28. As if standing: only passes before 09-27 (through 09-26) -> same four decline
        # points, 0.75 at 09-11 falling 0.02 a day crosses 0.25 after 25 days = 2026-10-06: 8 days late.
        # Lag model: status peak 07-13 + corn lag 76 days = 2026-09-27: 1 day early.
        truth = {"fields": {"H": {"crop": "corn", "peak_date": "2026-07-14", "peak_ndvi": 0.85, "harvest_date": "2026-09-28",
                                  "burned": False, "burn_date": None, "sown": False},
                            "C1": {"crop": "corn", "peak_date": "2026-07-14", "peak_ndvi": 0.85, "harvest_date": "2026-11-18",
                                   "burned": False, "burn_date": None, "sown": False}}}       # still standing: not scored
        data = make_data({"H": HARVESTED, "C1": STANDING}, [feature("H"), feature("C1")],
                         [rec("H", "harvested", harvest_date="2026-09-28"), rec("C1", "maturing")], truth=truth)
        ev = phenology.analyze(data, CONFIG)["landscape"]["evaluation"]
        assert ev["n_harvested_in_truth"] == 1
        assert ev["curve"] == {"n": 1, "mae_days": 8.0, "bias_days": 8.0}
        assert ev["lag"] == {"n": 1, "mae_days": 1.0, "bias_days": -1.0}
        assert ev["paired"]["n"] == 1

    def test_no_truth_means_no_evaluation(self):
        out = phenology.analyze(make_data({"C1": STANDING}, [feature("C1")], [rec("C1", "maturing")]), CONFIG)
        assert out["landscape"]["evaluation"] is None


class TestEdges:
    def test_field_absent_from_timeseries(self):
        data = make_data({"C1": STANDING}, [feature("C1"), feature("X9")], [rec("C1", "maturing")])
        out = phenology.analyze(data, CONFIG)
        f = out["fields"]["X9"]
        assert f["n_clear"] == 0 and f["green_up_date"] is None and f["curve_status"] == "no_data"
        assert any("X9" in n for n in out["landscape"]["notes"])

    def test_all_null_series_and_one_pass(self):
        nulls = [None] * 28
        single = [None] * 27 + [0.62]
        data = make_data({"N": nulls, "P": single}, [feature("N"), feature("P")], recs=None)
        out = phenology.analyze(data, CONFIG)
        n, p = out["fields"]["N"], out["fields"]["P"]
        assert n["n_clear"] == 0 and n["peak_ndvi"] is None and n["cohort"] is None
        assert p["n_clear"] == 1 and p["peak_ndvi"] == pytest.approx(0.62) and p["peak_date"] == "2026-10-16"
        assert p["green_up_date"] is None                            # already green on its first clear pass
        assert p["senescence_onset"] is None and p["senescence_rate"] is None and p["curve_harvest_date"] is None

    def test_green_up_unknown_when_first_pass_already_green(self):
        f = one([0.6] * 12 + STANDING[12:])
        assert f["green_up_date"] is None and f["days_green_up_to_peak"] is None and f["season_length_days"] is None

    def test_missing_weather_fires_status_and_fields_do_not_raise(self):
        data = make_data({"C1": STANDING}, [feature("C1")], recs=None)
        data["weather"] = None
        data["fires"] = None
        data["fields"] = None                                        # fall back to the time series roster
        out = phenology.analyze(data, CONFIG)
        assert out["key"] == "phenology" and "C1" in out["fields"]
        assert out["fields"]["C1"]["crop"] is None

    def test_crop_other_is_kept(self):
        data = make_data({"O1": STANDING}, [feature("O1", "other")], recs=None)
        f = phenology.analyze(data, CONFIG)["fields"]["O1"]
        assert f["crop"] == "other" and f["green_up_date"] == "2026-06-10" and f["cohort"] is None

    def test_no_timeseries_at_all(self):
        data = make_data({}, [feature("C1")], recs=None)
        data["timeseries"] = None
        out = phenology.analyze(data, CONFIG)
        assert out["fields"]["C1"]["n_clear"] == 0
        assert out["landscape"]["notes"]

    def test_output_is_plain_json(self):
        out = phenology.analyze(make_data({"C1": STANDING, "H": HARVESTED}, [feature("C1"), feature("H")],
                                          [rec("C1", "maturing"), rec("H", "harvested", harvest_date="2026-09-28")]), CONFIG)
        text = json.dumps(out)
        assert "NaN" not in text and "Infinity" not in text
        assert set(out) == {"key", "fields", "landscape", "zones", "assumptions", "method"}
        assert all(len(json.dumps(v)) < 1000 for v in out["fields"].values())


ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"


@pytest.mark.skipif(not (MOCK / "timeseries.json").exists(), reason="mock data missing")
def test_mock_season_shapes_and_ranges():
    def load(name, default=None):
        p = MOCK / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    data = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
            "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
            "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
            "truth": load("truth.json"), "usda": load("usda_crop_progress.json"), "chcs": None}
    config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
    out = phenology.analyze(data, config)
    json.dumps(out)
    n = len(data["fields"]["features"])
    assert len(out["fields"]) == n
    season_start, as_of = D(config["season_start"]), D(config["as_of"])
    for fid, f in out["fields"].items():
        assert len(json.dumps(f)) < 1000
        if f["green_up_date"]:
            assert season_start <= D(f["green_up_date"]) <= as_of
        if f["peak_ndvi"] is not None:
            assert 0.0 <= f["peak_ndvi"] <= 1.0
        if f["senescence_rate"] is not None:
            assert -0.2 < f["senescence_rate"] < 0.01              # a crop already dry before onset can fit slightly up
        if f["curve_harvest_date"]:
            assert f["senescence_rate"] < phenology.MIN_DECLINE_SLOPE
        if f["curve_harvest_date"]:
            assert D(f["curve_harvest_date"]) <= as_of + timedelta(days=phenology.CURVE_MAX_DAYS_AHEAD)
        assert f["agreement"] in ("agree", "earlier", "later", None)
        assert f["cohort"] in ("early", "mid", "late", None)
    assert sum(1 for f in out["fields"].values() if f["green_up_date"]) > n // 2
    assert sum(1 for f in out["fields"].values() if f["curve_harvest_date"]) > 0
    crops = out["landscape"]["crops"]
    assert {"corn", "soybean"} <= set(crops)
    for c in crops.values():
        assert sum(c["cohorts"].values()) == c["n"]
    assert len(out["zones"]) == len(data["zones"]["features"])
    ev = out["landscape"]["evaluation"]
    assert ev and ev["curve"]["n"] > 0 and ev["lag"]["n"] > 0
    assert ev["curve"]["mae_days"] >= 0 and ev["lag"]["mae_days"] >= 0
