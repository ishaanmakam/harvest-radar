"""yield_model.py: a relative yield index from the NDVI curve (green-up, trapezoid integral, crop median, bands,
zone rank, reference-yield estimate). Synthetic 10-day passes with hand-computed answers, then one read-only pass
over data/mock for shapes and ranges."""
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

import yield_model as ym

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"
D = date.fromisoformat

# Twelve passes every ten days from 2026-06-01 (so 06-11, 06-21, 07-01, ... 09-19).
DATES = [(D("2026-06-01") + timedelta(days=10 * i)).isoformat() for i in range(12)]

# Field A (corn): NDVI - 0.20 clipped = 0.20 at green-up, then .30 .50 .60 .60 .60 .50 .30 .10 0 0
#   green-up: 0.30 on 06-11 -> 0.50 on 06-21 crosses 0.40 halfway = 2026-06-16
#   trapezoid: 1.25 + 4 + 5.5 + 6 + 6 + 5.5 + 4 + 2 + 0.5 + 0 = 34.75 NDVI-days
NDVI_A = [0.20, 0.30, 0.50, 0.70, 0.80, 0.80, 0.80, 0.70, 0.50, 0.30, 0.20, 0.15]
# Field B (corn), lower plateau: 1.25 + 3.5 + 4.5 + 5 + 5 + 4.5 + 3.5 + 2 + 0.5 = 29.75
NDVI_B = [0.20, 0.30, 0.50, 0.60, 0.70, 0.70, 0.70, 0.60, 0.50, 0.30, 0.20, 0.15]
# Field C (corn), higher plateau: 1.25 + 4.5 + 6.5 + 7 + 7 + 6.5 + 4.5 + 2 + 0.5 = 39.75
NDVI_C = [0.20, 0.30, 0.50, 0.80, 0.90, 0.90, 0.90, 0.80, 0.50, 0.30, 0.20, 0.15]


def feature(fid, crop, area_ha=10.0, zone="Z01"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": area_ha, "crop": crop, "source": "test"}}


def series(ndvi):
    return {"ndvi": list(ndvi), "nbr": [None if v is None else round(v * 0.6, 3) for v in ndvi],
            "clear_frac": [0.0 if v is None else 0.9 for v in ndvi]}


def rec(fid, peak_ndvi=0.8, harvest_date=None, zone="Z01", stage="harvested"):
    return {"field_id": fid, "zone_id": zone, "stage": stage, "peak_date": "2026-07-21", "peak_ndvi": peak_ndvi,
            "harvest_date": harvest_date, "predicted_harvest_start": None, "predicted_harvest_end": None,
            "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.8, "notes": ""}


def make_data(fields, ts_fields, recs, as_of="2026-10-17"):
    return {
        "fields": {"type": "FeatureCollection", "features": fields},
        "zones": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"zone_id": "Z01", "name": "Zone A1", "centroid": [0.5, 0.5]}},
            {"type": "Feature", "geometry": None, "properties": {"zone_id": "Z02", "name": "Zone A2", "centroid": [1.5, 0.5]}}]},
        "timeseries": {"source": "test", "dates": DATES, "fields": ts_fields},
        "status": {"as_of": as_of, "peak_to_harvest_days": 60, "peak_to_harvest_days_by_crop": {}, "fields": recs},
        "weather": {}, "fires": {"detections": []}, "insights": None, "plans": None, "route": None,
        "truth": None, "usda": None, "chcs": None,
    }


def config(as_of="2026-10-17"):
    return {"as_of": as_of, "season_start": "2026-06-01",
            "thresholds": {"peak_ndvi_min": 0.55, "peak_plateau_drop": 0.04, "harvest_ndvi_max": 0.25}}


@pytest.fixture
def three_corn():
    fields = [feature("A", "corn", 10.0), feature("B", "corn", 20.0), feature("C", "corn", 5.0)]
    ts = {"A": series(NDVI_A), "B": series(NDVI_B), "C": series(NDVI_C)}
    recs = [rec("A"), rec("B"), rec("C")]
    return make_data(fields, ts, recs)


def passes(ndvi, dates=DATES):
    return [(D(x), v) for x, v in zip(dates, ndvi) if v is not None]


# ---------------------------------------------------------------- green-up

class TestGreenUp:
    def test_linear_interpolation_between_the_passes_around_0_40(self):
        # 0.30 on 06-11, 0.50 on 06-21: 0.40 is reached halfway, on 06-16
        gu, known = ym.green_up(passes(NDVI_A))
        assert known is True
        assert gu == pytest.approx(D("2026-06-16").toordinal())

    def test_uneven_crossing_is_weighted(self):
        # 0.35 then 0.55: (0.40 - 0.35) / (0.55 - 0.35) = 0.25 of the ten days = 2.5 days after 06-11
        p = [(D("2026-06-11"), 0.35), (D("2026-06-21"), 0.55), (D("2026-07-01"), 0.7)]
        gu, known = ym.green_up(p)
        assert gu == pytest.approx(D("2026-06-11").toordinal() + 2.5)

    def test_first_clear_pass_already_green_is_unknown(self):
        gu, known = ym.green_up([(D("2026-06-01"), 0.6), (D("2026-06-11"), 0.7)])
        assert gu is None and known is False

    def test_never_reaching_0_40_is_none(self):
        gu, known = ym.green_up([(D("2026-06-01"), 0.2), (D("2026-06-11"), 0.3)])
        assert gu is None and known is False

    def test_empty(self):
        assert ym.green_up([]) == (None, False)

    def test_green_state_tells_never_crossed_from_already_green(self):
        assert ym.green_state(passes(NDVI_A)) == "crossed"
        assert ym.green_state([(D("2026-06-01"), 0.6), (D("2026-06-11"), 0.7)]) == "already_green"
        assert ym.green_state([(D("2026-06-01"), 0.2), (D("2026-06-11"), 0.3)]) == "never"
        assert ym.green_state([]) == "never"


# Lead-in gap series: 06-01 0.20, 06-11 0.30, nothing clear for 40 days, 07-21 0.70, then every 5 days to 09-19.
#   green-up: 0.30 on 06-11 -> 0.70 on 07-21, (0.40 - 0.30) / (0.70 - 0.30) = 0.25 of 40 days = 06-21
#   the window 06-21 -> 09-19 has no clear pass until 07-21: a 30-day blind spot before 13 passes 5 days apart
#   integral: 06-21 (z 0.20) -> 07-21 (z 0.50): 10.5; then 2.5 * (1.05 + 1.15 + 1.20 + 1.20 + 1.15 + 1.05 + 0.90
#             + 0.70 + 0.50 + 0.30 + 0.15 + 0.05) = 23.5; total 34.0
LEAD_DATES = ["2026-06-01", "2026-06-11"] + [(D("2026-07-21") + timedelta(days=5 * i)).isoformat() for i in range(13)]
LEAD_NDVI = [0.20, 0.30, 0.70, 0.75, 0.80, 0.80, 0.80, 0.75, 0.70, 0.60, 0.50, 0.40, 0.30, 0.25, 0.20]


# ---------------------------------------------------------------- integral, peak, plateau

class TestCurve:
    def test_trapezoid_integral_from_green_up_to_last_pass(self):
        c = ym.curve(passes(NDVI_A), end=D("2026-10-17").toordinal(), plateau_drop=0.04)
        assert c["integral"] == pytest.approx(34.75)
        assert c["green_up_known"] is True
        assert c["start"] == pytest.approx(D("2026-06-16").toordinal())
        assert c["end"] == D("2026-09-19").toordinal()            # never past the last clear pass
        assert c["passes"] == 10                                     # 06-21 .. 09-19 inside the window
        assert c["max_gap_days"] == 10

    def test_lower_and_higher_curves(self):
        assert ym.curve(passes(NDVI_B), end=D("2026-10-17").toordinal())["integral"] == pytest.approx(29.75)
        assert ym.curve(passes(NDVI_C), end=D("2026-10-17").toordinal())["integral"] == pytest.approx(39.75)

    def test_cut_at_harvest_between_two_passes(self):
        # to 08-20: 1.25 + 4 + 5.5 + 6 + 6 + 5.5 + 4 = 32.25; 08-20 (0.30) to 08-25 (NDVI interpolated 0.40 -> 0.20):
        # (0.30 + 0.20) / 2 * 5 = 1.25 -> 33.5
        c = ym.curve(passes(NDVI_A), end=D("2026-08-25").toordinal())
        assert c["integral"] == pytest.approx(33.5)
        assert c["end"] == D("2026-08-25").toordinal()
        assert c["passes"] == 7                                      # 06-21 .. 08-20

    def test_values_below_baseline_add_nothing(self):
        p = [(D("2026-06-01"), 0.3), (D("2026-06-11"), 0.5), (D("2026-06-21"), 0.1), (D("2026-07-01"), 0.1)]
        # green-up 06-06; 06-06 (0.20) -> 06-11 (0.30): 1.25; 06-11 (0.30) -> 06-21 (0): 1.5; then 0
        assert ym.curve(p, end=D("2026-07-01").toordinal())["integral"] == pytest.approx(2.75)

    def test_unknown_green_up_starts_at_first_pass_and_is_flagged(self):
        p = [(D("2026-06-01"), 0.6), (D("2026-06-11"), 0.8), (D("2026-06-21"), 0.6)]
        c = ym.curve(p, end=D("2026-07-01").toordinal())
        assert c["green_up_known"] is False and c["green_up"] is None
        assert c["start"] == D("2026-06-01").toordinal()
        assert c["integral"] == pytest.approx((0.4 + 0.6) / 2 * 10 + (0.6 + 0.4) / 2 * 10)   # 10.0

    def test_peak_and_plateau_span_in_days(self):
        c = ym.curve(passes(NDVI_A), end=D("2026-10-17").toordinal(), plateau_drop=0.04)
        assert c["peak_ndvi"] == pytest.approx(0.80)
        assert c["peak_date"] == D("2026-07-11").toordinal()        # the greenest pass, first on ties
        assert c["plateau_days"] == 20                               # 07-11, 07-21, 07-31 within 0.04 of 0.80

    def test_sharp_peak_has_zero_plateau_days(self):
        p = [(D("2026-06-01"), 0.2), (D("2026-06-11"), 0.5), (D("2026-06-21"), 0.8), (D("2026-07-01"), 0.5)]
        assert ym.curve(p, end=D("2026-07-01").toordinal())["plateau_days"] == 0

    def test_gap_in_the_window_is_measured(self):
        p = passes([0.20, 0.30, 0.50, 0.70, None, None, None, 0.70, 0.50, 0.30, 0.20, 0.15])
        c = ym.curve(p, end=D("2026-10-17").toordinal())
        assert c["max_gap_days"] == 40                               # 07-01 -> 08-10

    def test_one_pass_or_end_before_start_is_none(self):
        assert ym.curve([(D("2026-06-01"), 0.6)], end=D("2026-10-17").toordinal()) is None
        assert ym.curve(passes(NDVI_A), end=D("2026-06-10").toordinal()) is None

    def test_lead_in_gap_after_an_interpolated_green_up_counts(self):
        c = ym.curve(passes(LEAD_NDVI, LEAD_DATES), end=D("2026-10-17").toordinal())
        assert c["green_up_known"] is True
        assert c["start"] == pytest.approx(D("2026-06-21").toordinal())
        assert c["passes"] == 13                                     # 07-21 .. 09-19
        assert c["max_gap_days"] == 30                               # 06-21 -> 07-21, not the 5-day pass spacing
        assert c["integral"] == pytest.approx(34.0)

    def test_trailing_gap_to_a_cut_window_end_counts(self):
        # passes 06-01 .20, 06-11 .30, 06-21 .50, 07-01 .70, then nothing clear until 08-20 .70, 08-30 .50, 09-09 .30;
        # harvest cuts the window at 08-15: 07-01 -> 08-15 is 45 unobserved days inside the window
        # integral: 06-16 (z .20) -> 06-21 (.30): 1.25; -> 07-01 (.50): 4.0; -> 08-15 (interpolated .70, z .50): 22.5
        p = [(D("2026-06-01"), 0.2), (D("2026-06-11"), 0.3), (D("2026-06-21"), 0.5), (D("2026-07-01"), 0.7),
             (D("2026-08-20"), 0.7), (D("2026-08-30"), 0.5), (D("2026-09-09"), 0.3)]
        c = ym.curve(p, end=D("2026-08-15").toordinal())
        assert c["passes"] == 2                                      # 06-21, 07-01
        assert c["max_gap_days"] == 45
        assert c["integral"] == pytest.approx(27.75)

    def test_no_pass_inside_a_short_window_reports_its_length(self):
        # green-up 06-16, harvest 06-18: two unobserved days, nothing inside
        c = ym.curve(passes(NDVI_A), end=D("2026-06-18").toordinal())
        assert c["passes"] == 0 and c["max_gap_days"] == 2


# ---------------------------------------------------------------- confidence, estimate, band, rank

class TestRules:
    def test_confidence(self):
        assert ym.confidence(10, 10) == "high"
        assert ym.confidence(8, 15) == "high"
        assert ym.confidence(8, 16) == "medium"
        assert ym.confidence(7, 10) == "medium"
        assert ym.confidence(5, 25) == "medium"
        assert ym.confidence(4, 10) == "low"
        assert ym.confidence(10, 26) == "low"

    def test_estimate_scales_the_reference_and_clips(self):
        e = ym.estimate("corn", 1.0, 10.0)
        assert e["bu_per_acre"] == pytest.approx(200.0)
        assert e["t_per_ha"] == pytest.approx(12.55)
        assert e["production_bu"] == pytest.approx(200.0 * 10.0 * 2.471, rel=1e-3)      # 4942
        assert e["production_t"] == pytest.approx(125.5, rel=1e-3)
        assert ym.estimate("soybean", 0.3, 1.0)["bu_per_acre"] == pytest.approx(30.0)   # clipped at 0.5
        assert ym.estimate("soybean", 2.0, 1.0)["bu_per_acre"] == pytest.approx(84.0)   # clipped at 1.4
        assert ym.estimate("other", 1.0, 1.0) is None

    def test_bands_by_tertile(self):
        cuts = ym.band_cuts([0.86, 1.00, 1.14])
        assert cuts[0] == pytest.approx(0.9533, abs=1e-3) and cuts[1] == pytest.approx(1.0467, abs=1e-3)
        assert ym.band(0.86, cuts) == "low" and ym.band(1.00, cuts) == "typical" and ym.band(1.14, cuts) == "high"
        assert ym.band(None, cuts) is None
        assert ym.band(1.0, ym.band_cuts([1.0])) == "typical"

    def test_rank_in_zone_is_1_for_the_highest_and_shares_ties(self):
        assert ym.ranks({"A": 1.0, "B": 0.86, "C": 1.14}) == {"C": 1, "A": 2, "B": 3}
        assert ym.ranks({"A": 1.0, "B": 1.0, "C": 0.9}) == {"A": 1, "B": 1, "C": 3}


# ---------------------------------------------------------------- analyze on the synthetic landscape

class TestAnalyze:
    def test_shape(self, three_corn):
        out = ym.analyze(three_corn, config())
        assert out["key"] == "yield"
        assert set(out) >= {"key", "fields", "landscape", "zones", "assumptions", "method"}
        assert set(out["fields"]) == {"A", "B", "C"}
        json.dumps(out)                                              # everything serialisable
        assert "uncalibrated" in out["method"] and "yield monitor" in out["method"]
        assert all(isinstance(v, str) for v in out["assumptions"].values())

    def test_index_is_integral_over_crop_median(self, three_corn):
        f = ym.analyze(three_corn, config())["fields"]
        assert f["A"]["integral"] == pytest.approx(34.75, abs=0.01)
        assert f["A"]["index"] == 1.0                                # 34.75 is the median of the three
        assert f["B"]["index"] == pytest.approx(0.86)                # 29.75 / 34.75 = 0.856
        assert f["C"]["index"] == pytest.approx(1.14)                # 39.75 / 34.75 = 1.144
        assert f["A"]["green_up"] == "2026-06-16"
        assert f["A"]["confidence"] == "high"
        assert f["A"]["peak_ndvi"] == pytest.approx(0.8) and f["A"]["plateau_days"] == 20

    def test_estimate_and_production(self, three_corn):
        f = ym.analyze(three_corn, config())["fields"]
        assert f["A"]["estimate"]["bu_per_acre"] == pytest.approx(200.0)
        assert f["B"]["estimate"]["bu_per_acre"] == pytest.approx(172.0)      # 200 * 0.86
        assert f["B"]["estimate"]["t_per_ha"] == pytest.approx(10.79, abs=0.01)
        assert f["B"]["production"]["bu"] == pytest.approx(172.0 * 20.0 * 2.471, rel=1e-3)
        assert f["B"]["production"]["t"] == pytest.approx(10.793 * 20.0, rel=1e-2)

    def test_band_and_rank(self, three_corn):
        f = ym.analyze(three_corn, config())["fields"]
        assert (f["B"]["band"], f["A"]["band"], f["C"]["band"]) == ("low", "typical", "high")
        assert (f["C"]["rank_in_zone"], f["A"]["rank_in_zone"], f["B"]["rank_in_zone"]) == (1, 2, 3)
        assert f["A"]["zone_peers"] == 3

    def test_landscape_and_zones(self, three_corn):
        out = ym.analyze(three_corn, config())
        corn = out["landscape"]["crops"]["corn"]
        assert corn["fields"] == 3 and corn["ha"] == pytest.approx(35.0)
        assert corn["median_index"] == pytest.approx(1.0)
        assert corn["p10_index"] <= corn["median_index"] <= corn["p90_index"]
        bu = 200 * 10 * 2.471 + 172 * 20 * 2.471 + 228 * 5 * 2.471
        assert corn["production_bu"] == pytest.approx(bu, rel=1e-3)
        assert corn["production_t"] == pytest.approx(12.55 * 10 + 10.79 * 20 + 14.31 * 5, rel=1e-2)
        assert corn["mean_bu_per_acre"] == pytest.approx(bu / (35.0 * 2.471), rel=1e-3)
        z = out["zones"]["Z01"]["corn"]
        assert z["fields"] == 3 and z["production_bu"] == pytest.approx(bu, rel=1e-3)
        assert z["mean_index"] == pytest.approx(1.0)
        assert out["landscape"]["reference_integral"]["corn"] == pytest.approx(34.75, abs=0.01)

    def test_harvest_date_cuts_the_window(self, three_corn):
        three_corn["status"]["fields"][0]["harvest_date"] = "2026-08-25"
        f = ym.analyze(three_corn, config())["fields"]
        assert f["A"]["integral"] == pytest.approx(33.5, abs=0.01)
        assert f["A"]["window"]["end"] == "2026-08-25"

    def test_as_of_cuts_the_window(self, three_corn):
        f = ym.analyze(three_corn, config(as_of="2026-08-25"))["fields"]
        assert f["A"]["integral"] == pytest.approx(33.5, abs=0.01)
        assert f["A"]["window"]["end"] == "2026-08-25"

    def test_crop_other_no_peak_no_passes_missing_field(self, three_corn):
        three_corn["fields"]["features"] += [feature("O", "other"), feature("P", "corn"), feature("N", "corn"),
                                             feature("M", "soybean")]
        three_corn["timeseries"]["fields"]["O"] = series(NDVI_A)
        three_corn["timeseries"]["fields"]["P"] = series([0.2, 0.3, 0.45, 0.5, 0.5, 0.45, 0.4, 0.3, 0.2, 0.2, 0.2, 0.2])
        three_corn["timeseries"]["fields"]["N"] = series([None] * 12)
        # M is absent from the timeseries altogether
        three_corn["status"]["fields"] += [rec("O"), rec("P", peak_ndvi=0.50), rec("N", peak_ndvi=None), rec("M")]
        out = ym.analyze(three_corn, config())
        for fid in ("O", "P", "N", "M"):
            assert out["fields"][fid]["index"] is None and out["fields"][fid]["note"]
        assert "other" in out["fields"]["O"]["note"]
        assert out["landscape"]["crops"]["corn"]["fields"] == 3                      # P and N are not in the reference
        assert "soybean" not in out["landscape"]["crops"] or out["landscape"]["crops"]["soybean"]["fields"] == 0

    def test_one_pass_only(self, three_corn):
        three_corn["timeseries"]["fields"]["A"] = series([None] * 5 + [0.8] + [None] * 6)
        out = ym.analyze(three_corn, config())
        assert out["fields"]["A"]["index"] is None and "pass" in out["fields"]["A"]["note"]

    def test_status_missing_falls_back_to_the_passes(self, three_corn):
        three_corn["status"] = None
        out = ym.analyze(three_corn, config())
        assert out["fields"]["A"]["index"] == 1.0                    # raw peak 0.80 >= 0.55 counts as a peak
        assert any("status" in n for n in out["landscape"]["notes"])

    def test_missing_weather_fires_insights_and_prior_do_not_matter(self, three_corn):
        three_corn["weather"] = None
        three_corn["fires"] = None
        three_corn["insights"] = None
        out = ym.analyze(three_corn, config(), prior={"something": {"fields": {}}})
        assert out["fields"]["A"]["index"] == 1.0

    def test_no_timeseries_degrades(self, three_corn):
        three_corn["timeseries"] = None
        out = ym.analyze(three_corn, config())
        assert all(v["index"] is None for v in out["fields"].values())
        assert out["landscape"]["notes"]

    def test_no_fields_degrades(self):
        out = ym.analyze({"fields": None, "timeseries": None, "status": None}, {"as_of": "2026-10-17"})
        assert out["key"] == "yield" and out["fields"] == {} and out["landscape"]["notes"]

    def test_gap_lowers_confidence_and_is_noted(self, three_corn):
        three_corn["timeseries"]["fields"]["A"] = series([0.20, 0.30, 0.50, 0.70, None, None, None, 0.70, 0.50, 0.30, 0.20, 0.15])
        f = ym.analyze(three_corn, config())["fields"]["A"]
        assert f["confidence"] == "low"                              # gap 40 d > 25
        assert f["max_gap_days"] == 40
        assert any("gap" in n for n in f["notes"])

    def test_per_field_dicts_stay_small(self, three_corn):
        out = ym.analyze(three_corn, config())
        assert all(len(json.dumps(v)) < 1000 for v in out["fields"].values())

    def test_interpolated_green_up_across_a_long_gap_lowers_confidence(self, three_corn):
        three_corn["timeseries"]["dates"] = LEAD_DATES
        for fid in ("A", "B", "C"):
            three_corn["timeseries"]["fields"][fid] = series(LEAD_NDVI)
        f = ym.analyze(three_corn, config())["fields"]["A"]
        assert f["green_up"] == "2026-06-21" and f["passes"] == 13
        assert f["max_gap_days"] == 30
        assert f["confidence"] == "low"                              # 30 d blind spot > 25
        assert any("gap" in n and "30" in n for n in f["notes"])

    def test_series_that_never_greens_is_not_scored_despite_the_status_peak(self, three_corn):
        # status.json says peak 0.83, the clear passes never pass 0.35: no canopy to score, and the first clear
        # pass (0.15) was certainly not "already green"
        three_corn["fields"]["features"].append(feature("X", "corn"))
        three_corn["timeseries"]["fields"]["X"] = series([0.15, 0.18, 0.22, 0.25, 0.30, 0.35, 0.30, 0.25, 0.22, 0.20, 0.18, 0.15])
        three_corn["status"]["fields"].append(rec("X", peak_ndvi=0.83))
        out = ym.analyze(three_corn, config())
        x = out["fields"]["X"]
        assert x["index"] is None
        assert "0.40" in x["note"] and "0.35" in x["note"]
        assert "already green" not in x["note"]
        assert out["landscape"]["skipped"].get("never_green") == 1
        assert out["landscape"]["crops"]["corn"]["fields"] == 3

    def test_series_under_the_crop_level_is_not_scored_despite_the_status_peak(self, three_corn):
        # the passes top out at 0.50 (under peak_ndvi_min 0.55) while status.json carries a stale 0.83
        three_corn["fields"]["features"].append(feature("Y", "corn"))
        three_corn["timeseries"]["fields"]["Y"] = series([0.2, 0.3, 0.45, 0.5, 0.5, 0.45, 0.4, 0.3, 0.2, 0.2, 0.2, 0.2])
        three_corn["status"]["fields"].append(rec("Y", peak_ndvi=0.83))
        out = ym.analyze(three_corn, config())
        y = out["fields"]["Y"]
        assert y["index"] is None
        assert "0.50" in y["note"] and "0.55" in y["note"] and "0.83" in y["note"]
        assert out["landscape"]["skipped"].get("no_peak") == 1

    def test_low_peak_threshold_still_needs_green_up(self, three_corn):
        # peak_ndvi_min tuned down to 0.30: a series topping out at 0.35 clears it but never crosses 0.40
        three_corn["fields"]["features"].append(feature("X", "corn"))
        three_corn["timeseries"]["fields"]["X"] = series([0.15, 0.18, 0.22, 0.25, 0.30, 0.35, 0.30, 0.25, 0.22, 0.20, 0.18, 0.15])
        three_corn["status"]["fields"].append(rec("X", peak_ndvi=None))
        cfg = config()
        cfg["thresholds"]["peak_ndvi_min"] = 0.30
        x = ym.analyze(three_corn, cfg)["fields"]["X"]
        assert x["index"] is None and "0.40" in x["note"]

    def test_already_green_note_names_the_first_pass(self, three_corn):
        three_corn["timeseries"]["fields"]["A"] = series([0.60, 0.70, 0.80, 0.80, 0.80, 0.80, 0.70, 0.60, 0.50, 0.30, 0.20, 0.15])
        f = ym.analyze(three_corn, config())["fields"]["A"]
        assert f["green_up_known"] is False and f["green_up"] is None
        assert any("already green" in n and "2026-06-01" in n and "0.60" in n for n in f["notes"])

    def test_unfinished_season_is_noted(self, three_corn):
        # no harvest date and as_of mid-season: the window stops at as_of and the integral may still grow
        f = ym.analyze(three_corn, config(as_of="2026-08-25"))["fields"]["A"]
        assert any("not harvested" in n and "2026-08-25" in n for n in f["notes"])
        three_corn["status"]["fields"][0]["harvest_date"] = "2026-08-25"
        f = ym.analyze(three_corn, config())["fields"]["A"]
        assert not any("not harvested" in n for n in f["notes"])


# ---------------------------------------------------------------- data/mock (read-only, shapes and ranges)

@pytest.mark.skipif(not (MOCK / "timeseries.json").exists(), reason="data/mock not present")
def test_mock_landscape_is_sane():
    def load(n, default=None):
        p = MOCK / n
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    data = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
            "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
            "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
            "truth": load("truth.json"), "usda": load("usda_crop_progress.json"), "chcs": None}
    cfg = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
    out = ym.analyze(data, cfg)
    json.dumps(out)
    n_fields = len(data["fields"]["features"])
    assert out["key"] == "yield" and len(out["fields"]) == n_fields
    scored = {k: v for k, v in out["fields"].items() if v.get("index") is not None}
    assert len(scored) >= 0.7 * n_fields
    crops = {f["properties"]["field_id"]: f["properties"]["crop"] for f in data["fields"]["features"]}
    assert all(out["fields"][k]["index"] is None for k, c in crops.items() if c == "other")
    for v in scored.values():
        assert 0.3 <= v["index"] <= 2.0
        assert v["confidence"] in ("high", "medium", "low")
        assert v["band"] in ("low", "typical", "high")
        assert v["rank_in_zone"] >= 1
        assert 0.4 <= v["peak_ndvi"] <= 1.0
        assert v["integral"] > 0
        assert 0.5 * 60 <= v["estimate"]["bu_per_acre"] <= 1.4 * 200
        assert len(json.dumps(v)) < 1000
    for crop in ("corn", "soybean"):
        c = out["landscape"]["crops"][crop]
        assert c["fields"] > 0 and c["ha"] > 0 and c["production_bu"] > 0 and c["production_t"] > 0
        assert abs(c["median_index"] - 1.0) < 0.05                   # the median field defines the index
        assert c["p10_index"] <= c["median_index"] <= c["p90_index"]
    assert set(out["zones"]) <= {f["properties"]["zone_id"] for f in data["zones"]["features"]}
    ranks = [v["rank_in_zone"] for v in scored.values()]
    assert 1 in ranks
    for s in out["assumptions"].values():
        assert isinstance(s, str) and s
